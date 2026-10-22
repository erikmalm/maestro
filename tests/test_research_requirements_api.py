"""Requirements-route contracts with disposable data and synthetic local inference."""
from contextlib import closing
from copy import deepcopy
from datetime import datetime
import json
import sqlite3
import unittest
from unittest.mock import patch
import uuid

from fastapi.testclient import TestClient

from backend import app as backend
from backend.memory import MemoryStore
from backend.provider import DEFAULT
from backend.web_search import ENDPOINT
from tests import test_research_api as fixtures


REQUEST = "Compare Acme revenue in USD for FY2025."


def ready_assessment():
    return {
        "version": 1, "decision": "ready",
        "subjects": [{"id": "s1", "kind": "company", "name": "Acme", "identifiers": []}],
        "items": [{"id": "i1", "subject_ids": ["s1"], "criterion": "revenue", "period": "FY2025",
                   "unit": None, "currency": "USD", "freshness": None}],
        "constraints": [], "clarifications": [],
    }


class ResearchRequirementsApiTests(unittest.TestCase):
    def setUp(self):
        # Reuse setup without inheriting/discovering the lookup test class twice.
        self.fixture = fixtures.ResearchApiTests("test_missing_or_malformed_identifiers_use_generic_unavailable_responses")
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.setUp()
        self.client, self.headers = self.fixture.client, self.fixture.headers
        self.model = "synthetic-requirements:latest"
        self.config = {**DEFAULT, "protocol": "ollama", "base_url": "http://127.0.0.1:11434",
                       "model": self.model, "max_output_tokens": 4096, "ollama_context_tokens": 131072,
                       "input_usd_per_million": 0, "output_usd_per_million": 0, "pricing_verified": True}
        with backend.provider().transaction() as state:
            state.update(config=deepcopy(self.config), models=[self.model], tested_at=None)
        self.requests = []
        self.raw = ready_assessment()
        self.reply = None
        self.show_hook = self.chat_hook = None
        self.enterContext(patch("backend.provider.background_network", side_effect=self.inference))
        self.enterContext(patch.object(MemoryStore, "recall", side_effect=AssertionError("Private memory recall")))

    def inference(self, config, path, payload, deadline):
        self.requests.append((deepcopy(config), path, deepcopy(payload)))
        if path == "/api/show":
            if self.show_hook:
                self.show_hook()
            return {"details": {"format": "gguf"}, "capabilities": ["completion"]}
        if path == "/api/ps":
            return {"models": []}
        self.assertEqual(path, "/api/chat")
        if self.chat_hook:
            self.chat_hook()
        return {"done": True, "message": {"role": "assistant", "content": self.reply if self.reply is not None
                                          else json.dumps(self.raw)},
                "prompt_eval_count": 41, "eval_count": 7}

    def payload(self, **changes):
        return {"submission_id": uuid.uuid4().hex, "request": REQUEST, "query": "", "chat_id": None,
                "workflow": "requirements", **changes}

    def submit(self, payload=None, **changes):
        return self.client.post("/api/research/runs", json=payload or self.payload(**changes), headers=self.headers)

    def queued(self, **changes):
        with patch.object(backend, "execute_research_run") as deferred:
            response = self.submit(**changes)
        self.assertEqual(response.status_code, 202, response.text)
        run = response.json()["run"]
        self.assertTrue(response.json()["created"])
        self.assertEqual(run["state"], "queued")
        self.assertEqual(run["workflow"], "requirements")
        deferred.assert_called_once_with(run["id"])
        return run

    def get(self, run_id):
        response = self.client.get("/api/research/runs/" + run_id)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def run_now(self, **changes):
        response = self.submit(**changes)
        self.assertEqual(response.status_code, 202, response.text)
        return self.get(response.json()["run"]["id"])

    def chats(self):
        return [request for request in self.requests if request[1] == "/api/chat"]

    def assert_released(self):
        with closing(sqlite3.connect(backend.DATABASE)) as db:
            self.assertFalse(backend.ResearchStore.owns_foreground(db))
        self.assertFalse(any(entry["status"] in ("reserved", "local_unknown", "uncertain")
                             for entry in backend.provider().read_state()["ledger"]))

    def assert_actual_usage(self, run):
        self.assertEqual(run["budget"]["consumed"]["model_calls"], 1)
        self.assertEqual(run["budget"]["consumed"]["tokens"], 48)
        self.assertEqual(run["budget"]["consumed"]["hosted_searches"], 0)
        self.assertTrue(all(value == 0 for value in run["budget"]["reserved"].values()))
        entry = backend.provider().read_state()["ledger"][-1]
        self.assertEqual(entry["status"], "settled")
        self.assertEqual((entry["input_tokens"], entry["output_tokens"]), (41, 7))

    def assert_no_stage_output(self, run_id):
        with closing(sqlite3.connect(backend.DATABASE)) as db:
            rows = db.execute("SELECT output FROM research_stages WHERE run_id=?", (run_id,)).fetchall()
        self.assertTrue(all(row[0] is None for row in rows))

    def test_valid_assessment_uses_one_real_adapter_call_and_releases_foreground(self):
        # The stage must not fall through to the old saved-evidence workflow.
        with patch.object(backend, "search_context_candidates", side_effect=AssertionError("Evidence lookup")):
            run = self.run_now()
        self.assertEqual(run["workflow"], "requirements")
        self.assertEqual(run["state"], "partial")
        self.assertIsNone(run["result"])
        self.assertEqual(run["assessment"]["decision"], "ready")
        self.assertEqual(run["assessment"]["completion_criterion"], REQUEST)
        self.assertTrue(run["assessment"]["items"][0]["mandatory"])
        self.assertNotIn("answer", run["assessment"])
        kinds = [event["kind"] for event in run["events"]]
        self.assertIn("requirements_started", kinds)
        self.assertIn("requirements_recorded", kinds)
        self.assertFalse(any(kind.startswith("local_lookup") for kind in kinds))
        self.assertEqual(len(self.chats()), 1)
        self.assertEqual(self.chats()[0][2]["model"], self.model)
        self.assertEqual(self.chats()[0][2]["options"]["num_predict"], 4096)
        prompt = self.chats()[0][2]["messages"][0]["content"]
        envelope = json.loads(prompt.split("\nServer-authored stage assignment:\n", 1)[1])
        self.assertEqual(envelope["schema"], self.chats()[0][2]["format"])
        self.assert_actual_usage(run)
        self.assert_released()
        # A second ordinary foreground run is admitted after the assessment.
        self.queued()

    def test_material_ambiguity_publishes_fixed_clarifications_and_stops(self):
        request = "Assess ABC last quarter in kr."
        self.raw = {"version": 1, "decision": "clarify", "subjects": [], "items": [], "constraints": [],
                    "clarifications": [{"kind": kind, "item_ids": [], "quote": quote}
                                       for kind, quote in (("security", "ABC"), ("period", "last quarter"),
                                                           ("currency", "kr"))]}
        run = self.run_now(request=request)
        self.assertEqual(run["state"], "clarification")
        self.assertIsNone(run["result"])
        self.assertEqual(run["assessment"]["decision"], "clarify")
        self.assertEqual(run["assessment"]["completion_criterion"], request)
        self.assertEqual(run["assessment"]["clarifications"][0]["question"],
                         'Which security or listing do you mean by "ABC"?')
        self.assertIn("clarification_needed", [event["kind"] for event in run["events"]])
        self.assertEqual(len(self.chats()), 1)
        self.assert_actual_usage(run)
        self.assert_released()

    def test_short_request_fits_a_16384_context_and_1024_output_configuration(self):
        with backend.provider().transaction() as state:
            state["config"].update(ollama_context_tokens=16384, max_output_tokens=1024)
        run = self.run_now()
        self.assertEqual(run["state"], "partial")
        self.assertEqual(len(self.chats()), 1)
        self.assertEqual(self.chats()[0][2]["options"]["num_predict"], 1024)
        self.assert_actual_usage(run)
        self.assert_released()

    def test_security_currency_period_and_source_constraints_remain_distinct(self):
        request = ("Compare ACME A revenue FY2024 in USD and ACME B revenue FY2025 in SEK. "
                   "Use annual reports only; do not search; use documents published since 2025-01-01.")
        self.raw = {"version": 1, "decision": "ready", "subjects": [
            {"id": "a", "kind": "security", "name": "ACME A", "identifiers": [{"kind": "listing", "text": "ACME A"}]},
            {"id": "b", "kind": "security", "name": "ACME B", "identifiers": [{"kind": "listing", "text": "ACME B"}]},
        ], "items": [
            {"id": "ia", "subject_ids": ["a"], "criterion": "ACME A revenue FY2024 in USD", "period": "FY2024",
             "unit": None, "currency": "USD", "freshness": "published since 2025-01-01"},
            {"id": "ib", "subject_ids": ["b"], "criterion": "ACME B revenue FY2025 in SEK", "period": "FY2025",
             "unit": None, "currency": "SEK", "freshness": "published since 2025-01-01"},
        ], "constraints": [
            {"kind": "source", "strength": "require", "item_ids": [], "criterion": "annual reports only"},
            {"kind": "search", "strength": "forbid", "item_ids": [], "criterion": "do not search"},
        ], "clarifications": []}
        run = self.run_now(request=request)
        self.assertEqual(run["state"], "partial")
        self.assertEqual([(item["subject_ids"], item["period"], item["currency"])
                          for item in run["assessment"]["items"]], [(["a"], "FY2024", "USD"), (["b"], "FY2025", "SEK")])
        self.assertEqual(run["assessment"]["constraints"], self.raw["constraints"])
        self.assertEqual(len(self.chats()), 1)
        self.assert_released()

    def test_unrelated_chat_memory_accounting_and_archive_never_enter_the_stage(self):
        chat_id = self.fixture.chat()
        history = "PRIVATE_HISTORY_MUST_NOT_REACH_REQUIREMENTS_24"
        memory = "PRIVATE_MEMORY_PROVENANCE_ONLY_24"
        with backend.workspace_transaction() as state:
            chat = next(chat for chat in state["chats"] if chat["id"] == chat_id)
            chat["messages"].append({"id": uuid.uuid4().hex, "role": "assistant", "text": history,
                                     "memory_ids": [memory], "at": backend.now()})
        self.fixture.capture(content="PRIVATE_ARCHIVED_BODY_REQUIREMENTS_24")
        before = self.fixture.source_snapshot()
        with patch.object(backend, "search_context_candidates", side_effect=AssertionError("Evidence lookup")):
            run = self.run_now(chat_id=chat_id)
        rendered = json.dumps(self.chats()[0][2])
        for private in (history, memory, "PRIVATE_ARCHIVED_BODY_REQUIREMENTS_24"):
            self.assertNotIn(private, rendered)
        self.assertFalse(run["permissions"]["memory_recall"])
        self.assertFalse(run["permissions"]["hosted_allowed"])
        self.assertTrue(run["permissions"]["memory_derived"])
        self.assertNotIn("tools", self.chats()[0][2])
        self.assertEqual(self.fixture.source_snapshot(), before)
        self.assert_released()

    def test_malformed_or_unproven_assessments_fail_without_persisting_model_output_or_retry(self):
        valid = ready_assessment()
        invented = deepcopy(valid)
        invented["items"][0]["currency"] = "EUR"
        answered = {**valid, "answer": "MODEL_PROSE_MUST_NOT_BE_SAVED_24"}
        for raw, reply in (({}, None), (invented, None), (answered, None), (valid, "not JSON")):
            with self.subTest(raw=raw, reply=reply):
                self.raw, self.reply = raw, reply
                before = len(self.chats())
                run = self.run_now()
                self.assertEqual(run["state"], "failed")
                self.assertIsNone(run["result"])
                self.assertIsNone(run.get("assessment"))
                self.assertEqual(run["events"][-1]["kind"], "failed")
                self.assertEqual(run["events"][-1]["summary"],
                                 "Requirements assessment did not produce an accepted result. No answer was produced.")
                self.assertNotIn("MODEL_PROSE_MUST_NOT_BE_SAVED_24", json.dumps(run))
                self.assertEqual(len(self.chats()), before + 1)
                self.assert_no_stage_output(run["id"])
                self.assert_actual_usage(run)
                self.assert_released()

    def test_observed_search_constraint_paraphrase_is_withheld_but_exact_request_span_succeeds(self):
        request = ("Research Novo Nordisk revenue in DKK for FY2025 using annual reports only; "
                   "do not search.")
        self.raw = {
            "version": 1, "decision": "ready",
            "subjects": [{"id": "s1", "kind": "company", "name": "Novo Nordisk", "identifiers": []}],
            "items": [{"id": "i1", "subject_ids": ["s1"], "criterion": "revenue", "period": "FY2025",
                       "unit": None, "currency": "DKK", "freshness": None}],
            "constraints": [
                {"kind": "source", "strength": "require", "item_ids": [], "criterion": "annual reports only"},
                {"kind": "search", "strength": "forbid", "item_ids": [], "criterion": "no web search"},
            ], "clarifications": [],
        }
        # This valid JSON differs only in a plausible paraphrase of the user's
        # search restriction. It must not be repaired or accepted as provenance.
        rejected = self.run_now(request=request)
        self.assertEqual(rejected["state"], "failed")
        self.assertIsNone(rejected["assessment"])
        self.assertIsNone(rejected["result"])
        self.assert_no_stage_output(rejected["id"])
        self.assertNotIn("no web search", json.dumps(rejected))
        self.assertEqual(len(self.chats()), 1)
        self.assertEqual(rejected["events"][-1]["kind"], "failed")
        self.assert_actual_usage(rejected)
        self.assert_released()

        self.raw["constraints"][1]["criterion"] = "do not search"
        accepted = self.run_now(request=request)
        self.assertNotEqual(accepted["id"], rejected["id"])
        self.assertEqual(accepted["state"], "partial")
        self.assertEqual(accepted["assessment"]["completion_criterion"], request)
        self.assertEqual(accepted["assessment"]["constraints"], self.raw["constraints"])
        self.assertIsNone(accepted["result"])
        self.assertEqual(len(self.chats()), 2)
        self.assert_actual_usage(accepted)
        self.assert_released()

    def test_workflow_is_immutable_idempotent_and_old_clients_keep_lookup(self):
        payload = self.payload()
        with patch.object(backend, "execute_research_run") as deferred:
            first, duplicate = self.submit(payload), self.submit(payload)
            self.assertEqual((first.status_code, duplicate.status_code), (202, 202))
            self.assertTrue(first.json()["created"])
            self.assertFalse(duplicate.json()["created"])
            self.assertEqual(first.json()["run"]["id"], duplicate.json()["run"]["id"])
            conflict = self.submit({**payload, "workflow": "lookup"})
            self.assertEqual(conflict.status_code, 409, conflict.text)
            deferred.assert_called_once()
        self.fixture.execute(first.json()["run"]["id"])
        before = len(self.chats())
        legacy = {key: value for key, value in self.payload(query="aurora").items() if key != "workflow"}
        response = self.submit(legacy)
        self.assertEqual(response.status_code, 202, response.text)
        lookup = self.get(response.json()["run"]["id"])
        self.assertEqual(lookup["workflow"], "lookup")
        self.assertEqual(lookup["state"], "partial")
        self.assertIsNotNone(lookup["result"])
        self.assertIsNone(lookup.get("assessment"))
        self.assertEqual(len(self.chats()), before)

    def test_requirements_reject_query_workflow_overrides_and_encoded_credentials_before_admission(self):
        key = "syntheticRequirementsAdmissionCredential24"
        self.fixture.keys[ENDPOINT] = key
        encoded = "".join("\\u%04x" % ord(character) for character in key)
        with patch.object(backend, "execute_research_run") as deferred:
            for changes in ({"query": "aurora"}, {"workflow": "answer"}, {"workflow": None},
                            {"request": "Assess " + encoded}):
                with self.subTest(changes=changes):
                    response = self.submit(**changes)
                    self.assertEqual(response.status_code, 422, response.text)
                    self.assertNotIn(key, response.text)
            deferred.assert_not_called()
        self.assertEqual(self.client.get("/api/research/runs").json()["runs"], [])
        self.assertEqual(self.requests, [])
        self.assert_released()

    def test_missing_uninstalled_or_remote_model_fails_synchronously_without_network(self):
        cases = ({"model": "", "orchestrator_model": ""}, {"model": "not-installed:latest"},
                 {"orchestrator_model": "not-installed-orchestrator:latest"},
                 {"protocol": "responses", "base_url": "https://provider.example.org/v1"})
        with patch.object(backend, "execute_research_run") as deferred:
            for change in cases:
                with self.subTest(change=change):
                    with backend.provider().transaction() as state:
                        state["config"] = {**self.config, **change}
                    response = self.submit()
                    self.assertEqual(response.status_code, 409, response.text)
                    self.assertEqual(self.client.get("/api/research/runs").json()["runs"], [])
            deferred.assert_not_called()
        self.assertEqual(self.requests, [])
        self.assert_released()

    def test_configured_installed_orchestrator_is_used_without_changing_chat_model(self):
        orchestrator = "synthetic-orchestrator:latest"
        with backend.provider().transaction() as state:
            state["config"]["orchestrator_model"] = orchestrator
            state["models"].append(orchestrator)
        run = self.run_now()
        self.assertEqual(run["state"], "partial")
        self.assertEqual(self.chats()[0][2]["model"], orchestrator)
        self.assertEqual(backend.provider().read_state()["config"]["model"], self.model)
        self.assert_released()

    def test_cancel_before_callback_prevents_model_dispatch(self):
        run = self.queued()
        response = self.client.post("/api/research/runs/" + run["id"] + "/cancel", headers=self.headers)
        self.assertEqual(response.status_code, 200, response.text)
        self.fixture.execute(run["id"])
        saved = self.get(run["id"])
        self.assertEqual(saved["state"], "cancelled")
        self.assertIsNone(saved.get("assessment"))
        self.assertEqual(self.requests, [])
        self.assert_released()

    def test_cancel_or_delete_during_inference_fences_late_assessment_and_keeps_actual_charge(self):
        for action in ("cancel", "delete"):
            with self.subTest(action=action):
                run = self.queued()
                path = "/api/research/runs/" + run["id"]

                def action_before_reply():
                    response = (self.client.post(path + "/cancel", headers=self.headers) if action == "cancel"
                                else self.client.delete(path, headers=self.headers))
                    self.assertEqual(response.status_code, 200, response.text)

                self.chat_hook = action_before_reply
                self.fixture.execute(run["id"])
                self.chat_hook = None
                if action == "cancel":
                    saved = self.get(run["id"])
                    self.assertEqual(saved["state"], "cancelled")
                    self.assertIsNone(saved.get("assessment"))
                    self.assert_actual_usage(saved)
                else:
                    self.assertEqual(self.client.get(path).status_code, 404)
                    entry = backend.provider().read_state()["ledger"][-1]
                    self.assertEqual((entry["status"], entry["input_tokens"], entry["output_tokens"]), ("settled", 41, 7))
                self.assert_no_stage_output(run["id"])
                self.assert_released()

    def test_credential_rotation_during_inference_withholds_assessment_but_settles_actual_usage(self):
        key = "syntheticFutureRequirementsCredential24"
        run = self.queued(request=REQUEST + " " + key)
        self.chat_hook = lambda: self.fixture.keys.update({ENDPOINT: key})
        self.fixture.execute(run["id"])
        saved = backend.research_store().get(run["id"])
        self.assertEqual(saved["state"], "failed")
        self.assertIsNone(saved.get("assessment"))
        self.assert_actual_usage(saved)
        self.assert_no_stage_output(run["id"])
        response = self.client.get("/api/research/runs/" + run["id"])
        self.assertEqual(response.status_code, 404, response.text)
        self.assertNotIn(key, response.text)
        self.assertEqual(self.client.get("/api/research/runs").json()["skipped_count"], 1)
        self.assert_released()

    def test_completed_assessment_exports_recheck_current_credentials(self):
        run = self.run_now()
        self.fixture.keys[ENDPOINT] = "Acme"
        detail = self.client.get("/api/research/runs/" + run["id"])
        self.assertEqual(detail.status_code, 404, detail.text)
        listed = self.client.get("/api/research/runs").json()
        self.assertEqual(listed["runs"], [])
        self.assertEqual(listed["skipped_count"], 1)
        self.fixture.keys.clear()
        self.assertEqual(self.get(run["id"])["assessment"]["decision"], "ready")

    def test_deadline_crossed_during_inference_cannot_publish_or_retry_the_assessment(self):
        run = self.queued()
        clock = [datetime.now().timestamp()]
        self.chat_hook = lambda: clock.__setitem__(0, datetime.fromisoformat(run["budget"]["deadline"]).timestamp() + 1)
        with patch.object(backend.ResearchStore, "clock", side_effect=lambda: clock[0]):
            self.fixture.execute(run["id"])
        saved = self.get(run["id"])
        self.assertEqual(saved["state"], "failed")
        self.assertIsNone(saved.get("assessment"))
        self.assertEqual(len(self.chats()), 1)
        self.assert_actual_usage(saved)
        self.assert_no_stage_output(run["id"])
        self.assert_released()

    def test_restart_interrupts_requirements_without_replaying_the_callback(self):
        run = self.queued()
        self.client.__exit__(None, None, None)
        with patch.object(backend, "execute_research_run", side_effect=AssertionError("Restart redispatch")):
            self.client = self.fixture.enterContext(TestClient(backend.app))
            self.fixture.client = self.client
            self.headers = {"X-Maestro-CSRF": self.client.get("/api/session").json()["csrf"]}
            saved = self.get(run["id"])
        self.assertEqual(saved["state"], "interrupted")
        self.assertIsNone(saved.get("assessment"))
        self.assertIsNone(saved["result"])
        self.assertEqual(self.requests, [])
        self.assert_released()


if __name__ == "__main__":
    unittest.main()

"""Opt-in reviewed Workspace messages reuse owned stages and real chat storage."""
from contextlib import closing
from copy import deepcopy
from datetime import datetime
import json
import sqlite3
import unittest
from unittest.mock import patch
import uuid

from backend import app as backend
from backend.memory import MemoryStore
from backend.provider import LocalUncertain
from backend.web_search import ENDPOINT, WebSearch
from tests import test_research_final_review_api as finals
from tests import test_research_requirements_api as requirements


REAL_RECALL = MemoryStore.recall


class ReviewedChatTests(unittest.TestCase):
    def setUp(self):
        self.fixture = finals.ResearchFinalReviewApiTests(
            "test_supported_answer_uses_four_owned_calls_and_preserves_original_draft")
        self.fixture.legacy_allowance = False
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.setUp()
        self.client, self.headers = self.fixture.client, self.fixture.headers
        self.evidence = self.fixture.fixture.fixture
        self.requirements = self.evidence.fixture
        self.archive = self.evidence.archive_fixture
        self.chat_id = self.archive.chat()
        self.route, self.assessment = "direct", requirements.ready_assessment()
        self.reply = "A bounded local direct reply."
        self.calls, self.route_hook, self.direct_hook = [], None, None
        self.recall = self.enterContext(patch.object(MemoryStore, "recall", return_value=[]))
        self.enterContext(patch("backend.provider.background_network", side_effect=self.inference))

    def inference(self, config, path, payload, deadline):
        properties = (payload or {}).get("format", {}).get("properties", {})
        if path != "/api/chat" or not ("route" in properties or "reply" in properties):
            return self.fixture.inference(config, path, payload, deadline)
        kind = "route" if "route" in properties else "direct"
        self.calls.append((kind, deepcopy(payload)))
        hook = self.route_hook if kind == "route" else self.direct_hook
        if hook:
            hook(payload)
        raw = {"route": self.route, "assessment": None if self.route == "direct" else self.assessment} if kind == "route" else {"reply": self.reply}
        return {"done": True, "message": {"role": "assistant", "content": json.dumps(raw)},
                "prompt_eval_count": 41, "eval_count": 7}

    def payload(self, **changes):
        return {"submission_id": uuid.uuid4().hex, "text": requirements.REQUEST,
                "chat_id": self.chat_id, "model": self.requirements.model, **changes}

    def post(self, payload=None, **changes):
        response = self.client.post("/api/chat/reviewed", json=payload or self.payload(**changes), headers=self.headers)
        self.assertEqual(response.status_code, 202, response.text)
        return response.json()

    def submit(self, **changes):
        admitted = self.post(**changes)
        return self.requirements.get(admitted["run"]["id"])

    def messages(self, chat_id=None):
        state = backend.read_workspace()
        return next(chat["messages"] for chat in state["chats"] if chat["id"] == (chat_id or self.chat_id))

    def current(self):
        with closing(sqlite3.connect(backend.DATABASE)) as db:
            run_id, token = db.execute("SELECT id,claim_token FROM research_runs WHERE state='running'").fetchone()
        return backend.research_store(), run_id, token

    def search(self, tested=True):
        self.archive.keys[ENDPOINT] = "synthetic-reviewed-search-secret"
        with WebSearch(backend.DATABASE, backend.TIMEZONE).transaction() as state:
            state.update(config={"enabled": True, "daily_limit": 20, "max_results": 3},
                         tested_at=datetime.now(backend.TIMEZONE).isoformat() if tested else None)

    @staticmethod
    def envelope(payload):
        return json.loads(payload["messages"][0]["content"].split("Server-authored stage assignment:\n", 1)[1])

    def assert_usage(self, run, calls):
        self.assertEqual(run["budget"]["consumed"]["model_calls"], calls)
        self.assertEqual(run["budget"]["consumed"]["hosted_searches"], 0)
        self.assertTrue(all(amount == 0 for amount in run["budget"]["reserved"].values()))
        self.assertEqual((run["budget"]["total"]["model_calls"], run["budget"]["total"]["seconds"]), (7, 180))

    def test_direct_uses_two_owned_calls_and_commits_one_exchange_without_title_call(self):
        self.search()
        run = self.submit()
        self.assertEqual(run["state"], "answered")
        self.assertEqual([kind for kind, _ in self.calls], ["route", "direct"])
        self.assertFalse(self.envelope(self.calls[0][1])["permissions"]["memory_recall"])
        effective = self.envelope(self.calls[1][1])["permissions"]
        self.assertFalse(effective["hosted_allowed"])
        self.assertEqual(effective["source_mode"], "saved_only")
        self.assertFalse(run["permissions"]["memory_recall"])
        self.assertEqual([message["text"] for message in self.messages()], [requirements.REQUEST, self.reply])
        self.assertEqual(self.messages()[-1]["research_run_id"], run["id"])
        self.assertTrue(run["chat_route"]["published"])
        self.assert_usage(run, 2)

    def test_clarification_publishes_validated_question_in_one_call(self):
        self.route = "clarify"
        self.assessment = {"version": 1, "decision": "clarify", "subjects": [], "items": [], "constraints": [],
                           "clarifications": [{"kind": "security", "item_ids": [], "quote": "ABC"}]}
        run = self.submit(text="What is ABC?")
        self.assertEqual(run["state"], "clarification")
        self.assertIn("ABC", self.messages()[-1]["text"])
        self.assertNotIn("View result", self.messages()[-1]["text"])
        self.recall.assert_not_called()
        self.assert_usage(run, 1)

    def test_research_reuses_combined_assessment_and_publishes_only_a_result_link(self):
        self.route = "research"
        source = self.fixture.capture()
        run = self.submit()
        self.assertEqual(run["state"], "accepted")
        rows = self.fixture.sql("SELECT kind FROM research_stages WHERE run_id=?", (run["id"],))
        self.assertEqual([row[0] for row in rows], ["route", "evidence_review", "writer", "reviewer"])
        reply = self.messages()[-1]
        self.assertIn(run["id"], reply["text"])
        self.assertNotIn("USD 10", reply["text"])
        self.recall.assert_not_called()
        self.assert_usage(run, 4)
        path = self.evidence.manifest(source)
        manifest = json.loads(path.read_text(encoding="utf-8"))
        path.write_text(json.dumps({**manifest, "eligible": False}), encoding="utf-8")
        withdrawn = self.requirements.get(run["id"])
        self.assertEqual(withdrawn["state"], "partial")
        self.assertEqual(self.messages()[-1], reply)

    def test_unavailable_research_never_becomes_a_direct_answer(self):
        self.route = "research"
        run = self.submit()
        self.assertEqual(run["state"], "partial")
        self.assertEqual([kind for kind, _ in self.calls], ["route"])
        self.assertIn("View result", self.messages()[-1]["text"])
        self.recall.assert_not_called()
        self.assert_usage(run, 1)

    def test_current_claim_keeps_existing_origin_verification_limit(self):
        self.route = "research"
        self.fixture.capture()
        self.assessment["items"][0]["freshness"] = "current"
        run = self.submit(text="Compare current Acme revenue in USD for FY2025.")
        self.assertEqual(run["state"], "partial")
        self.assertEqual(run["evidence_review"]["items"][0]["state"], "unavailable")
        self.assertEqual(run["final_review"]["outcome"], "unverifiable")
        self.assert_usage(run, 2)

    def test_private_recall_is_used_only_after_direct_decision_and_records_provenance(self):
        remembered = self.client.post("/api/memory", json={"content": "Aurora examples should use PRIVATE_RECALL_MARKER."}, headers=self.headers)
        self.assertEqual(remembered.status_code, 200, remembered.text)
        memory_id = remembered.json()["id"]
        def recall(store, *args, **kwargs):
            owned, run_id, _ = self.current()
            self.assertEqual(owned.get(run_id)["chat_route"]["decision"], "direct")
            return REAL_RECALL(store, *args, **kwargs)
        with patch.object(MemoryStore, "recall", new=recall):
            run = self.submit(text="Explain aurora.")
        self.assertNotIn("PRIVATE_RECALL_MARKER", json.dumps(self.calls[0][1]))
        self.assertIn("PRIVATE_RECALL_MARKER", json.dumps(self.calls[1][1]))
        self.assertEqual(self.messages()[-1]["memory_ids"], [memory_id])
        self.assertTrue(run["permissions"]["memory_derived"])
        self.assert_usage(run, 2)

    def test_disabled_recall_ceiling_is_never_granted_by_direct_child_stage(self):
        with backend.workspace_transaction() as state:
            state.setdefault("work_config", {})["memory_recall_count"] = 0
        run = self.submit()
        self.assertEqual(run["state"], "answered")
        self.assertFalse(run["chat_route"]["direct_memory_allowed"])
        self.assertFalse(self.envelope(self.calls[-1][1])["permissions"]["memory_recall"])
        self.recall.assert_not_called()
        self.assert_usage(run, 2)

    def test_recall_policy_change_before_direct_dispatch_stops_without_private_context(self):
        def change(payload):
            with backend.workspace_transaction() as state:
                state.setdefault("work_config", {})["memory_recall_count"] = 0
        self.route_hook = change
        run = self.submit()
        self.assertEqual(run["state"], "failed")
        self.assertEqual([kind for kind, _ in self.calls], ["route"])
        self.recall.assert_not_called()
        self.assert_usage(run, 1)

    def test_submission_is_idempotent_and_cannot_switch_model_or_research_origin(self):
        payload = self.payload()
        first = self.post(payload)
        repeated = self.post(payload)
        self.assertFalse(repeated["created"])
        self.assertEqual(repeated["run"]["id"], first["run"]["id"])
        self.assertEqual(len(self.messages()), 2)
        self.assertEqual(len(self.calls), 2)
        changed = self.client.post("/api/chat/reviewed", json={**payload, "model": "other-model:latest"}, headers=self.headers)
        self.assertEqual(changed.status_code, 409)
        research = self.requirements.payload(submission_id=payload["submission_id"], workflow="research_answer", chat_id=self.chat_id)
        changed = self.client.post("/api/research/runs", json=research, headers=self.headers)
        self.assertEqual(changed.status_code, 409)

    def test_model_override_and_original_conversation_survive_active_chat_switch(self):
        override = "synthetic-override:latest"
        with backend.provider().transaction() as state:
            state["models"].append(override)
        other = []
        self.direct_hook = lambda payload: other.append(self.archive.chat())
        run = self.submit(model=override)
        self.assertEqual(run["state"], "answered")
        self.assertTrue(all(payload["model"] == override for _, payload in self.calls))
        self.assertEqual(self.messages()[-1]["model"], override)
        self.assertEqual(self.messages(other[0]), [])
        self.assertEqual(run["chat_id"], self.chat_id)
        self.assert_usage(run, 2)

    def test_explicit_forbid_search_constraint_survives_automatic_research_routing(self):
        self.route = "research"
        self.search()
        config = self.client.get("/api/context").json()["config"]
        self.client.put("/api/context", json={**config, "enabled": True}, headers=self.headers)
        self.assessment["constraints"] = [{"kind": "search", "strength": "forbid", "item_ids": [], "criterion": "Do not search the web"}]
        run = self.submit(text=requirements.REQUEST + " Do not search the web.")
        self.assertTrue(run["permissions"]["hosted_allowed"])
        self.assertEqual(run["state"], "partial")
        self.assertEqual(WebSearch(backend.DATABASE, backend.TIMEZONE).summary()["searches_today"], 0)
        self.assert_usage(run, 1)

    def test_memory_derived_history_never_enters_assessment_or_grants_hosted_scope(self):
        self.route = "research"
        self.search()
        with backend.workspace_transaction() as state:
            backend.selected_chat(state, self.chat_id)["messages"].append({"id": "private-prior", "role": "assistant",
                "text": "PRIVATE_HISTORY_MUST_STAY_LOCAL", "memory_ids": ["private-memory"]})
        run = self.submit()
        self.assertFalse(run["permissions"]["hosted_allowed"])
        self.assertTrue(run["permissions"]["memory_derived"])
        self.assertNotIn("PRIVATE_HISTORY_MUST_STAY_LOCAL", json.dumps(self.calls[0][1]))
        self.recall.assert_not_called()
        self.assert_usage(run, 1)

    def test_untested_search_settings_cannot_grant_hosted_scope(self):
        self.route = "research"
        self.search(tested=False)
        run = self.submit()
        self.assertEqual(run["permissions"]["source_mode"], "saved_only")
        self.assertEqual(run["budget"]["total"]["hosted_searches"], 0)
        self.assert_usage(run, 1)

    def test_cancel_during_route_settles_usage_and_prevents_direct_reply(self):
        def cancel(payload):
            store, run_id, _ = self.current()
            store.cancel(run_id)
        self.route_hook = cancel
        run = self.submit()
        self.assertEqual(run["state"], "cancelled")
        self.assertEqual([kind for kind, _ in self.calls], ["route"])
        self.assertNotIn(self.reply, [item["text"] for item in self.messages()])
        self.assert_usage(run, 1)

    def test_delete_during_direct_reply_prevents_late_exchange_append(self):
        def delete(payload):
            store, run_id, _ = self.current()
            store.delete(run_id)
        self.direct_hook = delete
        admitted = self.post()
        self.assertEqual(self.client.get("/api/research/runs/" + admitted["run"]["id"]).status_code, 404)
        self.assertEqual(self.messages(), [])
        self.assertEqual(len(self.calls), 2)

    def test_deleted_submission_cannot_replay_or_create_another_exchange(self):
        payload = self.payload()
        admitted = self.post(payload)
        backend.research_store().delete(admitted["run"]["id"])
        repeated = self.client.post("/api/chat/reviewed", json=payload, headers=self.headers)
        self.assertEqual(repeated.status_code, 409)
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(len(self.messages()), 2)

    def test_restart_marks_queued_admission_interrupted_without_replay(self):
        payload = self.payload()
        with patch.object(backend, "execute_research_run") as deferred:
            admitted = self.post(payload)
        deferred.assert_called_once_with(admitted["run"]["id"])
        backend.research_store().recover()
        run = self.requirements.get(admitted["run"]["id"])
        self.assertEqual(run["state"], "interrupted")
        self.assertEqual(self.calls, [])
        self.assertFalse(self.post(payload)["created"])
        backend.execute_research_run(run["id"])
        self.assertEqual(self.calls, [])
        self.assertEqual(len(self.messages()), 2)

    def test_unknown_direct_completion_retains_reservation_and_never_replays(self):
        def unknown(payload):
            raise LocalUncertain("Synthetic direct completion unknown.", False)
        self.direct_hook = unknown
        payload = self.payload()
        admitted = self.post(payload)
        run = self.requirements.get(admitted["run"]["id"])
        self.assertEqual(run["state"], "failed")
        self.assertGreater(run["budget"]["reserved"]["tokens"], 0)
        self.assertEqual(run["budget"]["consumed"]["model_calls"], 1)
        self.assertFalse(self.post(payload)["created"])
        backend.execute_research_run(run["id"])
        self.assertEqual(len(self.calls), 2)
        self.assertNotIn(self.reply, [item["text"] for item in self.messages()])

    def test_answered_export_requires_original_completed_route_direct_and_linked_message(self):
        run = self.submit()
        original = deepcopy(run)
        cases = [lambda value: value.update(chat_route=None),
                 lambda value: value.update(request="A different request."),
                 lambda value: value["chat_route"].update(decision="research"),
                 lambda value: value["chat_route"].update(published=False),
                 lambda value: value["chat_route"].update(direct_stage_id=uuid.uuid4().hex),
                 lambda value: value["chat_route"].update(direct_memory_allowed=False)]
        for mutate in cases:
            with self.subTest(mutate=mutate):
                changed = deepcopy(original)
                mutate(changed)
                with self.assertRaises(ValueError):
                    backend.safe_research_run(changed, secrets=())
        with backend.workspace_transaction() as state:
            backend.selected_chat(state, self.chat_id)["messages"][-1]["text"] = "A different unproven reply."
        self.assertEqual(self.client.get("/api/research/runs/" + run["id"]).status_code, 404)
        self.assertEqual(len(self.calls), 2)

    def test_direct_export_rechecks_current_credentials_and_completed_output(self):
        run = self.submit()
        self.archive.keys[ENDPOINT] = self.reply
        self.assertEqual(self.client.get("/api/research/runs/" + run["id"]).status_code, 404)
        self.archive.keys.clear()
        self.assertEqual(self.client.get("/api/research/runs/" + run["id"]).status_code, 200)
        with closing(sqlite3.connect(backend.DATABASE)) as db, db:
            db.execute("UPDATE research_stages SET output=? WHERE id=?",
                       (json.dumps({"reply": "Replaced raw output."}), run["chat_route"]["direct_stage_id"]))
        self.assertEqual(self.client.get("/api/research/runs/" + run["id"]).status_code, 404)
        self.assertEqual(len(self.calls), 2)

    def test_direct_publication_rechecks_policy_after_inference(self):
        def withdraw(payload):
            with backend.workspace_transaction() as state:
                state.setdefault("work_config", {})["memory_recall_count"] = 0
        self.direct_hook = withdraw
        run = self.submit()
        self.assertEqual(run["state"], "failed")
        self.assertNotIn(self.reply, [message["text"] for message in self.messages()])
        self.assert_usage(run, 2)

    def test_malformed_combined_route_stops_without_fallback_or_extra_inference(self):
        self.route = "unknown"
        run = self.submit()
        self.assertEqual(run["state"], "failed")
        self.assertEqual([kind for kind, _ in self.calls], ["route"])
        self.recall.assert_not_called()
        self.assert_usage(run, 1)


if __name__ == "__main__":
    unittest.main()

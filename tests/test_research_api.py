"""Opt-in research receipts use disposable evidence, without model or hosted calls."""
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
import uuid

import httpx
from fastapi.testclient import TestClient

from backend import app as backend
from backend.web_search import ENDPOINT


ESCAPED_CREDENTIALS = ('synthetic"researchcredential', "synthetic\\researchcredential")


class ResearchApiTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory(prefix="maestro-research-api-"))).resolve()
        self.private, self.archive = self.root / "private", self.root / "public"
        self.private.mkdir()
        self.enterContext(patch.object(backend, "DATABASE", self.private / "workspace.sqlite3"))
        self.enterContext(patch.dict("os.environ", {"MAESTRO_CONTEXT_ARCHIVE_DIR": str(self.archive)}))
        self.keys = {}
        self.enterContext(patch.object(backend.credentials, "read", side_effect=lambda endpoint:
                                      (self.keys.get(endpoint), "synthetic" if endpoint in self.keys else "missing")))
        for transport in (httpx.HTTPTransport, httpx.AsyncHTTPTransport):
            self.enterContext(patch.object(transport, "handle_request" if transport is httpx.HTTPTransport
                                          else "handle_async_request", side_effect=AssertionError("External network")))
        self.enterContext(patch.object(backend.WebSearch, "search", side_effect=AssertionError("Hosted acquisition")))
        self.client = self.enterContext(TestClient(backend.app))
        self.headers = {"X-Maestro-CSRF": self.client.get("/api/session").json()["csrf"]}
        self.execute = backend.execute_research_run

    def payload(self, **changes):
        return {"submission_id": uuid.uuid4().hex, "request": "Inspect saved aurora evidence.",
                "query": "aurora", "chat_id": None, **changes}

    def submit(self, payload=None, **changes):
        return self.client.post("/api/research/runs", json=payload or self.payload(**changes), headers=self.headers)

    def queued(self, **changes):
        with patch.object(backend, "execute_research_run") as deferred:
            response = self.submit(**changes)
        self.assertEqual(response.status_code, 202, response.text)
        self.assertTrue(response.json()["created"])
        run = response.json()["run"]
        self.assertEqual(run["state"], "queued")
        deferred.assert_called_once_with(run["id"])
        return run

    def get(self, run_id):
        response = self.client.get("/api/research/runs/" + run_id)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def chat(self):
        response = self.client.post("/api/chats", headers=self.headers)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["active_chat_id"]

    def capture(self, *, content="Aurora SYNTHETIC_SOURCE_BODY_ONLY_77.", at="2020-01-02T12:00:00+00:00"):
        config = self.client.get("/api/context").json()["config"]
        response = self.client.put("/api/context", json={**config, "enabled": True}, headers=self.headers)
        self.assertEqual(response.status_code, 200, response.text)
        saved = backend.context_store().capture("Synthetic aurora report", at, [{
            "title": "Synthetic aurora report", "url": "https://reports.example.org/aurora",
            "content": content,
        }], 1)["sources"][0]
        self.assertEqual(saved["archive_status"], "saved")
        return saved

    def source_snapshot(self):
        index = backend.context_store().index
        return (index.read_bytes() if index.is_file() else None,
                {str(path.relative_to(self.archive)): path.read_bytes() for path in self.archive.rglob("*")
                 if path.is_file()})

    def test_session_csrf_and_local_origin_protect_every_research_route(self):
        run = self.queued()
        detail = "/api/research/runs/" + run["id"]
        anonymous = TestClient(backend.app)
        self.addCleanup(anonymous.close)
        for path in ("/api/research/runs", detail):
            self.assertEqual(anonymous.get(path).status_code, 401)
            self.assertEqual(self.client.get(path, headers={"Origin": "https://unrelated.example"}).status_code, 403)
        for method, path, body in (("POST", "/api/research/runs", self.payload()),
                                   ("POST", detail + "/cancel", None), ("DELETE", detail, None)):
            with self.subTest(method=method, path=path):
                self.assertEqual(anonymous.request(method, path, json=body).status_code, 401)
                self.assertEqual(self.client.request(method, path, json=body).status_code, 403)
                response = self.client.request(method, path, json=body,
                                               headers={**self.headers, "Origin": "https://unrelated.example"})
                self.assertEqual(response.status_code, 403)
        self.assertEqual(self.get(run["id"])["state"], "queued")

    def test_submission_is_idempotent_but_a_changed_payload_cannot_reuse_its_id(self):
        payload = self.payload()
        with patch.object(backend, "execute_research_run") as deferred:
            first, again = self.submit(payload), self.submit(payload)
            self.assertEqual((first.status_code, again.status_code), (202, 202))
            self.assertTrue(first.json()["created"])
            self.assertFalse(again.json()["created"])
            self.assertEqual(first.json()["run"]["id"], again.json()["run"]["id"])
            deferred.assert_called_once_with(first.json()["run"]["id"])
            for change in ({"request": "A different private request."}, {"query": "different"}):
                conflict = self.submit({**payload, **change})
                self.assertEqual(conflict.status_code, 409, conflict.text)
                self.assertNotIn("different private request", conflict.text)
        listed = self.client.get("/api/research/runs").json()
        self.assertEqual(len(listed["runs"]), 1)
        self.assertEqual([event["kind"] for event in self.get(first.json()["run"]["id"])["events"]], ["submitted"])

    def test_invalid_or_credential_bearing_submissions_are_not_saved_or_executed(self):
        key = "syntheticresearchcredential"
        self.keys[ENDPOINT] = key
        cases = ({"submission_id": "A" * 32}, {"submission_id": "a" * 31}, {"request": ""},
                 {"request": "x" * 32001}, {"request": 7}, {"query": "x" * 513},
                 {"chat_id": 17}, {"hosted_allowed": True}, {"request": key},
                 {"query": "".join(f"%{ord(character):02X}" for character in key)})
        with patch.object(backend, "execute_research_run") as deferred:
            for change in cases:
                with self.subTest(change=change):
                    response = self.submit(self.payload(**change))
                    self.assertEqual(response.status_code, 422, response.text)
                    self.assertNotIn(key, response.text)
            deferred.assert_not_called()
        self.assertEqual(self.client.get("/api/research/runs").json()["runs"], [])

    def test_credentials_in_submission_or_origin_chat_ids_are_rejected_before_persistence(self):
        chat_id = self.chat()
        for field, key in (("submission_id", "0123456789abcdef" * 2), ("chat_id", chat_id)):
            with self.subTest(field=field):
                self.keys.clear()
                self.keys[ENDPOINT] = key
                before = backend.DATABASE.read_bytes()
                with patch.object(backend, "execute_research_run") as deferred:
                    response = self.submit(**{field: key})
                self.assertEqual(response.status_code, 422, response.text)
                self.assertNotIn(key, response.text)
                deferred.assert_not_called()
                with closing(sqlite3.connect(backend.DATABASE)) as db:
                    self.assertEqual(db.execute("SELECT COUNT(*) FROM research_runs").fetchone()[0], 0)
                    self.assertEqual(db.execute("SELECT COUNT(*) FROM research_events").fetchone()[0], 0)
                self.assertEqual(backend.DATABASE.read_bytes(), before)

    def test_credential_rotation_inside_admission_rolls_back_new_run_and_events(self):
        chat_id = self.chat()
        escaped_chat_id = 'synthetic"origin\\chat'
        with backend.workspace_transaction() as state:
            backend.selected_chat(state, chat_id)["id"] = escaped_chat_id
        create = backend.ResearchStore.create
        key = "syntheticadmissionrotationcredential"
        cases = (
            ("request", key, "Synthetic aurora " + key),
            ("query", key, "Synthetic aurora " + key),
            ("submission_id", "fedcba9876543210" * 2, "fedcba9876543210" * 2),
            ("chat_id", escaped_chat_id, escaped_chat_id),
        )
        for field, key, value in cases:
            with self.subTest(field=field):
                self.keys.clear()
                before = backend.DATABASE.read_bytes()

                def rotate_after_insert(store, *args, **kwargs):
                    admitted = create(store, *args, **kwargs)
                    self.keys[ENDPOINT] = key
                    return admitted

                with (patch.object(backend.ResearchStore, "create", new=rotate_after_insert),
                      patch.object(backend, "execute_research_run") as deferred):
                    response = self.submit(**{field: value})
                self.assertEqual(response.status_code, 422, response.text)
                self.assertNotIn(key, response.text)
                deferred.assert_not_called()
                with closing(sqlite3.connect(backend.DATABASE)) as db:
                    self.assertEqual(db.execute("SELECT COUNT(*) FROM research_runs").fetchone()[0], 0)
                    self.assertEqual(db.execute("SELECT COUNT(*) FROM research_events").fetchone()[0], 0)
                self.assertEqual(backend.DATABASE.read_bytes(), before)
                if field != "chat_id":
                    self.assertNotIn(key.encode(), backend.DATABASE.read_bytes())

    def test_real_lookup_records_only_pins_and_actual_events_without_inference_or_archive_writes(self):
        source = self.capture()
        before = self.source_snapshot()
        with (patch.object(backend.Provider, "generate", side_effect=AssertionError("Model inference")),
              patch.object(backend.Provider, "generate_context", side_effect=AssertionError("Model stage"))):
            response = self.submit()
        self.assertEqual(response.status_code, 202, response.text)
        run = self.get(response.json()["run"]["id"])
        self.assertEqual(run["state"], "partial")
        self.assertEqual(run["result"]["lookup_state"], "ready")
        expected = {field: source[field] for field in ("capture_id", "content_hash", "manifest_hash", "retrieved_at")}
        self.assertEqual(run["result"]["candidates"], [{**expected, "stale": True}])
        self.assertEqual(run["result"]["references_unavailable"], 0)
        self.assertNotIn(source["content"], json.dumps(run))
        self.assertIn("No reviewed answer", run["result"]["summary"])
        self.assertEqual([event["kind"] for event in run["events"]],
                         ["submitted", "local_lookup_started", "local_lookup_finished", "finished"])
        self.assertEqual([event["sequence"] for event in run["events"]], [1, 2, 3, 4])
        for amount in ("model_calls", "tokens", "estimated_model_usd", "hosted_searches"):
            self.assertEqual(run["budget"]["consumed"][amount], 0)
        self.assertFalse(run["permissions"]["hosted_allowed"])
        self.assertFalse(run["permissions"]["memory_recall"])
        self.assertEqual(run["budget"]["daily_search"]["used"], 0)
        with patch.object(backend.context_store(), "get_capture", side_effect=AssertionError("List body read")):
            listed = self.client.get("/api/research/runs")
        self.assertEqual(listed.status_code, 200, listed.text)
        self.assertEqual(listed.json()["runs"][0]["id"], run["id"])
        self.assertIsNone(listed.json()["runs"][0]["result"])
        self.assertEqual(self.source_snapshot(), before)
        with closing(sqlite3.connect(backend.DATABASE)) as db:
            receipt = db.execute("SELECT payload FROM research_runs WHERE id=?", (run["id"],)).fetchone()[0]
        self.assertNotIn(source["content"], receipt)

    def test_parent_limits_and_memory_derived_chat_fences_survive_the_preview(self):
        chat_id = self.chat()
        with backend.workspace_transaction() as state:
            chat = backend.selected_chat(state, chat_id)
            chat["messages"].append({"id": uuid.uuid4().hex, "role": "assistant", "content": "Private synthetic reply.",
                                     "memory_ids": ["synthetic-memory"], "at": backend.now()})
        limits = {"run_usd": 0.25, "daily_usd": 1, "monthly_usd": 2, "max_tokens": 900}
        self.assertEqual(self.client.put("/api/limits", json=limits, headers=self.headers).status_code, 200)
        self.assertEqual(self.client.put("/api/web-search", json={
            "config": {"enabled": False, "daily_limit": 3, "max_results": 1}, "persist": False,
        }, headers=self.headers).status_code, 200)
        with patch.object(backend.MemoryStore, "recall", side_effect=AssertionError("Memory recall")):
            response = self.submit(chat_id=chat_id)
        self.assertEqual(response.status_code, 202, response.text)
        run = self.get(response.json()["run"]["id"])
        self.assertTrue(run["permissions"]["memory_derived"])
        self.assertFalse(run["permissions"]["hosted_allowed"])
        self.assertFalse(run["permissions"]["memory_recall"])
        self.assertEqual(run["budget"]["total"]["tokens"], 900)
        self.assertEqual(run["budget"]["total"]["estimated_model_usd"], 0.25)
        self.assertEqual(run["budget"]["total"]["hosted_searches"], 0)
        self.assertEqual(run["budget"]["daily_search"], {"limit": 3, "used": 0, "remaining": 3})
        for name, amount in run["budget"]["total"].items():
            self.assertAlmostEqual(run["budget"]["remaining"][name],
                                   amount - run["budget"]["consumed"][name] - run["budget"]["reserved"][name])

    def test_credential_rotation_hides_an_unsafe_request_on_detail_and_list_reads(self):
        key = "laterresearchrequestcredential"
        run = self.queued(request="Private synthetic request " + key)
        self.keys[ENDPOINT] = key
        response = self.client.get("/api/research/runs/" + run["id"])
        self.assertEqual(response.status_code, 404, response.text)
        self.assertNotIn(key, response.text)
        listed = self.client.get("/api/research/runs")
        self.assertEqual(listed.json()["runs"], [])
        self.assertEqual(listed.json()["skipped_count"], 1)
        self.assertNotIn(key, listed.text)

    def test_key_rotation_during_later_list_entry_rechecks_earlier_rendered_requests(self):
        key = "latebatchresearchcredential"
        earlier = self.submit().json()["run"]
        sensitive = self.submit(request="Private synthetic request " + key).json()["run"]
        view = backend.research_view

        def rotate_after_later_entry(run):
            visible = view(run)
            if run["id"] == earlier["id"]:
                self.keys[ENDPOINT] = key
            return visible

        with patch.object(backend, "research_view", side_effect=rotate_after_later_entry):
            response = self.client.get("/api/research/runs")
        self.assertEqual(response.status_code, 200, response.text)
        ids = [run["id"] for run in response.json()["runs"]]
        self.assertNotIn(sensitive["id"], ids)
        self.assertIn(earlier["id"], ids)
        self.assertGreaterEqual(response.json()["skipped_count"], 1)
        self.assertNotIn(key, response.text)

    def test_json_escaped_credentials_hide_raw_request_and_query_in_detail_and_lists(self):
        for field in ("request", "query"):
            for key in ESCAPED_CREDENTIALS:
                with self.subTest(field=field, key=key):
                    self.keys.clear()
                    response = self.submit(**{field: "Synthetic aurora " + key})
                    self.assertEqual(response.status_code, 202, response.text)
                    run_id = response.json()["run"]["id"]
                    self.keys[ENDPOINT] = key
                    detail = self.client.get("/api/research/runs/" + run_id)
                    listed = self.client.get("/api/research/runs")
                    with self.subTest(surface="detail"):
                        self.assertEqual(detail.status_code, 404)
                        self.assertEqual(detail.json(), {"detail": "That research run is unavailable."})
                    with self.subTest(surface="list"):
                        self.assertEqual(listed.status_code, 200, listed.text)
                        self.assertNotIn(run_id, [run["id"] for run in listed.json()["runs"]])
                        self.assertGreaterEqual(listed.json()["skipped_count"], 1)

    def test_json_escaped_credentials_rotated_at_the_final_detail_snapshot_withhold_raw_fields(self):
        for field in ("request", "query"):
            for key in ESCAPED_CREDENTIALS:
                with self.subTest(field=field, key=key):
                    self.keys.clear()
                    response = self.submit(**{field: "Synthetic aurora " + key})
                    self.assertEqual(response.status_code, 202, response.text)
                    run_id = response.json()["run"]["id"]
                    current_secrets = backend.source_secrets
                    calls = []

                    def rotate_at_final_snapshot(*args, **kwargs):
                        calls.append(True)
                        if len(calls) == 2:
                            self.keys[ENDPOINT] = key
                        return current_secrets(*args, **kwargs)

                    with patch.object(backend, "source_secrets", side_effect=rotate_at_final_snapshot):
                        detail = self.client.get("/api/research/runs/" + run_id)
                    self.assertEqual(len(calls), 2)
                    self.assertEqual(detail.status_code, 404)
                    self.assertEqual(detail.json(), {"detail": "That research run is unavailable."})

    def test_json_escaped_keys_rotated_during_a_later_list_entry_recheck_raw_request_and_query(self):
        for field in ("request", "query"):
            for key in ESCAPED_CREDENTIALS:
                with self.subTest(field=field, key=key):
                    self.keys.clear()
                    earlier = self.submit().json()["run"]
                    response = self.submit(**{field: "Synthetic aurora " + key})
                    self.assertEqual(response.status_code, 202, response.text)
                    sensitive = response.json()["run"]
                    view = backend.research_view

                    def rotate_after_later_entry(run):
                        visible = view(run)
                        if run["id"] == earlier["id"]:
                            self.keys[ENDPOINT] = key
                        return visible

                    with patch.object(backend, "research_view", side_effect=rotate_after_later_entry):
                        listed = self.client.get("/api/research/runs")
                    self.assertEqual(listed.status_code, 200, listed.text)
                    ids = [run["id"] for run in listed.json()["runs"]]
                    self.assertNotIn(sensitive["id"], ids)
                    self.assertIn(earlier["id"], ids)
                    self.assertGreaterEqual(listed.json()["skipped_count"], 1)

    def test_current_source_credentials_and_revocation_withhold_pins_without_rewriting_history(self):
        key = "laterresearchsourcecredential"
        source = self.capture(content="Aurora public source with " + key)
        response = self.submit()
        self.assertEqual(response.status_code, 202, response.text)
        run_id = response.json()["run"]["id"]
        original = self.get(run_id)["result"]["candidates"]
        self.assertEqual(original[0]["capture_id"], source["capture_id"])
        self.keys[ENDPOINT] = key
        filtered = self.get(run_id)
        self.assertEqual(filtered["result"]["candidates"], [])
        self.assertEqual(filtered["result"]["references_unavailable"], 1)
        self.assertEqual(filtered["result"]["lookup_state"], "ready")
        self.assertNotIn(key, json.dumps(filtered))
        self.keys.clear()
        self.assertEqual(self.get(run_id)["result"]["candidates"], original)
        config = backend.context_store().status()["config"]
        revoked = {**config, "capture_policy": "approved_sources", "public_sources": ["https://other.example.org/"]}
        self.assertEqual(self.client.put("/api/context", json=revoked, headers=self.headers).status_code, 200)
        self.assertEqual(self.get(run_id)["result"]["candidates"], [])
        self.assertEqual(self.get(run_id)["result"]["references_unavailable"], 1)

    def test_tampered_saved_content_cannot_reappear_as_a_current_run_reference(self):
        source = self.capture()
        response = self.submit()
        run_id = response.json()["run"]["id"]
        self.assertEqual(len(self.get(run_id)["result"]["candidates"]), 1)
        path = self.archive / "objects" / "sha256" / source["content_hash"][:2] / (source["content_hash"] + ".txt")
        raw = path.read_bytes()
        path.write_bytes(raw[:-1] + b"!")
        read = self.get(run_id)
        self.assertEqual(read["result"]["candidates"], [])
        self.assertEqual(read["result"]["references_unavailable"], 1)

    def test_cancel_before_execution_releases_ownership_without_inventing_a_completed_lookup(self):
        run = self.queued()
        response = self.client.post("/api/research/runs/" + run["id"] + "/cancel", headers=self.headers)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["state"], "cancelled")
        with patch.object(backend, "search_context_candidates", side_effect=AssertionError("Cancelled lookup")):
            self.execute(run["id"])
        cancelled = self.get(run["id"])
        self.assertEqual(cancelled["state"], "cancelled")
        self.assertIsNone(cancelled["result"])
        self.assertNotIn("local_lookup_finished", [event["kind"] for event in cancelled["events"]])
        self.assertEqual(self.submit().status_code, 202)

    def test_cancellation_and_deletion_fence_a_late_lookup_completion(self):
        self.capture()
        for action in ("cancel", "delete"):
            with self.subTest(action=action):
                run = self.queued()
                lookup = backend.search_context_candidates

                def finish_after_action(*args, **kwargs):
                    result = lookup(*args, **kwargs)
                    path = "/api/research/runs/" + run["id"]
                    response = (self.client.post(path + "/cancel", headers=self.headers) if action == "cancel"
                                else self.client.delete(path, headers=self.headers))
                    self.assertEqual(response.status_code, 200, response.text)
                    return result

                with patch.object(backend, "search_context_candidates", side_effect=finish_after_action):
                    self.execute(run["id"])
                if action == "cancel":
                    saved = self.get(run["id"])
                    self.assertEqual(saved["state"], "cancelled")
                    self.assertIsNone(saved["result"])
                    self.assertNotIn("local_lookup_finished", [event["kind"] for event in saved["events"]])
                else:
                    self.assertEqual(self.client.get("/api/research/runs/" + run["id"]).status_code, 404)
                    self.assertNotIn(run["id"], self.client.get("/api/research/runs").text)
                    original_payload = {field: run[field] for field in ("submission_id", "request", "query", "chat_id")}
                    retried = self.submit(original_payload)
                    self.assertEqual(retried.status_code, 409, retried.text)
                    self.assertEqual(self.client.get("/api/research/runs/" + run["id"]).status_code, 404)

    def test_deleting_the_originating_chat_fences_a_late_run_commit(self):
        self.capture()
        chat_id = self.chat()
        run = self.queued(chat_id=chat_id)
        lookup = backend.search_context_candidates

        def delete_chat_after_lookup(*args, **kwargs):
            result = lookup(*args, **kwargs)
            response = self.client.delete("/api/chats/" + chat_id, headers=self.headers)
            self.assertEqual(response.status_code, 200, response.text)
            return result

        with patch.object(backend, "search_context_candidates", side_effect=delete_chat_after_lookup):
            self.execute(run["id"])
        self.assertEqual(self.client.get("/api/research/runs/" + run["id"]).status_code, 404)
        self.assertNotIn(run["id"], self.client.get("/api/research/runs").text)

    def test_startup_recovery_interrupts_pending_work_without_redispatch(self):
        run = self.queued()
        self.client.__exit__(None, None, None)
        with patch.object(backend, "execute_research_run", side_effect=AssertionError("Restart redispatch")):
            self.client = self.enterContext(TestClient(backend.app))
            self.headers = {"X-Maestro-CSRF": self.client.get("/api/session").json()["csrf"]}
            saved = self.get(run["id"])
        self.assertEqual(saved["state"], "interrupted")
        self.assertIsNone(saved["result"])
        self.assertNotIn("local_lookup_finished", [event["kind"] for event in saved["events"]])

    def test_active_research_excludes_the_existing_provider_generation_path(self):
        chat_id = self.chat()
        run = self.queued()
        with backend.provider().transaction() as state:
            state["config"].update(protocol="ollama", base_url="http://127.0.0.1:11434",
                                   model="synthetic-local:latest", pricing_verified=True)
        with self.assertRaisesRegex(ValueError, "research run is active"):
            backend.provider().generate("Synthetic ordinary chat", chat_id)
        self.assertEqual(self.get(run["id"])["state"], "queued")
        self.assertEqual(backend.provider().read_state()["ledger"], [])

    def test_lookup_failures_keep_only_controlled_events_and_disabled_storage_is_not_an_answer(self):
        diagnostic = "Sensitive synthetic provider prose should not be persisted"
        with patch.object(backend, "search_context_candidates", side_effect=ValueError(diagnostic)):
            failed_response = self.submit()
        self.assertEqual(failed_response.status_code, 202, failed_response.text)
        failed = self.get(failed_response.json()["run"]["id"])
        self.assertEqual(failed["state"], "failed")
        self.assertIsNone(failed["result"])
        self.assertNotIn(diagnostic, json.dumps(failed))
        self.assertNotIn("local_lookup_finished", [event["kind"] for event in failed["events"]])
        response = self.submit()
        self.assertEqual(response.status_code, 202, response.text)
        empty = self.get(response.json()["run"]["id"])
        self.assertEqual(empty["state"], "partial")
        self.assertEqual(empty["result"]["lookup_state"], "disabled")
        self.assertEqual(empty["result"]["candidates"], [])
        self.assertIn("No reviewed answer", empty["result"]["summary"])

    def test_missing_or_malformed_identifiers_use_generic_unavailable_responses(self):
        for run_id in ("f" * 32, "invalid-run-id"):
            path = "/api/research/runs/" + run_id
            for method, target in (("GET", path), ("POST", path + "/cancel"), ("DELETE", path)):
                with self.subTest(method=method, run_id=run_id):
                    response = self.client.request(method, target, headers=self.headers)
                    self.assertEqual(response.status_code, 404, response.text)
                    self.assertEqual(response.json(), {"detail": "That research run is unavailable."})


if __name__ == "__main__":
    unittest.main()

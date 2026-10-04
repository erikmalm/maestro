"""Background deadlines retain the gate until an explicit server restart acknowledgement."""
import asyncio
from contextlib import closing, contextmanager
from datetime import timezone
import json
from pathlib import Path
import sqlite3
import tempfile
import time
import unittest
from unittest.mock import patch

import httpx

from backend.provider import BackgroundUncertain, DEFAULT, Provider, background_network
from backend.memory import MemoryStore
from backend.reflection import ReflectionStore, ReflectionWorker, OUTPUT_SCHEMA
from backend.work_config import WorkConfig


LOCAL = {**DEFAULT, "protocol": "ollama", "base_url": "http://127.0.0.1:11434", "model": "synthetic:7b"}


class BackgroundNetworkTests(unittest.TestCase):
    def mock_http(self, handler):
        client = httpx.AsyncClient
        return patch("backend.provider.httpx.AsyncClient", side_effect=lambda **options:
                     client(transport=httpx.MockTransport(handler), **options))

    def test_whole_request_deadline_cancels_ongoing_request(self):
        cancelled = []
        async def handler(request):
            self.assertNotIn("authorization", request.headers)
            try:
                await asyncio.sleep(10)
                return httpx.Response(200, json={"done": True})
            finally:
                cancelled.append(True)
        started = time.monotonic()
        with self.mock_http(handler), self.assertRaises(BackgroundUncertain):
            background_network(LOCAL, "/api/chat", {}, started + 0.05)
        self.assertLess(time.monotonic() - started, 1)
        self.assertEqual(cancelled, [True])

    def test_deadline_is_shared_across_metadata_and_generation(self):
        calls = []
        async def handler(request):
            calls.append(request.url.path)
            await asyncio.sleep(0.08)
            return httpx.Response(200, json={"done": True})
        deadline = time.monotonic() + 0.12
        with self.mock_http(handler):
            self.assertEqual(background_network(LOCAL, "/api/show", {}, deadline), {"done": True})
            with self.assertRaises(BackgroundUncertain):
                background_network(LOCAL, "/api/chat", {}, deadline)
        self.assertEqual(calls, ["/api/show", "/api/chat"])


class BackgroundRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="maestro-background-provider-")
        self.addCleanup(self.directory.cleanup)
        self.provider = Provider(Path(self.directory.name) / "workspace.sqlite3", timezone.utc)
        with self.provider.transaction() as state:
            state["ledger"].append({"id": "synthetic-request", "at": self.provider.stamp(),
                                    "model": "synthetic:7b", "protocol": "ollama", "cost": 0, "status": "reserved",
                                    "job_id": "synthetic-job", "attempt": "synthetic-attempt", "connection_config": LOCAL.copy()})

    def entry(self):
        return self.provider.read_state()["ledger"][0]

    def test_startup_never_releases_an_unconfirmed_background_request(self):
        self.provider.recover()
        self.assertEqual(self.entry()["status"], "reserved")
        self.assertTrue(self.entry()["background_unknown"])
        with patch("backend.provider.background_network", return_value={"models": []}) as request:
            with self.assertRaisesRegex(ValueError, "confirm"):
                self.provider.recover_local("synthetic-request", False, LOCAL["base_url"])
        request.assert_not_called()
        self.assertEqual(self.entry()["status"], "reserved")

    def test_recovery_requires_an_empty_valid_model_list(self):
        self.provider.recover()
        for response in ({}, {"models": None}, {"models": [None]}, {"models": [{"name": "synthetic:7b"}]},
                         {"models": [{"name": "another:7b", "model": "synthetic:7b"}]},
                         {"models": [{"name": "unrelated:7b"}]}):
            with self.subTest(response=response), patch("backend.provider.background_network", return_value=response):
                with self.assertRaisesRegex(ValueError, "empty model list"):
                    self.provider.recover_local("synthetic-request", True, LOCAL["base_url"])
                self.assertEqual(self.entry()["status"], "reserved")
        with patch("backend.provider.background_network", return_value={"models": []}) as request:
            self.provider.recover_local("synthetic-request", True, LOCAL["base_url"])
        self.assertEqual(request.call_args.args[:3], (LOCAL, "/api/ps", None))
        self.assertEqual(self.entry()["status"], "failed")
        self.assertFalse(self.entry()["background_unknown"])
        self.assertTrue(self.entry()["local_recovered"])
        recovered_at = self.entry()["local_recovered_at"]
        with patch("backend.provider.background_network") as request:
            self.provider.recover_local("synthetic-request", True, LOCAL["base_url"])
        request.assert_not_called()
        self.assertEqual(self.entry()["local_recovered_at"], recovered_at)

    def test_default_model_alias_does_not_falsely_release_the_gate(self):
        with self.provider.transaction() as state:
            state["ledger"][0]["model"] = "synthetic"
        self.provider.recover()
        with patch("backend.provider.background_network", return_value={"models": [{"name": "synthetic:latest"}]}):
            with self.assertRaisesRegex(ValueError, "empty model list"):
                self.provider.recover_local("synthetic-request", True, LOCAL["base_url"])
        self.assertEqual(self.entry()["status"], "reserved")

    def test_registry_alias_does_not_falsely_release_the_gate(self):
        with self.provider.transaction() as state:
            state["ledger"][0]["model"] = "registry.ollama.ai/library/synthetic:7b"
        self.provider.recover()
        with patch("backend.provider.background_network", return_value={"models": [{"name": "synthetic:7b"}]}):
            with self.assertRaisesRegex(ValueError, "empty model list"):
                self.provider.recover_local("synthetic-request", True, LOCAL["base_url"])
        self.assertEqual(self.entry()["status"], "reserved")

    def test_live_background_reservation_cannot_be_recovered_despite_empty_ps(self):
        self.assertNotIn("background_unknown", self.entry())
        self.assertEqual(self.provider.usage()["local_requests"], [])
        with patch("backend.provider.background_network", return_value={"models": []}) as request:
            with self.assertRaisesRegex(ValueError, "not waiting for local recovery"):
                self.provider.recover_local("synthetic-request", True, LOCAL["base_url"])
        request.assert_not_called()
        self.assertEqual(self.entry()["status"], "reserved")

    def test_explicit_recovery_keeps_gate_when_original_server_is_unreachable(self):
        self.provider.recover()
        with patch("backend.provider.background_network", side_effect=BackgroundUncertain("Synthetic disconnect")), \
                self.assertRaises(BackgroundUncertain):
            self.provider.recover_local("synthetic-request", True, LOCAL["base_url"])
        self.assertEqual(self.entry()["status"], "reserved")

    def test_unknown_flag_cannot_release_an_active_durable_job_or_a_job_that_becomes_active(self):
        self.provider.recover()
        with self.provider.transaction(with_db=True) as (state, db):
            db.execute("CREATE TABLE reflection_jobs (id TEXT PRIMARY KEY, chat_id TEXT NOT NULL, state TEXT NOT NULL, data TEXT NOT NULL)")
            db.execute("INSERT INTO reflection_jobs VALUES ('synthetic-job','synthetic-chat','claimed','{}')")
        for phase in ("claimed", "reviewing", "dispatched"):
            with self.provider.transaction(with_db=True) as (state, db):
                db.execute("UPDATE reflection_jobs SET state=?", (phase,))
            with self.subTest(phase=phase), patch("backend.provider.background_network") as poll, self.assertRaisesRegex(ValueError, "still running"):
                self.provider.recover_local("synthetic-request", True, LOCAL["base_url"])
            poll.assert_not_called()
            self.assertEqual(self.entry()["status"], "reserved")
        with self.provider.transaction(with_db=True) as (state, db):
            db.execute("UPDATE reflection_jobs SET state='failed'")
        def reactivated_job(config, path, payload, deadline):
            with self.provider.transaction(with_db=True) as (state, db):
                db.execute("UPDATE reflection_jobs SET state='dispatched'")
            return {"models": []}
        with patch("backend.provider.background_network", side_effect=reactivated_job), self.assertRaisesRegex(ValueError, "still running"):
            self.provider.recover_local("synthetic-request", True, LOCAL["base_url"])
        self.assertEqual(self.entry()["status"], "reserved")


class ClaimedProviderTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="maestro-claimed-provider-")
        self.addCleanup(self.directory.cleanup)
        self.database = Path(self.directory.name) / "workspace.sqlite3"
        self.workspace = {"limits": {"max_tokens": 100000}, "work_config": WorkConfig(
            enabled=True, debounce_seconds=0, idle_seconds=0, max_output_tokens=512).model_dump(), "chats": [{
                "id": "synthetic-chat", "title_source": "manual", "messages": [{
                    "id": "synthetic-user", "role": "user", "text": "I prefer concise Swedish replies."}]}]}
        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute("CREATE TABLE workspace (id INTEGER PRIMARY KEY, value TEXT NOT NULL)")
            db.execute("INSERT INTO workspace VALUES (1,?)", (json.dumps(self.workspace),))
        MemoryStore(self.database, timezone.utc).initialize()
        self.provider = Provider(self.database, timezone.utc)
        self.provider.configure({**LOCAL, "ollama_context_tokens": 8192, "max_output_tokens": 4096}, "", False)
        with self.provider.transaction() as state:
            state["models"] = ["gpt-oss:20b"]
        self.store = ReflectionStore(self.database, timezone.utc)
        with self.store.transaction() as db:
            self.store.enqueue("synthetic-chat", self.workspace, db)
        self.prepared = self.store.prepare()
        self.calls = []

    def network(self, config, path, payload, deadline):
        self.calls.append((config, path, payload, deadline))
        if path == "/api/show":
            return {"details": {"format": "gguf"}, "capabilities": ["completion"], "thinking": {"values": ["low", "high"]}}
        return {"done": True, "message": {"content": '{"memories": []}'}, "prompt_eval_count": 30, "eval_count": 10}

    def generate(self, **options):
        options.setdefault("kind", self.prepared["job"].get("kind", "reflection"))
        return self.provider.generate_context(self.prepared["instructions"], self.prepared["messages"], job=self.prepared["job"], **options)

    def test_background_context_is_bounded_without_changing_chat_preferences(self):
        self.store.recover()
        self.workspace["work_config"].update(background_context_tokens=16384, max_output_tokens=4096)
        with self.store.transaction() as db:
            db.execute("UPDATE workspace SET value=?", (json.dumps(self.workspace),))
        self.provider.configure({**self.provider.read_state()["config"], "ollama_context_tokens": 131072}, "", False)
        self.prepared = self.store.prepare()
        self.assertEqual(self.prepared["job"]["connection_config"]["ollama_context_tokens"], 131072)
        with patch("backend.provider.background_network", side_effect=self.network):
            result = self.generate()
        self.assertEqual(self.calls[-1][2]["options"]["num_ctx"], 16384)
        self.assertEqual(self.calls[-1][2]["options"]["num_predict"], 4096)
        self.store.complete(self.prepared["job"], result, lambda text: text)
        state = self.provider.read_state()
        self.assertEqual(state["config"]["ollama_context_tokens"], 131072)
        self.assertEqual(state["ledger"][-1]["connection_config"]["ollama_context_tokens"], 131072)
        self.assertEqual(state["ledger"][-1]["status"], "settled")
        status = self.store.status()
        self.assertEqual((status["today_jobs"], status["today_tokens"], status["running"]), (1, 40, False))

    def test_two_phases_use_separate_saved_models_and_schemas_with_one_job_budget(self):
        self.workspace["work_config"].update(auto_curate=True, memory_model="form:7b", reflection_model="review:7b",
                                             max_jobs_per_day=1, max_tokens_per_day=100000,
                                             background_context_tokens=65536, max_output_tokens=32768)
        self.store.recover()
        with self.store.transaction() as db:
            db.execute("UPDATE workspace SET value=?", (json.dumps(self.workspace),))
        self.provider.configure({**self.provider.read_state()["config"], "ollama_context_tokens": 131072,
                                 "max_output_tokens": 32768}, "", False)
        self.prepared = self.store.prepare()
        draft = {"operation": "add", "memory_id": "", "kind": "preference", "scope": "conversation",
                 "content": "I prefer concise Swedish replies.", "source_message_id": "synthetic-user", "evidence": "I prefer concise Swedish replies."}
        def network(config, path, payload, deadline):
            result = self.network(config, path, payload, deadline)
            if path == "/api/chat":
                result["message"]["content"] = json.dumps({"approved": [0], "summary": "Reviewed."} if "approved" in payload["format"]["properties"]
                                                          else {"memories": [draft], "summary": "Normalized."})
            return result
        deadline = time.monotonic() + 30
        with patch("backend.provider.background_network", side_effect=network):
            result = self.generate(schema=self.prepared["schema"], deadline=deadline)
            review = self.store.record_stage_result(self.prepared["job"], result, lambda text: text, self.prepared["context"])
            self.assertEqual(MemoryStore(self.database, timezone.utc).list(), [])
            with self.assertRaisesRegex(ValueError, "source or settings"):
                self.generate(schema=self.prepared["schema"], deadline=deadline)
            with self.assertRaisesRegex(ValueError, "deadline"):
                self.provider.generate_context(review["instructions"], review["messages"], kind=review["job"]["kind"],
                                               job=review["job"], schema=review["schema"], deadline=0)
            self.assertEqual(len(self.provider.read_state()["ledger"]), 1)
            self.assertEqual(self.store.status()["today_tokens"], 40)
            second = self.provider.generate_context(review["instructions"], review["messages"], kind=review["job"]["kind"],
                                                    job=review["job"], schema=review["schema"], deadline=deadline)
            self.store.complete_auto(review["job"], second, review["drafts"], lambda text: text)
        dispatches = [call for call in self.calls if call[1] == "/api/chat"]
        self.assertEqual([call[2]["model"] for call in dispatches], ["form:7b", "review:7b"])
        self.assertEqual([call[2]["format"] for call in dispatches], [self.prepared["schema"], review["schema"]])
        self.assertEqual([call[2]["options"]["num_predict"] for call in dispatches], [32768, 32768])
        self.assertEqual([call[2]["options"]["num_ctx"] for call in dispatches], [65536, 65536])
        self.assertTrue(all(call[2]["keep_alive"] == 0 and "tools" not in call[2] for call in dispatches))
        self.assertEqual([call[3] for call in dispatches], [deadline, deadline])
        ledger = self.provider.read_state()["ledger"]
        self.assertEqual([(entry["kind"], entry["stage"]) for entry in ledger], [("memory", 0), ("reflection", 1)])
        self.assertEqual(ledger[0]["job_id"], ledger[1]["job_id"])
        self.assertEqual(ledger[0]["attempt"], ledger[1]["attempt"])
        self.assertEqual(self.store.status()["today_jobs"], 1)
        self.assertEqual(self.store.status()["today_tokens"], 80)
        self.assertEqual(self.provider.read_state()["config"]["ollama_context_tokens"], 131072)
        self.assertEqual(self.provider.read_state()["config"]["max_output_tokens"], 32768)
        self.assertEqual(MemoryStore(self.database, timezone.utc).list()[0]["origin"], "curated")

    def test_background_kind_schema_and_deadline_are_checked_before_reserving(self):
        for options in ({"kind": "memory"}, {"kind": "coding"}, {"schema": {"type": "object"}},
                        {"deadline": 0}, {"deadline": float("inf")}, {"deadline": True}):
            with self.subTest(options=options), patch("backend.provider.background_network") as request, self.assertRaises(ValueError):
                self.generate(**options)
            request.assert_not_called()
            self.assertEqual(self.provider.read_state()["ledger"], [])
        for changes in ({"kind": "memory"}, {"mode": "curate"}):
            forged = {**self.prepared["job"], **changes}
            with self.subTest(changes=changes), patch("backend.provider.background_network") as request, self.assertRaisesRegex(ValueError, "source or settings"):
                self.provider.generate_context(self.prepared["instructions"], self.prepared["messages"], kind=forged["kind"], job=forged)
            request.assert_not_called()
            self.assertEqual(self.provider.read_state()["ledger"], [])
        self.assertEqual(self.store.status()["today_jobs"], 0)

    def test_claim_and_ledger_are_committed_before_one_tool_free_structured_dispatch(self):
        def inspect_dispatch(config, path, payload, deadline):
            with closing(sqlite3.connect(self.database)) as db:
                self.assertEqual(db.execute("SELECT state FROM reflection_jobs").fetchone()[0], "dispatched")
                ledger = json.loads(db.execute("SELECT value FROM provider_state").fetchone()[0])["ledger"]
                self.assertEqual(ledger[-1]["status"], "reserved")
                self.assertEqual(ledger[-1]["job_id"], self.prepared["job"]["id"])
                self.assertEqual(db.execute("SELECT jobs FROM reflection_budget").fetchone()[0], 1)
            status = self.store.status()
            self.assertTrue(status["running"])
            self.assertFalse(status["waiting_for_ollama"])
            return self.network(config, path, payload, deadline)
        with patch("backend.provider.background_network", side_effect=inspect_dispatch), patch("backend.provider.network") as foreground:
            result = self.generate()
        foreground.assert_not_called()
        self.assertEqual([call[1] for call in self.calls], ["/api/show", "/api/chat"])
        payload = self.calls[-1][2]
        self.assertEqual(payload["model"], "gpt-oss:20b")
        self.assertEqual(payload["options"]["num_ctx"], 8192)
        self.assertEqual(payload["format"], OUTPUT_SCHEMA)
        self.assertEqual(payload["keep_alive"], 0)
        self.assertEqual(payload["think"], "low")
        self.assertNotIn("tools", payload)
        self.assertEqual(self.calls[0][3], self.calls[1][3])
        self.assertEqual(self.provider.read_state()["ledger"][-1]["status"], "settled")
        self.store.complete(self.prepared["job"], result, lambda content: content)
        self.assertEqual(self.store.status()["today_tokens"], 40)

    def test_failed_settlement_orphan_is_reported_until_explicit_recovery(self):
        self.store.recover()
        original = self.provider.transaction
        failures = 0

        @contextmanager
        def transient(*args, **kwargs):
            nonlocal failures
            if failures:
                failures -= 1
                raise sqlite3.OperationalError("Synthetic temporary settlement failure.")
            with original(*args, **kwargs) as state:
                yield state

        def network(config, path, payload, deadline):
            nonlocal failures
            result = self.network(config, path, payload, deadline)
            if path == "/api/chat":
                failures = 2  # Settlement and its exception cleanup both fail.
            return result

        worker = ReflectionWorker(self.store, self.provider, lambda text: text)
        with patch.object(self.provider, "transaction", side_effect=transient), patch("backend.provider.background_network", side_effect=network):
            worker.step()
        entry = self.provider.read_state()["ledger"][-1]
        self.assertEqual(entry["status"], "reserved")
        self.assertTrue(entry["background_unknown"])
        self.assertEqual(self.store.status()["queued"], 0)
        self.assertTrue(self.store.status()["running"])
        self.assertTrue(self.store.status()["waiting_for_ollama"])
        with patch("backend.provider.background_network", return_value={"models": []}) as poll:
            worker.step()
        poll.assert_not_called()
        self.assertTrue(self.store.status()["waiting_for_ollama"])
        with patch("backend.provider.background_network", return_value={"models": []}):
            self.provider.recover_local(entry["id"], True, entry["connection_config"]["base_url"])
        self.assertFalse(self.store.status()["running"])
        self.assertFalse(self.store.status()["waiting_for_ollama"])

    def test_configuration_changes_away_and_back_abort_before_inference(self):
        def change_config(config, path, payload, deadline):
            if path == "/api/show":
                original = self.provider.read_state()["config"]
                self.provider.configure({**original, "max_output_tokens": 64}, "", False)
                self.provider.configure(original, "", False)
            return self.network(config, path, payload, deadline)
        with patch("backend.provider.background_network", side_effect=change_config), self.assertRaisesRegex(ValueError, "settings changed"):
            self.generate()
        self.assertEqual([call[1] for call in self.calls], ["/api/show"])
        self.assertEqual(self.provider.read_state()["ledger"][-1]["status"], "failed")

    def test_deleted_source_aborts_before_inference(self):
        def delete_source(config, path, payload, deadline):
            if path == "/api/show":
                self.workspace["chats"][0]["messages"] = []
                with closing(sqlite3.connect(self.database)) as db, db:
                    db.execute("UPDATE workspace SET value=?", (json.dumps(self.workspace),))
            return self.network(config, path, payload, deadline)
        with patch("backend.provider.background_network", side_effect=delete_source), self.assertRaisesRegex(ValueError, "source or settings"):
            self.generate()
        self.assertEqual([call[1] for call in self.calls], ["/api/show"])

    def test_ambiguous_dispatch_blocks_foreground_generation_until_confirmed_restart(self):
        def disconnect(config, path, payload, deadline):
            if path == "/api/chat":
                raise BackgroundUncertain("Synthetic disconnect", False)
            return self.network(config, path, payload, deadline)
        with patch("backend.provider.background_network", side_effect=disconnect), self.assertRaises(BackgroundUncertain):
            self.generate()
        self.store.finish_failure(self.prepared["job"], "Synthetic disconnected local inference")
        entry = self.provider.read_state()["ledger"][-1]
        self.assertEqual(entry["status"], "reserved")
        self.assertTrue(entry["background_unknown"])
        with patch("backend.provider.network") as request, self.assertRaisesRegex(ValueError, "request is running"):
            self.provider.generate_context("Synthetic request", [{"role": "user", "content": "Synthetic context"}])
        request.assert_not_called()
        worker = ReflectionWorker(self.store, self.provider, lambda text: text)
        with patch("backend.provider.background_network", return_value={"models": []}) as poll:
            worker.step()
        poll.assert_not_called()
        self.assertEqual(self.provider.read_state()["ledger"][-1]["status"], "reserved")
        with patch("backend.provider.background_network", return_value={"models": []}):
            self.provider.recover_local(entry["id"], True, entry["connection_config"]["base_url"])
        self.assertEqual(self.provider.read_state()["ledger"][-1]["status"], "failed")


if __name__ == "__main__":
    unittest.main()

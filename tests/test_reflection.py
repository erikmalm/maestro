"""Durable, bounded reflection proposes evidence without silently creating memory."""
import asyncio
from contextlib import closing
from datetime import datetime
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

from backend.memory import MemoryStore
from backend.provider import DEFAULT, Provider, ProviderFailure
from backend.reflection import INSTRUCTIONS, OUTPUT_SCHEMA, ReflectionStore, ReflectionWorker
from backend.work_config import WorkConfig


class ReflectionTests(unittest.TestCase):
    def setUp(self):
        self.directory = self.enterContext(tempfile.TemporaryDirectory(prefix="maestro-reflection-"))
        self.database = Path(self.directory) / "workspace.sqlite3"
        self.timezone = ZoneInfo("Europe/Stockholm")
        self.now = datetime(2026, 10, 3, 12, tzinfo=self.timezone).timestamp()
        self.enterContext(patch.object(ReflectionStore, "clock", lambda store: self.now))
        self.workspace = {"chats": [{"id": "chat", "messages": []}],
                          "limits": {"max_tokens": 100000, "run_usd": 1, "daily_usd": 5, "monthly_usd": 50},
                          "work_config": WorkConfig(enabled=True, debounce_seconds=0, idle_seconds=0, max_output_tokens=512, max_tokens_per_day=100000).model_dump()}
        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute("CREATE TABLE workspace (id INTEGER PRIMARY KEY, value TEXT NOT NULL)")
            db.execute("INSERT INTO workspace VALUES (1,?)", (json.dumps(self.workspace),))
        self.store = ReflectionStore(self.database, self.timezone)
        self.memory = MemoryStore(self.database, self.timezone)
        self.memory.initialize()
        self.store.initialize()
        self.provider = Provider(self.database, self.timezone)
        self.provider.configure({**DEFAULT, "protocol": "ollama", "base_url": "http://127.0.0.1:11434", "model": "qwen2.5:7b", "max_output_tokens": 512, "ollama_context_tokens": 4096}, "", False)
        with self.provider.transaction() as state:
            state["models"] = ["qwen2.5:7b", "gpt-oss:20b"]
        self.calls = []
        self.inference_hook = None
        self.body_override = None
        self.worker = ReflectionWorker(self.store, self.provider, lambda text: text)
        self.enterContext(patch("backend.provider.background_network", side_effect=self.network))

    def save(self, db=None):
        if db is None:
            with self.store.transaction() as connection:
                self.save(connection)
        else:
            db.execute("UPDATE workspace SET value=? WHERE id=1", (json.dumps(self.workspace),))

    def queue(self, text="I prefer concise Python code.", source_id=None):
        source_id = source_id or f"source-{len(self.workspace['chats'][0]['messages'])}"
        self.workspace["chats"][0]["messages"].append({"id": source_id, "role": "user", "text": text})
        with self.store.transaction() as db:
            self.save(db)
            self.store.enqueue("chat", self.workspace, db)

    def network(self, config, path, payload, deadline):
        self.calls.append((config, path, payload))
        if path == "/api/show":
            return {"details": {"format": "gguf", "family": "gptoss"}, "capabilities": ["completion"]}
        if path == "/api/ps":
            return {"models": []}
        sources = json.loads(payload["messages"][-1]["content"])
        text = sources[-1]["text"]
        body = self.body_override if self.body_override is not None else {"memories": [{
            "content": text[:1000], "evidence": text[:1000], "source_message_id": sources[-1]["source_message_id"]}]}
        if self.inference_hook:
            self.inference_hook()
        return {"done": True, "message": {"content": json.dumps(body)}, "prompt_eval_count": 100, "eval_count": 50}

    def jobs(self):
        with self.store.transaction() as db:
            return [(state, json.loads(data)) for state, data in db.execute("SELECT state,data FROM reflection_jobs ORDER BY rowid")]

    def dispatch(self):
        prepared = self.store.prepare()
        self.assertIsNotNone(prepared)
        result = self.provider.generate_context(prepared["instructions"], prepared["messages"], job=prepared["job"])
        return prepared["job"], result

    def test_source_checkpoints_coalesce_without_copying_or_replaying_old_chats(self):
        self.workspace["chats"][0]["messages"] = [{"id": "old", "role": "user", "text": "Old unprocessed source."}]
        self.queue()
        self.queue("I prefer SQLite for this project.")
        jobs = self.jobs()
        self.assertEqual(len(jobs), 1)
        self.assertEqual(len(jobs[0][1]["sources"]), 2)
        self.assertNotIn("I prefer", json.dumps(jobs))
        self.assertNotIn("old", [source["id"] for source in jobs[0][1]["sources"]])
        with self.store.transaction() as db:
            self.store.enqueue("chat", self.workspace, db)
        self.assertEqual(len(self.jobs()), 1)

    def test_single_local_call_proposes_exact_evidence_and_only_acceptance_enables_recall(self):
        self.queue()
        proposal = {"content": "I prefer concise Python code.", "evidence": "I prefer concise Python code.", "source_message_id": "source-0"}
        self.body_override = {"memories": [proposal, proposal.copy()]}
        self.worker.step()
        self.assertEqual([path for _, path, _ in self.calls], ["/api/show", "/api/chat"])
        payload = self.calls[-1][2]
        self.assertEqual(payload["model"], "gpt-oss:20b")
        self.assertEqual(payload["format"], OUTPUT_SCHEMA)
        self.assertEqual(payload["keep_alive"], 0)
        self.assertNotIn("tools", payload)
        self.assertEqual(self.memory.recall("concise Python", "chat"), [])
        self.assertEqual(len(self.store.status()["candidates"]), 1)
        candidate = self.store.status()["candidates"][0]
        record = self.store.accept(candidate["id"], self.memory, lambda text: text)
        self.assertEqual(record["origin"], "explicit")
        self.assertEqual(record["scope"], "conversation")
        self.assertEqual(record["source_message_id"], candidate["source_message_id"])
        self.assertEqual(self.memory.recall("concise Python", "chat"), [record])
        self.assertEqual(self.memory.recall("concise Python", "other"), [])
        self.assertEqual(self.store.status()["today_tokens"], 150)
        self.assertEqual(self.store.status()["candidates"], [])
        self.body_override = None
        # Conversation-only memory must not suppress another conversation's proposal.
        for chat_id in ("other", "third"):
            chat = {"id": chat_id, "messages": [{"id": chat_id + "-source", "role": "user", "text": record["content"]}]}
            self.workspace["chats"].append(chat)
            with self.store.transaction() as db:
                self.save(db)
                self.store.enqueue(chat_id, self.workspace, db)
            self.worker.step()
            if chat_id == "other":
                candidate = self.store.status()["candidates"][0]
                self.assertEqual(candidate["chat_id"], "other")
                self.store.accept(candidate["id"], self.memory, lambda text: text, scope="workspace")
            else:
                # The approved workspace memory now applies to every conversation.
                self.assertEqual(self.store.status()["candidates"], [])

    def test_claimed_jobs_recover_but_dispatched_jobs_never_retry(self):
        self.queue()
        prepared = self.store.prepare()
        first_attempt = prepared["job"]["attempt"]
        ReflectionStore(self.database, self.timezone).recover()
        job, result = self.dispatch()
        self.assertNotEqual(job["attempt"], first_attempt)
        self.store.recover()
        self.assertEqual(self.jobs()[0][0], "failed")
        self.assertEqual(self.store.status()["today_jobs"], 1)
        self.assertGreater(self.store.status()["today_tokens"], 150)
        self.worker.step()
        self.assertEqual(len(self.calls), 2)
        with self.assertRaises(ValueError):
            self.store.complete(job, result, lambda text: text)
        self.assertEqual(self.store.status()["candidates"], [])

    def test_debounce_global_chat_idle_and_pause_preserve_the_queue(self):
        self.workspace["work_config"].update(debounce_seconds=60, idle_seconds=30)
        self.queue()
        self.now += 29
        self.assertIsNone(self.store.prepare())
        self.now += 31
        with self.store.transaction() as db:
            self.store.touch(self.workspace, db)
        self.assertIsNone(self.store.prepare())
        self.now += 30
        self.workspace["work_config"]["enabled"] = False
        with self.store.transaction() as db:
            self.save(db)
            self.store.invalidate(db, clear_candidates=False)
        self.assertIsNone(self.store.prepare())
        self.assertEqual(self.store.status()["queued"], 1)
        self.workspace["work_config"]["enabled"] = True
        self.save()
        self.worker.step()
        self.assertEqual(len(self.store.status()["candidates"]), 1)

    def test_shared_foreground_reservation_prevents_claiming_background_job(self):
        self.queue()
        with self.provider.transaction() as state:
            state["ledger"].append({"id": "foreground", "status": "reserved"})
        self.worker.step()
        self.assertEqual(self.calls, [])
        self.assertEqual(self.store.status()["queued"], 1)

    def test_unknown_foreground_dispatch_blocks_worker_until_explicit_restart_recovery(self):
        self.queue()
        with self.provider.transaction() as state:
            state["ledger"].append({"id": "foreground-unknown", "at": self.store.stamp(), "protocol": "ollama", "kind": "chat", "cost": 0,
                                    "status": "reserved", "local_unknown": True, "connection_config": state["config"].copy()})
        self.assertTrue(self.store.status()["waiting_for_ollama"])
        self.assertTrue(self.store.status()["running"])
        with patch("backend.provider.background_network", return_value={"models": [{"name": "qwen2.5:7b"}]}):
            self.worker.step()
        self.assertEqual(self.calls, [])
        self.assertEqual(self.store.status()["queued"], 1)
        self.assertTrue(self.store.status()["waiting_for_ollama"])
        self.worker.step()
        self.assertEqual(self.calls, [])
        self.assertEqual(self.provider.read_state()["ledger"][0]["status"], "reserved")
        original_url = self.provider.read_state()["ledger"][0]["connection_config"]["base_url"]
        with patch("backend.provider.background_network", return_value={"models": []}):
            self.provider.recover_local("foreground-unknown", True, original_url)
        self.worker.step()
        self.assertEqual([path for _, path, _ in self.calls], ["/api/show", "/api/chat"])
        self.assertFalse(self.store.status()["waiting_for_ollama"])
        self.assertEqual(len(self.store.status()["candidates"]), 1)
        orphan = self.provider.read_state()["ledger"][0]
        self.assertEqual(orphan["status"], "failed")
        self.assertFalse(orphan["local_unknown"])

    def test_missing_endpoint_for_interrupted_foreground_dispatch_has_an_actionable_stop_reason(self):
        with self.provider.transaction() as state:
            state["ledger"].append({"id": "legacy-local", "protocol": "ollama", "cost": 0,
                                    "status": "reserved", "local_unknown": True})
        status = self.store.status()
        self.assertTrue(status["waiting_for_ollama"])
        self.assertIn("Restart its original Ollama server", status["last_stop_reason"])
        self.assertIn("Usage & limits", status["last_stop_reason"])
        self.worker.step()
        self.assertEqual(self.calls, [])
        self.assertEqual(self.provider.read_state()["ledger"][0]["status"], "reserved")

    def test_debounced_chat_does_not_block_another_chat_that_is_ready(self):
        self.workspace["work_config"]["debounce_seconds"] = 60
        self.queue()
        self.now += 10
        other = {"id": "other", "messages": [{"id": "other-source", "role": "user", "text": "I prefer concise Rust code."}]}
        self.workspace["chats"].append(other)
        with self.store.transaction() as db:
            self.save(db)
            self.store.enqueue("other", self.workspace, db)
        self.now += 60
        self.queue("I prefer Python docstrings.")
        prepared = self.store.prepare()
        self.assertEqual(prepared["job"]["chat_id"], "other")
        self.assertEqual([(state, job["chat_id"]) for state, job in self.jobs()], [("queued", "chat"), ("claimed", "other")])
        self.assertEqual(self.calls, [])

    def test_historical_missing_or_invalid_debounce_timestamp_is_due(self):
        for ready_at in (None, "obsolete", [], {}):
            with self.subTest(ready_at=ready_at):
                self.queue()
                with self.store.transaction() as db:
                    job = json.loads(db.execute("SELECT data FROM reflection_jobs WHERE state='queued'").fetchone()[0])
                    if ready_at is None:
                        job.pop("ready_at")
                    else:
                        job["ready_at"] = ready_at
                    self.store.write_job(db, job, "queued")
                prepared = self.store.prepare()
                self.assertIsNotNone(prepared)
                with self.store.transaction() as db:
                    self.store.write_job(db, prepared["job"], "cancelled")

    def test_due_periodic_job_runs_while_chat_waits_for_debounce(self):
        self.workspace["work_config"].update(auto_curate=True, periodic_reflection=True, debounce_seconds=60)
        with self.provider.transaction() as state:
            state["config"]["ollama_context_tokens"] = 8192
        self.queue()
        prepared = self.store.prepare()
        self.assertEqual(prepared["job"]["mode"], "periodic")
        self.assertEqual(self.jobs()[0][0], "queued")
        self.assertEqual(self.calls, [])
        self.store.finish_failure(prepared["job"])
        self.now += 60
        self.assertEqual(self.store.prepare()["job"]["chat_id"], "chat")

    def test_daily_budget_reserves_unknown_failure_and_waits_until_local_midnight(self):
        self.workspace["work_config"]["max_jobs_per_day"] = 1
        self.queue()
        with patch("backend.provider.background_network", side_effect=ProviderFailure("Synthetic unreachable model.", False)):
            self.worker.step()
        self.assertEqual(self.store.status()["today_jobs"], 1)
        self.assertGreater(self.store.status()["today_tokens"], 512)
        self.queue("I prefer tabs in synthetic projects.")
        self.worker.step()
        self.assertEqual(self.calls, [])
        self.assertIn("Daily reflection budget", self.store.status()["last_stop_reason"])
        self.now = datetime(2026, 10, 4, 0, 0, 1, tzinfo=self.timezone).timestamp()
        self.worker.step()
        self.assertEqual(self.store.status()["today_jobs"], 1)
        self.assertEqual(len(self.store.status()["candidates"]), 1)

    def test_token_budget_prevents_any_model_request(self):
        self.workspace["work_config"]["max_tokens_per_day"] = 100
        self.queue()
        self.worker.step()
        self.assertEqual(self.calls, [])
        self.assertEqual(self.store.status()["today_jobs"], 0)
        self.assertEqual(self.store.status()["queued"], 1)

    def test_daily_reservation_uses_work_output_limit_even_when_foreground_limit_is_smaller(self):
        self.workspace["work_config"].update(background_context_tokens=65536, max_output_tokens=8192)
        self.provider.configure({**self.provider.read_state()["config"], "ollama_context_tokens": 16384,
                                 "max_output_tokens": 1024}, "", False)
        self.queue()
        prepared = self.store.prepare()
        expected = len((prepared["instructions"] + json.dumps(prepared["messages"]) + json.dumps(prepared["schema"])).encode()) + 2048 + 8192
        self.store.recover()
        self.workspace["work_config"]["max_tokens_per_day"] = expected - 1
        self.save()
        self.assertIsNone(self.store.prepare())
        self.assertIn("Daily reflection budget", self.store.status()["last_stop_reason"])
        self.assertEqual(self.store.status()["today_jobs"], 0)
        self.assertEqual(self.provider.read_state()["ledger"], [])
        self.assertEqual(self.calls, [])

    def test_job_that_exceeds_remaining_budget_does_not_block_smaller_ready_job(self):
        self.workspace["work_config"]["max_tokens_per_day"] = 10000
        with self.provider.transaction() as state:
            state["config"]["ollama_context_tokens"] = 32768
        self.queue("I prefer concise Python examples. " + "Synthetic detail. " * 900)
        other = {"id": "other", "messages": [{"id": "other-source", "role": "user", "text": "I prefer concise Rust code."}]}
        self.workspace["chats"].append(other)
        with self.store.transaction() as db:
            self.save(db)
            self.store.enqueue("other", self.workspace, db)
        self.assertIsNone(self.store.prepare())
        self.assertIn("Daily reflection budget", self.jobs()[0][1]["stop_reason"])
        self.worker.step()
        self.assertEqual([candidate["chat_id"] for candidate in self.store.status()["candidates"]], ["other"])
        self.assertEqual(self.jobs()[0][0], "queued")
        self.assertEqual(self.store.status()["today_jobs"], 1)
        self.assertEqual(self.store.status()["today_tokens"], 150)

    def test_source_deletion_during_inference_cannot_recreate_candidates(self):
        self.queue()
        def remove_source():
            self.workspace["chats"] = []
            with self.store.transaction() as db:
                self.save(db)
                self.store.delete_chat("chat", db)
        self.inference_hook = remove_source
        self.worker.step()
        self.assertEqual(self.store.status()["candidates"], [])
        self.assertEqual(self.memory.list(), [])
        self.assertEqual(self.jobs(), [])

    def test_new_user_message_during_inference_invalidates_older_batch_even_with_zero_idle(self):
        self.queue()
        self.inference_hook = lambda: self.queue("I now prefer Rust instead.")
        self.worker.step()
        self.assertEqual(self.store.status()["candidates"], [])
        self.assertEqual(self.store.status()["queued"], 1)
        self.assertEqual(self.jobs()[0][0], "failed")
        self.inference_hook = None
        self.worker.step()
        self.assertEqual(self.store.status()["candidates"][0]["content"], "I now prefer Rust instead.")

    def test_explicit_memory_changes_cancel_late_results_and_pending_proposals(self):
        explicit = self.memory.remember("I prefer Ruby.")
        self.queue()
        self.inference_hook = lambda: self.memory.update(explicit["id"], "I prefer Rust.")
        self.worker.step()
        self.assertEqual(self.store.status()["candidates"], [])
        self.inference_hook = None
        self.queue("I prefer SQLite for this project.")
        self.worker.step()
        self.assertEqual(len(self.store.status()["candidates"]), 1)
        self.memory.forget(explicit["id"])
        self.assertEqual(self.store.status()["candidates"], [])

    def test_rejection_leaves_bounded_hash_tombstones_that_prevent_reproposal(self):
        self.queue()
        job, result = self.dispatch()
        self.store.complete(job, result, lambda text: text)
        candidate = self.store.status()["candidates"][0]
        self.store.reject(candidate["id"])
        with self.store.transaction() as db:
            state, data = db.execute("SELECT state,data FROM reflection_candidates").fetchone()
            self.assertEqual(state, "rejected")
            self.assertNotIn(candidate["content"], data)
            # Replaying the same evidence under a new job cannot resurrect rejection.
            epoch = db.execute("SELECT epoch FROM reflection_meta").fetchone()[0]
            replay = {**job, "id": "replay", "epoch": epoch, "attempt": "another", "request_id": "replay-request", "day": self.store.day(), "reserved_tokens": 4000}
            replay.pop("usage_settled", None)
            db.execute("INSERT INTO reflection_jobs VALUES ('replay','chat','dispatched',?)", (json.dumps(replay),))
            db.execute("UPDATE reflection_budget SET tokens=tokens+4000")
        self.store.complete(replay, {**result, "request_id": "replay-request"}, lambda text: text)
        self.assertEqual(self.store.status()["candidates"], [])

    def test_forgetting_approved_memory_preserves_tombstone_and_cancels_inflight_work(self):
        self.queue()
        self.worker.step()
        candidate = self.store.status()["candidates"][0]
        memory = self.store.accept(candidate["id"], self.memory, lambda text: text)
        self.queue("I prefer SQLite for synthetic projects.")
        self.inference_hook = lambda: self.memory.forget(memory["id"])
        self.worker.step()
        self.assertEqual(self.memory.list(), [])
        self.assertEqual(self.store.status()["candidates"], [])
        with self.store.transaction() as db:
            state, data = db.execute("SELECT state,data FROM reflection_candidates WHERE id=?", (candidate["id"],)).fetchone()
            self.assertEqual(state, "accepted")
            self.assertNotIn("content", json.loads(data))

    def test_invalid_schema_fabricated_evidence_and_secret_quotes_never_create_memory(self):
        invalid = [
            {"memories": [], "extra": True}, {"memories": "wrong type"},
            {"memories": [{"content": "I prefer Ruby.", "evidence": "I prefer concise Python code.", "source_message_id": "source-0"}]},
            {"memories": [{"content": "Invented fact", "evidence": "Invented fact", "source_message_id": "source-0"}]},
            {"memories": [{"content": "I prefer concise Python code.", "evidence": "I prefer concise Python code.", "source_message_id": "missing"}]},
        ]
        for index, body in enumerate(invalid):
            with self.subTest(index=index):
                self.body_override = body
                self.queue()
                self.worker.step()
                self.assertEqual(self.store.status()["candidates"], [])
        self.body_override = None
        self.queue("My password is synthetic-private-value.")
        self.worker.step()
        self.assertEqual(self.store.status()["candidates"], [])
        self.assertEqual(self.memory.list(), [])
        self.assertNotIn("synthetic-private-value", json.dumps(self.jobs()))

    def test_source_version_and_stale_attempt_rejected_before_candidate_commit(self):
        self.queue()
        job, result = self.dispatch()
        self.store.complete({**job, "attempt": "stale"}, result, lambda text: text)
        self.assertEqual(self.store.status()["candidates"], [])
        self.workspace["chats"][0]["messages"][0]["text"] = "Changed user fact."
        self.save()
        with self.assertRaises(ValueError):
            self.store.complete(job, result, lambda text: text)
        self.assertEqual(self.store.status()["candidates"], [])

    def test_work_context_trims_unicode_sources_within_conservative_allowance(self):
        self.workspace["work_config"]["background_context_tokens"] = 8192
        self.queue("I prefer concise code. " + "\u00e5\u4f8b" * 1500)
        prepared = self.store.prepare()
        self.assertIsNotNone(prepared)
        bound = len((INSTRUCTIONS + json.dumps(prepared["messages"]) + json.dumps(OUTPUT_SCHEMA)).encode("utf-8")) + 2048 + 512
        self.assertLessEqual(bound, 8192)
        self.assertLess(len(json.loads(prepared["messages"][0]["content"])[0]["text"]), 3023)
        self.assertNotIn("content", prepared["job"]["sources"][0])

    def test_status_reads_do_not_create_or_modify_storage_and_success_clears_old_reason(self):
        other = ReflectionStore(Path(self.directory) / "missing.sqlite3", self.timezone)
        self.assertEqual(other.status()["queued"], 0)
        self.assertFalse(Path(other.database).exists())
        self.queue()
        before = self.database.read_bytes()
        self.store.status()
        self.assertEqual(self.database.read_bytes(), before)
        self.worker.step()
        self.assertIsNone(self.store.status()["last_stop_reason"])

    def test_disabled_or_budget_waiting_controller_does_not_write_on_each_poll(self):
        self.workspace["work_config"]["max_jobs_per_day"] = 0
        self.queue()
        self.assertIsNone(self.store.prepare())
        before = self.database.read_bytes()
        for _ in range(3):
            self.assertIsNone(self.store.prepare())
        self.assertEqual(self.database.read_bytes(), before)
        self.workspace["work_config"]["enabled"] = False
        self.save()
        before = self.database.read_bytes()
        for _ in range(3):
            self.assertIsNone(self.store.prepare())
        self.assertEqual(self.database.read_bytes(), before)

    def test_graceful_worker_shutdown_waits_for_inflight_step(self):
        async def check():
            stop = asyncio.Event()
            calls = []
            def step():
                calls.append("finished")
                loop.call_soon_threadsafe(stop.set)
            loop = asyncio.get_running_loop()
            with patch.object(self.worker, "step", side_effect=step):
                await self.worker.run(stop)
            self.assertEqual(calls, ["finished"])
        asyncio.run(check())

    def test_controller_recovers_after_a_transient_step_failure(self):
        async def check():
            stop = asyncio.Event()
            calls = []
            loop = asyncio.get_running_loop()
            def step():
                calls.append("attempt")
                if len(calls) == 1:
                    raise sqlite3.OperationalError("Synthetic temporary storage failure.")
                loop.call_soon_threadsafe(stop.set)
            with patch.object(self.worker, "step", side_effect=step):
                await self.worker.run(stop)
            self.assertEqual(len(calls), 2)
        asyncio.run(check())

    def test_failed_failure_write_is_retried_before_processing_another_job(self):
        self.queue()
        self.body_override = {"invalid": "Synthetic malformed result."}
        finish = self.store.finish_failure
        attempts = []
        def transient(*arguments):
            attempts.append(True)
            if len(attempts) == 1:
                raise sqlite3.OperationalError("Synthetic temporary write failure.")
            return finish(*arguments)
        with patch.object(self.store, "finish_failure", side_effect=transient):
            with self.assertRaises(sqlite3.OperationalError):
                self.worker.step()
            self.assertTrue(self.store.status()["running"])
            self.worker.step()
        self.assertFalse(self.store.status()["running"])
        self.assertIsNone(self.worker.pending_failure)
        self.body_override = None
        self.queue("I prefer SQLite.")
        self.worker.step()
        self.assertEqual(len(self.store.status()["candidates"]), 1)


if __name__ == "__main__":
    unittest.main()

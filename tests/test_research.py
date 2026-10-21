"""Durable local research ownership with synthetic requests, fixed receipts and no dispatched work."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import patch
import uuid

from backend.research import MAX_REQUEST, ResearchStore


class ResearchTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory(prefix="maestro-research-core-")))
        self.database = self.root / "private" / "workspace.sqlite3"
        self.store = ResearchStore(self.database, timezone.utc)
        self.clock = datetime(2026, 10, 21, 10, tzinfo=timezone.utc).timestamp()
        self.enterContext(patch.object(ResearchStore, "clock", side_effect=lambda: self.clock))
        self.permissions = {"source_mode": "saved_only", "hosted_allowed": False,
                            "memory_recall": False, "memory_derived": False, "chat_id": None}
        totals = {"model_calls": 4, "tokens": 100000, "seconds": 120,
                  "estimated_model_usd": 1, "hosted_searches": 0}
        self.budget = {"total": totals, "consumed": dict.fromkeys(totals, 0), "reserved": dict.fromkeys(totals, 0),
                       "remaining": totals.copy(), "deadline": datetime.fromtimestamp(self.clock + 120, timezone.utc).isoformat(),
                       "parent_limits": {"run_usd": 1, "daily_usd": 5, "monthly_usd": 50, "max_tokens": 100000},
                       "daily_search": {"limit": 20, "used": 3, "remaining": 17}}
        self.result = {"lookup_state": "ready", "search_limited": False, "skipped_count": 0,
                       "candidates": [{"capture_id": "a" * 32, "content_hash": "b" * 64, "manifest_hash": "c" * 64,
                                       "retrieved_at": "2026-10-16T12:00:00+00:00", "stale": True}],
                       "summary": "Saved-evidence inspection finished. No reviewed answer was produced."}

    def create(self, submission=None, **changes):
        values = {"submission_id": submission or uuid.uuid4().hex, "request": "Inspect the saved public quarterly report.",
                  "query": "quarterly report", "chat_id": None, "permissions": deepcopy(self.permissions), "budget": deepcopy(self.budget)}
        values.update(changes)
        return self.store.create(**values)

    def snapshot(self):
        return {str(path.relative_to(self.root)): path.read_bytes()
                for path in self.root.rglob("*") if path.is_file()}

    def workspace(self, chats):
        self.database.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute("CREATE TABLE IF NOT EXISTS workspace(id INTEGER PRIMARY KEY,value TEXT NOT NULL)")
            db.execute("INSERT OR REPLACE INTO workspace VALUES(1,?)", (json.dumps({"chats": chats}),))

    def test_cold_reads_and_foreground_check_do_not_create_database_or_schema(self):
        self.assertEqual(self.store.list(), [])
        with self.assertRaises(KeyError):
            self.store.get("a" * 32)
        self.assertFalse(self.database.parent.exists())
        with closing(sqlite3.connect(":memory:")) as db:
            self.assertFalse(ResearchStore.owns_foreground(db))
            self.assertEqual(db.execute("SELECT name FROM sqlite_master").fetchall(), [])

    def test_idempotency_retains_run_events_and_captured_limits_after_settings_change(self):
        submission = uuid.uuid4().hex
        first, created = self.create(submission)
        self.assertTrue(created)
        before = self.snapshot()
        # Current settings are not part of the submitted-payload fingerprint.
        repeated, created = self.create(submission, permissions={"now": "invalid"}, budget={"changed": True})
        self.assertFalse(created)
        self.assertEqual(repeated, first)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(first["budget"]["parent_limits"], self.budget["parent_limits"])
        self.assertEqual([event["kind"] for event in first["events"]], ["submitted"])
        self.assertNotIn("claim_token", first)
        for field, value in (("request", "Different request"), ("query", "different"), ("chat_id", "another-chat")):
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.create(submission, **{field: value})

    def test_terminal_duplicate_does_not_repeat_claim_or_dispatch(self):
        run, _ = self.create()
        claimed, token = self.store.claim(run["id"])
        self.assertEqual(claimed["state"], "running")
        self.assertIsNone(self.store.claim(run["id"]))
        self.clock += 2.25
        self.assertTrue(self.store.finish(run["id"], token, self.result))
        finished = self.store.get(run["id"])
        duplicate, created = self.create(run["submission_id"])
        self.assertFalse(created)
        self.assertEqual(duplicate, finished)
        self.assertIsNone(self.store.claim(run["id"]))
        self.assertEqual(finished["state"], "partial")
        self.assertEqual([event["kind"] for event in finished["events"]],
                         ["submitted", "local_lookup_started", "local_lookup_finished", "finished"])
        self.assertEqual([event["sequence"] for event in finished["events"]], [1, 2, 3, 4])
        self.assertEqual(finished["budget"]["consumed"]["seconds"], 2.25)
        self.assertEqual(finished["budget"]["remaining"]["seconds"], 117.75)
        for name in ("model_calls", "tokens", "estimated_model_usd", "hosted_searches"):
            self.assertEqual(finished["budget"]["consumed"][name], 0)
        self.assertEqual(finished["result"], self.result)

    def test_same_submission_is_serialized_across_connections(self):
        self.store.initialize()
        submission, barrier = uuid.uuid4().hex, threading.Barrier(2)

        def submit():
            barrier.wait()
            return self.create(submission)

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: submit(), range(2)))
        self.assertEqual(sum(created for _, created in results), 1)
        self.assertEqual(len({run["id"] for run, _ in results}), 1)
        self.assertEqual(len(self.store.list()), 1)

    def test_only_one_distinct_foreground_submission_can_win(self):
        self.store.initialize()
        barrier = threading.Barrier(2)

        def submit():
            barrier.wait()
            try:
                return self.create()[0]["id"]
            except ValueError:
                return None

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: submit(), range(2)))
        self.assertEqual(sum(run is not None for run in results), 1)
        with closing(sqlite3.connect(self.database)) as db:
            self.assertTrue(ResearchStore.owns_foreground(db))
        run = self.store.list()[0]
        self.assertEqual(self.store.cancel(run["id"])["state"], "cancelled")
        self.assertTrue(self.create()[1])

    def test_creation_participates_in_callers_workspace_transaction_and_rollback(self):
        self.workspace([{"id": "chat", "messages": []}])
        with closing(sqlite3.connect(self.database)) as db:
            db.execute("BEGIN IMMEDIATE")
            run, _ = self.create(chat_id="chat", permissions={**self.permissions, "chat_id": "chat"}, db=db)
            self.assertTrue(ResearchStore.owns_foreground(db))
            db.rollback()
        self.assertEqual(self.store.list(), [])
        with self.assertRaises(KeyError):
            self.store.get(run["id"])

    def test_provider_reservations_and_unknown_calls_block_new_admission_but_not_retries(self):
        run, _ = self.create()
        self.store.cancel(run["id"])
        for state in ("reserved", "uncertain"):
            with self.subTest(state=state):
                with closing(sqlite3.connect(self.database)) as db, db:
                    db.execute("CREATE TABLE IF NOT EXISTS provider_state(id INTEGER PRIMARY KEY,value TEXT NOT NULL)")
                    db.execute("INSERT OR REPLACE INTO provider_state VALUES(1,?)", (json.dumps({"ledger": [{"status": state}]}),))
                self.assertFalse(self.create(run["submission_id"])[1])
                with self.assertRaises(ValueError):
                    self.create()

    def test_wrong_or_stale_claim_cannot_commit_after_cancellation_or_new_run(self):
        run, _ = self.create()
        _, token = self.store.claim(run["id"])
        self.assertFalse(self.store.finish(run["id"], "wrong-claim", self.result))
        self.clock += 3
        cancelled = self.store.cancel(run["id"])
        self.assertEqual(cancelled["budget"]["consumed"]["seconds"], 3)
        self.assertEqual(self.store.cancel(run["id"]), cancelled)
        self.assertTrue(self.create()[1])
        self.assertFalse(self.store.finish(run["id"], token, self.result))
        self.assertFalse(self.store.fail(run["id"], token))
        self.assertIsNone(self.store.get(run["id"])["result"])
        self.assertEqual([event["kind"] for event in cancelled["events"]], ["submitted", "local_lookup_started", "cancelled"])

    def test_delete_purges_private_request_events_and_result_but_retains_idempotency_guard(self):
        secret_request = "Disposable request text that must be forgotten."
        run, _ = self.create(request=secret_request)
        _, token = self.store.claim(run["id"])
        self.assertTrue(self.store.finish(run["id"], token, self.result))
        self.assertTrue(self.store.delete(run["id"]))
        with self.assertRaises(KeyError):
            self.store.get(run["id"])
        self.assertEqual(self.store.list(), [])
        self.assertFalse(self.store.finish(run["id"], token, self.result))
        with self.assertRaises(ValueError):
            self.create(run["submission_id"], request=secret_request)
        with closing(sqlite3.connect(self.database)) as db:
            row = db.execute("SELECT state,chat_id,claim_token,payload FROM research_runs WHERE id=?", (run["id"],)).fetchone()
            self.assertEqual(row, ("deleted", None, None, "{}"))
            self.assertEqual(db.execute("SELECT COUNT(*) FROM research_events").fetchone()[0], 0)
        self.assertNotIn(secret_request.encode(), self.database.read_bytes())

    def test_chat_deletion_purges_related_history_and_fences_running_claim_atomically(self):
        self.workspace([{"id": "chat", "messages": []}, {"id": "other", "messages": []}])
        run, _ = self.create(chat_id="chat", permissions={**self.permissions, "chat_id": "chat"})
        self.store.cancel(run["id"])
        active, _ = self.create(chat_id="chat", permissions={**self.permissions, "chat_id": "chat"})
        _, token = self.store.claim(active["id"])
        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute("BEGIN IMMEDIATE")
            self.assertEqual(self.store.delete_chat("chat", db), 2)
            db.execute("UPDATE workspace SET value=? WHERE id=1", (json.dumps({"chats": [{"id": "other", "messages": []}]}),))
        self.assertFalse(self.store.finish(active["id"], token, self.result))
        self.assertEqual(self.store.list(), [])

    def test_missing_origin_chat_fences_late_finish_even_without_delete_hook(self):
        self.workspace([{"id": "chat", "messages": []}])
        run, _ = self.create(chat_id="chat", permissions={**self.permissions, "chat_id": "chat"})
        _, token = self.store.claim(run["id"])
        self.workspace([])
        self.assertFalse(self.store.finish(run["id"], token, self.result))
        with self.assertRaises(KeyError):
            self.store.get(run["id"])

    def test_final_privacy_check_preserves_memory_derived_history_and_never_downgrades(self):
        self.workspace([{"id": "chat", "messages": []}])
        run, _ = self.create(chat_id="chat", permissions={**self.permissions, "chat_id": "chat"})
        _, token = self.store.claim(run["id"])
        self.workspace([{"id": "chat", "messages": [{"id": "message", "memory_ids": ["memory"]}]}])
        self.assertTrue(self.store.finish(run["id"], token, self.result,
                                         permissions={**self.permissions, "chat_id": "chat"}))
        self.assertTrue(self.store.get(run["id"])["permissions"]["memory_derived"])
        other, _ = self.create(permissions={**self.permissions, "memory_derived": True})
        _, token = self.store.claim(other["id"])
        self.assertTrue(self.store.finish(other["id"], token, self.result, permissions=self.permissions))
        self.assertTrue(self.store.get(other["id"])["permissions"]["memory_derived"])

    def test_conflicting_permissions_fail_without_saving_result(self):
        run, _ = self.create()
        _, token = self.store.claim(run["id"])
        self.assertFalse(self.store.finish(run["id"], token, self.result,
                                          permissions={**self.permissions, "hosted_allowed": True}))
        failed = self.store.get(run["id"])
        self.assertEqual(failed["state"], "failed")
        self.assertIsNone(failed["result"])
        self.assertIn("permissions changed", failed["events"][-1]["summary"])

    def test_restart_interrupts_queued_and_running_work_without_redispatch_or_reservation_loss(self):
        for state in ("queued", "running"):
            with self.subTest(state=state):
                allowance = deepcopy(self.budget)
                allowance["reserved"].update(model_calls=1, tokens=500, estimated_model_usd=0.1)
                run, _ = self.create(budget=allowance)
                token = self.store.claim(run["id"])[1] if state == "running" else None
                self.clock += 4
                restarted = ResearchStore(self.database, timezone.utc)
                self.assertEqual(restarted.recover(), 1)
                interrupted = restarted.get(run["id"])
                self.assertEqual(interrupted["state"], "interrupted")
                self.assertEqual(interrupted["budget"]["reserved"], allowance["reserved"])
                self.assertEqual(interrupted["budget"]["consumed"]["seconds"], 4)
                self.assertIsNone(restarted.claim(run["id"]))
                self.assertFalse(restarted.finish(run["id"], token, self.result))
                self.assertEqual(restarted.recover(), 0)

    def test_expired_queued_and_running_attempts_fail_without_work_or_result(self):
        for running in (False, True):
            with self.subTest(running=running):
                allowance = deepcopy(self.budget)
                allowance["deadline"] = datetime.fromtimestamp(self.clock + 1, timezone.utc).isoformat()
                run, _ = self.create(budget=allowance)
                token = self.store.claim(run["id"])[1] if running else None
                self.clock += 2
                if running:
                    self.assertFalse(self.store.finish(run["id"], token, self.result))
                else:
                    self.assertIsNone(self.store.claim(run["id"]))
                failed = self.store.get(run["id"])
                self.assertEqual(failed["state"], "failed")
                self.assertIsNone(failed["result"])
                self.assertIn("deadline", failed["events"][-1]["summary"])

    def test_controlled_failure_has_only_a_fixed_summary_and_real_elapsed_usage(self):
        run, _ = self.create()
        _, token = self.store.claim(run["id"])
        with self.assertRaises(ValueError):
            self.store.fail(run["id"], token, "Raw exception with private data")
        self.clock += 1.5
        self.assertTrue(self.store.fail(run["id"], token, "lookup_failed"))
        failed = self.store.get(run["id"])
        self.assertEqual(failed["state"], "failed")
        self.assertEqual(failed["budget"]["consumed"]["seconds"], 1.5)
        self.assertEqual(failed["events"][-1]["kind"], "failed")
        self.assertNotIn("Raw exception", json.dumps(failed))

    def test_list_is_bounded_and_reads_do_not_change_state(self):
        for _ in range(4):
            run, _ = self.create()
            self.store.cancel(run["id"])
            self.clock += 1
        before = self.snapshot()
        selected = self.store.list(2)
        self.assertEqual(len(selected), 2)
        self.assertNotIn("events", selected[0])
        self.assertEqual(selected[0]["id"], run["id"])
        self.assertEqual(self.store.get(run["id"])["state"], "cancelled")
        self.assertEqual(self.snapshot(), before)
        for limit in (True, 0, 51, "1"):
            with self.subTest(limit=limit), self.assertRaises(ValueError):
                self.store.list(limit)

    def test_bounded_request_permissions_budget_and_result_shapes_reject_excess_data(self):
        for changes in ({"submission_id": "arbitrary"}, {"request": "x" * (MAX_REQUEST + 1)}, {"query": "x" * 513},
                        {"permissions": {**self.permissions, "memory_recall": True}},
                        {"budget": {**self.budget, "total": {**self.budget["total"], "tokens": True}}}):
            with self.subTest(changes=tuple(changes)), self.assertRaises(ValueError):
                self.create(**changes)
        run, _ = self.create(request="x" * MAX_REQUEST)
        _, token = self.store.claim(run["id"])
        for result in ({**self.result, "content": "Never store source text here."},
                       {**self.result, "candidates": self.result["candidates"] * 21},
                       {**self.result, "candidates": [{**self.result["candidates"][0], "url": "https://example.org"}]}):
            with self.subTest(result_fields=tuple(result)), self.assertRaises(ValueError):
                self.store.finish(run["id"], token, result)
        self.assertEqual(self.store.get(run["id"])["state"], "running")
        self.assertTrue(self.store.finish(run["id"], token, self.result))


if __name__ == "__main__":
    unittest.main()

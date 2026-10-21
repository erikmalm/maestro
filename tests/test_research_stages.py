"""Owned stage reservations share one allowance without dispatching any services."""
from contextlib import closing
from copy import deepcopy
from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
import uuid

from backend.provider import DEFAULT
from backend.research import ResearchStore
from backend.research_stages import FINISHING_TOKENS, ResearchStage


class ResearchStageTests(unittest.TestCase):
    def setUp(self):
        self.directory = self.enterContext(tempfile.TemporaryDirectory(prefix="maestro-research-stages-"))
        self.database = Path(self.directory) / "workspace.sqlite3"
        self.store = ResearchStore(self.database)
        self.clock = datetime(2026, 10, 21, 10, tzinfo=timezone.utc).timestamp()
        self.enterContext(patch.object(ResearchStore, "clock", side_effect=lambda: self.clock))
        self.enterContext(patch("backend.research_stages.credentials.read", return_value=(None, "missing")))
        self.workspace = {"chats": [{"id": "chat", "messages": []}],
                          "limits": {"run_usd": 1, "daily_usd": 5, "monthly_usd": 50, "max_tokens": 100000}}
        self.provider = {"config": {**DEFAULT, "protocol": "ollama", "model": "synthetic-local:latest"},
                         "models": ["synthetic-local:latest"], "ledger": [{"private": "UNRELATED_ACCOUNTING"}]}
        self.permissions = {"source_mode": "saved_only", "hosted_allowed": False,
                            "memory_recall": False, "memory_derived": False, "chat_id": "chat"}
        total = {"model_calls": 4, "tokens": 100000, "seconds": 120,
                 "estimated_model_usd": 1, "hosted_searches": 0}
        self.budget = {"total": total, "consumed": dict.fromkeys(total, 0), "reserved": dict.fromkeys(total, 0),
                       "remaining": total.copy(), "deadline": datetime.fromtimestamp(self.clock + 120, timezone.utc).isoformat(),
                       "parent_limits": deepcopy(self.workspace["limits"]),
                       "daily_search": {"limit": 20, "used": 0, "remaining": 20}}
        with self.store._transaction() as db:
            db.execute("CREATE TABLE workspace(id INTEGER PRIMARY KEY,value TEXT NOT NULL)")
            db.execute("CREATE TABLE provider_state(id INTEGER PRIMARY KEY,value TEXT NOT NULL)")
            self.save(db)
        self.run, _ = self.store.create(uuid.uuid4().hex, "Inspect the supplied saved evidence.", "", "chat",
                                        self.permissions, self.budget)
        self.token = self.store.claim(self.run["id"], lookup=False)[1]

    def save(self, db):
        db.execute("INSERT OR REPLACE INTO workspace VALUES(1,?)", (json.dumps(self.workspace),))
        db.execute("INSERT OR REPLACE INTO provider_state VALUES(1,?)", (json.dumps(self.provider),))

    def prepare(self, kind="requirements", **changes):
        values = {"model": "synthetic-local:latest", "schema": {"type": "object", "properties": {"ok": {"type": "boolean"}}},
                  "assignment": "Return the assigned structured result.", "seconds": 5,
                  "permitted_actions": ["return_json", "report_gap"]}
        return self.store.prepare_stage(self.run["id"], self.token, kind, **{**values, **changes})

    def reserve(self, stage, input_bound=100, output_bound=50):
        request_id = uuid.uuid4().hex
        with self.store._transaction() as db:
            packet = self.store.reserve_stage(stage, self.workspace, self.provider, request_id, input_bound, output_bound, db)
        return request_id, packet

    def settle(self, stage, request_id, **changes):
        values = {"input_tokens": 60, "output_tokens": 20, "outcome": "completed", "output": {"ok": True},
                  "workspace": self.workspace, "provider_state": self.provider}
        with self.store._transaction() as db:
            return self.store.settle_stage(stage, request_id, db=db, **{**values, **changes})

    def test_server_identity_packet_and_no_fake_lookup_or_unrelated_accounting(self):
        schema = {"type": "object", "properties": {"ok": {"type": "boolean"}}}
        stage = self.prepare(schema=schema)
        self.assertIsInstance(stage, ResearchStage)
        with self.assertRaises(FrozenInstanceError):
            stage.attempt_id = uuid.uuid4().hex
        schema["private"] = "caller mutation"
        with self.store._transaction() as db:
            packet = self.store.authorize_stage(stage, self.workspace, self.provider, db)
        self.assertEqual(packet["objective"], self.run["request"])
        self.assertEqual(packet["permissions"], self.permissions)
        self.assertNotIn("private", packet["schema"])
        serialized = json.dumps(packet)
        self.assertNotIn("UNRELATED_ACCOUNTING", serialized)
        self.assertNotIn("base_url", serialized)
        self.assertEqual([event["kind"] for event in self.store.get(self.run["id"])["events"]], ["submitted"])

    def test_requirements_writer_reviewer_share_allowance_and_keep_foreground(self):
        remaining = []
        for kind in ("requirements", "writer", "reviewer"):
            stage = self.prepare(kind)
            request_id, packet = self.reserve(stage)
            self.assertEqual(packet["allowance"]["reserved"]["model_calls"], 3 - len(remaining))
            self.assertTrue(self.settle(stage, request_id))
            run = self.store.get(self.run["id"])
            remaining.append(run["budget"]["remaining"]["tokens"])
            self.assertEqual(run["state"], "running")
            with closing(sqlite3.connect(self.database)) as db:
                self.assertTrue(ResearchStore.owns_foreground(db))
        budget = self.store.get(self.run["id"])["budget"]
        self.assertEqual(budget["consumed"]["model_calls"], 3)
        self.assertEqual(budget["consumed"]["tokens"], 240)
        self.assertEqual(budget["reserved"]["tokens"], 0)
        # Each successful stage adds its actual usage; releasing a held finishing
        # slot makes remaining capacity usable without resetting consumed usage.
        self.assertEqual(budget["remaining"]["tokens"], 100000 - 240)
        self.assertEqual([event["kind"] for event in self.store.get(self.run["id"])["events"]],
                         ["submitted", "stage_reserved", "stage_finished", "stage_reserved", "stage_finished", "stage_reserved", "stage_finished"])

    def test_first_reservation_requires_finishing_capacity_and_rolls_back(self):
        stage = self.prepare()
        with self.store._transaction() as db:
            run = self.store._public(db, self.store._row(db, self.run["id"]))
            run["budget"]["total"]["tokens"] = 2 * FINISHING_TOKENS
            self.store._save(db, run, "running", token=self.token)
        before = self.database.read_bytes()
        with self.assertRaisesRegex(ValueError, "finishing capacity"):
            self.reserve(stage)
        self.assertEqual(self.database.read_bytes(), before)
        self.assertEqual(self.store.get(self.run["id"])["budget"]["reserved"]["model_calls"], 0)

    def test_foreign_claim_and_attempt_cannot_authorize_or_replay_dispatch(self):
        stage = self.prepare()
        for forged in (replace(stage, claim_token=uuid.uuid4().hex), replace(stage, attempt_id=uuid.uuid4().hex),
                       replace(stage, run_id=uuid.uuid4().hex)):
            with self.subTest(identity=forged), self.store._transaction() as db, self.assertRaises(ValueError):
                self.store.authorize_stage(forged, self.workspace, self.provider, db)
        request_id, _ = self.reserve(stage)
        self.assertTrue(self.settle(stage, request_id))
        with self.assertRaises(ValueError):
            self.reserve(stage)

    def test_uninstalled_remote_or_unavailable_actions_never_prepare(self):
        for change in ({"model": "missing-model"}, {"permitted_actions": ["hosted_search"]},
                       {"schema": {"type": "string"}}, {"seconds": float("inf")}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.prepare(**change)
        with self.store._transaction() as db:
            self.provider["config"]["protocol"] = "responses"
            self.save(db)
        with self.assertRaises(ValueError):
            self.prepare()

    def test_config_and_limits_changes_between_stages_block_a_fresh_attempt(self):
        for key in ("config", "limits"):
            with self.subTest(changed=key):
                stage = self.prepare()
                request_id, _ = self.reserve(stage)
                self.assertTrue(self.settle(stage, request_id))
                before = deepcopy(self.provider if key == "config" else self.workspace)
                if key == "config":
                    self.provider["config"]["ollama_threads"] += 1
                else:
                    self.workspace["limits"]["max_tokens"] -= 1
                with self.store._transaction() as db:
                    self.save(db)
                with self.assertRaisesRegex(ValueError, "settings|limits"):
                    self.prepare("writer")
                if key == "config":
                    self.provider = before
                else:
                    self.workspace = before
                with self.store._transaction() as db:
                    self.save(db)

    def test_limits_changed_after_submission_cannot_rebase_the_first_stage_allowance(self):
        captured = deepcopy(self.store.get(self.run["id"])["budget"])
        self.workspace["limits"]["max_tokens"] //= 2
        self.workspace["limits"]["run_usd"] /= 2
        with self.store._transaction() as db:
            self.save(db)
        with self.assertRaisesRegex(ValueError, "parent limits changed"):
            self.prepare()
        self.assertEqual(self.store.get(self.run["id"])["budget"], captured)
        with closing(sqlite3.connect(self.database)) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM research_stages").fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM research_stage_scope").fetchone()[0], 0)

    def test_observed_memory_privacy_cannot_disappear_before_dispatch_or_commit(self):
        self.workspace["chats"][0]["messages"] = [{"memory_ids": ["private-memory"]}]
        with self.store._transaction() as db:
            self.save(db)
        stage = self.prepare()
        self.workspace["chats"][0]["messages"] = []
        with self.store._transaction() as db:
            self.save(db)
        request_id, packet = self.reserve(stage)
        self.assertTrue(packet["permissions"]["memory_derived"])
        self.assertFalse(packet["permissions"]["memory_recall"])
        self.assertFalse(packet["permissions"]["hosted_allowed"])
        self.assertTrue(self.settle(stage, request_id))
        self.assertTrue(self.store.get(self.run["id"])["permissions"]["memory_derived"])

    def test_new_memory_observed_at_preflight_is_sticky_after_history_changes(self):
        stage = self.prepare()
        self.workspace["chats"][0]["messages"] = [{"memory_ids": ["private-memory"]}]
        with self.store._transaction() as db:
            self.save(db)
            self.assertTrue(self.store.authorize_stage(stage, self.workspace, self.provider, db)["permissions"]["memory_derived"])
        self.workspace["chats"][0]["messages"] = []
        with self.store._transaction() as db:
            self.save(db)
        request_id, packet = self.reserve(stage)
        self.assertTrue(packet["permissions"]["memory_derived"])
        self.assertTrue(self.settle(stage, request_id))

    def test_replacing_an_undispatched_preparation_cannot_reset_run_configuration(self):
        self.prepare()
        self.provider["config"]["ollama_threads"] += 1
        with self.store._transaction() as db:
            self.save(db)
        with self.assertRaisesRegex(ValueError, "settings"):
            self.prepare("writer")

    def test_undispatched_budget_rejection_does_not_trap_finishing_capacity(self):
        for _ in range(2):
            stage = self.prepare()
            request_id, _ = self.reserve(stage)
            self.assertTrue(self.settle(stage, request_id))
        rejected = self.prepare()
        with self.assertRaises(ValueError):
            self.reserve(rejected)
        for _ in range(10):
            rejected = self.prepare()
            with self.assertRaises(ValueError):
                self.reserve(rejected)
        writer = self.prepare("writer")
        with self.assertRaises(ValueError):
            self.reserve(rejected)
        request_id, _ = self.reserve(writer)
        self.assertTrue(self.settle(writer, request_id))
        reviewer = self.prepare("reviewer")
        request_id, _ = self.reserve(reviewer)
        self.assertTrue(self.settle(reviewer, request_id))
        self.assertEqual(self.store.get(self.run["id"])["budget"]["consumed"]["model_calls"], 4)
        with closing(sqlite3.connect(self.database)) as db:
            self.assertLessEqual(db.execute("SELECT COUNT(*) FROM research_stages").fetchone()[0], 8)

    def test_uncertain_usage_survives_cancellation_and_resolves_conservatively_once(self):
        stage = self.prepare()
        request_id, _ = self.reserve(stage)
        self.assertFalse(self.settle(stage, request_id, outcome="uncertain", input_tokens=0, output_tokens=0))
        cancelled = self.store.cancel(self.run["id"])
        self.assertEqual(cancelled["budget"]["reserved"]["model_calls"], 1)
        self.assertEqual(cancelled["budget"]["reserved"]["tokens"], 150)
        with self.store._transaction() as db:
            self.assertTrue(self.store.resolve_unknown(request_id, db))
            self.assertFalse(self.store.resolve_unknown(request_id, db))
        run = self.store.get(self.run["id"])
        self.assertEqual(run["state"], "cancelled")
        self.assertEqual(run["budget"]["consumed"]["tokens"], 150)
        self.assertEqual(run["budget"]["consumed"]["model_calls"], 1)
        self.assertEqual(run["budget"]["reserved"]["tokens"], 0)

    def test_delete_purges_stage_prose_but_late_usage_settles_without_output(self):
        stage = self.prepare(assignment="PRIVATE_STAGE_ASSIGNMENT_ONLY")
        request_id, _ = self.reserve(stage)
        self.store.delete(self.run["id"])
        self.assertFalse(self.settle(stage, request_id, output={"private": "LATE_UNSAVED_OUTPUT"}))
        with closing(sqlite3.connect(self.database)) as db:
            spec, output, consumed, reserved = db.execute("SELECT spec,output,consumed,reserved FROM research_stages WHERE id=?", (stage.stage_id,)).fetchone()
        self.assertEqual(spec, "{}")
        self.assertIsNone(output)
        self.assertEqual(json.loads(consumed)["tokens"], 80)
        self.assertEqual(json.loads(reserved)["tokens"], 0)
        self.assertNotIn(b"PRIVATE_STAGE_ASSIGNMENT_ONLY", self.database.read_bytes())
        self.assertNotIn(b"LATE_UNSAVED_OUTPUT", self.database.read_bytes())

    def test_reported_overrun_records_actual_usage_and_blocks_more_work(self):
        stage = self.prepare()
        request_id, _ = self.reserve(stage)
        self.assertTrue(self.settle(stage, request_id, input_tokens=110000, output_tokens=500))
        budget = self.store.get(self.run["id"])["budget"]
        self.assertEqual(budget["consumed"]["tokens"], 110500)
        self.assertEqual(budget["remaining"]["tokens"], 0)
        with self.assertRaises(ValueError):
            self.reserve(self.prepare("writer"))

    def test_deadline_fences_outputs_but_preserves_actual_usage(self):
        stage = self.prepare()
        request_id, _ = self.reserve(stage)
        self.clock += 6
        self.assertFalse(self.settle(stage, request_id))
        run = self.store.get(self.run["id"])
        self.assertEqual(run["budget"]["consumed"]["tokens"], 80)
        self.assertEqual(run["budget"]["consumed"]["model_calls"], 1)


if __name__ == "__main__":
    unittest.main()

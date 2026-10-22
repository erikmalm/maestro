"""Cross-service ownership/accounting checks with disposable local responses."""
from contextlib import closing
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
import uuid

from backend.provider import DEFAULT, LocalUncertain, Provider
from backend.research import ResearchStore


class ResearchStageIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory(prefix="maestro-stage-integration-")))
        self.database = self.root / "workspace.sqlite3"
        self.workspace = {"limits": {"run_usd": 1, "daily_usd": 5, "monthly_usd": 50, "max_tokens": 100000},
                          "chats": []}
        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute("CREATE TABLE workspace(id INTEGER PRIMARY KEY,value TEXT NOT NULL)")
            db.execute("INSERT INTO workspace VALUES(1,?)", (json.dumps(self.workspace),))
        self.provider = Provider(self.database, timezone.utc)
        self.config = {**DEFAULT, "protocol": "ollama", "base_url": "http://127.0.0.1:11434",
                       "model": "synthetic-local:latest", "max_output_tokens": 256}
        self.provider.configure(self.config, "", False)
        with self.provider.transaction() as state:
            state["models"] = [self.config["model"]]
            state["tested_at"] = self.provider.stamp()
        self.store = ResearchStore(self.database, timezone.utc)
        self.now = datetime.now(timezone.utc).timestamp()
        self.enterContext(patch.object(ResearchStore, "clock", return_value=self.now))
        total = {"model_calls": 4, "tokens": 100000, "seconds": 120,
                 "estimated_model_usd": 1, "hosted_searches": 0}
        budget = {"total": total, "consumed": dict.fromkeys(total, 0), "reserved": dict.fromkeys(total, 0),
                  "remaining": total.copy(), "parent_limits": self.workspace["limits"],
                  "deadline": datetime.fromtimestamp(self.now + 120, timezone.utc).isoformat(),
                  "daily_search": {"limit": 20, "used": 0, "remaining": 20}}
        permissions = {"source_mode": "saved_only", "hosted_allowed": False, "memory_recall": False,
                       "memory_derived": False, "chat_id": None}
        self.run = self.store.create(uuid.uuid4().hex, "Synthetic shared-budget task", "", None,
                                     permissions, budget)[0]
        _, self.claim = self.store.claim(self.run["id"], lookup=False)
        self.schema = {"type": "object", "properties": {"ok": {"type": "boolean"}},
                       "required": ["ok"], "additionalProperties": False}

    def stage(self, kind="requirements"):
        return self.store.prepare_stage(self.run["id"], self.claim, kind, model=self.config["model"],
                                        schema=self.schema, assignment="Return a synthetic structured result.")

    def generate(self, stage):
        return self.provider.generate_research_stage(stage, "Return JSON using the assigned schema.",
                                                     [{"role": "user", "content": "Frozen synthetic input."}])

    def response(self, config, path, payload, deadline):
        if path == "/api/show":
            return {"details": {"format": "gguf"}, "capabilities": ["completion"]}
        return {"done": True, "message": {"content": '{"ok":true}'},
                "prompt_eval_count": 37, "eval_count": 11}

    def test_provider_and_run_reservation_roll_back_as_one_transaction(self):
        stage = self.stage()
        before = self.store.get(self.run["id"])["budget"]
        original = ResearchStore.reserve_stage

        def fail_after_reservation(store, *args, **kwargs):
            original(store, *args, **kwargs)
            raise ValueError("Synthetic failure before admission commit.")

        with (patch.object(ResearchStore, "reserve_stage", new=fail_after_reservation),
              patch("backend.provider.background_network") as network,
              self.assertRaisesRegex(ValueError, "Synthetic failure")):
            self.generate(stage)
        network.assert_not_called()
        self.assertEqual(self.provider.read_state()["ledger"], [])
        self.assertEqual(self.store.get(self.run["id"])["budget"], before)

    def test_reported_overrun_is_recorded_and_blocks_further_dispatch(self):
        stage = self.stage()

        def overrun(*args):
            result = self.response(*args)
            if args[1] == "/api/chat":
                result["prompt_eval_count"] = 100001
            return result

        with patch("backend.provider.background_network", side_effect=overrun):
            try:
                self.generate(stage)
            except ValueError:
                pass
        ledger = self.provider.read_state()["ledger"]
        self.assertEqual(ledger[-1]["status"], "settled")
        self.assertEqual(ledger[-1]["input_tokens"], 100001)
        budget = self.store.get(self.run["id"])["budget"]
        self.assertEqual(budget["consumed"]["tokens"], 100012)
        self.assertEqual(budget["remaining"]["tokens"], 0)
        with patch("backend.provider.background_network") as network, self.assertRaises(ValueError):
            self.generate(self.stage("writer"))
        network.assert_not_called()

    def test_output_validation_crossing_deadline_cannot_save_a_late_result(self):
        from backend import research_stages
        self.enterContext(patch.object(ResearchStore, "clock", side_effect=lambda: self.now))
        stage = self.stage()
        original = research_stages.safe_stage_content

        def slow_validation(value, *args, **kwargs):
            result = original(value, *args, **kwargs)
            if value == {"ok": True} or isinstance(value, list) and {"ok": True} in value:
                self.now += 6  # The stage has five seconds, including validation.
            return result

        with (patch.object(research_stages, "safe_stage_content", side_effect=slow_validation),
              patch("backend.provider.background_network", side_effect=self.response),
              self.assertRaises(ValueError)):
            self.generate(stage)
        entry = self.provider.read_state()["ledger"][-1]
        self.assertEqual(entry["status"], "settled")
        self.assertEqual((entry["input_tokens"], entry["output_tokens"]), (37, 11))
        with closing(sqlite3.connect(self.database)) as db:
            recorded = db.execute("SELECT state,output FROM research_stages WHERE id=?", (stage.stage_id,)).fetchone()
        self.assertEqual(recorded, ("failed", None))
        self.assertEqual(self.store.get(stage.run_id)["budget"]["consumed"]["tokens"], 48)

    def test_confirmed_restart_cannot_refund_unknown_run_usage(self):
        stage = self.stage()

        def uncertain(*args):
            if args[1] == "/api/chat":
                raise LocalUncertain("Synthetic completion unknown.", False)
            return self.response(*args)

        with patch("backend.provider.background_network", side_effect=uncertain), self.assertRaises(ValueError):
            self.generate(stage)
        entry = self.provider.read_state()["ledger"][-1]
        self.assertEqual(entry["status"], "reserved")
        self.store.cancel(self.run["id"])
        self.provider.recover()
        self.store.recover()
        before = self.store.get(self.run["id"])["budget"]
        self.assertGreater(before["reserved"]["tokens"], 0)
        self.provider.configure({**self.config, "base_url": "http://127.0.0.1:11435"}, "", False)
        with patch("backend.provider.background_network", return_value={"models": []}) as network:
            self.provider.recover_local(entry["id"], True, self.config["base_url"])
        self.assertEqual(network.call_args.args[0]["base_url"], self.config["base_url"])
        after = self.store.get(self.run["id"])
        self.assertEqual(after["state"], "cancelled")
        self.assertGreaterEqual(after["budget"]["consumed"]["model_calls"], 1)
        self.assertGreater(after["budget"]["consumed"]["tokens"], 0)
        self.assertEqual(self.provider.read_state()["ledger"][-1]["status"], "failed")
        with patch("backend.provider.background_network") as network, self.assertRaises(ValueError):
            self.generate(stage)
        network.assert_not_called()

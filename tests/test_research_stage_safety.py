"""Adversarial owned-stage boundaries, using disposable data and synthetic inference."""
from contextlib import closing
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
import uuid

import httpx

from backend import credentials
from backend.memory import MemoryStore
from backend.provider import DEFAULT, Provider
from backend.research import ResearchStore
from backend.web_search import ENDPOINT, WebSearch


class ResearchStageSafetyTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory(prefix="maestro-stage-safety-")))
        self.database = self.root / "workspace.sqlite3"
        self.store = ResearchStore(self.database, timezone.utc)
        self.provider = Provider(self.database, timezone.utc)
        self.chat_id = uuid.uuid4().hex
        self.model = "synthetic-safety:latest"
        self.config = {**DEFAULT, "protocol": "ollama", "base_url": "http://127.0.0.1:11434",
                       "model": self.model, "max_output_tokens": 256, "ollama_context_tokens": 65536,
                       "input_usd_per_million": 0, "output_usd_per_million": 0, "pricing_verified": True}
        self.workspace = {"tasks": [], "chats": [{"id": self.chat_id, "messages": []}],
                          "limits": {"run_usd": 1, "daily_usd": 5, "monthly_usd": 50, "max_tokens": 500000},
                          "working_core_migrated": True}
        self.save_workspace()
        with self.provider.transaction() as state:
            state.update(config=deepcopy(self.config), models=[self.model], tested_at=None)
        self.keys, self.requests = {}, []
        self.show_hook, self.chat_hook = None, None
        self.response = {"done": True, "message": {"role": "assistant", "content": '{"result":"ok"}'},
                         "prompt_eval_count": 41, "eval_count": 7}
        self.enterContext(patch.object(credentials, "read", side_effect=lambda endpoint:
                                      (self.keys.get(endpoint), "synthetic")))
        self.enterContext(patch.object(MemoryStore, "recall", side_effect=AssertionError("Private memory recall")))
        self.enterContext(patch.object(WebSearch, "search", side_effect=AssertionError("Hosted search")))
        for transport in (httpx.HTTPTransport, httpx.AsyncHTTPTransport):
            self.enterContext(patch.object(transport, "handle_request" if transport is httpx.HTTPTransport
                                          else "handle_async_request", side_effect=AssertionError("External network")))
        self.enterContext(patch("backend.provider.background_network", side_effect=self.inference))

    def save_workspace(self):
        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute("CREATE TABLE IF NOT EXISTS workspace(id INTEGER PRIMARY KEY,value TEXT NOT NULL)")
            db.execute("INSERT OR REPLACE INTO workspace VALUES(1,?)", (json.dumps(self.workspace),))

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
        return deepcopy(self.response)

    def new_run(self, request="Synthetic stage safety objective."):
        amounts = {"model_calls": 4, "tokens": 500000, "seconds": 120,
                   "estimated_model_usd": 1, "hosted_searches": 0}
        budget = {"total": amounts, "consumed": dict.fromkeys(amounts, 0), "reserved": dict.fromkeys(amounts, 0),
                  "remaining": amounts.copy(), "deadline": (datetime.now(timezone.utc) + timedelta(seconds=120)).isoformat(),
                  "parent_limits": self.workspace["limits"].copy(),
                  "daily_search": {"limit": 20, "used": 0, "remaining": 20}}
        permissions = {"source_mode": "saved_only", "hosted_allowed": False, "memory_recall": False,
                       "memory_derived": False, "chat_id": self.chat_id}
        run, _ = self.store.create(uuid.uuid4().hex, request, "", self.chat_id, permissions, budget)
        return self.store.claim(run["id"], lookup=False)

    def stage(self, run=None, token=None, *, kind="requirements", **changes):
        if run is None:
            run, token = self.new_run()
        fields = {"model": self.model, "schema": {"type": "object", "properties": {"result": {"type": "string"}},
                                                  "required": ["result"], "additionalProperties": False},
                  "assignment": "Return the synthetic test result.", "seconds": 5}
        fields.update(changes)
        return self.store.prepare_stage(run["id"], token, kind, **fields)

    def generate(self, stage):
        return self.provider.generate_research_stage(stage, "Use only the supplied saved evidence.",
                                                     [{"role": "user", "content": "Synthetic stage input."}],
                                                     max_output_tokens=128)

    def ledger(self):
        return self.provider.read_state()["ledger"]

    def test_forged_and_replayed_identity_cannot_dispatch_or_reset_allowance(self):
        stage = self.stage()
        before = self.store.get(stage.run_id)["budget"]
        for field in ("run_id", "claim_token", "stage_id", "attempt_id"):
            with self.subTest(field=field), self.assertRaises((ValueError, KeyError)):
                self.generate(replace(stage, **{field: uuid.uuid4().hex}))
        self.assertEqual(self.requests, [])
        self.assertEqual(self.ledger(), [])
        self.assertEqual(self.store.get(stage.run_id)["budget"], before)
        self.generate(stage)
        requests, settled = len(self.requests), self.store.get(stage.run_id)["budget"]
        with self.assertRaises((ValueError, KeyError)):
            self.generate(stage)
        self.assertEqual(len(self.requests), requests)
        self.assertEqual(self.store.get(stage.run_id)["budget"], settled)

    def test_cancellation_during_model_check_prevents_inference_and_late_acceptance(self):
        stage = self.stage()
        self.show_hook = lambda: self.store.cancel(stage.run_id)
        with self.assertRaises(ValueError):
            self.generate(stage)
        self.assertEqual([request[1] for request in self.requests], ["/api/show"])
        self.assertEqual(self.store.get(stage.run_id)["state"], "cancelled")
        self.assertFalse(any(entry["status"] in ("reserved", "uncertain") for entry in self.ledger()))

    def test_connection_change_during_model_check_cannot_switch_or_dispatch_the_prepared_stage(self):
        stage = self.stage()

        def change_connection():
            with self.provider.transaction() as state:
                state["config"] = {**state["config"], "protocol": "responses", "base_url": "https://provider.example.org/v1"}

        self.show_hook = change_connection
        with self.assertRaises(ValueError):
            self.generate(stage)
        self.assertEqual([request[1] for request in self.requests], ["/api/show"])
        self.assertFalse(any(entry["status"] in ("reserved", "uncertain") for entry in self.ledger()))

    def test_current_memory_privacy_is_preserved_without_recall_or_unrelated_ledger_in_the_prompt(self):
        stage = self.stage()
        marker = "UNRELATED_PRIVATE_ACCOUNTING_MARKER_SAFETY_77"
        with self.provider.transaction() as state:
            state["ledger"].append({"id": uuid.uuid4().hex, "at": self.provider.stamp(), "protocol": "ollama",
                                    "status": "settled", "cost": 0, "input_tokens": 1, "output_tokens": 1,
                                    "model": self.model, "unrelated_note": marker})
        self.workspace["chats"][0]["messages"].append({"role": "assistant", "text": "Private derived answer.",
                                                       "memory_ids": ["private-memory"]})
        self.save_workspace()
        self.generate(stage)
        payload = next(request[2] for request in self.requests if request[1] == "/api/chat")
        rendered = json.dumps(payload)
        self.assertNotIn(marker, rendered)
        self.assertNotIn("Private derived answer", rendered)
        self.assertNotIn("tools", payload)
        self.assertTrue(self.store.get(stage.run_id)["permissions"]["memory_derived"])
        self.assertRegex(rendered.replace('\\"', '"'), r'"memory_derived"\s*:\s*true')

    def test_key_rotation_during_model_check_withholds_raw_objective_assignment_and_schema(self):
        for field in ("objective", "assignment", "schema"):
            for key in ('synthetic"stagecredential', "synthetic\\stagecredential"):
                with self.subTest(field=field, key=key):
                    self.keys.clear()
                    self.requests.clear()
                    run, token = self.new_run(request="Synthetic objective " + key if field == "objective"
                                              else "Synthetic stage safety objective.")
                    changes = {"assignment": "Synthetic assignment " + key} if field == "assignment" else {}
                    if field == "schema":
                        changes["schema"] = {"type": "object", "properties": {key: {"type": "boolean"}},
                                             "required": [key], "additionalProperties": False}
                    stage = self.stage(run, token, **changes)
                    self.show_hook = lambda: self.keys.update({ENDPOINT: key})
                    with self.assertRaises(ValueError):
                        self.generate(stage)
                    self.assertEqual([request[1] for request in self.requests], ["/api/show"])
                    self.store.cancel(run["id"])

    def test_initial_credential_remains_withheld_when_it_rotates_during_inference(self):
        initial = "syntheticInitialStageCredential77"
        self.keys[ENDPOINT] = initial
        stage = self.stage()
        self.response["message"]["content"] = json.dumps({"result": initial})
        self.chat_hook = lambda: self.keys.update({ENDPOINT: "syntheticRotatedStageCredential88"})
        with self.assertRaises(ValueError):
            self.generate(stage)
        entry = self.ledger()[-1]
        self.assertEqual(entry["status"], "settled")
        self.assertEqual((entry["input_tokens"], entry["output_tokens"]), (41, 7))
        with closing(sqlite3.connect(self.database)) as db:
            self.assertIsNone(db.execute("SELECT output FROM research_stages WHERE id=?",
                                         (stage.stage_id,)).fetchone()[0])
        self.assertNotIn(initial.encode(), self.database.read_bytes())

    def test_actual_usage_is_settled_when_output_is_empty_or_arrives_after_cancellation(self):
        for outcome in ("empty", "malformed", "cancelled"):
            with self.subTest(outcome=outcome):
                self.requests.clear()
                stage = self.stage()
                self.response["message"]["content"] = {"empty": "", "malformed": "not JSON",
                                                         "cancelled": '{"result":"late"}'}[outcome]
                self.chat_hook = (lambda: self.store.cancel(stage.run_id)) if outcome == "cancelled" else None
                with self.assertRaises(ValueError):
                    self.generate(stage)
                entry = self.ledger()[-1]
                self.assertEqual(entry["status"], "settled")
                self.assertEqual((entry["input_tokens"], entry["output_tokens"]), (41, 7))
                self.store.cancel(stage.run_id)
                run = self.store.get(stage.run_id)
                self.assertEqual(run["budget"]["consumed"]["tokens"], 48)
                self.assertEqual(run["budget"]["consumed"]["model_calls"], 1)
                self.assertEqual(run["budget"]["reserved"]["tokens"], 0)

    def test_deletion_during_inference_settles_usage_without_recreating_private_run_content(self):
        for delete_chat in (False, True):
            with self.subTest(delete_chat=delete_chat):
                marker = "PRIVATE_LATE_DELETION_MARKER_" + uuid.uuid4().hex
                run, token = self.new_run(request=marker)
                stage = self.stage(run, token)

                def delete_origin():
                    if not delete_chat:
                        self.store.delete(run["id"])
                        return
                    self.workspace["chats"] = []
                    with closing(sqlite3.connect(self.database)) as db, db:
                        db.execute("PRAGMA secure_delete=ON")
                        db.execute("BEGIN IMMEDIATE")
                        db.execute("UPDATE workspace SET value=? WHERE id=1", (json.dumps(self.workspace),))
                        self.store.delete_chat(self.chat_id, db)

                self.chat_hook = delete_origin
                with self.assertRaises(ValueError):
                    self.generate(stage)
                with self.assertRaises(KeyError):
                    self.store.get(run["id"])
                entry = self.ledger()[-1]
                self.assertEqual(entry["status"], "settled")
                self.assertEqual((entry["input_tokens"], entry["output_tokens"]), (41, 7))
                self.assertNotIn(marker.encode(), self.database.read_bytes())
                self.workspace["chats"] = [{"id": self.chat_id, "messages": []}]
                self.save_workspace()

    def test_invalid_confirmed_token_usage_retains_uncertainty_across_delete_and_restart(self):
        stage = self.stage()
        self.response.pop("prompt_eval_count")
        with self.assertRaises(ValueError):
            self.generate(stage)
        entry = self.ledger()[-1]
        self.assertEqual(entry["status"], "reserved")
        self.provider.recover()
        self.store.recover()
        held = self.store.get(stage.run_id)["budget"]["reserved"].copy()
        self.assertEqual(held["model_calls"], 1)
        self.assertGreater(held["tokens"], 0)
        self.store.delete(stage.run_id)
        with self.assertRaises(ValueError):
            self.new_run()
        with self.assertRaises(ValueError):
            self.provider.generate_context("Unrelated work", [{"role": "user", "content": "Synthetic"}])
        self.assertEqual(len([request for request in self.requests if request[1] == "/api/chat"]), 1)
        self.assertEqual(self.ledger()[-1]["status"], "reserved")
        self.provider.recover_local(entry["id"], True, self.config["base_url"])
        self.assertEqual(self.ledger()[-1]["status"], "failed")
        self.assertIsNotNone(self.new_run())

    def test_unreliable_completed_usage_never_refunds_tokens_or_the_call_on_explicit_recovery(self):
        valid = deepcopy(self.response)
        for field, value in (("prompt_eval_count", None), ("eval_count", -1),
                             ("prompt_eval_count", True), ("eval_count", "7")):
            with self.subTest(field=field, value=value):
                self.response = {**deepcopy(valid), field: value}
                stage = self.stage()
                with self.assertRaises(ValueError):
                    self.generate(stage)
                self.store.cancel(stage.run_id)
                held = self.store.get(stage.run_id)["budget"]["reserved"].copy()
                self.assertEqual(held["model_calls"], 1)
                self.assertGreater(held["tokens"], 0)
                entry = self.ledger()[-1]
                self.assertEqual(entry["status"], "reserved")
                self.provider.recover_local(entry["id"], True, self.config["base_url"])
                budget = self.store.get(stage.run_id)["budget"]
                self.assertEqual(budget["consumed"]["tokens"], held["tokens"])
                self.assertEqual(budget["consumed"]["model_calls"], 1)
                self.assertEqual(budget["reserved"]["tokens"], 0)
                self.store.cancel(stage.run_id)

    def test_optional_work_cannot_spend_the_calls_held_for_writer_and_reviewer(self):
        run, token = self.new_run()
        for kind in ("requirements", "evidence_review"):
            self.generate(self.stage(run, token, kind=kind))
        before = self.store.get(run["id"])["budget"]
        requests = len(self.requests)
        for _ in range(10):
            with self.assertRaises(ValueError):
                self.generate(self.stage(run, token))
        self.assertEqual(len(self.requests), requests)
        self.assertEqual(self.store.get(run["id"])["budget"]["consumed"], before["consumed"])
        for kind in ("writer", "reviewer"):
            self.generate(self.stage(run, token, kind=kind))
        budget = self.store.get(run["id"])["budget"]
        self.assertEqual(budget["consumed"]["model_calls"], 4)
        self.assertEqual(budget["reserved"]["model_calls"], 0)
        self.assertEqual(budget["remaining"]["model_calls"], 0)
        self.assertEqual(budget["consumed"]["tokens"], 4 * 48)
        with closing(sqlite3.connect(self.database)) as db:
            self.assertLessEqual(db.execute("SELECT COUNT(*) FROM research_stages WHERE run_id=?",
                                            (run["id"],)).fetchone()[0], 8)

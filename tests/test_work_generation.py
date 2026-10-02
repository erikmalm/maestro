"""Persisted task choices share local generation without changing worker policy."""
from contextlib import closing
from datetime import timezone
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from pydantic import ValidationError

from backend.memory import MemoryStore
from backend.provider import DEFAULT, Provider
from backend.work_config import WorkConfig, config_value, select_model


class WorkConfigTests(unittest.TestCase):
    def test_defaults_are_read_only_and_recommendations_require_installed_models(self):
        workspace = {"unrelated": "preserve"}
        config = config_value(workspace)
        self.assertEqual(workspace, {"unrelated": "preserve"})
        self.assertEqual(config["memory_recall_count"], 5)
        self.assertFalse(config["enabled"])
        for kind, expected in (("reflection", "gpt-oss:20b"), ("memory", "qwen2.5:7b"), ("coding", "devstral-small-2:24b")):
            with self.subTest(kind=kind):
                self.assertEqual(select_model(config, kind, [expected], "chat:7b"), expected)
                self.assertEqual(select_model(config, kind, [], "chat:7b"), "chat:7b")
                config[kind + "_model"] = "explicit:7b"
                self.assertEqual(select_model(config, kind, [expected], "chat:7b"), "explicit:7b")

    def test_types_shape_and_limits_are_strict(self):
        for field, value in (("enabled", "true"), ("debounce_seconds", True), ("idle_seconds", "30"),
                             ("max_output_tokens", 1025), ("max_jobs_per_day", -1), ("max_tokens_per_day", 10000001),
                             ("timeout_seconds", 0), ("memory_recall_count", 6), ("memory_recall_characters", 1001),
                             ("reflection_model", "remote key with spaces"), ("memory_model", "x" * 201), ("unexpected", 0)):
            with self.subTest(field=field, value=value), self.assertRaises(ValidationError):
                WorkConfig.model_validate({field: value})


class WorkGenerationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="maestro-work-test-")
        self.database = Path(self.directory.name) / "workspace.sqlite3"
        self.workspace = {"limits": {"run_usd": 1, "daily_usd": 5, "monthly_usd": 50, "max_tokens": 100000},
                          "chats": [{"id": "synthetic-chat", "title_source": "manual", "messages": []}],
                          "unrelated": {"private_setting": "preserve"}}
        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute("CREATE TABLE workspace (id INTEGER PRIMARY KEY, value TEXT NOT NULL)")
            db.execute("INSERT INTO workspace VALUES (1,?)", (json.dumps(self.workspace),))
        self.provider = Provider(self.database, timezone.utc)
        self.provider.configure({**DEFAULT, "protocol": "ollama", "base_url": "http://127.0.0.1:11434",
                                 "model": "chat:7b", "ollama_context_tokens": 8192}, "", False)
        with self.provider.transaction() as state:
            state["models"] = ["qwen2.5:7b", "gpt-oss:20b", "devstral-small-2:24b"]
        self.calls = []

    def tearDown(self):
        self.directory.cleanup()

    def read_workspace(self):
        with closing(sqlite3.connect(self.database)) as db:
            return json.loads(db.execute("SELECT value FROM workspace WHERE id=1").fetchone()[0])

    def save_policy(self, **values):
        workspace = self.read_workspace()
        workspace["work_config"] = WorkConfig(**values).model_dump()
        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute("UPDATE workspace SET value=? WHERE id=1", (json.dumps(workspace),))

    def network(self, config, key, method, path, payload=None):
        self.calls.append((config.copy(), path, payload))
        if path == "/api/show":
            return {"details": {"format": "gguf"}, "capabilities": ["completion", "tools"]}
        return {"done": True, "message": {"content": "Synthetic result"}, "prompt_eval_count": 20, "eval_count": 10}

    def generate(self, **options):
        return self.provider.generate_context("Extract only source-backed facts.", [{"role": "user", "content": "Synthetic source."}], **options)

    def test_installed_recommendations_use_one_ledger_and_no_tools_or_workspace_writes(self):
        before = self.read_workspace()
        with patch("backend.provider.network", side_effect=self.network):
            for kind, model, cap in (("reflection", "gpt-oss:20b", 512), ("memory", "qwen2.5:7b", 512), ("coding", "devstral-small-2:24b", 1024)):
                with self.subTest(kind=kind):
                    result = self.generate(kind=kind)
                    payload = self.calls[-1][2]
                    self.assertEqual(result["config"]["model"], model)
                    self.assertEqual(payload["options"]["num_predict"], cap)
                    self.assertEqual(payload["keep_alive"], 0)
                    self.assertNotIn("tools", payload)
        self.assertEqual(self.read_workspace(), before)
        entries = self.provider.read_state()["ledger"]
        self.assertEqual([entry["kind"] for entry in entries], ["reflection", "memory", "coding"])
        self.assertTrue(all(entry["status"] == "settled" and entry["thread_id"] is None for entry in entries))
        self.assertEqual(self.provider.usage()["calls"], 3)

    def test_saved_task_model_and_caps_survive_provider_restart(self):
        self.save_policy(reflection_model="selected:7b", memory_model="extractor:7b", coding_model="coder:7b", max_output_tokens=64)
        self.provider = Provider(self.database, timezone.utc)
        with patch("backend.provider.network", side_effect=self.network):
            reflection = self.generate(max_output_tokens=100)
            self.assertEqual(reflection["config"]["model"], "selected:7b")
            self.assertEqual(self.calls[-1][2]["options"]["num_predict"], 64)
            memory = self.generate(kind="memory", max_output_tokens=32)
            self.assertEqual(memory["config"]["model"], "extractor:7b")
            self.assertEqual(self.calls[-1][2]["options"]["num_predict"], 32)
            coding = self.generate(kind="coding", max_output_tokens=800)
            self.assertEqual(coding["config"]["model"], "coder:7b")
            self.assertEqual(self.calls[-1][2]["options"]["num_predict"], 800)
            self.generate(model="per-call:7b")
            self.assertEqual(self.calls[-1][2]["model"], "per-call:7b")
        self.assertEqual(self.provider.read_state()["config"]["model"], "chat:7b")
        self.assertEqual(self.read_workspace()["work_config"]["reflection_model"], "selected:7b")

    def test_provider_output_cap_also_limits_each_task(self):
        self.save_policy(max_output_tokens=128)
        with self.provider.transaction() as state:
            state["config"]["max_output_tokens"] = 96
        with patch("backend.provider.network", side_effect=self.network):
            for kind in ("reflection", "memory", "coding"):
                self.generate(kind=kind, max_output_tokens=1024)
                self.assertEqual(self.calls[-1][2]["options"]["num_predict"], 96)

    def test_saved_policy_snapshot_is_kept_when_settings_change_during_dispatch(self):
        self.save_policy(reflection_model="original:7b", max_output_tokens=64)
        def change_policy(config, key, method, path, payload=None):
            if path == "/api/show":
                self.save_policy(reflection_model="replacement:7b", max_output_tokens=128)
            return self.network(config, key, method, path, payload)
        with patch("backend.provider.network", side_effect=change_policy):
            result = self.generate()
        self.assertEqual(result["work_config"]["reflection_model"], "original:7b")
        self.assertEqual(self.calls[-1][2]["model"], "original:7b")
        self.assertEqual(self.calls[-1][2]["options"]["num_predict"], 64)
        self.assertEqual(self.read_workspace()["work_config"]["reflection_model"], "replacement:7b")
        self.assertEqual(self.read_workspace()["unrelated"], self.workspace["unrelated"])

    def test_worker_policy_is_saved_without_starting_or_blocking_explicit_inference(self):
        self.save_policy(enabled=False, max_jobs_per_day=0, max_tokens_per_day=0)
        with patch("backend.provider.network", side_effect=self.network):
            result = self.generate()
        self.assertEqual(result["work_config"]["max_jobs_per_day"], 0)
        self.assertEqual(self.provider.usage()["calls"], 1)

    def test_all_task_kinds_reject_remote_before_dispatch_or_credentials(self):
        with self.provider.transaction() as state:
            state["config"] = DEFAULT.copy()
        with patch("backend.provider.network") as network, patch("backend.provider.credentials.read") as credentials:
            for kind in ("reflection", "memory", "coding"):
                with self.subTest(kind=kind), self.assertRaisesRegex(ValueError, "Local Ollama"):
                    self.generate(kind=kind)
        network.assert_not_called()
        credentials.assert_not_called()
        self.assertEqual(self.provider.read_state()["ledger"], [])

    def test_local_chat_passes_saved_recall_limits(self):
        self.save_policy(memory_recall_count=0, memory_recall_characters=100)
        with patch.object(MemoryStore, "recall", return_value=[]) as recall, patch("backend.provider.network", side_effect=self.network):
            self.provider.generate("Synthetic query", "synthetic-chat")
        recall.assert_called_once_with("Synthetic query", "synthetic-chat", local=True, limit=0, max_characters=100)


if __name__ == "__main__":
    unittest.main()

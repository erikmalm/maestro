"""Private task-model configuration persists without dispatching background work."""
from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from backend import app as backend
from backend.memory import MemoryStore
from backend.work_config import WorkConfig


class WorkConfigAPITests(unittest.TestCase):
    def setUp(self):
        directory = Path(self.enterContext(tempfile.TemporaryDirectory(prefix="maestro-work-config-")))
        self.enterContext(patch.object(backend, "DATABASE", directory / "workspace.sqlite3"))
        self.enterContext(patch.object(backend.credentials, "read", return_value=(None, "missing")))
        self.client = TestClient(backend.app)
        self.addCleanup(self.client.close)
        self.headers = {"X-Maestro-CSRF": self.client.get("/api/session").json()["csrf"]}

    def test_defaults_and_saves_persist_independently_of_provider_without_inference(self):
        initial = self.client.get("/api/work-config").json()
        self.assertEqual(initial["config"], WorkConfig().model_dump())
        self.assertTrue(initial["worker_available"])
        self.assertEqual(initial["candidates"], [])
        self.assertEqual(initial["queued"], 0)
        config = {**initial["config"], "reflection_model": "synthetic-reasoning:20b",
                  "memory_model": "synthetic-small:7b", "coding_model": "synthetic-coding:24b",
                  "idle_seconds": 90, "max_jobs_per_day": 4, "memory_recall_count": 2}
        before = self.client.get("/api/workspace").json()
        with patch("backend.provider.network", side_effect=AssertionError("Saving configuration must not dispatch")):
            saved = self.client.put("/api/work-config", headers=self.headers, json=config)
            self.assertEqual(saved.status_code, 200, saved.text)
            self.assertEqual(saved.json()["config"], config)
            fresh = TestClient(backend.app)
            try:
                fresh.get("/api/session")
                self.assertEqual(fresh.get("/api/work-config").json(), saved.json())
            finally:
                fresh.close()
            provider = before["provider"]["config"]
            response = self.client.put("/api/provider", headers=self.headers,
                                       json={"config": {**provider, "model": "another-remote"}, "persist": False})
            self.assertEqual(response.status_code, 200, response.text)
            after = self.client.get("/api/workspace").json()
        self.assertEqual(after["work"], saved.json())
        self.assertEqual(after["usage"], before["usage"])
        self.assertEqual(after["chats"], before["chats"])

    def test_gets_do_not_change_private_database_after_initialization(self):
        self.client.get("/api/workspace")
        with closing(sqlite3.connect(backend.DATABASE)) as monitor:
            version = monitor.execute("PRAGMA data_version").fetchone()[0]
            before = monitor.execute("SELECT value FROM workspace").fetchone()[0]
            for _ in range(3):
                self.assertEqual(self.client.get("/api/work-config").status_code, 200)
                self.client.get("/api/workspace")
            self.assertEqual(monitor.execute("PRAGMA data_version").fetchone()[0], version)
            self.assertEqual(monitor.execute("SELECT value FROM workspace").fetchone()[0], before)

    def test_access_validation_and_remote_connection_cannot_enable_worker(self):
        original = self.client.get("/api/work-config").json()
        config = original["config"]
        anonymous = TestClient(backend.app)
        try:
            self.assertEqual(anonymous.get("/api/work-config").status_code, 401)
        finally:
            anonymous.close()
        self.assertEqual(self.client.put("/api/work-config", json=config).status_code, 403)
        self.assertEqual(self.client.put("/api/work-config", headers={**self.headers, "Origin": "https://unrelated.example"}, json=config).status_code, 403)
        for changes in ({"memory_recall_count": 6}, {"memory_recall_characters": 1001},
                        {"idle_seconds": -1}, {"max_jobs_per_day": True}, {"max_output_tokens": "512"},
                        {"reflection_model": "private model with spaces"}, {"unknown": "private input"}):
            response = self.client.put("/api/work-config", headers=self.headers, json={**config, **changes})
            self.assertEqual(response.status_code, 422, response.text)
            self.assertNotIn("private input", response.text)
        response = self.client.put("/api/work-config", headers=self.headers, json={**config, "enabled": True})
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(self.client.get("/api/work-config").json(), original)

    def test_enable_and_pause_require_local_connection_without_dispatching(self):
        config = self.client.get("/api/work-config").json()["config"]
        provider = self.client.get("/api/provider").json()["config"]
        with patch("backend.provider.network", side_effect=AssertionError("Config must not infer")):
            response = self.client.put("/api/provider", headers=self.headers, json={
                "config": {**provider, "protocol": "ollama", "base_url": "http://127.0.0.1:11434", "model": "synthetic:7b"},
                "persist": False})
            self.assertEqual(response.status_code, 200, response.text)
            response = self.client.put("/api/work-config", headers=self.headers, json={**config, "enabled": True})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertTrue(response.json()["config"]["enabled"])
            response = self.client.put("/api/work-config", headers=self.headers, json=config)
            self.assertEqual(response.status_code, 200, response.text)
            self.assertFalse(response.json()["config"]["enabled"])

    def test_configured_keys_cannot_be_saved_as_model_names(self):
        config = self.client.get("/api/work-config").json()["config"]
        key = "synthetic-configured-key"
        with patch.object(backend.credentials, "read", return_value=(key, "session")):
            for field in ("reflection_model", "memory_model", "coding_model"):
                response = self.client.put("/api/work-config", headers=self.headers, json={**config, field: key})
                self.assertEqual(response.status_code, 409, response.text)
                self.assertNotIn(key, response.text)
        self.assertNotIn(key.encode(), backend.DATABASE.read_bytes())

    def test_recall_controls_can_reduce_or_disable_context_without_erasing_records(self):
        self.client.get("/api/workspace")
        store = MemoryStore(backend.DATABASE, backend.TIMEZONE)
        for name in ("One", "Two", "Three"):
            store.remember(f"Synthetic project preference {name}.")
        self.assertEqual(len(store.recall("Synthetic project", None, limit=1, max_characters=1000)), 1)
        self.assertEqual(store.recall("Synthetic project", None, limit=0), [])
        self.assertEqual(store.recall("Synthetic project", None, max_characters=10), [])
        self.assertEqual(len(store.list()), 3)


if __name__ == "__main__":
    unittest.main()

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
from backend.reflection import ReflectionStore
from backend.web_search import ENDPOINT as SEARCH_ENDPOINT
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
        self.assertEqual(initial["config"]["reflection_interval_minutes"], 360)
        self.assertTrue(initial["worker_available"])
        self.assertEqual(initial["candidates"], [])
        self.assertEqual(initial["queued"], 0)
        config = {**initial["config"], "reflection_model": "synthetic-reasoning:20b",
                  "memory_model": "synthetic-small:7b", "coding_model": "synthetic-coding:24b",
                  "idle_seconds": 90, "max_jobs_per_day": 4, "memory_recall_count": 2,
                  "reflection_interval_minutes": 5}
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
        for changes in ({"memory_recall_count": 101}, {"memory_recall_characters": 200001},
                        {"background_context_tokens": 8191}, {"background_context_tokens": 131073},
                        {"reflection_exchange_count": 33}, {"reflection_context_characters": 200001},
                        {"idle_seconds": -1}, {"max_jobs_per_day": True}, {"max_output_tokens": "512"},
                        {"auto_curate": "true"}, {"periodic_reflection": 1},
                        {"reflection_interval_minutes": 4}, {"reflection_interval_minutes": 10081},
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

    def test_automatic_curation_requires_context_and_preserves_the_six_hour_schedule(self):
        config = self.client.get("/api/work-config").json()["config"]
        provider = self.client.get("/api/provider").json()["config"]
        local = {**provider, "protocol": "ollama", "base_url": "http://127.0.0.1:11434",
                 "model": "synthetic:7b", "ollama_context_tokens": 4096, "max_output_tokens": 256}
        automatic = {**config, "enabled": True, "auto_curate": True, "periodic_reflection": True}
        self.assertEqual(automatic["reflection_interval_minutes"], 360)
        with patch("backend.provider.network", side_effect=AssertionError("Saving settings must not infer")):
            self.assertEqual(self.client.put("/api/provider", headers=self.headers,
                json={"config": local, "persist": False}).status_code, 200)
            response = self.client.put("/api/work-config", headers=self.headers, json=automatic)
            self.assertEqual(response.status_code, 409, response.text)
            self.assertIn("8192", response.json()["detail"])
            self.assertEqual(self.client.get("/api/work-config").json()["config"], config)
            self.assertEqual(self.client.put("/api/provider", headers=self.headers,
                json={"config": {**local, "ollama_context_tokens": 8192}, "persist": False}).status_code, 200)
            response = self.client.put("/api/work-config", headers=self.headers, json=automatic)
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()["config"], automatic)
            self.assertEqual(self.client.get("/api/work-config").json()["config"], automatic)

    def test_configured_keys_cannot_be_saved_as_model_names(self):
        config = self.client.get("/api/work-config").json()["config"]
        key = "synthetic-configured-key"
        with patch.object(backend.credentials, "read", return_value=(key, "session")):
            for field in ("reflection_model", "memory_model", "coding_model"):
                response = self.client.put("/api/work-config", headers=self.headers, json={**config, field: key})
                self.assertEqual(response.status_code, 409, response.text)
                self.assertNotIn(key, response.text)
        self.assertNotIn(key.encode(), backend.DATABASE.read_bytes())

    def test_provider_fields_reject_search_credentials_before_changing_state(self):
        config = self.client.get("/api/workspace").json()["provider"]["config"]
        self.assertEqual(self.client.put("/api/provider", headers=self.headers,
            json={"config": config, "persist": False}).status_code, 200)
        key = "synthetic-search-key"
        before = backend.DATABASE.read_bytes()
        def read_key(endpoint):
            return (key, "session") if endpoint == SEARCH_ENDPOINT else (None, "missing")
        encoded = "".join(f"%{ord(char):02X}" for char in key)
        local = {**config, "protocol": "ollama", "base_url": "http://127.0.0.1:11434", "model": "synthetic:7b"}
        changes = [{**local, field: "model-" + key} for field in ("model", "orchestrator_model")]
        changes.extend({**config, "base_url": "https://synthetic.example/v1/" + path} for path in (key, encoded))
        with patch.object(backend.credentials, "read", side_effect=read_key), \
                patch.object(backend.credentials, "save") as save, patch("backend.provider.network") as network:
            for changed in changes:
                with self.subTest(config=changed):
                    response = self.client.put("/api/provider", headers=self.headers,
                                               json={"config": changed, "api_key": "" if changed["protocol"] == "ollama" else "synthetic-new-key", "persist": False})
                    self.assertEqual(response.status_code, 409, response.text)
                    self.assertNotIn(key, response.text)
                    self.assertNotIn(encoded, response.text)
                    self.assertEqual(backend.DATABASE.read_bytes(), before)
            save.assert_not_called()
            network.assert_not_called()

    def test_message_models_reject_provider_and_search_keys_without_creating_a_chat(self):
        config = self.client.get("/api/workspace").json()["provider"]["config"]
        keys = {config["base_url"]: "synthetic-provider-key", SEARCH_ENDPOINT: "synthetic-search-key"}
        local = {**config, "protocol": "ollama", "base_url": "http://127.0.0.1:11434", "model": "synthetic:7b"}
        with patch.object(backend.credentials, "read", side_effect=lambda endpoint: (keys.get(endpoint), "session")), \
                patch.object(backend.Provider, "chat") as generate, patch("backend.provider.network") as network:
            for connection, active_keys in ((config, keys.values()), (local, [keys[SEARCH_ENDPOINT]])):
                self.assertEqual(self.client.put("/api/provider", headers=self.headers,
                    json={"config": connection, "persist": False}).status_code, 200)
                before = backend.DATABASE.read_bytes()
                for key in active_keys:
                    response = self.client.post("/api/chat", headers=self.headers,
                                                json={"text": "Synthetic ordinary question.", "model": "model-" + key})
                    self.assertEqual(response.status_code, 409, response.text)
                    self.assertNotIn(key, response.text)
                    self.assertEqual(backend.DATABASE.read_bytes(), before)
            generate.assert_not_called()
            network.assert_not_called()

    def test_schedule_changes_take_effect_without_resetting_unchanged_saves(self):
        self.client.get("/api/workspace")
        provider = self.client.get("/api/provider").json()["config"]
        self.client.put("/api/provider", headers=self.headers, json={"config": {
            **provider, "protocol": "ollama", "base_url": "http://127.0.0.1:11434",
            "model": "synthetic:7b", "ollama_context_tokens": 8192, "max_output_tokens": 4096}, "persist": False})
        config = {**WorkConfig().model_dump(), "enabled": True, "auto_curate": True, "periodic_reflection": True}
        self.assertEqual(self.client.put("/api/work-config", headers=self.headers, json=config).status_code, 200)
        store = ReflectionStore(backend.DATABASE, backend.TIMEZONE)
        def deadline():
            with closing(sqlite3.connect(backend.DATABASE)) as db:
                return db.execute("SELECT next_due FROM reflection_schedule WHERE id=1").fetchone()[0]
        def future():
            with store.transaction() as db:
                db.execute("UPDATE reflection_schedule SET next_due=123456789 WHERE id=1")
        future()
        self.assertEqual(self.client.put("/api/work-config", headers=self.headers, json=config).status_code, 200)
        self.assertEqual(deadline(), 123456789)
        config["reflection_interval_minutes"] = 15
        self.assertEqual(self.client.put("/api/work-config", headers=self.headers, json=config).status_code, 200)
        self.assertEqual(deadline(), 0)
        future()
        self.client.put("/api/work-config", headers=self.headers, json={**config, "periodic_reflection": False})
        self.client.put("/api/work-config", headers=self.headers, json=config)
        self.assertEqual(deadline(), 0)

    def test_recall_controls_can_reduce_or_disable_context_without_erasing_records(self):
        self.client.get("/api/workspace")
        store = MemoryStore(backend.DATABASE, backend.TIMEZONE)
        for name in ("One", "Two", "Three"):
            store.remember(f"Synthetic project preference {name}.")
        self.assertEqual(len(store.recall("Synthetic project", None, limit=1, max_characters=1000)), 1)
        self.assertEqual(store.recall("Synthetic project", None, limit=0), [])
        self.assertEqual(store.recall("Synthetic project", None, max_characters=10), [])
        self.assertEqual(len(store.list()), 3)

    def test_larger_saved_limits_and_legacy_settings_preserve_explicit_preferences(self):
        config = self.client.get("/api/work-config").json()["config"]
        config.update(background_context_tokens=65536, reflection_exchange_count=24,
                      reflection_context_characters=80000, max_output_tokens=32768,
                      memory_recall_count=40, memory_recall_characters=64000)
        saved = self.client.put("/api/work-config", headers=self.headers, json=config)
        self.assertEqual(saved.status_code, 200, saved.text)
        self.assertEqual(self.client.get("/api/work-config").json()["config"], config)
        with backend.workspace_transaction() as state:
            state["work_config"] = {"memory_recall_count": 0, "max_jobs_per_day": 3,
                                    "max_output_tokens": 512, "reflection_interval_minutes": 360}
        legacy = self.client.get("/api/work-config").json()["config"]
        self.assertEqual(legacy["memory_recall_count"], 0)
        self.assertEqual(legacy["max_jobs_per_day"], 3)
        self.assertEqual(legacy["max_output_tokens"], 512)
        self.assertEqual(legacy["background_context_tokens"], 32768)
        self.assertEqual(legacy["reflection_exchange_count"], 16)
        self.assertEqual(legacy["max_tokens_per_day"], 2000000)
        self.assertEqual(legacy["reflection_context_characters"], 64000)
        self.assertEqual(legacy["memory_recall_characters"], 32000)


if __name__ == "__main__":
    unittest.main()

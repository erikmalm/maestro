"""Background work limits do not inherit smaller foreground chat settings."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient
from backend import app as backend
from backend.provider import DEFAULT


class BackgroundLimitAPITests(unittest.TestCase):
    def setUp(self):
        root = Path(self.enterContext(tempfile.TemporaryDirectory(prefix="maestro-independent-work-api-")))
        self.enterContext(patch.object(backend, "DATABASE", root / "workspace.sqlite3"))
        self.enterContext(patch.dict("os.environ", {"MAESTRO_CONTEXT_ARCHIVE_DIR": ""}))
        self.enterContext(patch("backend.credentials.read", return_value=(None, "synthetic")))
        self.client = TestClient(backend.app)
        self.addCleanup(self.client.close)
        self.headers = {"X-Maestro-CSRF": self.client.get("/api/session").json()["csrf"]}
        self.client.get("/api/workspace")
        self.connection = {**DEFAULT, "protocol": "ollama", "base_url": "http://127.0.0.1:11434",
                           "model": "synthetic-model:latest", "ollama_context_tokens": 4096, "max_output_tokens": 512}
        response = self.client.put("/api/provider", headers=self.headers,
                                   json={"config": self.connection, "api_key": "", "persist": False})
        self.assertEqual(response.status_code, 200, response.text)

    def test_work_can_be_enabled_with_its_own_valid_context_when_foreground_is_small(self):
        work = self.client.get("/api/work-config").json()["config"]
        response = self.client.put("/api/work-config", headers=self.headers,
                                   json={**work, "enabled": True, "auto_curate": True,
                                         "background_context_tokens": 65536, "max_output_tokens": 8192})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["config"]["background_context_tokens"], 65536)
        foreground = self.client.get("/api/provider").json()["config"]
        self.assertEqual(foreground["ollama_context_tokens"], 4096)
        self.assertEqual(foreground["max_output_tokens"], 512)

    def test_insufficient_work_context_still_fails_validation_without_changing_settings(self):
        original = self.client.get("/api/work-config").json()["config"]
        response = self.client.put("/api/work-config", headers=self.headers,
                                   json={**original, "background_context_tokens": 4096})
        self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(self.client.get("/api/work-config").json()["config"], original)

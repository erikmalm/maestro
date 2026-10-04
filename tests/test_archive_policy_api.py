"""Archive API compatibility, server-owned measurements and public test capture."""
from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from backend import app as backend, credentials
from backend.web_search import WebSearch


class ArchivePolicyAPITests(unittest.TestCase):
    def setUp(self):
        root = Path(self.enterContext(tempfile.TemporaryDirectory(prefix="maestro-public-policy-api-")))
        (root / "private").mkdir()
        self.enterContext(patch.object(backend, "DATABASE", root / "private" / "workspace.sqlite3"))
        self.enterContext(patch.dict("os.environ", {"MAESTRO_CONTEXT_ARCHIVE_DIR": str(root / "public")}))
        self.enterContext(patch.object(credentials, "read", return_value=("synthetic-public-search-key", "synthetic")))
        self.client = TestClient(backend.app)
        self.addCleanup(self.client.close)
        self.headers = {"X-Maestro-CSRF": self.client.get("/api/session").json()["csrf"]}
        self.client.get("/api/workspace")

    def save(self, config):
        return self.client.put("/api/context", headers=self.headers, json=config)

    def test_new_status_proposes_disabled_broad_capture_and_visible_capacity(self):
        status = self.client.get("/api/context").json()
        self.assertEqual(status["config"]["capture_policy"], "all_public")
        self.assertFalse(status["config"]["enabled"])
        self.assertEqual(status["config"]["max_bytes"], 10 * 1024 ** 3)
        self.assertEqual(status["config"]["max_items"], 200000)
        self.assertIsNone(status["last_capture"])

    def test_legacy_client_body_does_not_silently_expand_source_permission(self):
        config = self.client.get("/api/context").json()["config"]
        config.pop("capture_policy")
        config.update(enabled=True, public_sources=["https://docs.example.org/public/"])
        result = self.save(config)
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(result.json()["config"]["capture_policy"], "approved_sources")

    def test_broad_policy_can_be_enabled_without_a_site_allowlist(self):
        config = self.client.get("/api/context").json()["config"]
        result = self.save({**config, "enabled": True, "public_sources": []})
        self.assertEqual(result.status_code, 200, result.text)
        self.assertTrue(result.json()["available"])
        self.assertEqual(result.json()["config"]["capture_policy"], "all_public")

    def test_capture_measurements_are_owned_by_the_server(self):
        config = self.client.get("/api/context").json()["config"]
        for changed in ({**config, "capture_policy": "all_private"},
                        {**config, "last_capture": {"sources_saved": 99}},
                        {**config, "max_bytes": 100 * 1024 ** 3 + 1}):
            response = self.save(changed)
            self.assertEqual(response.status_code, 422, response.text)
            self.assertEqual(self.client.get("/api/context").json()["config"], config)

    def test_connection_test_results_are_captured_under_broad_policy(self):
        config = self.client.get("/api/context").json()["config"]
        self.assertEqual(self.save({**config, "enabled": True}).status_code, 200)
        result = {"query": "Ollama official web search documentation", "at": datetime.now(timezone.utc).isoformat(),
                  "sources": [{"title": "Public synthetic search guide", "url": "https://new.example.org/guide", "content": "Public synthetic tool documentation."}]}
        with patch.object(WebSearch, "search", return_value=result) as search:
            response = self.client.post("/api/web-search/test", headers=self.headers, json={})
        self.assertEqual(response.status_code, 200, response.text)
        search.assert_called_once_with(result["query"], test=True)
        status = self.client.get("/api/context").json()
        self.assertEqual(status["indexed_count"], 1)
        self.assertEqual(status["last_capture"]["sources_saved"], 1)
        self.assertGreater(status["last_capture"]["new_bytes"], 0)

    def test_connection_test_preserves_search_excerpt_truncation_metadata(self):
        config = self.client.get("/api/context").json()["config"]
        self.assertEqual(self.save({**config, "enabled": True}).status_code, 200)
        result = {"query": "Ollama official web search documentation", "at": datetime.now(timezone.utc).isoformat(),
                  "sources": [{"title": "Public synthetic guide", "url": "https://new.example.org/guide", "content": "A bounded excerpt."}],
                  "completeness": [{"maestro_truncated": True}]}
        with patch.object(WebSearch, "search", return_value=result):
            response = self.client.post("/api/web-search/test", headers=self.headers, json={})
        self.assertEqual(response.status_code, 200, response.text)
        captures = self.client.post("/api/context/search", headers=self.headers,
                                    json={"query": "bounded excerpt"}).json()
        source = self.client.get("/api/context/sources/" + captures["sources"][0]["capture_id"]).json()
        self.assertTrue(source["completeness"]["maestro_truncated"])
        self.assertFalse(source["completeness"]["full_page"])

    def test_connection_test_preserves_legacy_restricted_capture_behavior(self):
        config = self.client.get("/api/context").json()["config"]
        configured = {**config, "enabled": True, "capture_policy": "approved_sources", "public_sources": ["https://docs.example.org/"]}
        self.assertEqual(self.save(configured).status_code, 200)
        result = {"query": "Ollama official web search documentation", "at": datetime.now(timezone.utc).isoformat(),
                  "sources": [{"title": "Synthetic documentation", "url": "https://docs.example.org/guide", "content": "Public synthetic evidence."}]}
        with patch.object(WebSearch, "search", return_value=result):
            response = self.client.post("/api/web-search/test", headers=self.headers, json={})
        self.assertEqual(response.status_code, 200, response.text)
        status = self.client.get("/api/context").json()
        self.assertEqual(status["indexed_count"], 0)
        self.assertIsNone(status["last_capture"])

"""Evidence reads exercise the real capture core with temporary, synthetic data."""
from contextlib import closing
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from backend import app as backend


class EvidenceApiTests(unittest.TestCase):
    def setUp(self):
        root = Path(self.enterContext(tempfile.TemporaryDirectory(prefix="maestro-evidence-api-")))
        self.archive = root / "public"
        (root / "private").mkdir()
        self.enterContext(patch.object(backend, "DATABASE", root / "private" / "workspace.sqlite3"))
        self.enterContext(patch.dict("os.environ", {"MAESTRO_CONTEXT_ARCHIVE_DIR": str(self.archive)}))
        self.enterContext(patch.object(backend.credentials, "read", return_value=(None, "missing")))
        self.client = self.enterContext(TestClient(backend.app))
        csrf = self.client.get("/api/session").json()["csrf"]
        self.headers = {"X-Maestro-CSRF": csrf}

    def capture(self):
        config = self.client.get("/api/context").json()["config"]
        response = self.client.put("/api/context", json={**config, "enabled": True}, headers=self.headers)
        self.assertEqual(response.status_code, 200, response.text)
        return backend.context_store().capture("Synthetic financial report", "2026-10-16T12:00:00+00:00", [{
            "title": "Synthetic company report", "url": "https://reports.example.org/report",
            "content": "Saved evidence <script>literal text</script> from the report.",
        }], 1)["sources"][0]

    def open_source(self, source, **pins):
        return self.client.get("/api/context/sources/" + source["capture_id"], params={
            "content_hash": source["content_hash"], "manifest_hash": source["manifest_hash"], **pins,
        })

    def test_inventory_and_pinned_readback_use_authenticated_no_store_reads(self):
        source = self.capture()
        anonymous = TestClient(backend.app)
        self.addCleanup(anonymous.close)
        for path in ("/api/context/sources", "/api/context/sources/" + source["capture_id"]):
            self.assertEqual(anonymous.get(path).status_code, 401)
            self.assertEqual(self.client.get(path, headers={"Origin": "https://unrelated.example"}).status_code, 403)
        database_before = backend.DATABASE.read_bytes()
        index_before = backend.context_store().index.read_bytes()
        for response in (self.client.get("/api/context/sources"), self.open_source(source)):
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertEqual(backend.DATABASE.read_bytes(), database_before)
        self.assertEqual(backend.context_store().index.read_bytes(), index_before)

    def test_metadata_then_exact_text_and_revoked_access(self):
        source = self.capture()
        response = self.client.get("/api/context/sources")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.headers["cache-control"], "no-store")
        result = response.json()
        self.assertEqual(result["state"], "ready")
        self.assertIsNone(result["next_cursor"])
        self.assertEqual(len(result["sources"]), 1)
        row = result["sources"][0]
        self.assertNotIn("content", row)
        self.assertFalse(row["body_verified"])
        self.assertEqual(row["content_bytes"], len(source["content"].encode()))
        self.assertIsNone(row["published_at"])
        self.assertEqual(row["manifest_hash"], source["manifest_hash"])
        opened = self.open_source(source)
        self.assertEqual(opened.status_code, 200, opened.text)
        self.assertEqual(opened.json()["content"], source["content"])
        self.assertEqual(self.open_source(source, content_hash="0" * 64).status_code, 404)
        self.assertEqual(self.open_source(source, manifest_hash="0" * 64).status_code, 404)
        config = backend.context_store().status()["config"]
        self.client.put("/api/context", json={**config, "enabled": False}, headers=self.headers)
        self.assertEqual(self.client.get("/api/context/sources").json()["state"], "disabled")
        self.assertEqual(self.open_source(source).status_code, 404)

    def test_missing_index_is_unavailable_but_valid_empty_index_is_ready(self):
        config = self.client.get("/api/context").json()["config"]
        self.client.put("/api/context", json={**config, "enabled": True}, headers=self.headers)
        store = backend.context_store()
        # Explicit fixture damage after empty-archive initialization.
        store.index.unlink()
        self.assertFalse(store.index.exists())
        self.assertEqual(self.client.get("/api/context/sources").json()["state"], "unavailable")
        self.assertFalse(store.index.exists())
        # Explicit fixture setup, never done by the read-only endpoint.
        with closing(store._index_connection()):
            pass
        result = self.client.get("/api/context/sources").json()
        self.assertEqual((result["state"], result["sources"]), ("ready", []))

    def test_changed_body_is_not_returned_even_when_inventory_metadata_still_matches(self):
        source = self.capture()
        body = self.archive / "objects" / "sha256" / source["content_hash"][:2] / (source["content_hash"] + ".txt")
        body.write_text("Unexpected replacement", encoding="utf-8")
        self.assertEqual(len(self.client.get("/api/context/sources").json()["sources"]), 1)
        response = self.open_source(source)
        self.assertEqual(response.status_code, 404)
        self.assertNotIn("Unexpected replacement", response.text)

    def test_read_access_and_bounded_parameters(self):
        source = self.capture()
        for params in ({"limit": 0}, {"limit": 51}, {"cursor": "not-a-page"}, {"cursor": "x" * 300}):
            response = self.client.get("/api/context/sources", params=params)
            self.assertEqual(response.status_code, 422, response.text)
        missing_pins = self.client.get("/api/context/sources/" + source["capture_id"])
        self.assertEqual(missing_pins.status_code, 422)
        self.assertEqual(self.open_source(source, content_hash="invalid-private-value").status_code, 422)
        self.assertNotIn("invalid-private-value", self.open_source(source, content_hash="invalid-private-value").text)


if __name__ == "__main__":
    unittest.main()

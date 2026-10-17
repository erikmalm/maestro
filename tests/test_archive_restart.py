"""Independent app processes reopen synthetic captures at fixed private paths."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parent.parent
HARNESS = r'''
import json
import os
from pathlib import Path
import sys
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient
from backend import app as backend

expected = json.loads(sys.stdin.read())
mode = sys.argv[1]
backend.credentials.read = lambda *_: (None, "missing")
with (patch.object(httpx.HTTPTransport, "handle_request", side_effect=AssertionError("External network")),
      patch.object(httpx.AsyncHTTPTransport, "handle_async_request", side_effect=AssertionError("External network")),
      patch("backend.provider.network", side_effect=AssertionError("Model dispatch")),
      patch("backend.web_search.WebSearch.search", side_effect=AssertionError("Hosted search"))):
    with TestClient(backend.app) as client:
        headers = {"X-Maestro-CSRF": client.get("/api/session").json()["csrf"]}
        if mode == "seed":
            config = client.get("/api/context").json()["config"]
            config.update(enabled=True, capture_policy="approved_sources", reuse_hours=72,
                          max_bytes=16 * 1024 ** 2, max_items=500,
                          public_sources=["https://reports.example.org/"])
            saved = client.put("/api/context", json=config, headers=headers)
            assert saved.status_code == 200, saved.text
            source = backend.context_store().capture("Synthetic restart fixture", "2026-10-16T12:00:00+00:00", [{
                "title": "SYNTHETIC restart report", "url": "https://reports.example.org/restart",
                "content": "SYNTHETIC TEST DATA. Literal archived text <b>42</b>.\n" * 30,
            }], 1)["sources"][0]
            assert source["archive_status"] == "saved", source
        else:
            source = expected["source"]
        change_status = None
        if mode in ("disable", "enable", "invalid"):
            config = client.get("/api/context").json()["config"]
            change_status = client.put("/api/context", json={**config, "enabled": mode == "enable"}, headers=headers).status_code
        status_response = client.get("/api/context")
        assert status_response.status_code == 200, status_response.text
        workspace = client.get("/api/workspace")
        assert workspace.status_code == 200, workspace.text
        assert workspace.json()["context_archive"] == status_response.json()
        listing = client.get("/api/context/sources")
        assert listing.status_code == 200, listing.text
        opened = client.get("/api/context/sources/" + source["capture_id"], params={
            "content_hash": source["content_hash"], "manifest_hash": source["manifest_hash"],
        })
        result = {"pid": os.getpid(), "source": source, "status": status_response.json(),
                  "inventory": listing.json(), "opened_status": opened.status_code, "opened": opened.json(),
                  "change_status": change_status, "stored_config": backend.context_store()._state()["config"]}
    assert not backend.app.state.archive_owner
    print(json.dumps(result))
'''


class ArchiveRestartTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory(prefix="maestro-archive-restart-")))
        self.private = self.root / "private"
        self.archive = self.root / "OneDrive" / "Maestro-SearchArchive"
        self.earlier = self.root / "OneDrive" / "web-content" / "untouched.txt"
        self.earlier.parent.mkdir(parents=True)
        self.earlier.write_text("Earlier scaffold must stay untouched.", encoding="utf-8")

    def process(self, mode, expected=None, archive=None):
        environment = {**os.environ, "MAESTRO_DATA_DIR": str(self.private),
                       "MAESTRO_CONTEXT_ARCHIVE_DIR": str(self.archive) if archive is None else archive}
        for key in ("OPENAI_API_KEY", "MAESTRO_API_KEY_FILE", "MAESTRO_SEARCH_KEY_FILE"):
            environment.pop(key, None)
        result = subprocess.run([sys.executable, "-c", HARNESS, mode], cwd=ROOT, env=environment,
                                input=json.dumps(expected or {}), text=True, capture_output=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return json.loads(result.stdout)

    def archive_files(self):
        return {str(path.relative_to(self.archive)): path.read_bytes()
                for path in self.archive.rglob("*") if path.is_file()}

    def assert_same_capture(self, result, expected):
        self.assertEqual(result["opened_status"], 200)
        self.assertEqual(result["opened"], expected["source"])
        self.assertEqual(result["inventory"]["state"], "ready")
        self.assertEqual([source["capture_id"] for source in result["inventory"]["sources"]],
                         [expected["source"]["capture_id"]])
        self.assertEqual(result["status"]["database_path"], str(self.private / "workspace.sqlite3"))
        self.assertEqual(result["status"]["index_path"], str(self.private / "context" / "index.sqlite3"))
        self.assertEqual(result["status"]["path"], str(self.archive))

    def test_independent_restart_reopens_exact_versions_without_seeding_or_refreshing_dates(self):
        seeded = self.process("seed")
        self.assert_same_capture(seeded, seeded)
        before = self.archive_files()
        index = (self.private / "context" / "index.sqlite3").read_bytes()
        restarted = self.process("read", seeded)
        self.assertNotEqual(restarted["pid"], seeded["pid"])
        self.assert_same_capture(restarted, seeded)
        self.assertEqual(restarted["status"]["config"], seeded["status"]["config"])
        self.assertEqual(restarted["status"]["last_capture"], seeded["status"]["last_capture"])
        self.assertEqual(self.archive_files(), before)
        self.assertEqual((self.private / "context" / "index.sqlite3").read_bytes(), index)
        self.assertEqual(len(list(self.archive.glob("records/captures/*/*.json"))), 1)
        self.assertEqual(self.earlier.read_text(encoding="utf-8"), "Earlier scaffold must stay untouched.")
        self.assertFalse((self.archive / "writer-owner.tmp").exists())

    def test_disabled_restart_and_reenable_preserve_the_original_capture(self):
        seeded = self.process("seed")
        before = self.archive_files()
        disabled = self.process("disable", seeded)
        self.assertEqual(disabled["change_status"], 200)
        restarted = self.process("read", seeded)
        self.assertFalse(restarted["status"]["config"]["enabled"])
        self.assertEqual(restarted["inventory"]["state"], "disabled")
        self.assertEqual(restarted["opened_status"], 404)
        self.assertEqual(self.archive_files(), before)
        enabled = self.process("enable", seeded)
        self.assertEqual(enabled["change_status"], 200)
        self.assert_same_capture(enabled, seeded)
        self.assertEqual(enabled["status"]["config"], seeded["status"]["config"])
        self.assertEqual(self.archive_files(), before)

    def test_invalid_or_unavailable_startup_paths_do_not_replace_saved_configuration_or_captures(self):
        seeded = self.process("seed")
        before = self.archive_files()
        invalid = self.process("invalid", seeded, archive="relative/invalid-archive")
        self.assertEqual(invalid["change_status"], 409)
        self.assertTrue(invalid["status"]["path_error"])
        self.assertEqual(invalid["stored_config"], seeded["status"]["config"])
        self.assertEqual(invalid["inventory"]["state"], "unavailable")
        self.assertEqual(invalid["opened_status"], 404)
        self.assertEqual(self.archive_files(), before)
        unavailable_path = self.root / "not-a-directory"
        unavailable_path.write_text("Existing unrelated file.", encoding="utf-8")
        unavailable = self.process("read", seeded, archive=str(unavailable_path))
        self.assertFalse(unavailable["status"]["available"])
        self.assertTrue(unavailable["status"]["last_error"])
        self.assertEqual(unavailable["stored_config"], seeded["status"]["config"])
        self.assertEqual(unavailable["inventory"]["state"], "unavailable")
        self.assertEqual(self.archive_files(), before)
        self.assertEqual(unavailable_path.read_text(encoding="utf-8"), "Existing unrelated file.")
        restored = self.process("read", seeded)
        self.assert_same_capture(restored, seeded)
        self.assertIsNone(restored["status"]["last_error"])
        self.assertEqual(self.archive_files(), before)


if __name__ == "__main__":
    unittest.main()

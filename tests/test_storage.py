"""Container secret isolation, workspace ownership and private snapshots."""
from contextlib import closing
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend import app as backend, credentials
from backend.provider import DEFAULT, Provider
from backend.storage import backup_database, workspace_owner


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.directory = Path(self.enterContext(tempfile.TemporaryDirectory(prefix="maestro-storage-")))
        self.secret = self.directory / "key"
        self.secret.write_text("synthetic-mounted-key\n", encoding="utf-8")
        self.enterContext(patch.dict(os.environ, {
            "MAESTRO_API_KEY_FILE": str(self.secret), "MAESTRO_API_KEY_URL": "https://provider.example/v1",
            "MAESTRO_SEARCH_KEY_FILE": "", "OPENAI_API_KEY": ""}))

    def test_mounted_keys_are_endpoint_scoped_read_only_and_not_echoed(self):
        endpoint = "https://provider.example/v1"
        with patch.object(credentials, "vault") as vault, patch.dict(credentials.session_keys, {}, clear=True):
            self.assertEqual(credentials.read(endpoint), ("synthetic-mounted-key", "mounted secret"))
            with patch.dict(os.environ, {"MAESTRO_API_KEY_URL": endpoint + "/"}):
                self.assertEqual(credentials.read(endpoint), ("synthetic-mounted-key", "mounted secret"))
                self.assertEqual(credentials.storage_options(endpoint), {"persist_supported": False, "managed_credentials": True})
            self.assertEqual(credentials.storage_options(endpoint), {"persist_supported": False, "managed_credentials": True})
            for action in (lambda: credentials.save(endpoint, "synthetic-replacement", False), lambda: credentials.delete(endpoint)):
                with self.assertRaisesRegex(ValueError, "mounted secret") as caught:
                    action()
                self.assertNotIn("synthetic-mounted-key", str(caught.exception))
            vault.assert_not_called()
            self.assertEqual(credentials.session_keys, {})
        with patch.object(credentials, "vault") as vault:
            vault.return_value.CredReadW.return_value = False
            with patch.object(credentials.ctypes, "get_last_error", return_value=1168, create=True):
                self.assertEqual(credentials.read("https://other.example/v1"), (None, "missing"))
        with patch.dict(os.environ, MAESTRO_SEARCH_KEY_FILE=str(self.secret)):
            self.assertEqual(credentials.read("https://ollama.com/api/web_search")[1], "mounted secret")

    def test_bad_mounted_secret_is_a_generic_error(self):
        for content in ("", "synthetic-key\ncontrol", "\u00e9", "x" * 4098):
            with self.subTest(length=len(content)):
                self.secret.write_text(content, encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "missing or invalid") as caught:
                    credentials.read("https://provider.example/v1")
                if content:
                    self.assertNotIn(content, str(caught.exception))
                status = credentials.status("https://provider.example/v1")
                self.assertFalse(status["credentials_present"])
                self.assertEqual(status["credential_source"], "unavailable")
                self.assertIn("missing or invalid", status["credential_error"])
                self.assertNotIn(str(self.secret), str(status))
        self.secret.unlink()
        with self.assertRaisesRegex(ValueError, "missing or invalid"):
            credentials.read("https://provider.example/v1")

    def test_invalid_mounted_keys_keep_workspace_usable_and_dispatch_closed(self):
        database = self.directory / "workspace.sqlite3"
        remote = {**DEFAULT, "base_url": "https://provider.example/v1", "model": "synthetic-remote", "pricing_verified": True}
        local = {**DEFAULT, "base_url": "http://127.0.0.1:11434", "protocol": "ollama", "model": "synthetic-local"}
        with patch.object(backend, "DATABASE", database), patch.object(credentials, "vault") as vault:
            vault.return_value.CredReadW.return_value = False
            with patch.object(credentials.ctypes, "get_last_error", return_value=1168, create=True), TestClient(backend.app) as client:
                headers = {"X-Maestro-CSRF": client.get("/api/session").json()["csrf"]}
                self.assertEqual(client.put("/api/provider", headers=headers, json={"config": remote}).status_code, 200)
                self.secret.unlink()
                for path in ("/api/provider", "/api/workspace"):
                    result = client.get(path)
                    self.assertEqual(result.status_code, 200, result.text)
                    status = result.json()["provider"] if path.endswith("workspace") else result.json()
                    self.assertFalse(status["credentials_present"])
                    self.assertIn("missing or invalid", status["credential_error"])
                    self.assertNotIn(str(self.secret), result.text)
                with patch("backend.provider.network") as network:
                    for path, body in (("/api/provider/test", {}), ("/api/chat", {"text": "Synthetic bounded request"})):
                        result = client.post(path, headers=headers, json=body)
                        self.assertEqual(result.status_code, 409, result.text)
                    network.assert_not_called()
                self.assertEqual(Provider(database, backend.TIMEZONE).read_state()["ledger"], [])
                result = client.put("/api/provider", headers=headers, json={"config": local})
                self.assertEqual(result.status_code, 200, result.text)
                with patch.dict(os.environ, {"MAESTRO_SEARCH_KEY_FILE": str(self.secret)}):
                    result = client.get("/api/workspace")
                    self.assertEqual(result.status_code, 200, result.text)
                    self.assertIn("missing or invalid", result.json()["web_search"]["credential_error"])
                    result = client.put("/api/web-search", headers=headers, json={"config": {"enabled": False}})
                    self.assertEqual(result.status_code, 200, result.text)
                    with patch("backend.provider.network", side_effect=lambda config, key, method, path, payload=None:
                               {"details": {"format": "gguf"}, "capabilities": ["completion"]} if path == "/api/show" else
                               {"done": True, "message": {"content": "Synthetic local reply"}, "prompt_eval_count": 20, "eval_count": 5}):
                        result = client.post("/api/chat", headers=headers, json={"text": "Local work with search disabled"})
                    self.assertEqual(result.status_code, 200, result.text)
                    self.assertEqual(result.json()["messages"][-1]["text"], "Synthetic local reply")

    def test_workspace_has_one_owner_and_releases_after_failure(self):
        with self.assertRaisesRegex(ValueError, "synthetic failure"):
            with workspace_owner(self.directory):
                with self.assertRaisesRegex(RuntimeError, "already running"):
                    with workspace_owner(self.directory):
                        self.fail("A second process must not recover another owner's requests.")
                raise ValueError("synthetic failure")
        with workspace_owner(self.directory):
            pass

    def test_backup_is_consistent_preserves_source_and_never_overwrites(self):
        source, destination = self.directory / "workspace.sqlite3", self.directory / "snapshot.sqlite3"
        with closing(sqlite3.connect(source)) as db, db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("CREATE TABLE private_data (value TEXT)")
            db.execute("INSERT INTO private_data VALUES ('synthetic record')")
        backup_database(source, destination)
        with closing(sqlite3.connect(destination)) as db:
            self.assertEqual(db.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            self.assertEqual(db.execute("SELECT value FROM private_data").fetchone()[0], "synthetic record")
        with self.assertRaises(FileExistsError):
            backup_database(source, destination)
        with self.assertRaises(FileExistsError):
            backup_database(source, source)
        with self.assertRaises(ValueError):
            backup_database(self.directory / "missing.sqlite3", self.directory / "unused.sqlite3")
        if os.name != "nt":
            self.assertEqual(destination.stat().st_mode & 0o777, 0o600)

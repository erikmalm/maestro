"""Actual storage locations, safe status exports and explicit empty setup."""
from pathlib import Path
import os
import tempfile
import threading
import unittest
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient

from backend import app as backend
from backend.web_search import ENDPOINT


class ArchiveStatusTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory(prefix="maestro-archive-status-")))
        self.private = self.root / "private"
        self.private.mkdir()
        self.archive = self.root / "OneDrive" / "Maestro-SearchArchive"
        self.enterContext(patch.object(backend, "DATABASE", self.private / "workspace.sqlite3"))
        self.enterContext(patch.dict(os.environ, {"MAESTRO_CONTEXT_ARCHIVE_DIR": str(self.archive)}))
        self.keys = {}
        self.enterContext(patch.object(backend.credentials, "read", side_effect=lambda endpoint:
                                      (self.keys.get(endpoint), "synthetic" if endpoint in self.keys else "missing")))
        self.enterContext(patch.object(httpx.HTTPTransport, "handle_request", side_effect=AssertionError("External network")))
        self.enterContext(patch.object(httpx.AsyncHTTPTransport, "handle_async_request", side_effect=AssertionError("External network")))

    def enable(self, client):
        headers = {"X-Maestro-CSRF": client.get("/api/session").json()["csrf"]}
        config = client.get("/api/context").json()["config"]
        response = client.put("/api/context", json={**config, "enabled": True}, headers=headers)
        self.assertEqual(response.status_code, 200, response.text)
        return headers, response.json()["config"]

    def capture(self):
        return backend.context_store().capture("Synthetic archived report", "2026-10-16T12:00:00+00:00", [{
            "title": "Synthetic report", "url": "https://reports.example.org/report",
            "content": "Synthetic evidence retained in private test storage.",
        }], 1)["sources"][0]

    def snapshot(self):
        # Windows intentionally denies reads of the active runtime lock byte.
        return {str(path.relative_to(self.root)): path.read_bytes()
                for path in self.root.rglob("*") if path.is_file() and path.name != "runtime.lock"}

    def archive_snapshot(self):
        return {str(path.relative_to(self.archive)): path.read_bytes()
                for path in self.archive.rglob("*") if path.is_file() and path.name != "writer-owner.tmp"}

    def test_status_reports_resolved_paths_without_creating_the_optional_archive(self):
        with TestClient(backend.app) as client:
            client.get("/api/session")
            before = self.snapshot()
            for path in ("/api/context", "/api/workspace"):
                response = client.get(path)
                self.assertEqual(response.status_code, 200, response.text)
                status = response.json() if path == "/api/context" else response.json()["context_archive"]
                self.assertEqual(status["path"], str(self.archive.resolve()))
                self.assertEqual(status["database_path"], str(backend.DATABASE.resolve()))
                self.assertEqual(status["index_path"], str(self.private / "context" / "index.sqlite3"))
                self.assertIsNone(status["path_error"])
                self.assertIsNone(status["config_error"])
                self.assertFalse(status["config"]["enabled"])
            self.assertEqual(self.snapshot(), before)
            self.assertFalse(self.archive.exists())
            self.assertFalse((self.private / "context").exists())

    def test_explicit_enable_initializes_an_empty_private_index_without_seeding_sources(self):
        with TestClient(backend.app) as client:
            self.enable(client)
            store = backend.context_store()
            self.assertTrue(store.index.is_file())
            self.assertNotIn(self.archive, store.index.parents)
            self.assertEqual(client.get("/api/context/sources").json(),
                             {"state": "ready", "sources": [], "next_cursor": None, "skipped_count": 0})
            self.assertEqual(list(self.archive.glob("records/captures/*/*.json")), [])
            self.assertEqual(store.status()["indexed_count"], 0)

    def test_existing_captures_without_an_index_are_not_replaced_by_an_empty_catalogue(self):
        with TestClient(backend.app) as client:
            headers, config = self.enable(client)
            source = self.capture()
            store = backend.context_store()
            index = store.index.read_bytes()
            self.assertEqual(client.put("/api/context", json={**config, "enabled": False}, headers=headers).status_code, 200)
            store.index.unlink()
            before = self.archive_snapshot()
            response = client.put("/api/context", json=config, headers=headers)
            self.assertEqual(response.status_code, 200, response.text)
            self.assertFalse(store.index.exists())
            self.assertEqual(client.get("/api/context/sources").json()["state"], "unavailable")
            self.assertEqual(self.archive_snapshot(), before)
            # Explicit test recovery proves capture bytes and pins survived.
            store.index.write_bytes(index)
            self.assertEqual(store.get_capture(source["capture_id"], source["content_hash"], source["manifest_hash"]), source)

    def test_invalid_archive_paths_leave_startup_and_private_workspace_usable_without_saving(self):
        for invalid in ("relative/archive", str(self.private), str(backend.ROOT / "invalid-source-archive")):
            with self.subTest(path=invalid), patch.dict(os.environ, {"MAESTRO_CONTEXT_ARCHIVE_DIR": invalid}):
                with TestClient(backend.app) as client:
                    headers = {"X-Maestro-CSRF": client.get("/api/session").json()["csrf"]}
                    before = self.snapshot()
                    status = client.get("/api/context").json()
                    self.assertFalse(status["configured"])
                    self.assertFalse(status["available"])
                    self.assertIsNone(status["path"])
                    self.assertIn("invalid", status["path_error"])
                    workspace = client.get("/api/workspace")
                    self.assertEqual(workspace.status_code, 200, workspace.text)
                    self.assertEqual(workspace.json()["context_archive"], status)
                    self.assertEqual(client.get("/api/context/sources").json()["state"], "unavailable")
                    response = client.put("/api/context", json={**status["config"], "enabled": True}, headers=headers)
                    self.assertEqual(response.status_code, 409, response.text)
                    self.assertNotIn(invalid, response.text)
                    response = client.patch("/api/context", json={"enabled": False}, headers=headers)
                    self.assertEqual(response.status_code, 409, response.text)
                    self.assertEqual(self.snapshot(), before)
                    self.assertFalse(backend.app.state.archive_owner)
        self.assertFalse(self.archive.exists())

    def test_later_credentials_withhold_matching_paths_without_mutating_settings_or_files(self):
        provider_key, search_key = "syntheticproviderq7", "syntheticsearchp8"
        for encoded in (False, True):
            def representation(value):
                return "".join(f"%{ord(character):02X}" for character in value) if encoded else value

            database = self.root / str(encoded) / representation(search_key) / "workspace.sqlite3"
            archive = self.root / str(encoded) / "OneDrive" / representation(provider_key)
            database.parent.mkdir(parents=True)
            with (self.subTest(encoded=encoded), patch.object(backend, "DATABASE", database),
                  patch.dict(os.environ, {"MAESTRO_CONTEXT_ARCHIVE_DIR": str(archive)})):
                self.keys.clear()
                with TestClient(backend.app) as client:
                    headers, _ = self.enable(client)
                    self.capture()
                    original = client.get("/api/context").json()
                    provider_url = backend.provider().read_state()["config"]["base_url"]
                    before = self.snapshot()
                    self.keys.update({provider_url: provider_key, ENDPOINT: search_key})
                    for path in ("/api/context", "/api/workspace"):
                        response = client.get(path)
                        self.assertEqual(response.status_code, 200, response.text)
                        status = response.json() if path == "/api/context" else response.json()["context_archive"]
                        for field in ("path", "database_path", "index_path"):
                            self.assertIsNone(status[field])
                        self.assertTrue(status["path_error"])
                        self.assertEqual(status["config"], original["config"])
                        self.assertIsNone(status["config_error"])
                        for field in ("indexed_count", "archive_bytes", "archive_items", "last_capture", "last_error"):
                            self.assertEqual(status[field], original[field])
                        for key in (provider_key, search_key, representation(provider_key), representation(search_key)):
                            self.assertNotIn(key, response.text)
                    self.assertEqual(client.patch("/api/context", json={"enabled": False}, headers=headers).status_code, 409)
                    self.assertEqual(self.snapshot(), before)
                    self.keys.clear()
                    self.assertEqual(client.get("/api/context").json(), original)
                    self.assertEqual(self.snapshot(), before)

    def test_optional_path_resolution_runtime_error_has_the_same_safe_status(self):
        constructor = backend.ContextStore

        def unresolved_optional_path(database, archive_dir=None):
            if archive_dir is None:
                raise RuntimeError("Synthetic private path resolution diagnostic")
            return constructor(database, archive_dir)

        with patch.object(backend, "ContextStore", side_effect=unresolved_optional_path):
            with TestClient(backend.app) as client:
                client.get("/api/session")
                response = client.get("/api/context")
                self.assertEqual(response.status_code, 200, response.text)
                self.assertIn("invalid", response.json()["path_error"])
                self.assertNotIn("Synthetic private", response.text)
                self.assertEqual(client.get("/api/workspace").status_code, 200)
                self.assertFalse(self.archive.exists())

    def test_toggle_preserves_a_concurrent_full_update_admitted_before_it(self):
        with TestClient(backend.app) as client:
            headers = {"X-Maestro-CSRF": client.get("/api/session").json()["csrf"]}
            initial = client.get("/api/context").json()["config"]
            update = {**initial, "capture_policy": "approved_sources",
                      "public_sources": ["https://reports.example.org/new/"],
                      "reuse_hours": 60, "max_bytes": 20 * 1024 ** 2, "max_items": 5000}
            store = backend.context_store()
            configure, lock = store.configure, backend._archive_config_lock
            updated, toggle_waiting, release = threading.Event(), threading.Event(), threading.Event()
            errors, responses = [], {}

            class ObservedLock:
                def __init__(self):
                    self.admissions = 0
                    self.guard = threading.Lock()

                def __enter__(self):
                    with self.guard:
                        self.admissions += 1
                        if self.admissions == 2:
                            toggle_waiting.set()
                    lock.acquire()

                def __exit__(self, *_):
                    lock.release()

            def held_update(settings):
                result = configure(settings)
                if not settings["enabled"]:
                    updated.set()
                    if not release.wait(5):
                        raise AssertionError("Full configuration update was not released.")
                return result

            def request(method, payload):
                try:
                    responses[method] = getattr(client, method)("/api/context", json=payload, headers=headers)
                except Exception as error:
                    errors.append(error)

            writer = threading.Thread(target=request, args=("put", update), daemon=True)
            toggle = threading.Thread(target=request, args=("patch", {"enabled": True}), daemon=True)
            with (patch.object(backend, "_archive_config_lock", ObservedLock()),
                  patch.object(store, "configure", side_effect=held_update)):
                try:
                    writer.start()
                    self.assertTrue(updated.wait(5), "Full configuration update did not reach storage.")
                    toggle.start()
                    self.assertTrue(toggle_waiting.wait(5), "Toggle did not contend for configuration ownership.")
                finally:
                    release.set()
                    writer.join(5)
                    if toggle.ident is not None:
                        toggle.join(5)
            self.assertFalse(writer.is_alive() or toggle.is_alive(), "Configuration requests deadlocked.")
            self.assertEqual(errors, [], repr(errors))
            self.assertEqual({method: response.status_code for method, response in responses.items()}, {"put": 200, "patch": 200})
            self.assertEqual(client.get("/api/context").json()["config"], {**update, "enabled": True})

    def test_toggle_rejects_withheld_configuration_without_persisting_or_claiming(self):
        with TestClient(backend.app) as client:
            headers, config = self.enable(client)
            secret = "synthetic-future-scope-key"
            config = {**config, "public_sources": ["https://reports.example.org/" + secret + "/"]}
            self.assertEqual(client.put("/api/context", json=config, headers=headers).status_code, 200)
            self.keys[ENDPOINT] = secret
            before = self.snapshot()
            store = backend.context_store()
            with (patch.object(store, "configure", wraps=store.configure) as configure,
                  patch.object(store, "archive_owner", wraps=store.archive_owner) as owner):
                response = client.patch("/api/context", json={"enabled": False}, headers=headers)
            self.assertEqual(response.status_code, 409, response.text)
            self.assertNotIn(secret, response.text)
            configure.assert_not_called()
            owner.assert_not_called()
            self.assertEqual(store._state()["config"], config)
            self.assertEqual(self.snapshot(), before)
            self.assertTrue(backend.app.state.archive_owner)

    def test_toggle_accepts_only_an_explicit_strict_enabled_boolean(self):
        with TestClient(backend.app) as client:
            headers = {"X-Maestro-CSRF": client.get("/api/session").json()["csrf"]}
            before = self.snapshot()
            for payload in ({}, {"enabled": "true"}, {"enabled": 1}, {"enabled": None},
                            {"enabled": True, "public_sources": []}):
                with self.subTest(payload=payload):
                    response = client.patch("/api/context", json=payload, headers=headers)
                    self.assertEqual(response.status_code, 422, response.text)
            self.assertEqual(self.snapshot(), before)


if __name__ == "__main__":
    unittest.main()

"""Large index recovery, durable batches and mutation exclusion."""
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
import uuid

from fastapi.testclient import TestClient
from backend import app as backend, credentials
from backend.context_store import ContextStore, DEFAULT


class ContextRebuildTests(unittest.TestCase):
    def setUp(self):
        root = Path(self.enterContext(tempfile.TemporaryDirectory(prefix="maestro-index-batches-")))
        self.database = root / "private" / "workspace.sqlite3"
        self.archive = root / "public"
        self.store = ContextStore(self.database, self.archive)
        self.config = {**DEFAULT, "enabled": True, "public_sources": []}
        self.store.configure(self.config)
        self.source = {"title": "Public guide", "url": "https://docs.example.org/guide", "content": "Verified original evidence."}
        self.original = self.store.capture("public guide", self.store.now().isoformat(), [self.source], 3)["sources"][0]

    def add_manifests(self, count):
        path = next(self.archive.glob("records/captures/*/*.json"))
        template = json.loads(path.read_text(encoding="utf-8"))
        for _ in range(count):
            capture_id = uuid.uuid4().hex
            (path.parent / (capture_id + ".json")).write_text(json.dumps({**template, "capture_id": capture_id}), encoding="utf-8")

    def test_5001_captures_rebuild_in_bounded_batches_and_survive_store_recreation(self):
        self.add_manifests(5000)
        previous = self.store.index.read_bytes()
        status = self.store.rebuild(limit=1000)
        progress = status["rebuild_progress"]
        self.assertEqual(progress["total"], 5001)
        self.assertLessEqual(progress["processed"], 1000)
        self.assertFalse(progress["complete"])
        self.assertEqual(self.store.index.read_bytes(), previous)
        self.assertEqual(self.store.get_capture(self.original["capture_id"]), self.original)
        restarted = ContextStore(self.database, self.archive)
        calls = 1
        while not progress["complete"]:
            prior = progress
            status = restarted.rebuild(limit=1000, continuation=progress["id"])
            progress = status["rebuild_progress"]
            self.assertEqual(progress["id"], prior["id"])
            self.assertGreater(progress["processed"], prior["processed"])
            self.assertLessEqual(progress["processed"] - prior["processed"], 1000)
            calls += 1
            self.assertLess(calls, 20)
        self.assertEqual(progress["processed"], 5001)
        self.assertEqual(status["indexed_count"], 5001)
        self.assertFalse(restarted._rebuild_path.exists())
        self.assertEqual(restarted.get_capture(self.original["capture_id"]), self.original)
        self.assertEqual(len(list(self.archive.glob("records/captures/*/*.json"))), 5001)
        with closing(sqlite3.connect(restarted.index)) as db:
            self.assertFalse(db.execute("SELECT name FROM sqlite_master WHERE name LIKE 'rebuild_%'").fetchall())

    def test_completed_snapshot_never_discards_a_new_capture_or_resurrects_deletion(self):
        for mutation in ("capture", "delete", "scope", "disable"):
            with self.subTest(mutation=mutation):
                self.store.configure(self.config)
                self.add_manifests(1)
                progress = self.store.rebuild(limit=1)["rebuild_progress"]
                self.assertFalse(progress["complete"])
                if mutation == "capture":
                    new = self.store.capture("different guide", self.store.now().isoformat(),
                                             [{**self.source, "content": "Newest public evidence."}], 3)["sources"][0]
                elif mutation == "delete":
                    self.store.delete_capture(self.original["capture_id"])
                elif mutation == "scope":
                    self.store.configure({**self.config, "capture_policy": "approved_sources", "public_sources": ["https://other.example.org/"]})
                else:
                    self.store.configure({**self.config, "enabled": False})
                self.assertIsNone(self.store.status()["rebuild_progress"])
                self.assertFalse(self.store._rebuild_path.exists())
                with self.assertRaises(ValueError):
                    self.store.rebuild(limit=1, continuation=progress["id"])
                if mutation == "capture":
                    self.assertEqual(self.store.get_capture(new["capture_id"]), new)
                elif mutation in ("delete", "scope", "disable"):
                    with self.assertRaises(KeyError):
                        self.store.get_capture(self.original["capture_id"])

    def test_external_capture_arrival_rejects_final_replacement_without_losing_old_index(self):
        self.add_manifests(1)
        old = self.store.index.read_bytes()
        progress = self.store.rebuild(limit=1)["rebuild_progress"]
        self.add_manifests(1)
        with self.assertRaisesRegex(ValueError, "archive changed"):
            self.store.rebuild(limit=5000, continuation=progress["id"])
        self.assertEqual(self.store.index.read_bytes(), old)
        self.assertIsNone(self.store.status()["rebuild_progress"])
        self.assertEqual(self.store.rebuild()["indexed_count"], 3)

    def test_interrupted_rebuild_resumes_without_client_token_and_repairs_corrupt_index(self):
        self.add_manifests(1)
        self.store.index.write_bytes(b"Synthetic corrupt disposable index")
        progress = self.store.rebuild(limit=1)["rebuild_progress"]
        self.assertFalse(progress["complete"])
        restarted = ContextStore(self.database, self.archive)
        result = restarted.rebuild(limit=1)
        self.assertTrue(result["rebuild_progress"]["complete"])
        self.assertEqual(result["rebuild_progress"]["id"], progress["id"])
        self.assertEqual(result["indexed_count"], 2)

    def test_work_deadline_still_advances_one_capture_and_wrong_token_leaves_progress(self):
        self.add_manifests(2)
        with patch("backend.context_store.REBUILD_SECONDS", 0):
            progress = self.store.rebuild(limit=1000)["rebuild_progress"]
        self.assertEqual(progress["processed"], 1)
        with self.assertRaisesRegex(ValueError, "archive changed"):
            self.store.rebuild(continuation="0" * 32)
        self.assertEqual(self.store.status()["rebuild_progress"], progress)
        self.assertTrue(self.store.rebuild(continuation=progress["id"])["rebuild_progress"]["complete"])


class ContextRebuildAPITests(unittest.TestCase):
    def setUp(self):
        root = Path(self.enterContext(tempfile.TemporaryDirectory(prefix="maestro-rebuild-api-")))
        (root / "private").mkdir()
        self.enterContext(patch.object(backend, "DATABASE", root / "private" / "workspace.sqlite3"))
        self.enterContext(patch.dict("os.environ", {"MAESTRO_CONTEXT_ARCHIVE_DIR": str(root / "public")}))
        self.enterContext(patch.object(credentials, "read", return_value=("synthetic-search-key", "synthetic")))
        self.client = TestClient(backend.app)
        self.addCleanup(self.client.close)
        self.headers = {"X-Maestro-CSRF": self.client.get("/api/session").json()["csrf"]}
        self.client.get("/api/workspace")
        self.store = backend.context_store()
        self.store.configure({**DEFAULT, "enabled": True})
        source = {"title": "Guide", "url": "https://docs.example.org/", "content": "Public guide."}
        for index in range(2):
            self.store.capture("guide " + str(index), self.store.now().isoformat(), [source], 3)

    def test_api_treats_limit_as_batch_and_pins_continuation(self):
        result = self.client.post("/api/context/rebuild", headers=self.headers, json={"limit": 1})
        self.assertEqual(result.status_code, 200, result.text)
        progress = result.json()["rebuild_progress"]
        self.assertEqual((progress["processed"], progress["total"], progress["complete"]), (1, 2, False))
        invalid = self.client.post("/api/context/rebuild", headers=self.headers, json={"limit": 1, "continuation": "0" * 32})
        self.assertEqual(invalid.status_code, 409)
        valid = self.client.post("/api/context/rebuild", headers=self.headers, json={"limit": 1, "continuation": progress["id"]})
        self.assertEqual(valid.status_code, 200, valid.text)
        self.assertTrue(valid.json()["rebuild_progress"]["complete"])
        for data in ({"limit": 0}, {"limit": 5001}, {"continuation": "../index.sqlite3"}):
            self.assertEqual(self.client.post("/api/context/rebuild", headers=self.headers, json=data).status_code, 422)

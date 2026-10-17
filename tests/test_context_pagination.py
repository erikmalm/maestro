"""Seek pagination uses private metadata indexes, never archived source bodies."""
import base64
from contextlib import closing
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from backend.context_policy import DEFAULT, MANIFEST_BYTES, SCHEMA, manifest_hash
from backend.context_store import ContextStore


class ContextPaginationTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory(prefix="maestro-pagination-")))
        self.store = ContextStore(self.root / "private" / "workspace.sqlite3", self.root / "public")
        self.config = {**DEFAULT, "enabled": True}
        self.manifests = {}

    def seed_catalogue(self, count):
        """Bulk private metadata only; no per-source files or hydrated objects."""
        self.store.configure(self.config)
        observed = datetime(2026, 10, 16, 9, tzinfo=timezone.utc)
        url = "https://docs.example.org/catalogue"
        content_hash = hashlib.sha256(b"x").hexdigest()
        rows = []
        for number in range(count):
            capture_id = f"{number:032x}"
            at = (observed + timedelta(seconds=number // 100)).isoformat()
            relative = f"records/captures/{at[:7]}/{capture_id}.json"
            manifest = {
                "schema_version": SCHEMA, "capture_id": capture_id,
                "source_id": hashlib.sha256(url.encode()).hexdigest(),
                "source_url": url, "title": f"Synthetic source {number}",
                "object": {"path": f"objects/sha256/{content_hash[:2]}/{content_hash}.txt",
                           "sha256": content_hash, "bytes": 1, "encoding": "utf-8"},
                "retrieved_at": at, "content_kind": "search_excerpt",
                "completeness": {"maestro_truncated": False, "full_page": False},
                "published_at": None, "modified_at": None,
            }
            self.manifests[relative] = manifest
            rows.append((capture_id, relative, manifest["title"], url,
                         "Body must not be loaded by inventory", at, manifest_hash(manifest)))
        with closing(self.store._index_connection()) as db, db:
            db.executemany("INSERT INTO captures VALUES(?,?,?,?,?,?,?)", rows)
        return sorted(rows, key=lambda row: (row[5], row[0]), reverse=True)

    def rows(self):
        with closing(sqlite3.connect(self.store.index)) as db:
            return db.execute("SELECT * FROM captures ORDER BY id").fetchall()

    def index_names(self):
        with closing(sqlite3.connect(self.store.index)) as db:
            return {row[1] for row in db.execute("PRAGMA index_list(captures)")}

    def make_legacy_index(self):
        with closing(sqlite3.connect(self.store.index)) as db, db:
            db.execute("DROP INDEX captures_by_retrieval")

    def measure_page(self, cursor=None):
        """Measure the actual production SELECT, including its own work guard."""
        connect = sqlite3.connect
        measurement = {"vm_steps": 0, "sql": [], "manifest_reads": 0}

        class MeteredConnection(sqlite3.Connection):
            def set_progress_handler(connection, callback, interval):
                def counted():
                    measurement["vm_steps"] += interval
                    return callback()
                return super().set_progress_handler(counted, interval)

        def readonly_connect(database, **kwargs):
            self.assertIn("?mode=ro", database)
            self.assertTrue(kwargs.get("uri"))
            if database == self.store.index.as_uri() + "?mode=ro":
                db = connect(database, factory=MeteredConnection, **kwargs)
                db.set_trace_callback(measurement["sql"].append)
                return db
            return connect(database, **kwargs)

        def read_manifest(archive, relative, limit):
            self.assertEqual(archive, self.store.archive)
            self.assertEqual(limit, MANIFEST_BYTES)
            measurement["manifest_reads"] += 1
            return self.manifests[relative]

        with (patch("backend.context_store.sqlite3.connect", side_effect=readonly_connect),
              patch("backend.context_store.read_archive_json", side_effect=read_manifest),
              patch("backend.context_store.read_archive", side_effect=AssertionError("Source body read")),
              patch.object(self.store, "get_capture", side_effect=AssertionError("Source body verification")),
              patch.object(self.store, "_index_connection", side_effect=AssertionError("Read-side migration"))):
            result = self.store.list_captures(limit=5, cursor=cursor)
        self.assertEqual(result["state"], "ready", result)
        self.assertEqual(measurement["manifest_reads"], len(result["sources"]))
        self.assertTrue(all(not source["body_verified"] for source in result["sources"]))
        self.assertEqual(len(measurement["sql"]), 1)
        sql = measurement["sql"][0]
        self.assertNotIn("content", sql.split("FROM", 1)[0].lower())
        with closing(connect(self.store.index.as_uri() + "?mode=ro", uri=True)) as db:
            measurement["plan"] = [row[3] for row in db.execute("EXPLAIN QUERY PLAN " + sql)]
        return result, measurement

    def test_first_and_deep_pages_seek_with_bounded_vm_work_on_large_catalogue(self):
        ordered = self.seed_catalogue(10000)
        before = (self.store.database.read_bytes(), self.store.index.read_bytes())
        first, first_work = self.measure_page()
        pivot = ordered[8999]
        cursor = base64.urlsafe_b64encode(json.dumps([pivot[5], pivot[0]]).encode()).decode().rstrip("=")
        deep, deep_work = self.measure_page(cursor)
        self.assertEqual([source["capture_id"] for source in first["sources"]],
                         [row[0] for row in ordered[:5]])
        self.assertEqual([source["capture_id"] for source in deep["sources"]],
                         [row[0] for row in ordered[9000:9005]])
        for work in (first_work, deep_work):
            self.assertTrue(any("captures_by_retrieval" in step for step in work["plan"]), work)
            self.assertFalse(any("TEMP B-TREE" in step.upper() for step in work["plan"]), work)
            self.assertLessEqual(work["vm_steps"], 5000, work)
        self.assertTrue(any("SEARCH" in step.upper() for step in deep_work["plan"]), deep_work)
        self.assertLessEqual(deep_work["vm_steps"], first_work["vm_steps"] + 3000)
        self.assertEqual((self.store.database.read_bytes(), self.store.index.read_bytes()), before)
        self.assertFalse((self.store.archive / "objects").exists())
        self.assertFalse((self.store.archive / "records").exists())

    def test_legacy_index_is_not_scanned_or_repaired_until_explicit_configuration(self):
        self.seed_catalogue(3)
        self.make_legacy_index()
        records = self.rows()
        before = (self.store.database.read_bytes(), self.store.index.read_bytes())
        with (patch.object(self.store, "_index_connection", side_effect=AssertionError("Read-side repair")),
              patch("backend.context_store.read_archive_json", side_effect=AssertionError("Legacy scan"))):
            self.assertEqual(self.store.list_captures()["state"], "unavailable")
        self.assertEqual((self.store.database.read_bytes(), self.store.index.read_bytes()), before)
        self.assertNotIn("captures_by_retrieval", self.index_names())
        self.store.configure({**self.config, "enabled": False})
        self.assertNotIn("captures_by_retrieval", self.index_names())
        self.assertEqual(self.store.list_captures()["state"], "disabled")
        self.assertEqual(self.rows(), records)
        self.store.configure(self.config)
        self.assertIn("captures_by_retrieval", self.index_names())
        self.assertEqual(self.rows(), records)
        self.measure_page()

    def test_explicit_initialization_and_capture_upgrade_existing_index_without_losing_records(self):
        self.seed_catalogue(3)
        self.make_legacy_index()
        records = self.rows()
        restarted = ContextStore(self.store.database, self.store.archive)
        restarted.initialize()
        self.assertIn("captures_by_retrieval", self.index_names())
        self.assertEqual(self.rows(), records)
        self.make_legacy_index()
        saved = self.store.capture("Synthetic capture", "2026-10-16T12:00:00+00:00", [{
            "title": "New capture", "url": "https://docs.example.org/new",
            "content": "A newly acquired synthetic excerpt.",
        }], 1)["sources"][0]
        self.assertEqual(saved["archive_status"], "saved")
        self.assertIn("captures_by_retrieval", self.index_names())
        self.assertEqual([row for row in self.rows() if row[0] != saved["capture_id"]], records)

    def test_explicit_empty_setup_creates_index_but_inventory_never_repairs_its_loss(self):
        self.store.configure(self.config)
        self.assertTrue(self.store.index.is_file())
        self.assertEqual(self.store.list_captures()["state"], "ready")
        self.store.index.unlink()
        self.assertFalse(self.store.index.exists())
        before = self.store.database.read_bytes()
        self.assertEqual(self.store.list_captures()["state"], "unavailable")
        self.assertFalse(self.store.index.exists())
        self.assertEqual(self.store.database.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()

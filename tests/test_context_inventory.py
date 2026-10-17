"""Read-only inventory fixtures; no live archive, hosted searches or model calls."""
import base64
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from backend.context_policy import DEFAULT, MANIFEST_BYTES
from backend.context_store import ContextStore
from backend import storage


class ContextInventoryTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory(prefix="maestro-inventory-test-")))
        self.archive = self.root / "public"
        self.database = self.root / "private" / "workspace.sqlite3"
        self.store = ContextStore(self.database, self.archive)
        self.config = {**DEFAULT, "enabled": True, "capture_policy": "approved_sources",
                       "public_sources": ["https://docs.example.org/docs/"]}
        self.stamp = "2026-10-16T09:00:00+00:00"

    def seed(self, count=1):
        self.store.configure(self.config)
        sources = []
        for start in range(0, count, 3):
            batch = [{"title": f"Synthetic source {number}", "url": f"https://docs.example.org/docs/{number}",
                      "content": f"Synthetic body {number}; never read during inventory."}
                     for number in range(start, min(start + 3, count))]
            sources.extend(self.store.capture("Synthetic public sources", self.stamp, batch, 3)["sources"])
        return sorted(sources, key=lambda source: (source["retrieved_at"], source["capture_id"]), reverse=True)

    def snapshot(self):
        return {str(path.relative_to(self.root)): path.read_bytes()
                for path in self.root.rglob("*") if path.is_file()}

    def manifest_path(self, source):
        return self.archive / "records" / "captures" / self.stamp[:7] / (source["capture_id"] + ".json")

    def mark_deleted(self, source):
        marker = self.archive / "records" / "deletions" / (source["capture_id"] + ".json")
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text("{}", encoding="utf-8")

    def test_disabled_and_unconfigured_reads_create_nothing(self):
        self.assertEqual(self.store.list_captures(),
                         {"state": "disabled", "sources": [], "next_cursor": None, "skipped_count": 0})
        unconfigured = ContextStore(self.database, archive_dir="")
        self.assertEqual(unconfigured.list_captures()["state"], "unconfigured")
        self.assertFalse(self.database.parent.exists())
        self.assertFalse(self.archive.exists())

    def test_missing_index_is_unavailable_but_existing_empty_index_is_ready(self):
        self.store.configure(self.config)
        before = self.snapshot()
        result = self.store.list_captures()
        self.assertEqual(result["state"], "unavailable")
        self.assertIn("message", result)
        self.assertFalse(self.store.index.exists())
        self.assertEqual(self.snapshot(), before)
        with closing(self.store._index_connection()):
            pass
        self.assertEqual(self.store.list_captures(),
                         {"state": "ready", "sources": [], "next_cursor": None, "skipped_count": 0})

    def test_inventory_projects_pinned_metadata_using_only_selects_and_manifest_reads(self):
        source = self.seed()[0]
        before = self.snapshot()
        connect, read = sqlite3.connect, storage.read_archive
        statements, reads = [], []

        def readonly_connect(database, **kwargs):
            self.assertIn("?mode=ro", database)
            self.assertTrue(kwargs.get("uri"))
            connection = connect(database, **kwargs)
            connection.set_trace_callback(statements.append)
            return connection

        def metadata_read(root, relative, limit):
            self.assertTrue(relative.startswith("records/captures/"))
            self.assertEqual(limit, MANIFEST_BYTES)
            reads.append(relative)
            return read(root, relative, limit)

        with (patch("backend.context_store.sqlite3.connect", side_effect=readonly_connect),
              patch("backend.storage.read_archive", side_effect=metadata_read),
              patch.object(self.store, "_manifest", side_effect=AssertionError("Body read")),
              patch.object(self.store, "initialize", side_effect=AssertionError("Initialization")),
              patch.object(self.store, "_index_connection", side_effect=AssertionError("Index write"))):
            result = self.store.list_captures()
        self.assertEqual(result["state"], "ready")
        self.assertEqual(len(reads), 1)
        self.assertTrue(statements)
        self.assertTrue(all(statement.lstrip().upper().startswith("SELECT ") for statement in statements))
        self.assertFalse(any("SELECT CONTENT" in statement.upper() for statement in statements))
        metadata = result["sources"][0]
        expected = {field: source[field] for field in (
            "capture_id", "title", "url", "retrieved_at", "published_at", "modified_at",
            "content_kind", "completeness", "content_hash", "manifest_hash")}
        self.assertEqual(metadata, {**expected, "content_bytes": len(source["content"].encode()), "body_verified": False})
        self.assertNotIn("content", metadata)
        self.assertEqual(self.snapshot(), before)

    def test_missing_body_does_not_turn_metadata_into_verified_content(self):
        source = self.seed()[0]
        content = next(self.archive.glob("objects/sha256/*/*.txt"))
        content.unlink()
        result = self.store.list_captures()
        self.assertEqual(result["sources"][0]["capture_id"], source["capture_id"])
        self.assertFalse(result["sources"][0]["body_verified"])
        with self.assertRaises(KeyError):
            self.store.get_capture(source["capture_id"], source["content_hash"], source["manifest_hash"])

    def test_pagination_preserves_tied_dates_and_pinned_source_opening(self):
        expected = self.seed(7)
        cursor, actual = None, []
        for _ in range(5):
            page = self.store.list_captures(limit=2, cursor=cursor)
            self.assertEqual(page["state"], "ready")
            self.assertEqual(page["skipped_count"], 0)
            self.assertLessEqual(len(page["sources"]), 2)
            actual.extend(page["sources"])
            cursor = page["next_cursor"]
            if cursor is None:
                break
        self.assertIsNone(cursor)
        self.assertEqual([source["capture_id"] for source in actual], [source["capture_id"] for source in expected])
        self.assertEqual(len({source["capture_id"] for source in actual}), 7)
        for source in actual:
            opened = self.store.get_capture(source["capture_id"], source["content_hash"], source["manifest_hash"])
            self.assertEqual(opened["retrieved_at"], self.stamp)

    def test_scan_budget_returns_progress_even_when_an_eligible_page_is_empty(self):
        sources = self.seed(5)
        for source in sources[:4]:
            self.mark_deleted(source)
        with patch("backend.context_store.INVENTORY_SCAN_LIMIT", 2):
            first = self.store.list_captures()
            second = self.store.list_captures(cursor=first["next_cursor"])
            third = self.store.list_captures(cursor=second["next_cursor"])
        for page in (first, second):
            self.assertEqual(page["state"], "ready")
            self.assertEqual(page["sources"], [])
            self.assertEqual(page["skipped_count"], 2)
            self.assertIsNotNone(page["next_cursor"])
        self.assertNotEqual(first["next_cursor"], second["next_cursor"])
        self.assertEqual([source["capture_id"] for source in third["sources"]], [sources[-1]["capture_id"]])
        self.assertIsNone(third["next_cursor"])

    def test_revoked_and_deleted_captures_are_skipped_without_changing_them(self):
        sources = self.seed(2)
        self.mark_deleted(sources[0])
        narrowed = {**self.config, "public_sources": ["https://docs.example.org/other/"]}
        self.store.configure(narrowed)
        before = self.snapshot()
        self.assertEqual(self.store.list_captures(),
                         {"state": "ready", "sources": [], "next_cursor": None, "skipped_count": 2})
        self.assertEqual(self.snapshot(), before)

    def test_changed_manifest_and_index_metadata_pins_are_skipped(self):
        source = self.seed()[0]
        path = self.manifest_path(source)
        original = path.read_bytes()
        manifest = json.loads(original)
        manifest["title"] = "Changed source title"
        path.write_text(json.dumps(manifest), encoding="utf-8")
        self.assertEqual(self.store.list_captures()["skipped_count"], 1)
        path.write_bytes(original)
        for field, changed in (("title", "Changed index title"), ("url", "https://docs.example.org/docs/changed"),
                               ("at", "2026-10-17T09:00:00+00:00"), ("manifest_hash", "0" * 64)):
            with self.subTest(field=field):
                with closing(sqlite3.connect(self.store.index)) as db, db:
                    saved = db.execute(f"SELECT {field} FROM captures").fetchone()[0]
                    db.execute(f"UPDATE captures SET {field}=?", (changed,))
                self.assertEqual(self.store.list_captures(),
                                 {"state": "ready", "sources": [], "next_cursor": None, "skipped_count": 1})
                with closing(sqlite3.connect(self.store.index)) as db, db:
                    db.execute(f"UPDATE captures SET {field}=?", (saved,))

    def test_redirected_index_manifest_paths_are_skipped_before_any_archive_read(self):
        redirected, legitimate = self.seed(2)
        legitimate_path = self.manifest_path(legitimate).relative_to(self.archive).as_posix()
        object_path = ("objects/sha256/" + redirected["content_hash"][:2] + "/"
                       + redirected["content_hash"] + ".txt")
        for wrong_path in (object_path, legitimate_path):
            with self.subTest(wrong_path=wrong_path):
                with closing(sqlite3.connect(self.store.index)) as db, db:
                    db.execute("UPDATE captures SET manifest=? WHERE id=?", (wrong_path, redirected["capture_id"]))
                with (patch("backend.context_store.read_archive_json", wraps=storage.read_archive_json) as json_read,
                      patch("backend.storage.read_archive", wraps=storage.read_archive) as archive_read):
                    result = self.store.list_captures()
                self.assertEqual(result["state"], "ready")
                self.assertEqual(result["skipped_count"], 1)
                self.assertEqual([source["capture_id"] for source in result["sources"]], [legitimate["capture_id"]])
                expected_read = (self.archive, legitimate_path, MANIFEST_BYTES)
                self.assertEqual([call.args for call in json_read.call_args_list], [expected_read])
                self.assertEqual([call.args for call in archive_read.call_args_list], [expected_read])

    def test_missing_oversized_and_invalid_manifests_are_skipped(self):
        source = self.seed()[0]
        path = self.manifest_path(source)
        for data in (b"{", b" " * (MANIFEST_BYTES + 1), b"[]"):
            with self.subTest(data_length=len(data)):
                path.write_bytes(data)
                self.assertEqual(self.store.list_captures()["skipped_count"], 1)
        path.unlink()
        self.assertEqual(self.store.list_captures()["skipped_count"], 1)

    def test_archive_or_index_failure_returns_fixed_unavailable_without_repair(self):
        self.seed()
        self.store.index.write_bytes(b"Invalid synthetic SQLite")
        before = self.snapshot()
        result = self.store.list_captures()
        self.assertEqual(result["state"], "unavailable")
        self.assertEqual(result["sources"], [])
        self.assertNotIn(str(self.root), result["message"])
        self.assertEqual(self.snapshot(), before)
        self.store.archive = self.root / "missing-archive"
        self.assertEqual(self.store.list_captures(), result)
        self.assertFalse(self.store.archive.exists())

    def test_writer_unavailability_does_not_disable_valid_read_only_inventory(self):
        self.seed()
        self.store.record_error()
        self.assertEqual(self.store.list_captures()["state"], "ready")

    def test_invalid_limits_and_cursors_are_rejected_before_storage_access(self):
        for limit in (True, 0, -1, 51, 1.5, "2", None):
            with self.subTest(limit=limit), self.assertRaisesRegex(ValueError, "one to fifty"):
                self.store.list_captures(limit=limit)

        def encoded(value):
            return base64.urlsafe_b64encode(json.dumps(value).encode()).decode().rstrip("=")

        invalid = ("", "!", "a" * 161, 12, False, encoded([]), encoded({"at": self.stamp}),
                   encoded([self.stamp, "z" * 32]), encoded(["2026-10-16", "0" * 32]),
                   encoded(["2026-10-16T09:00:00Z", "0" * 32]), encoded([self.stamp, "0" * 64]))
        for cursor in invalid:
            with self.subTest(cursor=cursor), self.assertRaisesRegex(ValueError, "page cursor"):
                self.store.list_captures(cursor=cursor)
        self.assertFalse(self.database.parent.exists())
        self.assertFalse(self.archive.exists())


if __name__ == "__main__":
    unittest.main()

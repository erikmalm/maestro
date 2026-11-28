"""Complete bounded synthetic source content; no hosted calls or personal data."""
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
import hashlib
import sqlite3
import tempfile
import time
import unittest
from unittest.mock import patch

from backend.context_policy import DEFAULT, SOURCE_BYTES
from backend.context_store import (CANDIDATE_BODY_BYTES, CAPACITY_UNKNOWN_WARNING, CAPACITY_WARNING,
                                   CAPTURE_LIMIT_WARNING, LARGE_SOURCE_WARNING, SOURCE_LIMIT_WARNING,
                                   CandidateWork, ContextStore)


class ContextContentBoundsTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory(prefix="maestro-content-bounds-")))
        self.store = ContextStore(self.root / "private" / "workspace.sqlite3", self.root / "public")
        self.config = {**DEFAULT, "enabled": True, "capture_policy": "all_public", "public_sources": []}
        self.source = {"title": "Synthetic bounded source", "url": "https://docs.example.org/public",
                       "content": "Synthetic public evidence."}

    def enable(self, **changes):
        self.config = {**self.config, **changes}
        self.store.configure(self.config)

    def capture(self, sources=None, **kwargs):
        return self.store.capture("Synthetic evidence", self.store.now().isoformat(),
                                  [self.source] if sources is None else sources, 3, **kwargs)

    def manifests(self):
        return list(self.store.archive.glob("records/captures/*/*.json"))

    def snapshot(self):
        return {str(path.relative_to(self.root)): path.read_bytes() for path in self.root.rglob("*") if path.is_file()}

    def test_complete_large_utf8_source_is_saved_pinned_and_warned_without_an_error(self):
        self.enable()
        content = "\u03b1" * (self.config["warn_source_bytes"] // 2) + " evidence beyond the old excerpt limit"
        source = self.capture([{**self.source, "content": content}])["sources"][0]
        self.assertEqual(source["content"], content)
        self.assertEqual(source["content_hash"], hashlib.sha256(content.encode()).hexdigest())
        self.assertEqual(source["completeness"], {"maestro_truncated": False, "full_page": False})
        self.assertEqual(self.store.get_capture(source["capture_id"], source["content_hash"], source["manifest_hash"]), source)
        status = self.store.status()
        self.assertEqual(status["last_capture"]["warnings"], [LARGE_SOURCE_WARNING])
        self.assertEqual(status["last_capture"]["sources_refused"], 0)
        self.assertIsNone(status["last_error"])

    def test_fixed_read_ceiling_preserves_old_and_larger_pins_after_lowering_write_limits(self):
        self.enable()
        legacy = self.capture([{**self.source, "content": "L" * 8192, "maestro_truncated": True}])["sources"][0]
        large = self.capture([{**self.source, "content": "N" * SOURCE_BYTES}])["sources"][0]
        before = {str(path): path.read_bytes() for path in self.store.archive.rglob("*") if path.is_file()}
        self.enable(max_source_bytes=8192, warn_source_bytes=1024)
        for source in (legacy, large):
            self.assertEqual(self.store.get_capture(source["capture_id"], source["content_hash"], source["manifest_hash"]), source)
        self.assertEqual({str(path): path.read_bytes() for path in self.store.archive.rglob("*") if path.is_file()}, before)
        refused = self.capture([{**self.source, "content": "R" * 8193}])
        self.assertEqual(refused["sources"], [])
        self.assertEqual(self.store.status()["last_capture"]["warnings"], [SOURCE_LIMIT_WARNING])
        self.assertEqual(len(self.manifests()), 2)

    def test_transient_oversized_source_is_refused_before_encoding_or_redaction(self):
        class UnprocessedText(str):
            def encode(self, *args, **kwargs):
                raise AssertionError("Oversized source was encoded")

            def replace(self, *args, **kwargs):
                raise AssertionError("Oversized source was redacted")

        result = self.capture([{**self.source, "content": UnprocessedText("x" * (SOURCE_BYTES + 1))}], secrets=("x",))
        self.assertEqual(result["sources"], [])
        self.assertEqual(result["archive_warning"], SOURCE_LIMIT_WARNING)
        self.assertFalse(self.store.database.exists())
        self.assertFalse(self.store.archive.exists())

    def test_utf8_byte_limit_refuses_whole_source_without_damaging_a_saved_version(self):
        self.enable(max_source_bytes=8192, warn_source_bytes=8192)
        saved = self.capture([{**self.source, "content": "\u00e9" * 4096}])["sources"][0]
        result = self.capture([{**self.source, "content": "\u00e9" * 4097}])
        self.assertEqual(result["sources"], [])
        self.assertEqual(self.store.status()["last_capture"]["sources_refused"], 1)
        self.assertEqual(self.store.get_capture(saved["capture_id"]), saved)
        self.assertEqual(len(self.manifests()), 1)

    def test_aggregate_capture_limit_refuses_a_whole_source_and_retains_later_fitting_sources(self):
        self.enable(max_source_bytes=8192, warn_source_bytes=8192, max_capture_bytes=10000)
        result = self.capture([{**self.source, "content": "a" * 6000},
                               {**self.source, "content": "b" * 5000},
                               {**self.source, "content": "c" * 4000}])
        self.assertEqual([source["content"] for source in result["sources"]], ["a" * 6000, "c" * 4000])
        self.assertTrue(all(source["archive_status"] == "saved" for source in result["sources"]))
        measured = self.store.status()["last_capture"]
        self.assertEqual((measured["sources_received"], measured["sources_saved"], measured["sources_refused"]), (3, 2, 1))
        self.assertEqual(measured["warnings"], [CAPTURE_LIMIT_WARNING])
        self.assertEqual(measured["excerpt_bytes"], 10000)

    def test_disabled_archive_still_enforces_saved_configured_aggregate_bounds(self):
        self.enable(enabled=False, max_source_bytes=8192, warn_source_bytes=8192, max_capture_bytes=10000)
        before = self.snapshot()
        result = self.capture([{**self.source, "content": "a" * 6000}, {**self.source, "content": "b" * 5000}])
        self.assertEqual([source["content"] for source in result["sources"]], ["a" * 6000])
        self.assertEqual(result["archive_warning"], CAPTURE_LIMIT_WARNING)
        self.assertEqual(self.snapshot(), before)

    def test_redacted_copy_must_fit_its_byte_limit_too(self):
        self.enable(max_source_bytes=8192, warn_source_bytes=8192)
        result = self.capture([{**self.source, "content": "q" * 8000}], secrets=("q",))
        self.assertEqual(result["sources"], [])
        self.assertEqual(self.store.status()["last_capture"]["sources_refused"], 1)
        self.assertEqual(self.manifests(), [])

    def test_credentials_after_previous_screening_boundary_are_checked_in_complete_content(self):
        self.enable()
        secret = "synthetic-late-credential"
        prefix = "Public evidence. " * 9000
        saved = self.capture([{**self.source, "content": prefix + secret}], secrets=(secret,))["sources"][0]
        self.assertEqual(saved["content"], prefix + "\u2588")
        encoded = "".join("%" + format(ord(character), "02x") for character in secret)
        result = self.capture([{**self.source, "content": prefix + encoded}], secrets=(secret,))
        self.assertEqual(result["sources"], [])
        self.assertEqual(len(self.manifests()), 1)

    def test_capacity_reservations_add_archive_and_private_growth_on_one_device(self):
        self.enable()
        minimum = self.config["min_free_bytes"]
        # Either reservation fits alone, but their sum must retain the floor.
        with patch("backend.context_store.shutil.disk_usage", return_value=SimpleNamespace(free=minimum + 299)):
            self.assertEqual(self.store._capture_space(100, 200, minimum), "insufficient")
        with patch("backend.context_store.shutil.disk_usage", return_value=SimpleNamespace(free=minimum + 300)):
            self.assertIsNone(self.store._capture_space(100, 200, minimum))

    def test_capacity_reservations_apply_the_floor_independently_to_distinct_devices(self):
        self.enable()
        original = Path.stat

        def stat(path, *args, **kwargs):
            value = original(path, *args, **kwargs)
            if path in (self.store.archive, self.store.index.parent):
                return SimpleNamespace(st_mode=value.st_mode, st_dev=1 if path == self.store.archive else 2)
            return value

        with patch.object(Path, "stat", stat), patch("backend.context_store.shutil.disk_usage",
                                                     return_value=SimpleNamespace(free=self.config["min_free_bytes"] + 200)):
            self.assertIsNone(self.store._capture_space(100, 200, self.config["min_free_bytes"]))

    def test_low_private_space_and_unknown_capacity_refuse_before_publication(self):
        self.enable()
        saved = self.capture()["sources"][0]
        for unknown in (False, True):
            before = self.snapshot()

            def usage(path):
                if path == self.store.archive:
                    return SimpleNamespace(free=10 ** 12)
                if unknown:
                    raise OSError("Synthetic private disk is unavailable")
                return SimpleNamespace(free=self.config["min_free_bytes"] - 1)

            with self.subTest(unknown=unknown), patch("backend.context_store.shutil.disk_usage", side_effect=usage), \
                    patch.object(self.store, "_index_capture", side_effect=AssertionError("Indexed refused source")):
                result = self.capture([{**self.source, "content": "A new unsaved version."}])
            self.assertEqual(result["sources"][0]["archive_status"], "not_saved")
            measured = self.store.status()["last_capture"]
            self.assertEqual((measured["sources_saved"], measured["sources_refused"]), (0, 1))
            self.assertEqual(measured["warnings"], [CAPACITY_UNKNOWN_WARNING if unknown else CAPACITY_WARNING])
            self.assertEqual(self.store.get_capture(saved["capture_id"]), saved)
            # Only private diagnostic state may change on refusal.
            self.assertEqual({path: data for path, data in self.snapshot().items() if path.startswith("public")},
                             {path: data for path, data in before.items() if path.startswith("public")})

    def test_deduplicated_archive_object_still_requires_private_index_growth_space(self):
        self.enable()
        source = {**self.source, "content": "Synthetic evidence. " * 5000}
        saved = self.capture([source])["sources"][0]
        before = len(self.manifests())
        # Much more than a new manifest, but less than conservative private
        # catalogue/FTS/journal growth for another observation of the same body.
        free = self.config["min_free_bytes"] + len(source["content"].encode()) * 8 - 1
        with patch("backend.context_store.shutil.disk_usage", return_value=SimpleNamespace(free=free)):
            result = self.capture([source])
        self.assertEqual(result["sources"][0]["archive_status"], "not_saved")
        self.assertEqual(len(self.manifests()), before)
        self.assertEqual(self.store.get_capture(saved["capture_id"]), saved)

    def test_uninspectable_private_index_directory_fails_capacity_check_closed(self):
        self.enable()
        original = Path.stat

        def stat(path, *args, **kwargs):
            if path == self.store.index.parent:
                raise PermissionError("Synthetic uninspectable private directory")
            return original(path, *args, **kwargs)

        with patch.object(Path, "stat", stat):
            self.assertEqual(self.store._capture_space(100, 200, self.config["min_free_bytes"]), "unknown")

    def test_candidate_byte_budget_is_reserved_before_loading_a_body(self):
        self.enable()
        body = "Synthetic complete body. " * 3000
        for number in range(3):
            self.capture([{**self.source, "content": body[:-1] + str(number)}])
        size = len(body.encode())
        before = self.snapshot()
        original = self.store._manifest
        with patch("backend.context_store.CANDIDATE_BODY_BYTES", 2 * size), \
                patch.object(self.store, "_manifest", wraps=original) as load:
            result = self.store.lookup_candidates(limit=20)
        self.assertEqual((result["state"], result["search_limited"], len(result["candidates"])), ("limited", True, 2))
        self.assertEqual(load.call_count, 2)
        self.assertEqual(self.snapshot(), before)

    def test_candidate_body_allowance_is_shared_across_queries(self):
        self.enable()
        body = "Synthetic complete body. " * 3000
        self.capture([{**self.source, "content": body}])
        size = len(body.encode())
        work = CandidateWork()
        work.deadline = time.monotonic() + 30
        self.assertEqual(CANDIDATE_BODY_BYTES, 2 * 1024 * 1024)
        with patch("backend.context_store.CANDIDATE_BODY_BYTES", 2 * size):
            first = self.store.lookup_candidates(limit=1, work=work)
            second = self.store.lookup_candidates("complete", limit=1, work=work)
            with patch.object(self.store, "_manifest", side_effect=AssertionError("Body read after shared byte limit")):
                third = self.store.lookup_candidates(limit=1, work=work)
        self.assertEqual(len(first["candidates"]), 1)
        self.assertEqual(len(second["candidates"]), 1)
        self.assertEqual(work.body_bytes, 2 * size)
        self.assertEqual((third["state"], third["search_limited"], third["candidates"]), ("limited", True, []))

    def test_mismatched_declared_index_length_is_rejected_before_object_read(self):
        self.enable()
        self.capture()
        with closing(sqlite3.connect(self.store.index)) as db, db:
            db.execute("UPDATE captures SET content='different length'")
        work = CandidateWork()
        with patch.object(self.store, "_manifest", side_effect=AssertionError("Read an object with mismatched declared length")):
            result = self.store.lookup_candidates(work=work)
        self.assertEqual(result["candidates"], [])
        self.assertEqual(result["skipped_count"], 1)
        self.assertEqual(work.body_bytes, 0)

    def test_enlarged_corrupt_objects_cannot_exceed_the_reserved_read_allowance(self):
        self.enable()
        objects = set()
        for number in range(9):
            saved = self.capture([{**self.source, "content": f"item{number}"}])["sources"][0]
            digest = saved["content_hash"]
            path = self.store.archive / "objects" / "sha256" / digest[:2] / (digest + ".txt")
            path.write_bytes(path.read_bytes() + b"x" * (SOURCE_BYTES - 5))
            objects.add(path)

        # Count bytes consumed by the actual file stream: read_archive raises
        # on oversized objects, so counting only successful returns misses them.
        opened, reads = Path.open, []

        class TrackedReader:
            def __init__(self, stream):
                self.stream = stream

            def __enter__(self):
                return self

            def __exit__(self, *error):
                return self.stream.__exit__(*error)

            def read(self, size=-1):
                value = self.stream.read(size)
                reads.append((size, len(value)))
                return value

        def tracked_open(path, *args, **kwargs):
            stream = opened(path, *args, **kwargs)
            return TrackedReader(stream) if path in objects else stream

        work = CandidateWork()
        with patch.object(Path, "open", tracked_open):
            result = self.store.lookup_candidates(work=work)
        self.assertEqual((result["candidates"], result["skipped_count"]), ([], 9))
        self.assertEqual(work.body_bytes, 45)
        # Each five-byte reservation permits only one extra rejection sentinel,
        # rather than a complete 256 KiB read from every corrupt object.
        self.assertEqual(reads, [(6, 6)] * 9)
        self.assertLessEqual(sum(actual for _, actual in reads), work.body_bytes + work.examined)

    def test_accepted_duplicate_fts_nominations_do_not_reload_full_content(self):
        self.enable()
        saved = self.capture([{**self.source, "content": "public valid " * 2000}])["sources"][0]
        with closing(sqlite3.connect(self.store.index)) as db, db:
            db.executemany("INSERT INTO captures_fts(id,title,content) VALUES(?,?,?)",
                           [(saved["capture_id"], saved["title"], saved["content"])] * 5)
        work = CandidateWork()
        size = len(saved["content"].encode())
        with patch("backend.context_store.CANDIDATE_BODY_BYTES", size):
            result = self.store.lookup_candidates("public", limit=20, work=work)
        self.assertEqual((result["state"], len(result["candidates"])), ("ready", 1))
        self.assertEqual(work.body_bytes, size)

    def test_rejected_duplicate_fts_bodies_consume_the_shared_content_allowance(self):
        self.enable()
        saved = self.capture([{**self.source, "content": "public valid " * 2000}])["sources"][0]
        forged = "public wrong " * 2000
        self.assertEqual(len(forged), len(saved["content"]))
        with closing(sqlite3.connect(self.store.index)) as db, db:
            db.execute("DELETE FROM captures_fts")
            db.executemany("INSERT INTO captures_fts(id,title,content) VALUES(?,?,?)",
                           [(saved["capture_id"], saved["title"], forged)] * 5)
        work = CandidateWork()
        size = len(saved["content"].encode())
        with patch("backend.context_store.CANDIDATE_BODY_BYTES", 2 * size):
            result = self.store.lookup_candidates("public", limit=20, work=work)
        self.assertEqual((result["state"], result["search_limited"], result["candidates"]), ("limited", True, []))
        self.assertEqual(result["skipped_count"], 2)
        self.assertEqual(work.body_bytes, 2 * size)


if __name__ == "__main__":
    unittest.main()

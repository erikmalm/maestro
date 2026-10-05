"""Writer claims and bounded inventory against disposable synthetic archives."""
from contextlib import contextmanager
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

from backend import storage
from backend.storage import archive_available, archive_inventory, archive_owner


class ArchiveOwnershipTests(unittest.TestCase):
    def setUp(self):
        self.directory = Path(self.enterContext(tempfile.TemporaryDirectory(prefix="maestro-archive-owner-"))).resolve()
        self.root = self.directory / "archive"
        self.token, self.lock = "a" * 32, threading.RLock()

    def owner(self, token=None):
        return archive_owner(self.root, token or self.token, self.lock)

    @contextmanager
    def directory_link(self, link, target):
        if os.name == "nt":
            result = subprocess.run(["cmd.exe", "/c", "mklink", "/J", str(link), str(target)],
                                    capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
        else:
            link.symlink_to(target, target_is_directory=True)
        try:
            yield
        finally:
            link.rmdir() if os.name == "nt" else link.unlink()

    def test_availability_never_initializes_storage_and_handles_missing_configuration(self):
        self.assertFalse(archive_available(None, self.token))
        self.assertFalse(archive_available(self.root, self.token))
        self.assertFalse(self.root.exists())
        with self.assertRaisesRegex(ValueError, "Configure"):
            with archive_owner(None, self.token, self.lock):
                self.fail("An unconfigured archive cannot be claimed.")
        self.root.mkdir()
        self.assertTrue(archive_available(self.root, self.token))
        self.assertEqual(list(self.root.iterdir()), [])

    def test_nested_non_lifo_owners_release_only_after_the_last_exit(self):
        first, second = self.owner(), self.owner()
        first.__enter__()
        second.__enter__()
        try:
            first.__exit__(None, None, None)
            self.assertEqual((self.root / "writer-owner.tmp").read_text(), self.token)
            self.assertTrue(archive_available(self.root, self.token))
            self.assertFalse(archive_available(self.root, "b" * 32))
            with self.assertRaisesRegex(ValueError, "owns"):
                with self.owner("b" * 32):
                    self.fail("A competing owner cannot take the active claim.")
        finally:
            second.__exit__(None, None, None)
        self.assertFalse((self.root / "writer-owner.tmp").exists())
        with self.owner("b" * 32):
            self.assertTrue(archive_available(self.root, "b" * 32))

    def test_lifetime_claim_allows_other_threads_to_use_the_callers_write_lock(self):
        results = []
        def work():
            try:
                with self.lock, self.owner():
                    results.append(archive_available(self.root, self.token))
            except Exception as error:
                results.append(error)
        with self.owner():
            thread = threading.Thread(target=work, daemon=True)
            thread.start()
            thread.join(timeout=5)
            self.assertFalse(thread.is_alive(), "Lifetime ownership must not hold the caller's RLock over its yield.")
            self.assertEqual(results, [True])
        self.assertFalse((self.root / "writer-owner.tmp").exists())

    def test_independent_process_cannot_acquire_a_live_claim(self):
        program = """import sys, threading
from pathlib import Path
from backend.storage import archive_owner
try:
    with archive_owner(Path(sys.argv[1]), 'b' * 32, threading.RLock()):
        sys.exit(2)
except ValueError as error:
    sys.exit(0 if 'existing writer claim' in str(error) else 3)
"""
        with self.owner():
            result = subprocess.run([sys.executable, "-c", program, str(self.root)], capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual((self.root / "writer-owner.tmp").read_text(), self.token)

    def test_tampered_or_unreadable_claim_is_unavailable_and_preserved_for_explicit_cleanup(self):
        with self.owner():
            with patch.object(storage, "read_archive", side_effect=OSError("Synthetic offline file")):
                self.assertFalse(archive_available(self.root, self.token))
                with self.assertRaisesRegex(ValueError, "writer claim changed"):
                    with self.owner():
                        self.fail("An unreadable live claim cannot authorize another write.")
            (self.root / "writer-owner.tmp").write_bytes(b"External stale claim")
            self.assertFalse(archive_available(self.root, self.token))
            with self.assertRaisesRegex(ValueError, "writer claim changed"):
                with self.owner():
                    self.fail("A tampered live claim cannot authorize another write.")
        self.assertEqual((self.root / "writer-owner.tmp").read_bytes(), b"External stale claim")
        with self.assertRaisesRegex(ValueError, "previous Maestro"):
            with self.owner():
                self.fail("A stale claim requires explicit cleanup.")

    def test_missing_live_claim_is_unavailable_and_cannot_be_reentered_or_replaced(self):
        with self.owner():
            (self.root / "writer-owner.tmp").unlink()
            for token in (self.token, "b" * 32):
                self.assertFalse(archive_available(self.root, token))
                with self.assertRaisesRegex(ValueError, "changed" if token == self.token else "owns"):
                    with self.owner(token):
                        self.fail("An active owner with a missing claim cannot authorize another writer.")
            self.assertFalse((self.root / "writer-owner.tmp").exists())
        with self.owner():
            self.assertTrue(archive_available(self.root, self.token))

    def test_failed_claim_sync_retains_a_stale_claim_instead_of_allowing_another_writer(self):
        with patch.object(storage.os, "fsync", side_effect=OSError("Synthetic sync failure")):
            with self.assertRaisesRegex(ValueError, "could not be created"):
                with self.owner():
                    self.fail("An unsynced claim cannot establish ownership.")
        self.assertEqual((self.root / "writer-owner.tmp").read_text(), self.token)
        self.assertFalse(archive_available(self.root, self.token))
        with self.assertRaisesRegex(ValueError, "previous Maestro"):
            with self.owner("b" * 32):
                self.fail("A failed claim must keep the next writer blocked.")

    def test_redirected_roots_ancestors_and_child_entries_are_never_scanned_or_claimed(self):
        outside = self.directory / "outside"
        outside.mkdir()
        marker = outside / "private.txt"
        marker.write_bytes(b"Unrelated private bytes")
        for link in (self.root, self.root / "child"):
            link.parent.mkdir(parents=True, exist_ok=True)
            with self.directory_link(link, outside):
                for candidate in (link, link / "missing"):
                    self.assertFalse(archive_available(candidate, self.token))
                    with self.assertRaises(ValueError):
                        with archive_owner(candidate, self.token, self.lock):
                            self.fail("A redirected directory cannot receive a claim.")
                with patch.object(storage.os, "scandir", wraps=os.scandir) as scan:
                    with self.assertRaises(ValueError):
                        archive_inventory(link / "missing" if link == self.root else self.root, 20)
                    self.assertEqual(scan.call_count, 0 if link == self.root else 1)
                self.assertEqual(list(outside.iterdir()), [marker])
                self.assertEqual(marker.read_bytes(), b"Unrelated private bytes")

    def test_inventory_counts_files_directories_residue_and_only_recognized_capture_paths(self):
        records = self.root / "records" / "captures" / "2026-10"
        records.mkdir(parents=True)
        capture = records / ("a" * 32 + ".json")
        capture.write_bytes(b"Saved metadata")
        (records / "other.json").write_bytes(b"Unrecognized")
        (self.root / "interrupted.tmp").write_bytes(b"Staging residue")
        with self.owner():
            files = list(self.root.rglob("*"))
            expected = (sum(path.stat().st_size for path in files if path.is_file()), len(files),
                        [capture.relative_to(self.root).as_posix()])
            self.assertEqual(archive_inventory(self.root, len(files)), expected)
            with self.assertRaisesRegex(ValueError, "scan allowance"):
                archive_inventory(self.root, len(files) - 1)

    def test_inventory_rejects_invalid_allowances_and_stops_when_time_expires(self):
        for limit in (-1, True, False, 2.0, "2", None):
            with self.subTest(limit=limit), self.assertRaises(ValueError):
                archive_inventory(None, limit)
        with self.assertRaises(ValueError):
            archive_inventory(self.root, 1)
        self.assertFalse(self.root.exists())
        self.root.mkdir()
        self.assertEqual(archive_inventory(self.root, 0), (0, 0, []))
        (self.root / "first.tmp").write_bytes(b"one")
        (self.root / "second.tmp").write_bytes(b"two")
        with patch.object(storage.time, "monotonic", side_effect=(0, 10, 11)):
            with self.assertRaisesRegex(ValueError, "scan allowance"):
                archive_inventory(self.root, 2)

    def test_inventory_rejects_an_empty_final_directory_that_completes_after_the_deadline(self):
        (self.root / "empty").mkdir(parents=True)
        with patch.object(storage.time, "monotonic", side_effect=(0, 1, 1, 11)):
            with patch.object(storage.os, "scandir", wraps=os.scandir) as scan:
                with self.assertRaisesRegex(ValueError, "scan allowance"):
                    archive_inventory(self.root, 1)
                self.assertEqual(scan.call_count, 2)

    def test_inventory_revalidates_a_queued_directory_before_scanning_it(self):
        child, outside = self.root / "child", self.directory / "outside"
        child.mkdir(parents=True)
        outside.mkdir()
        (outside / "private.txt").write_bytes(b"Unrelated private bytes")
        scan = os.scandir
        @contextmanager
        def changing_directory(directory):
            with scan(directory) as entries:
                yield entries
            if directory == self.root:
                child.rmdir()
                self.enterContext(self.directory_link(child, outside))
        with patch.object(storage.os, "scandir", side_effect=changing_directory) as scanned:
            with self.assertRaises(ValueError):
                archive_inventory(self.root, 10)
            scanned.assert_called_once_with(self.root)
        self.assertEqual((outside / "private.txt").read_bytes(), b"Unrelated private bytes")

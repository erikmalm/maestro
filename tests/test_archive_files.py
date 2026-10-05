"""Synthetic archive file checks; temporary storage only, without an archive API."""
from io import BytesIO
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from backend import storage
from backend.storage import archive_path, publish_archive, read_archive, read_archive_json


class ArchiveFileTests(unittest.TestCase):
    def setUp(self):
        self.directory = Path(self.enterContext(tempfile.TemporaryDirectory(prefix="maestro-archive-files-"))).resolve()
        self.root = self.directory / "archive"

    def test_path_and_missing_reads_do_not_create_storage(self):
        self.assertEqual(archive_path(self.root, "records/source.json"), self.root / "records/source.json")
        for reader in (read_archive, read_archive_json):
            with self.subTest(reader=reader.__name__), self.assertRaises(FileNotFoundError):
                reader(self.root, "missing.json", 16)
        self.assertFalse(self.root.exists())

    def test_paths_reject_traversal_drives_alternate_streams_and_missing_root(self):
        for relative in (None, "", ".", "../private", "records/../../private", str(self.directory / "outside"),
                         "records\\source.json", "C:format.json", "format.json:payload", "NUL", "NUL.json",
                         "CON/source.txt", "COM1.txt", "records/LPT2.txt", "records./source.json", "source.txt "):
            with self.subTest(relative=relative), self.assertRaises(ValueError):
                archive_path(self.root, relative)
        with self.assertRaises(ValueError):
            archive_path(None, "source.json")
        self.assertFalse(self.root.exists())

    def test_replaced_archive_root_and_child_links_cannot_read_or_publish_outside(self):
        outside = self.directory / "outside"
        outside.mkdir()
        (outside / "source.txt").write_bytes(b"Unrelated synthetic bytes")
        for link in (self.root, self.root / "nested"):
            link.parent.mkdir(parents=True, exist_ok=True)
            if os.name == "nt":
                created = subprocess.run(["cmd.exe", "/c", "mklink", "/J", str(link), str(outside)],
                                         capture_output=True, text=True)
                self.assertEqual(created.returncode, 0, created.stderr)
            else:
                link.symlink_to(outside, target_is_directory=True)
            try:
                relative = "source.txt" if link == self.root else "nested/source.txt"
                for operation in (lambda: read_archive(self.root, relative, 100),
                                  lambda: publish_archive(self.root, relative, b"Replacement")):
                    with self.subTest(link=link, operation=operation), self.assertRaises(ValueError):
                        operation()
                self.assertEqual((outside / "source.txt").read_bytes(), b"Unrelated synthetic bytes")
            finally:
                link.rmdir() if os.name == "nt" else link.unlink()

    def test_reads_bound_actual_bytes_and_reject_invalid_limits_before_opening(self):
        self.root.mkdir()
        (self.root / "source.txt").write_bytes(b"1234")
        self.assertEqual(read_archive(self.root, "source.txt", 4), b"1234")
        (self.root / "empty.txt").write_bytes(b"")
        self.assertEqual(read_archive(self.root, "empty.txt", 0), b"")
        for limit in (0, 3):
            with self.subTest(limit=limit), self.assertRaises(ValueError):
                read_archive(self.root, "source.txt", limit)
        for limit in (-2, -1, True, False, 4.0, "4", None):
            with self.subTest(limit=limit), self.assertRaises(ValueError):
                read_archive(self.root, "missing.txt", limit)
        stream = BytesIO(b"Grown after initial observation")
        with patch.object(stream, "read", wraps=stream.read) as read, patch.object(Path, "open", return_value=stream):
            with self.assertRaises(ValueError):
                read_archive(self.root, "source.txt", 4)
            read.assert_called_once_with(5)

    def test_json_reader_preserves_unicode_and_rejects_corruption_or_excessive_depth(self):
        self.root.mkdir()
        source = self.root / "source.json"
        source.write_bytes('{"title":"\u00c5rsta"}'.encode("utf-8"))
        self.assertEqual(read_archive_json(self.root, "source.json", 64), {"title": "\u00c5rsta"})
        for content in (b"{broken", b"\xff", b"[" * 3000 + b"0" + b"]" * 3000):
            source.write_bytes(content)
            with self.subTest(content=content[:12]), self.assertRaises(ValueError):
                read_archive_json(self.root, "source.json", 8192)
        with self.assertRaises(ValueError):
            read_archive_json(self.root, "source.json", 4)

    def test_publication_is_immutable_deduplicated_and_leaves_no_staging_files(self):
        relative, original = "objects/source.txt", b"Synthetic immutable source"
        self.assertTrue(publish_archive(self.root, relative, original))
        self.assertFalse(publish_archive(self.root, relative, original))
        for conflicting in (b"Different length", b"x" * len(original)):
            with self.subTest(conflicting=conflicting), self.assertRaises(ValueError):
                publish_archive(self.root, relative, conflicting)
        self.assertEqual(read_archive(self.root, relative, len(original)), original)
        self.assertEqual([path.relative_to(self.root).as_posix() for path in self.root.rglob("*") if path.is_file()],
                         [relative])

    def test_destination_created_during_publication_is_never_replaced(self):
        link = storage.os.link
        def racing_link(source, destination):
            destination.write_bytes(b"External synthetic artifact")
            return link(source, destination)
        with patch.object(storage.os, "link", side_effect=racing_link):
            with self.assertRaises(FileExistsError):
                publish_archive(self.root, "source.txt", b"New capture")
        self.assertEqual((self.root / "source.txt").read_bytes(), b"External synthetic artifact")
        self.assertEqual(list(self.root.glob("*.tmp")), [])

    def test_flush_or_publication_failures_clean_staging_without_publishing_partial_bytes(self):
        for stage in ("fsync", "link"):
            with self.subTest(stage=stage), patch.object(storage.os, stage, side_effect=OSError("Synthetic failure")):
                with self.assertRaises(OSError):
                    publish_archive(self.root, "source.txt", b"Complete synthetic bytes")
            self.assertEqual(list(self.root.iterdir()), [])

    def test_staging_name_collision_preserves_the_unrelated_file(self):
        self.root.mkdir()
        collision = self.root / ("source.txt." + "a" * 32 + ".tmp")
        collision.write_bytes(b"Existing staging artifact")
        with patch.object(storage.uuid, "uuid4") as identifier:
            identifier.return_value.hex = "a" * 32
            with self.assertRaises(FileExistsError):
                publish_archive(self.root, "source.txt", b"New capture")
        self.assertEqual(collision.read_bytes(), b"Existing staging artifact")
        self.assertFalse((self.root / "source.txt").exists())

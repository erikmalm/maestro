"""Pure excerpt-manifest checks; no archive, index, network or model calls."""
from copy import deepcopy
import hashlib
import unittest

from backend.context_policy import (
    DEFAULT, SOURCE_BYTES, capture_source, manifest_hash, public_url, validate_capture_manifest,
)


class CaptureManifestTests(unittest.TestCase):
    def setUp(self):
        self.content = "Årsta public evidence ☀"
        raw = self.content.encode("utf-8")
        digest = hashlib.sha256(raw).hexdigest()
        url = "https://Docs.example.org:443/page?location=%C3%85rsta&label=%2f&label=%2F"
        self.data = {"schema_version": 1, "capture_id": "ab" * 16,
                     "source_id": hashlib.sha256(public_url(url, allow_query=True).encode()).hexdigest(),
                     "source_url": url, "title": "Årsta evidence ☀", "retrieved_at": "2026-10-01T00:15:00+02:00",
                     "content_kind": "search_excerpt", "published_at": None, "modified_at": None,
                     "completeness": {"maestro_truncated": True, "full_page": False},
                     "object": {"path": "objects/sha256/" + digest[:2] + "/" + digest + ".txt",
                                "sha256": digest, "bytes": len(raw), "encoding": "utf-8"}}
        self.relative = "records/captures/2026-09/" + self.data["capture_id"] + ".json"

    def test_unicode_query_identity_and_metadata_are_preserved_without_mutation(self):
        before = deepcopy(self.data)
        self.assertIs(validate_capture_manifest(self.data, self.relative, DEFAULT), self.data)
        source = capture_source(self.data, self.content)
        self.assertEqual(source, {"capture_id": self.data["capture_id"], "title": self.data["title"],
                                 "url": self.data["source_url"], "content": self.content,
                                 "content_hash": self.data["object"]["sha256"], "manifest_hash": manifest_hash(self.data),
                                 "archive_status": "saved", **{key: self.data[key] for key in (
                                     "content_kind", "completeness", "retrieved_at", "published_at", "modified_at")}})
        self.assertEqual(self.data, before)

    def test_exact_scope_policy_and_canonical_source_identity_remain_required(self):
        config = {**DEFAULT, "capture_policy": "approved_sources", "public_sources": [self.data["source_url"]]}
        self.assertEqual(validate_capture_manifest(self.data, self.relative, config), self.data)
        for url in (self.data["source_url"].replace("label=%2f&label=%2F", "label=%2F&label=%2f"),
                    self.data["source_url"].replace("https://", "http://").replace(":443", ":80"),
                    "https://docs.example.org/account/private", "https://docs.example.org/page?token=synthetic"):
            changed = {**self.data, "source_url": url,
                       "source_id": hashlib.sha256(public_url(url, allow_query=True, allow_http=True).encode()).hexdigest()}
            with self.subTest(url=url), self.assertRaises(ValueError):
                validate_capture_manifest(changed, self.relative, config)
        http = {**self.data, "source_url": "http://docs.example.org/page"}
        http["source_id"] = hashlib.sha256(http["source_url"].encode()).hexdigest()
        self.assertEqual(validate_capture_manifest(http, self.relative, DEFAULT), http)

    def test_invalid_schema_metadata_and_completeness_are_managed_errors(self):
        invalid = [None, [], {}, {**self.data, "extra": None}]
        for field, values in (("schema_version", (True, 1.0, 2)),
                              ("capture_id", (None, "AB" * 16, "ab" * 15, "../capture")),
                              ("source_id", (None, "0" * 64)), ("source_url", (None, "https://127.0.0.1/page")),
                              ("title", (None, "x" * 201, "\ud800", "\udfff")),
                              ("content_kind", ("full_page",)), ("published_at", ("2026-10-01",)),
                              ("modified_at", ("2026-10-01",)),
                              ("completeness", (None, {}, {"maestro_truncated": 1, "full_page": False},
                                                {"maestro_truncated": False, "full_page": 0}))):
            invalid.extend({**self.data, field: value} for value in values)
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_capture_manifest(value, self.relative, DEFAULT)

    def test_invalid_time_or_observation_path_cannot_change_the_capture_identity(self):
        for relative in (None, 1, self.relative.replace("2026-09", "2026-10"),
                         "../" + self.relative, self.relative.replace("ab" * 16, "cd" * 16), self.relative + ".bak"):
            with self.subTest(relative=relative), self.assertRaises(ValueError):
                validate_capture_manifest(self.data, relative, DEFAULT)
        for value in (None, "2026-10-01", "2026-02-30T00:00:00Z", "0001-01-01T00:00:00+01:00",
                      "9999-12-31T23:59:59-01:00"):
            with self.subTest(time=value), self.assertRaises(ValueError):
                validate_capture_manifest({**self.data, "retrieved_at": value}, self.relative, DEFAULT)

    def test_object_contract_rejects_traversal_bad_hashes_encodings_and_byte_types(self):
        invalid = [None, {}, {**self.data["object"], "extra": None}]
        for field, values in (("path", (None, "../private.sqlite3", self.data["object"]["path"] + ".bak")),
                              ("sha256", (None, "A" * 64, "a" * 63, "a" * 64 + "\n")),
                              ("bytes", (True, 1.0, 0, SOURCE_BYTES + 1)), ("encoding", ("utf-16", None))):
            invalid.extend({**self.data["object"], field: value} for value in values)
        for obj in invalid:
            with self.subTest(object=obj), self.assertRaises(ValueError):
                validate_capture_manifest({**self.data, "object": obj}, self.relative, DEFAULT)
        for size in (1, SOURCE_BYTES):
            data = {**self.data, "title": "☀" * 200, "object": {**self.data["object"], "bytes": size}}
            self.assertIs(validate_capture_manifest(data, self.relative, DEFAULT), data)

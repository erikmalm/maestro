"""Synthetic archive fixtures; no real OneDrive, network, keys or model calls."""
from datetime import datetime, timedelta, timezone
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import patch

from backend.context_store import ContextStore, DEFAULT, MANIFEST_BYTES, ROOT, decoded_url_variants, public_url, validate_search
from backend.storage import archive_inventory, publish_archive


class ContextStoreTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="maestro-context-test-")
        self.root = Path(self.temporary.name)
        self.archive = self.root / "public-archive"
        self.database = self.root / "private" / "workspace.sqlite3"
        self.store = ContextStore(self.database, self.archive)
        self.config = {**DEFAULT, "enabled": True, "capture_policy": "approved_sources",
                       "public_sources": ["https://docs.example.org/docs/"]}
        self.query = "Synthetic 2026 documentation"
        self.source = {"title": "Synthetic public documentation", "url": "https://docs.example.org/docs/page",
                       "content": "Public excerpt with distinct historical evidence.", "maestro_truncated": True}

    def tearDown(self):
        self.temporary.cleanup()

    def enable(self):
        self.store.configure(self.config)

    def capture(self, **changes):
        return self.store.capture(changes.get("query", self.query), changes.get("at", self.store.now().isoformat()),
                                  changes.get("sources", [self.source]), changes.get("max_results", 3), changes.get("secrets", ()))

    def manifests(self):
        return list(self.archive.glob("records/captures/*/*.json"))

    def test_constructor_status_and_disabled_capture_do_not_create_storage(self):
        self.assertFalse(self.store.status()["available"])
        self.assertIsNone(self.store.lookup(self.query, 3))
        self.assertEqual(self.capture()["sources"][0]["archive_status"], "skipped")
        self.assertFalse(self.database.exists())
        self.assertFalse(self.archive.exists())
        self.store.initialize()
        self.assertTrue(self.database.exists())
        self.assertFalse(self.archive.exists())

    def test_new_defaults_are_disabled_and_legacy_settings_keep_their_policy_and_limits(self):
        status = self.store.status()
        self.assertFalse(status["config"]["enabled"])
        self.assertEqual(status["config"]["capture_policy"], "all_public")
        self.assertEqual(status["config"]["max_bytes"], 10 * 1024 ** 3)
        self.assertEqual(status["config"]["max_items"], 200000)
        self.assertIsNone(status["last_capture"])
        self.assertFalse(self.database.exists())
        legacy = {key: value for key, value in self.config.items() if key != "capture_policy"}
        legacy.update(max_bytes=256 * 1024 ** 2, max_items=50000)
        self.store.configure(legacy)
        self.assertEqual(self.store.status()["config"], {**legacy, "capture_policy": "approved_sources"})
        saved = self.capture()["sources"][0]
        with closing(sqlite3.connect(self.database)) as db, db:
            state = json.loads(db.execute("SELECT value FROM context_state WHERE id=1").fetchone()[0])
            state["config"] = legacy
            db.execute("UPDATE context_state SET value=? WHERE id=1", (json.dumps(state),))
        before = self.database.read_bytes()
        restarted = ContextStore(self.database, self.archive)
        self.assertEqual(restarted.status()["config"], {**legacy, "capture_policy": "approved_sources"})
        self.assertEqual(restarted.get_capture(saved["capture_id"]), saved)
        self.assertEqual(self.database.read_bytes(), before)
        excluded = {**self.source, "url": "https://unlisted.example.org/weather"}
        self.assertEqual(restarted.capture("Unlisted", restarted.now().isoformat(), [excluded], 3)["sources"][0]["archive_status"], "skipped")

    def test_all_public_saves_unlisted_http_and_distinct_query_urls_without_changing_identity(self):
        self.config = {**self.config, "capture_policy": "all_public", "public_sources": []}
        self.enable()
        urls = ["http://weather.example.org/forecast?date=2026-10-05",
                "https://weather.example.org/forecast?date=2026-10-05",
                "https://weather.example.org/forecast?date=2026-10-06"]
        with patch("backend.context_store.archive_inventory", wraps=archive_inventory) as inventory:
            captured = self.capture(sources=[{**self.source, "url": url} for url in urls])["sources"]
        self.assertEqual(inventory.call_count, 2, "Capture should scan once before the batch and once after it.")
        self.assertEqual([source["archive_status"] for source in captured], ["saved"] * 3)
        self.assertEqual([source["url"] for source in captured], urls)
        self.assertEqual(len({json.loads(path.read_text())["source_id"] for path in self.manifests()}), 3)
        self.assertEqual(self.store.rebuild()["indexed_count"], 3)
        for source in captured:
            self.assertEqual(self.store.get_capture(source["capture_id"], source["content_hash"], source["manifest_hash"]), source)
        self.assertEqual({source["url"] for source in self.store.search("historical evidence", limit=3)["sources"]}, set(urls))

    def test_all_public_excludes_unsafe_authenticated_signed_and_personal_source_urls(self):
        self.config = {**self.config, "capture_policy": "all_public", "public_sources": []}
        self.enable()
        rejected = ["http://127.0.0.1/forecast", "https://192.168.1.2/forecast", "https://device.local/forecast",
                    "https://weather.internal/forecast",
                    "https://user:password@weather.example.org/forecast", "https://weather.example.org:444/forecast",
                    "https://weather.example.org/account/profile", "https://weather.example.org/forecast?access_token=synthetic",
                    "https://weather.example.org/forecast?%2574oken=synthetic", "https://weather.example.org/forecast?X-Amz-Signature=synthetic",
                    "https://weather.example.org/forecast?session_id=synthetic", "https://weather.example.org/forecast?download_token=synthetic",
                    "https://weather.example.org/forecast?email=person%40example.org",
                    "https://weather.example.org/forecast?place=person%2540example.org", "https://weather.example.org/forecast?user_id=synthetic",
                    "https://weather.example.org/forecast?place=%250Asecret", "https://weather.example.org/forecast#private"]
        for url in rejected:
            with self.subTest(url=url):
                result = self.capture(sources=[{**self.source, "url": url}])
                self.assertEqual(result["sources"][0]["archive_status"], "skipped")
                self.assertEqual(self.store.status()["last_capture"]["sources_saved"], 0)
        secret = "synthetic-known-search-secret"
        encoded = "".join("%" + format(ord(character), "02X") for character in secret).replace("%", "%25")
        result = self.capture(sources=[{**self.source, "url": "https://weather.example.org/forecast?value=" + encoded}], secrets=(secret,))
        self.assertEqual(result["sources"], [])
        self.assertEqual(self.store.status()["last_capture"]["sources_received"], 1)
        self.assertEqual(self.manifests(), [])
        self.assertEqual(list(self.archive.glob("objects/sha256/*/*.txt")), [])

    def test_last_capture_measures_utf8_and_new_durable_bytes_with_repeated_versions(self):
        self.config = {**self.config, "capture_policy": "all_public", "public_sources": []}
        self.enable()
        source = {**self.source, "url": "https://weather.example.org/forecast?date=2026-10-05", "content": "Synthetic Årsta forecast: 15 °C."}
        def archive_bytes():
            return sum(path.stat().st_size for path in self.archive.rglob("*") if path.is_file())
        with self.store.archive_owner():
            before = archive_bytes()
            first = self.capture(sources=[source])["sources"][0]
            status = self.store.status()
            measured = status["last_capture"]
            self.assertEqual(measured["sources_received"], 1)
            self.assertEqual(measured["sources_saved"], 1)
            self.assertEqual(measured["excerpt_bytes"], len(source["content"].encode("utf-8")))
            self.assertEqual(measured["object_bytes"], measured["excerpt_bytes"])
            self.assertEqual(measured["manifest_bytes"], self.manifests()[0].stat().st_size)
            self.assertEqual(measured["new_bytes"], archive_bytes() - before)
            self.assertEqual(status["archive_bytes"], archive_bytes())
            before = archive_bytes()
            second = self.capture(sources=[source], at=(self.store.now() + timedelta(seconds=1)).isoformat())["sources"][0]
            measured = self.store.status()["last_capture"]
            self.assertEqual(measured["sources_saved"], 1)
            self.assertEqual(measured["excerpt_bytes"], len(source["content"].encode("utf-8")))
            self.assertEqual(measured["object_bytes"], 0)
            self.assertGreater(measured["manifest_bytes"], 0)
            self.assertEqual(measured["new_bytes"], measured["manifest_bytes"])
            self.assertEqual(measured["new_bytes"], archive_bytes() - before)
        self.assertNotEqual(first["capture_id"], second["capture_id"])
        self.assertNotEqual(first["retrieved_at"], second["retrieved_at"])
        self.assertEqual(first["content_hash"], second["content_hash"])
        self.assertEqual(len(self.manifests()), 2)
        self.assertEqual(len(list(self.archive.glob("objects/sha256/*/*.txt"))), 1)
        self.assertEqual(self.store.get_capture(first["capture_id"]), first)

    def test_configure_and_deletion_refresh_real_archive_inventory_without_changing_last_capture(self):
        def measured_inventory():
            paths = list(self.archive.rglob("*"))
            return sum(path.stat().st_size for path in paths if path.is_file()), len(paths)

        # The application retains this ownership claim while the archive is enabled.
        with self.store.archive_owner():
            status = self.store.configure(self.config)
            self.assertEqual((status["archive_bytes"], status["archive_items"]), measured_inventory())
            self.assertGreater(status["archive_bytes"], 0)
            self.assertEqual(status["archive_items"], 2)  # Format and live writer claim.
            saved = self.capture()["sources"][0]
            last_capture = self.store.status()["last_capture"]
            before = measured_inventory()
            self.store.delete_capture(saved["capture_id"])
            status = self.store.status()
            self.assertEqual((status["archive_bytes"], status["archive_items"]), measured_inventory())
            self.assertGreater(status["archive_bytes"], before[0])
            self.assertEqual(status["archive_items"], before[1] + 2)  # Deletion directory and record.
            self.assertEqual(status["indexed_count"], 0)
            self.assertEqual(status["last_capture"], last_capture)
            self.store.delete_capture(saved["capture_id"])
            status = self.store.configure(self.config)
            self.assertEqual((status["archive_bytes"], status["archive_items"]), measured_inventory())
            self.assertEqual(status["last_capture"], last_capture)
            self.assertIsNone(self.store.lookup(self.query, 3))

    def test_noop_and_numeric_settings_saves_preserve_exact_reuse_and_immutable_files(self):
        with self.store.archive_owner():
            self.enable()
            saved = self.capture()["sources"][0]
            self.assertEqual(self.store.search(self.query)["sources"], [])
            files = {path.relative_to(self.archive): path.read_bytes() for path in self.archive.rglob("*") if path.is_file()}
            for change in ({}, {"max_bytes": 1048576}, {"max_items": 100}, {"reuse_hours": 48}):
                with self.subTest(change=change):
                    self.config.update(change)
                    self.store.configure(self.config)
                    reused = self.store.lookup(self.query, 3)
                    self.assertTrue(reused["from_cache"])
                    self.assertFalse(reused["stale"])
                    self.assertEqual(reused["sources"], [{**saved, "stale": False}])
                    self.assertEqual(reused["at"], saved["retrieved_at"])
                    self.assertEqual({path.relative_to(self.archive): path.read_bytes() for path in self.archive.rglob("*") if path.is_file()}, files)

    def test_shortening_reuse_window_expires_retained_mapping_without_rewriting_evidence(self):
        now = datetime(2026, 10, 4, 12, tzinfo=timezone.utc)
        with patch.object(self.store, "now", return_value=now):
            self.enable()
            saved = self.capture(at=(now - timedelta(hours=12)).isoformat())["sources"][0]
            self.assertFalse(self.store.lookup(self.query, 3)["stale"])
            self.store.configure({**self.config, "reuse_hours": 6})
            self.assertIsNone(self.store.lookup(self.query, 3))
            historical = self.store.lookup(self.query, 3, allow_stale=True)
            self.assertTrue(historical["stale"])
            self.assertEqual(historical["sources"], [{**saved, "stale": True}])
            self.store.configure({**self.config, "reuse_hours": 48})
            self.assertEqual(self.store.lookup(self.query, 3)["sources"], [{**saved, "stale": False}])

    def test_eligibility_transitions_clear_mappings_and_numeric_saves_preserve_deletion(self):
        broad = {**self.config, "capture_policy": "all_public", "public_sources": []}
        cases = (("scopes", self.config, {**self.config, "public_sources": ["https://other.example.org/"]}, self.source),
                 ("policy", broad, self.config, {**self.source, "url": "https://other.example.org/public"}),
                 ("disabled", self.config, {**self.config, "enabled": False}, self.source))
        for name, initial, restricted, source in cases:
            with self.subTest(change=name):
                self.store.configure(initial)
                saved = self.capture(sources=[source])["sources"][0]
                self.assertIsNotNone(self.store.lookup(self.query, 3))
                self.store.configure(restricted)
                self.assertIsNone(self.store.lookup(self.query, 3))
                with closing(sqlite3.connect(self.database)) as db:
                    self.assertEqual(db.execute("SELECT COUNT(*) FROM context_queries").fetchone()[0], 0)
                with self.assertRaises(KeyError):
                    self.store.get_capture(saved["capture_id"])
                self.store.configure(initial)
                self.assertIsNone(self.store.lookup(self.query, 3))
                self.assertEqual(self.store.get_capture(saved["capture_id"]), saved)
                self.store.delete_capture(saved["capture_id"])
                self.store.configure({**initial, "reuse_hours": 48})
                self.assertIsNone(self.store.lookup(self.query, 3, allow_stale=True))
                with self.assertRaises(KeyError):
                    self.store.get_capture(saved["capture_id"])
        self.assertEqual(self.store.rebuild()["indexed_count"], 0)

    def test_partial_index_failure_reports_durable_bytes_without_claiming_every_source_saved(self):
        self.config = {**self.config, "capture_policy": "all_public", "public_sources": []}
        self.enable()
        second = {**self.source, "url": "https://other.example.org/forecast", "content": "Second synthetic excerpt."}
        original = self.store._index_capture
        calls = 0
        def index_once(*args):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise sqlite3.OperationalError("Synthetic index failure")
            return original(*args)
        with self.store.archive_owner(), patch.object(self.store, "_index_capture", side_effect=index_once):
            before = sum(path.stat().st_size for path in self.archive.rglob("*") if path.is_file())
            result = self.capture(sources=[self.source, second])
            self.assertIn("archive_warning", result)
            self.assertEqual([source["archive_status"] for source in result["sources"]], ["saved", "not_saved"])
            status = self.store.status()
            measured = status["last_capture"]
            self.assertEqual((measured["sources_received"], measured["sources_saved"]), (2, 1))
            self.assertEqual(measured["excerpt_bytes"], len(self.source["content"].encode()))
            self.assertEqual(measured["object_bytes"], sum(path.stat().st_size for path in self.archive.glob("objects/sha256/*/*.txt")))
            self.assertEqual(measured["manifest_bytes"], sum(path.stat().st_size for path in self.manifests()))
            self.assertEqual(measured["new_bytes"], status["archive_bytes"] - before)
        self.assertIsNone(self.store.lookup(self.query, 3))
        self.assertEqual(self.store.get_capture(result["sources"][0]["capture_id"]), result["sources"][0])
        self.assertEqual(self.store.rebuild()["indexed_count"], 2)

    def test_narrowing_all_public_to_approved_policy_hides_unlisted_versions_without_deleting_them(self):
        broad = {**self.config, "capture_policy": "all_public", "public_sources": []}
        self.store.configure(broad)
        urls = [self.source["url"], "https://other.example.org/forecast", "http://weather.example.org/forecast?date=2026-10-05"]
        saved = self.capture(sources=[{**self.source, "url": url} for url in urls])["sources"]
        self.store.configure(self.config)
        self.assertEqual(self.store.get_capture(saved[0]["capture_id"]), saved[0])
        for source in saved[1:]:
            with self.assertRaises(KeyError):
                self.store.get_capture(source["capture_id"])
        self.assertEqual(self.store.rebuild()["indexed_count"], 1)
        self.assertEqual(self.store.search("historical evidence")["sources"][0]["capture_id"], saved[0]["capture_id"])
        self.assertEqual(len(self.manifests()), 3)
        self.store.configure(broad)
        self.assertEqual(self.store.rebuild()["indexed_count"], 3)
        for source in saved:
            self.assertEqual(self.store.get_capture(source["capture_id"], source["content_hash"], source["manifest_hash"]), source)

    def test_archive_must_be_outside_checkout_database_and_private_index(self):
        for path in (ROOT / "tests" / "synthetic-context", self.database.parent, self.store.index.parent):
            with self.subTest(path=path), self.assertRaises(ValueError):
                ContextStore(self.database, path)

    def test_conservative_scopes_use_exact_origin_and_path_components(self):
        self.enable()
        eligible = [self.source, {**self.source, "url": "https://docs.example.org/docs"}]
        skipped = [{**self.source, "url": url} for url in (
            "https://docs.example.org/docs-private", "https://sub.docs.example.org/docs/page",
            "https://docs.example.org/docs/page?token=private", "https://docs.example.org/docs/page#fragment",
            "http://docs.example.org/docs/page", "https://docs.example.org/docs/%2e%2e/private",
        )]
        for source in eligible + skipped:
            result = self.capture(sources=[source])
            self.assertEqual(result["sources"][0]["archive_status"], "saved" if source in eligible else "skipped")
        for scope in ("https://127.0.0.1/", "https://user:password@docs.example.org/", "https://docs.example.org:444/",
                      "https://docs.example.org/docs/%252e%252e/", "https://docs.example.org/docs/%2fprivate"):
            with self.subTest(scope=scope), self.assertRaises(ValueError):
                public_url(scope)

    def test_only_public_metadata_is_exported_and_secrets_are_redacted(self):
        self.enable()
        secret = "synthetic-provider-secret"
        source = {**self.source, "title": "Title " + secret, "content": "Body " + secret,
                  "chat_id": "synthetic-private-chat", "query": "synthetic-private-query"}
        result = self.capture(query="A private local query mapping", sources=[source], secrets=(secret,))
        saved = result["sources"][0]
        self.assertEqual(saved["archive_status"], "saved")
        self.assertEqual(saved["completeness"], {"maestro_truncated": True, "full_page": False})
        self.assertIsNone(saved["published_at"])
        self.assertIsNone(saved["modified_at"])
        text = "\n".join(path.read_text(encoding="utf-8") for path in self.archive.rglob("*") if path.is_file())
        for private in (secret, "synthetic-private-chat", "synthetic-private-query", "A private local query mapping"):
            self.assertNotIn(private, text)
        with closing(sqlite3.connect(self.database)) as db:
            self.assertEqual(db.execute("SELECT query FROM context_queries").fetchone()[0], "A private local query mapping")
        credential_link = self.capture(sources=[{**source, "url": self.source["url"] + "/" + secret}], secrets=(secret,))
        self.assertEqual(credential_link["sources"], [])

    def test_credential_iterators_protect_every_field_and_source_in_one_capture(self):
        self.enable()
        secrets = ("synthetic-first-secret", "synthetic-second-secret")
        encoded = ["".join("%" + format(ord(character), "02X") for character in secret) for secret in secrets]
        sources = [{**self.source, "title": "Title " + secrets[0], "content": "Body " + secrets[1]},
                   {**self.source, "title": "Encoded " + encoded[1]},
                   {**self.source, "url": self.source["url"] + "/" + encoded[0]}]
        result = self.capture(sources=sources, secrets=iter(("", None, *secrets)))
        self.assertEqual(len(result["sources"]), 1)
        saved = result["sources"][0]
        self.assertEqual((saved["title"], saved["content"], saved["archive_status"]), ("Title \u2588", "Body \u2588", "saved"))
        self.assertEqual(len(self.manifests()), 1)
        self.assertEqual(self.store.status()["last_capture"]["sources_saved"], 1)
        public_text = "\n".join(path.read_text(encoding="utf-8") for path in self.archive.rglob("*") if path.is_file())
        for secret in (*secrets, *encoded):
            self.assertNotIn(secret, public_text)

    def test_encoded_known_credentials_in_titles_and_content_are_never_exported(self):
        self.enable()
        secret = "synthetic-known-archive-secret"
        percent = "".join("%" + format(ord(character), "02X") for character in secret)
        entities = "".join("&#x" + format(ord(character), "x") + ";" for character in secret)
        encodings = (percent, percent.lower(), percent.replace("%", "%25"), entities,
                     "".join("&#" + str(ord(character)) + ";" for character in secret),
                     entities.replace("&", "%26"), percent.replace("%", "&#37;"))
        for encoded in encodings:
            for field in ("title", "content", "url"):
                with self.subTest(field=field, encoding=encodings.index(encoded)):
                    compromised = {**self.source, field: "Encoded credential " + encoded}
                    if field == "url":
                        compromised[field] = self.source["url"] + "?value=" + encoded
                    result = self.capture(sources=[compromised], secrets=(secret,))
                    self.assertEqual(result["sources"], [])
                    self.assertEqual(self.store.status()["last_capture"]["sources_saved"], 0)
        self.assertEqual(self.manifests(), [])
        self.assertEqual(list(self.archive.glob("objects/sha256/*/*.txt")), [])

    def test_secret_checks_keep_normal_escaped_source_text_and_completeness_unchanged(self):
        self.enable()
        source = {**self.source, "title": "Code &amp; URL escaping",
                  "content": "Examples: %41, &lt;div&gt;, &#37;20, invalid %FF and 100% complete."}
        saved = self.capture(sources=[source], secrets=("synthetic-known-archive-secret",))["sources"][0]
        self.assertEqual(saved["archive_status"], "saved")
        self.assertEqual(saved["title"], source["title"])
        self.assertEqual(saved["content"], source["content"])
        self.assertEqual(saved["completeness"], {"maestro_truncated": True, "full_page": False})
        self.assertEqual(self.store.get_capture(saved["capture_id"])["content"], source["content"])

    def test_query_scope_configuration_preserves_normalized_complete_urls(self):
        url = "https://weather.example.org/forecast?latitude=59.3&longitude=18.0&date=2026-10-05"
        canonical_variant = url.replace("weather.example.org", "WEATHER.example.org:443")
        opaque = "https://weather.example.org/forecast?location=%C3%85rsta%20Stockholm&label=%2f&label=%2F"
        self.store.configure({**self.config, "public_sources": [canonical_variant, url, opaque]})
        self.assertEqual(self.store.status()["config"]["public_sources"], [url, opaque])

    def test_query_capture_reuse_and_rebuild_keep_original_hashes_and_dates(self):
        url = "https://weather.example.org/forecast?location=Stockholm&date=2026-10-05"
        self.config = {**self.config, "public_sources": [url]}
        self.enable()
        source = {**self.source, "url": url, "content": "Synthetic dated forecast evidence."}
        saved = self.capture(sources=[source])["sources"][0]
        self.assertEqual(saved["archive_status"], "saved")
        manifest = json.loads(self.manifests()[0].read_text(encoding="utf-8"))
        self.assertEqual(manifest["source_url"], url)
        self.assertEqual(manifest["source_id"], hashlib.sha256(url.encode()).hexdigest())
        for changed in (url.replace("2026-10-05", "2026-10-06"), url.split("?")[0], url.replace("forecast?", "forecast/child?")):
            self.assertEqual(self.capture(query="An unapproved variant", sources=[{**source, "url": changed}])["sources"][0]["archive_status"], "skipped")
        restarted = ContextStore(self.database, self.archive)
        reused = restarted.lookup(self.query, 3)
        self.assertEqual(reused["sources"], [{**saved, "stale": False}])
        self.assertEqual(reused["at"], saved["retrieved_at"])
        self.assertEqual(restarted.rebuild()["indexed_count"], 1)
        self.assertEqual(restarted.get_capture(saved["capture_id"], saved["content_hash"], saved["manifest_hash"]), saved)
        self.assertEqual(restarted.search("dated forecast", domain="weather.example.org")["sources"], [{**saved, "stale": False}])

    def test_query_capture_is_hidden_when_approval_narrows_to_an_ordinary_path(self):
        url = "https://weather.example.org/forecast?date=2026-10-05"
        self.config = {**self.config, "public_sources": [url]}
        self.enable()
        saved = self.capture(sources=[{**self.source, "url": url}])["sources"][0]
        self.store.configure({**self.config, "public_sources": ["https://weather.example.org/forecast"]})
        self.assertIsNone(self.store.lookup(self.query, 3))
        with self.assertRaises(KeyError):
            self.store.get_capture(saved["capture_id"])
        self.assertEqual(self.store.search("historical evidence")["sources"], [])
        self.assertEqual(self.store.rebuild()["indexed_count"], 0)
        self.store.configure(self.config)
        self.assertEqual(self.store.rebuild()["indexed_count"], 1)
        self.assertEqual(self.store.get_capture(saved["capture_id"]), saved)
        self.assertIsNone(self.store.lookup(self.query, 3))

    def test_query_manifest_tampering_cannot_change_an_existing_citation(self):
        first_url = "https://weather.example.org/forecast?date=2026-10-05"
        second_url = first_url.replace("2026-10-05", "2026-10-06")
        self.config = {**self.config, "public_sources": [first_url, second_url]}
        self.enable()
        saved = self.capture(sources=[{**self.source, "url": first_url}])["sources"][0]
        path = self.manifests()[0]
        manifest = json.loads(path.read_text(encoding="utf-8"))
        manifest["source_url"] = second_url
        path.write_text(json.dumps(manifest), encoding="utf-8")
        with self.assertRaises(KeyError):
            self.store.get_capture(saved["capture_id"])
        self.assertIsNone(self.store.lookup(self.query, 3))
        self.assertEqual(self.store.rebuild()["corrupt_count"], 1)
        manifest["source_id"] = hashlib.sha256(second_url.encode()).hexdigest()
        path.write_text(json.dumps(manifest), encoding="utf-8")
        self.assertEqual(self.store.rebuild()["indexed_count"], 1)
        self.assertEqual(self.store.get_capture(saved["capture_id"])["url"], second_url)
        with self.assertRaises(KeyError):
            self.store.get_capture(saved["capture_id"], saved["content_hash"], saved["manifest_hash"])
        with self.assertRaises(ValueError):
            with self.store.evidence_guard([saved]):
                pass

    def test_query_url_deletion_survives_rebuild_and_keeps_other_dates(self):
        urls = ["https://weather.example.org/forecast?date=2026-10-05", "https://weather.example.org/forecast?date=2026-10-06"]
        self.config = {**self.config, "public_sources": urls}
        self.enable()
        first = self.capture(sources=[{**self.source, "url": urls[0]}])["sources"][0]
        second = self.capture(query="Other date", sources=[{**self.source, "url": urls[1]}])["sources"][0]
        self.store.delete_capture(first["capture_id"])
        self.assertIsNone(self.store.lookup(self.query, 3))
        self.assertEqual(self.store.rebuild()["indexed_count"], 1)
        with self.assertRaises(KeyError):
            self.store.get_capture(first["capture_id"])
        self.assertEqual(self.store.get_capture(second["capture_id"]), second)
        self.assertEqual(self.store.search("historical evidence")["sources"], [{**second, "stale": False}])

    def test_keyword_url_dedup_keeps_exact_query_identities_separate(self):
        urls = ["https://weather.example.org/forecast?place=Stockholm&date=2026-10-05",
                "https://weather.example.org/forecast?place=Stockholm&date=2026-10-06",
                "https://weather.example.org/forecast?date=2026-10-05&place=Stockholm"]
        self.config = {**self.config, "public_sources": urls}
        self.enable()
        now = self.store.now()
        saved = [self.capture(query="Date " + str(index), at=(now - timedelta(minutes=3 - index)).isoformat(),
                              sources=[{**self.source, "url": url}])["sources"][0] for index, url in enumerate(urls)]
        newest = self.capture(at=now.isoformat(), sources=[{**self.source, "url": urls[0]}])["sources"][0]
        result = self.store.search("historical evidence", limit=3)
        self.assertEqual({source["capture_id"] for source in result["sources"]}, {newest["capture_id"], saved[1]["capture_id"], saved[2]["capture_id"]})
        self.assertEqual({source["url"] for source in result["sources"]}, set(urls))
        self.assertEqual(len(list(self.archive.glob("objects/sha256/*/*.txt"))), 1)
        self.assertEqual(len({json.loads(path.read_text())["source_id"] for path in self.manifests()}), 3)

    def test_known_secret_in_an_explicit_query_scope_is_never_exported(self):
        secret = "synthetic-provider-secret"
        url = "https://weather.example.org/forecast?value=" + secret
        encoded = url.replace(secret, "%73ynthetic-provider-secret")
        fully_encoded = "".join("%" + format(ord(character), "02X") for character in secret)
        twice_encoded = url.replace(secret, fully_encoded.replace("%", "%25"))
        three_times_encoded = twice_encoded.replace("%", "%25")
        urls = [url, encoded, twice_encoded, three_times_encoded]
        self.config = {**self.config, "public_sources": urls}
        self.enable()
        for source_url in urls:
            with self.subTest(url=source_url):
                self.assertIn(url, decoded_url_variants(source_url))
                result = self.capture(sources=[{**self.source, "url": source_url}], secrets=(secret,))
                self.assertEqual(result["sources"], [])
        self.assertEqual(self.manifests(), [])

    def test_restart_reuses_exact_query_without_changing_retrieval_date(self):
        self.enable()
        captured = self.capture()
        restarted = ContextStore(self.database, self.archive)
        reused = restarted.lookup("  Synthetic   2026 documentation  ", 3)
        self.assertTrue(reused["from_cache"])
        self.assertEqual(reused["sources"], [{**source, "stale": False} for source in captured["sources"]])
        self.assertFalse(reused["stale"])
        self.assertEqual(reused["at"], captured["at"])
        self.assertIsNone(restarted.lookup(self.query, 1))
        self.assertIsNone(restarted.lookup("Synthetic 2025 documentation", 3))
        later = restarted.now() + timedelta(hours=25)
        with patch.object(restarted, "now", return_value=later):
            self.assertIsNone(restarted.lookup(self.query, 3))
            stale = restarted.lookup(self.query, 3, allow_stale=True)
            self.assertEqual(stale["at"], captured["at"])
            self.assertTrue(stale["stale"])
            self.assertTrue(stale["sources"][0]["stale"])

    def test_identical_bytes_share_an_object_but_keep_capture_versions(self):
        self.enable()
        first = self.capture()["sources"][0]
        second = self.capture()["sources"][0]
        third = self.capture(sources=[{**self.source, "content": "A changed public version."}])["sources"][0]
        self.assertNotEqual(first["capture_id"], second["capture_id"])
        self.assertEqual(first["content_hash"], second["content_hash"])
        self.assertNotEqual(first["content_hash"], third["content_hash"])
        self.assertEqual(len(list(self.archive.glob("objects/sha256/*/*.txt"))), 2)
        self.assertEqual(self.store.get_capture(first["capture_id"]), first)

    def test_hash_tampering_and_missing_objects_never_reuse_saved_evidence(self):
        self.enable()
        saved = self.capture()["sources"][0]
        obj = next(self.archive.glob("objects/sha256/*/*.txt"))
        obj.write_text("Different bytes", encoding="utf-8")
        self.assertIsNone(self.store.lookup(self.query, 3))
        with self.assertRaises(KeyError):
            self.store.get_capture(saved["capture_id"])
        self.assertEqual(self.store.rebuild()["corrupt_count"], 1)
        obj.unlink()
        self.assertEqual(self.store.rebuild()["missing_count"], 1)

    def test_manifest_traversal_and_schema_tampering_are_rejected(self):
        self.enable()
        saved = self.capture()["sources"][0]
        path = self.manifests()[0]
        original = json.loads(path.read_text(encoding="utf-8"))
        for mutation in ({**original, "schema_version": 2}, {**original, "object": {**original["object"], "path": "../private/workspace.sqlite3"}},
                         {**original, "published_at": "2020-01-01"}):
            path.write_text(json.dumps(mutation), encoding="utf-8")
            with self.subTest(mutation=mutation), self.assertRaises(KeyError):
                self.store.get_capture(saved["capture_id"])
        self.assertEqual(self.store.rebuild()["corrupt_count"], 1)

    def test_deeply_nested_manifest_is_unavailable_without_blocking_healthy_sources(self):
        self.enable()
        broken = self.capture()["sources"][0]
        manifest = self.manifests()[0]
        healthy = self.capture(query="Healthy source", sources=[{
            **self.source, "url": self.source["url"] + "/healthy", "content": "Healthy historical evidence."}])["sources"][0]
        nested = "[" * 3000 + "0" + "]" * 3000
        self.assertLess(len(nested.encode()), MANIFEST_BYTES)
        manifest.write_text(nested, encoding="utf-8")
        with self.assertRaises(KeyError):
            self.store.get_capture(broken["capture_id"])
        self.assertIsNone(self.store.lookup(self.query, 3))
        self.assertEqual(self.store.search("historical evidence")["sources"], [{**healthy, "stale": False}])
        rebuilt = self.store.rebuild()
        self.assertTrue(rebuilt["rebuild_progress"]["complete"])
        self.assertEqual((rebuilt["indexed_count"], rebuilt["corrupt_count"]), (1, 1))
        self.assertEqual(self.store.get_capture(healthy["capture_id"]), healthy)
        self.assertEqual(self.store.search("historical evidence")["sources"], [{**healthy, "stale": False}])
        with self.assertRaises(KeyError):
            self.store.delete_capture(broken["capture_id"])
        self.store.delete_capture(healthy["capture_id"])
        self.store.delete_capture(healthy["capture_id"])
        self.assertEqual(self.store.search("historical evidence")["sources"], [])
        self.assertEqual(self.store.rebuild()["indexed_count"], 0)
        with self.assertRaises(KeyError):
            self.store.get_capture(healthy["capture_id"])

    def test_invalid_format_is_a_managed_error_and_preserves_existing_captures(self):
        self.enable()
        saved = self.capture()["sources"][0]
        path = self.archive / "format.json"
        original = path.read_bytes()
        nested = ("[" * 2000 + "0" + "]" * 2000).encode()
        self.assertLess(len(nested), 4096)
        files = {entry.relative_to(self.archive): entry.read_bytes()
                 for entry in self.archive.rglob("*") if entry.is_file() and entry != path}
        changed = {**self.config, "reuse_hours": 48}
        for invalid in (nested, json.dumps({"format": "maestro-public-context", "schema_version": True}).encode(),
                        json.dumps({"format": "maestro-public-context", "schema_version": 1.0}).encode()):
            with self.subTest(invalid=invalid[:80]):
                path.write_bytes(invalid)
                with self.assertRaises(ValueError):
                    self.store.configure(changed)
                self.assertEqual(self.store.status()["config"], self.config)
                self.assertEqual(self.store.get_capture(saved["capture_id"]), saved)
                self.assertEqual(path.read_bytes(), invalid)
                self.assertEqual({entry.relative_to(self.archive): entry.read_bytes()
                                  for entry in self.archive.rglob("*") if entry.is_file() and entry != path}, files)
        path.write_bytes(original)
        self.store.configure(changed)
        self.assertEqual(self.store.get_capture(saved["capture_id"]), saved)

    def test_unencodable_manifest_titles_are_corrupt_without_blocking_healthy_unicode_evidence(self):
        self.enable()
        broken = []
        for number, title in enumerate(("\ud800", "\udfff")):
            query = "Malformed title " + str(number)
            saved = self.capture(query=query)["sources"][0]
            path = next(path for path in self.manifests() if path.stem == saved["capture_id"])
            manifest = json.loads(path.read_text(encoding="utf-8"))
            manifest["title"] = title
            path.write_text(json.dumps(manifest), encoding="utf-8")
            broken.append((query, saved))
        healthy = self.capture(query="Healthy source", sources=[{
            **self.source, "title": "Healthy \U0001f680 Årsta evidence", "url": self.source["url"] + "/healthy",
            "content": "Healthy historical evidence."}])["sources"][0]
        files = {path.relative_to(self.archive): path.read_bytes() for path in self.archive.rglob("*") if path.is_file()}
        for query, source in broken:
            with self.subTest(query=query), self.assertRaises(KeyError):
                self.store.get_capture(source["capture_id"], source["content_hash"], source["manifest_hash"])
            self.assertIsNone(self.store.lookup(query, 3))
        self.assertEqual(self.store.search("historical evidence")["sources"], [{**healthy, "stale": False}])
        previous_index = self.store.index.read_bytes()
        progress = self.store.rebuild(limit=1)["rebuild_progress"]
        self.assertFalse(progress["complete"])
        self.assertEqual(self.store.index.read_bytes(), previous_index)
        self.assertEqual(self.store.get_capture(healthy["capture_id"], healthy["content_hash"], healthy["manifest_hash"]), healthy)
        while not progress["complete"]:
            rebuilt = self.store.rebuild(limit=1, continuation=progress["id"])
            progress = rebuilt["rebuild_progress"]
        self.assertEqual((rebuilt["indexed_count"], rebuilt["corrupt_count"]), (1, 2))
        self.assertEqual(self.store.get_capture(healthy["capture_id"], healthy["content_hash"], healthy["manifest_hash"]), healthy)
        self.assertEqual(self.store.search("historical evidence")["sources"], [{**healthy, "stale": False}])
        self.assertEqual({path.relative_to(self.archive): path.read_bytes() for path in self.archive.rglob("*") if path.is_file()}, files)

    def test_out_of_range_utc_manifest_times_do_not_block_healthy_sources(self):
        self.enable()
        broken = []
        for offset in ("0001-01-01T00:00:00+01:00", "9999-12-31T23:59:59-01:00"):
            query = "Invalid retrieval time " + offset
            saved = self.capture(query=query)["sources"][0]
            path = next(path for path in self.manifests() if path.stem == saved["capture_id"])
            manifest = json.loads(path.read_text(encoding="utf-8"))
            manifest["retrieved_at"] = offset
            path.write_text(json.dumps(manifest), encoding="utf-8")
            broken.append((query, saved))
        healthy = self.capture(query="Healthy source", sources=[{
            **self.source, "url": self.source["url"] + "/healthy", "content": "Healthy historical evidence."}])["sources"][0]
        for query, source in broken:
            with self.subTest(query=query):
                with self.assertRaises(KeyError):
                    self.store.get_capture(source["capture_id"], source["content_hash"], source["manifest_hash"])
                self.assertIsNone(self.store.lookup(query, 3))
        self.assertEqual(self.store.search("historical evidence")["sources"], [{**healthy, "stale": False}])
        rebuilt = self.store.rebuild()
        self.assertTrue(rebuilt["rebuild_progress"]["complete"])
        self.assertEqual((rebuilt["indexed_count"], rebuilt["corrupt_count"]), (1, 2))
        self.assertEqual(self.store.get_capture(healthy["capture_id"], healthy["content_hash"], healthy["manifest_hash"]), healthy)
        self.assertEqual(self.store.search("historical evidence")["sources"], [{**healthy, "stale": False}])

    def test_interrupted_publication_leaves_no_reusable_partial_capture(self):
        self.enable()
        original = publish_archive
        def interrupted(root, relative, data):
            if relative.startswith("records/captures/"):
                raise OSError("Synthetic disk interruption with private diagnostics")
            return original(root, relative, data)
        with patch("backend.context_store.publish_archive", side_effect=interrupted):
            result = self.capture()
        self.assertIn("archive_warning", result)
        self.assertEqual(result["sources"][0]["archive_status"], "not_saved")
        self.assertNotIn("private diagnostics", self.store.status()["last_error"])
        self.assertIsNone(self.store.lookup(self.query, 3))
        self.assertEqual(self.manifests(), [])
        self.assertEqual(self.store.rebuild()["indexed_count"], 0)

    def test_indexing_failure_preserves_capture_for_explicit_rebuild(self):
        self.enable()
        with patch.object(self.store, "_index_capture", side_effect=sqlite3.OperationalError("Synthetic failed local index")):
            result = self.capture()
        self.assertIn("archive_warning", result)
        self.assertEqual(len(self.manifests()), 1)
        self.assertIsNone(self.store.lookup(self.query, 3))
        state = self.store.rebuild()
        self.assertEqual(state["indexed_count"], 1)
        capture_id = json.loads(self.manifests()[0].read_text())["capture_id"]
        self.assertEqual(self.store.get_capture(capture_id)["content"], self.source["content"])
        self.assertIsNone(self.store.lookup(self.query, 3))  # Lost private query history is not guessed.

    def test_deletion_invalidates_cache_and_cannot_be_resurrected_by_rebuild(self):
        self.enable()
        first = self.capture()["sources"][0]
        second = self.capture(query="Other exact query")["sources"][0]
        self.store.delete_capture(first["capture_id"])
        self.store.delete_capture(first["capture_id"])
        self.assertIsNone(self.store.lookup(self.query, 3))
        with self.assertRaises(KeyError):
            self.store.get_capture(first["capture_id"])
        self.assertEqual(self.store.get_capture(second["capture_id"]), second)
        self.assertEqual(self.store.rebuild()["indexed_count"], 1)
        self.assertIsNone(self.store.lookup(self.query, 3))
        with closing(sqlite3.connect(self.store.index)) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM captures_fts WHERE id=?", (first["capture_id"],)).fetchone()[0], 0)

    def test_full_item_cap_keeps_all_deletion_and_recovery_operations_available(self):
        self.config["max_items"] = 100
        # Scale the global hard bound down, then fill it using legitimate captures.
        with patch("backend.context_store.MAX_ARCHIVE_ITEMS", 100), self.store.archive_owner():
            self.enable()
            first = self.capture()["sources"][0]
            healthy = self.capture(query="Healthy source", sources=[{
                **self.source, "url": self.source["url"] + "/healthy", "content": "Healthy historical evidence."}])["sources"][0]
            saved = [first, healthy]
            for number in range(98 - archive_inventory(self.archive, 200)[1]):
                source = self.capture(query="Repeated capture " + str(number))["sources"][0]
                self.assertEqual(source["archive_status"], "saved")
                saved.append(source)
            partial = self.capture(query="Partially saved query", sources=[self.source,
                {**self.source, "content": "A new object that exceeds the remaining allowance."},
                {**self.source, "url": self.source["url"] + "/smaller"}])
            self.assertEqual([source["archive_status"] for source in partial["sources"]], ["saved", "not_saved", "saved"])
            reused = self.store.lookup("Partially saved query", 3)
            subset = [partial["sources"][0], partial["sources"][2]]
            self.assertEqual(reused["sources"], [{**source, "stale": False} for source in subset])
            self.assertEqual(reused["at"], partial["at"])
            measured = self.store.status()
            self.assertEqual(measured["last_error"], partial["archive_warning"])
            self.assertEqual((measured["last_capture"]["sources_received"], measured["last_capture"]["sources_saved"]), (3, 2))
            saved.extend(subset)
            self.assertEqual(archive_inventory(self.archive, 200)[1], 100)
            self.store.delete_capture(first["capture_id"])
            measured = archive_inventory(self.archive, 200)[:2]
            status = self.store.status()
            self.assertEqual((status["archive_bytes"], status["archive_items"]), measured)
            self.assertEqual(measured[1], 102)  # One deletion directory and tombstone.
            self.assertIsNone(self.store.lookup(self.query, 3))
            with closing(sqlite3.connect(self.database)) as db:
                self.assertEqual(db.execute("SELECT COUNT(*) FROM context_queries WHERE query=?", (self.query,)).fetchone()[0], 0)
            with closing(sqlite3.connect(self.store.index)) as db:
                for table in ("captures", "captures_fts"):
                    self.assertEqual(db.execute("SELECT COUNT(*) FROM " + table + " WHERE id=?", (first["capture_id"],)).fetchone()[0], 0)
            self.store.delete_capture(first["capture_id"])
            self.assertEqual(archive_inventory(self.archive, 200)[:2], measured)
            self.assertEqual(self.store.rebuild()["indexed_count"], len(saved) - 1)
            self.store.configure({**self.config, "enabled": False})
            self.store.configure(self.config)
            self.assertEqual(self.store.get_capture(healthy["capture_id"], healthy["content_hash"], healthy["manifest_hash"]), healthy)
            for source in saved[1:]:
                self.store.delete_capture(source["capture_id"])
            measured = archive_inventory(self.archive, 200)[:2]
            self.assertLessEqual(measured[1], 200)
            self.assertEqual(len(list(self.archive.glob("records/deletions/*.json"))), len(saved))
            for source in saved:
                self.store.delete_capture(source["capture_id"])
                with self.assertRaises(KeyError):
                    self.store.get_capture(source["capture_id"])
            self.assertEqual(archive_inventory(self.archive, 200)[:2], measured)
            with closing(sqlite3.connect(self.database)) as db:
                self.assertEqual(db.execute("SELECT COUNT(*) FROM context_queries").fetchone()[0], 0)
            with closing(sqlite3.connect(self.store.index)) as db:
                self.assertEqual(db.execute("SELECT COUNT(*) FROM captures_fts").fetchone()[0], 0)
            self.store.configure({**self.config, "enabled": False})
            self.store.configure(self.config)
            self.assertEqual(self.store.rebuild()["indexed_count"], 0)
            self.assertIn("archive_warning", self.capture(query="New acquisition"))
            self.assertEqual(archive_inventory(self.archive, 200)[:2], measured)
            # Recovery is still bounded when unrelated external residue overfills it.
            for number in range(201 - measured[1]):
                (self.archive / ("external-" + str(number) + ".tmp")).write_bytes(b"residue")
            old_index = self.store.index.read_bytes()
            with self.assertRaisesRegex(ValueError, "scan allowance"):
                self.store.rebuild()
            with self.assertRaisesRegex(ValueError, "scan allowance"):
                self.store.configure(self.config)
            self.assertEqual(self.store.index.read_bytes(), old_index)
            self.assertEqual(self.store.search("historical evidence")["sources"], [])

    def test_lowered_acquisition_cap_does_not_prevent_existing_archive_rebuild(self):
        self.config["max_items"] = 200
        with patch("backend.context_store.MAX_ARCHIVE_ITEMS", 200), self.store.archive_owner():
            self.enable()
            saved = self.capture()["sources"][0]
            path = self.manifests()[0]
            template = json.loads(path.read_text(encoding="utf-8"))
            for number in range(100):
                capture_id = format(number, "032x")
                (path.parent / (capture_id + ".json")).write_text(json.dumps({**template, "capture_id": capture_id}), encoding="utf-8")
            measured = archive_inventory(self.archive, 400)[:2]
            self.assertGreater(measured[1], 100)
            self.config["max_items"] = 100
            self.store.configure(self.config)
            status = self.store.rebuild()
            self.assertEqual(status["indexed_count"], 101)
            self.assertEqual((status["archive_bytes"], status["archive_items"]), measured)
            self.assertEqual(self.store.get_capture(saved["capture_id"], saved["content_hash"], saved["manifest_hash"]), saved)
            self.assertIn("archive_warning", self.capture(query="Over the lowered cap"))
            self.assertEqual(archive_inventory(self.archive, 400)[:2], measured)

    def test_disabling_or_changing_approved_scope_suppresses_retrieval(self):
        self.enable()
        saved = self.capture()["sources"][0]
        self.store.configure({**self.config, "public_sources": ["https://other.example.org/"]})
        self.assertIsNone(self.store.lookup(self.query, 3))
        with self.assertRaises(KeyError):
            self.store.get_capture(saved["capture_id"])
        self.store.configure({**self.config, "enabled": False})
        before = sorted(path.as_posix() for path in self.archive.rglob("*"))
        self.capture()
        self.assertEqual(before, sorted(path.as_posix() for path in self.archive.rglob("*")))

    def test_byte_item_and_free_space_limits_do_not_damage_existing_capture(self):
        self.enable()
        saved = self.capture()["sources"][0]
        for metric in ("items", "bytes", "free"):
            with self.subTest(metric=metric):
                inventory = (self.config["max_bytes"] if metric == "bytes" else 100, self.config["max_items"] if metric == "items" else 5, [])
                with patch("backend.context_store.archive_inventory", return_value=inventory), patch("backend.context_store.shutil.disk_usage") as usage:
                    usage.return_value.free = 0 if metric == "free" else 10 ** 10
                    result = self.capture(query="New query", sources=[{**self.source, "content": "A fresh version"}])
                self.assertIn("archive_warning", result)
                self.assertEqual(result["sources"][0]["archive_status"], "not_saved")
                self.assertIsNone(self.store.lookup("New query", 3))
                self.assertEqual(self.store.status()["last_capture"]["sources_saved"], 0)
                self.assertEqual(self.store.get_capture(saved["capture_id"]), saved)
                self.assertEqual(len(self.manifests()), 1)

    def test_rebuild_replaces_corrupt_index_and_obeys_limit_without_partial_replacement(self):
        self.enable()
        first = self.capture()["sources"][0]
        self.capture(query="Other query")
        partial = self.store.rebuild(limit=1)
        self.assertFalse(partial["rebuild_progress"]["complete"])
        self.assertEqual(partial["rebuild_progress"]["processed"], 1)
        self.assertEqual(self.store.get_capture(first["capture_id"]), first)
        self.store.index.write_bytes(b"Synthetic corrupt database")
        self.assertIn("rebuilding", self.store.status()["last_error"])
        self.assertEqual(self.store.rebuild()["indexed_count"], 2)
        with closing(sqlite3.connect(self.store.index)) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM captures_fts WHERE captures_fts MATCH 'historical'").fetchone()[0], 2)

    def test_lifetime_owner_is_reentrant_and_does_not_block_api_threads(self):
        self.enable()
        errors = []
        with self.store.archive_owner():
            def work():
                try:
                    self.capture()
                    self.store.configure(self.config)
                except Exception as error:
                    errors.append(error)
            thread = threading.Thread(target=work, daemon=True)
            thread.start()
            thread.join(timeout=5)
            self.assertFalse(thread.is_alive(), "The lifespan claim must not hold a thread RLock across its yield.")
            self.assertEqual(errors, [])
        self.assertFalse((self.archive / "writer-owner.tmp").exists())

    def test_independent_workspaces_and_stale_claims_cannot_take_writer_ownership(self):
        self.enable()
        other = ContextStore(self.root / "other-private" / "workspace.sqlite3", self.archive)
        with self.store.archive_owner():
            with self.assertRaisesRegex(ValueError, "owns"):
                other.configure(self.config)
            self.assertFalse(other.status()["available"])
        claim = self.archive / "writer-owner.tmp"
        claim.write_text("synthetic-stale-owner-token", encoding="ascii")
        with self.assertRaisesRegex(ValueError, "previous Maestro"):
            self.store.configure(self.config)
        self.store.record_error("Synthetic private exception should not be persisted")
        self.assertFalse(self.store.status()["available"])
        self.assertNotIn("private exception", self.store.status()["last_error"])
        self.assertEqual(claim.read_text(), "synthetic-stale-owner-token")

    def test_missing_or_tampered_live_claim_reports_unavailable_and_refuses_configure_and_capture(self):
        self.enable()
        claim = self.archive / "writer-owner.tmp"
        with self.store.archive_owner() as owner:
            self.assertIs(owner, self.store)
            token = claim.read_bytes()
            for replacement in (None, b"Synthetic changed claim"):
                with self.subTest(replacement=replacement):
                    claim.unlink() if replacement is None else claim.write_bytes(replacement)
                    self.assertFalse(self.store.status()["available"])
                    with self.assertRaisesRegex(ValueError, "writer claim changed"):
                        self.store.configure(self.config)
                    result = self.capture()
                    self.assertIn("archive_warning", result)
                    self.assertNotEqual(result["sources"][0]["archive_status"], "saved")
                    self.assertEqual(self.store.status()["last_capture"]["sources_saved"], 0)
                    self.assertEqual(self.manifests(), [])
                    self.assertEqual(claim.read_bytes() if claim.exists() else None, replacement)
                    claim.write_bytes(token)

    def test_replaced_archive_root_and_ancestor_cannot_claim_or_create_private_storage(self):
        private = self.database.parent
        private.mkdir()
        for ancestor in (False, True):
            archive = self.root / ("linked-parent/public-archive" if ancestor else "linked-root")
            store = ContextStore(self.database, archive)
            link = archive.parent if ancestor else archive
            if os.name == "nt":
                created = subprocess.run(["cmd.exe", "/c", "mklink", "/J", str(link), str(private)],
                                         capture_output=True, text=True, timeout=10)
                self.assertEqual(created.returncode, 0, created.stderr)
            else:
                link.symlink_to(private, target_is_directory=True)
            try:
                with self.subTest(ancestor=ancestor):
                    self.assertFalse(store.status()["available"])
                    with self.assertRaises(ValueError):
                        with store.archive_owner():
                            self.fail("A redirected archive cannot acquire writer ownership.")
                    self.assertEqual(list(private.iterdir()), [])
                    if ancestor:
                        (private / "public-archive").mkdir()
                        self.assertFalse(store.status()["available"])
            finally:
                link.rmdir() if os.name == "nt" else link.unlink()

    def test_overlapping_owner_contexts_retain_claim_until_the_last_exit(self):
        entered, release = threading.Event(), threading.Event()
        errors = []
        def work():
            try:
                with self.store.archive_owner():
                    entered.set()
                    if not release.wait(timeout=10):
                        raise TimeoutError("The owner test did not release its worker.")
            except Exception as error:
                errors.append(error)
            finally:
                entered.set()
        thread = threading.Thread(target=work, daemon=True)
        claim = self.archive / "writer-owner.tmp"
        other = ContextStore(self.root / "other-private" / "workspace.sqlite3", self.archive)
        try:
            with self.store.archive_owner():
                token = claim.read_bytes()
                thread.start()
                self.assertTrue(entered.wait(timeout=5))
                self.assertEqual(errors, [])
            self.assertTrue(thread.is_alive())
            self.assertEqual(claim.read_bytes(), token)
            with self.assertRaisesRegex(ValueError, "owns"):
                with other.archive_owner():
                    self.fail("Another store cannot claim an archive while its owner is active.")
        finally:
            release.set()
            if thread.ident is not None:
                thread.join(timeout=5)
        self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])
        self.assertFalse(claim.exists())
        with other.archive_owner():
            self.assertTrue(claim.exists())
        self.assertFalse(claim.exists())

    def test_index_cannot_redirect_a_capture_id_to_another_verified_manifest(self):
        self.enable()
        first = self.capture()["sources"][0]
        second = self.capture(query="Second source", sources=[{
            **self.source, "url": self.source["url"] + "/second", "content": "Different public evidence."}])["sources"][0]
        with closing(sqlite3.connect(self.store.index)) as db, db:
            row = db.execute("SELECT manifest,manifest_hash FROM captures WHERE id=?", (second["capture_id"],)).fetchone()
            db.execute("UPDATE captures SET manifest=?,manifest_hash=? WHERE id=?", (*row, first["capture_id"]))
        for action in (self.store.get_capture, self.store.delete_capture):
            with self.subTest(action=action.__name__), self.assertRaises(KeyError):
                action(first["capture_id"])
        self.assertFalse((self.archive / "records" / "deletions" / (first["capture_id"] + ".json")).exists())
        self.assertIsNone(self.store.lookup(self.query, 3))
        self.assertEqual(self.store.get_capture(second["capture_id"]), second)

    def test_replaced_manifest_cannot_change_a_hash_bound_view_or_query_reuse(self):
        self.enable()
        first = self.capture()["sources"][0]
        manifest_path = self.manifests()[0]
        manifest = json.loads(manifest_path.read_text())
        replacement = b"Self-consistent replacement with different historical facts."
        content_hash = hashlib.sha256(replacement).hexdigest()
        relative = "objects/sha256/" + content_hash[:2] + "/" + content_hash + ".txt"
        destination = self.archive / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(replacement)
        manifest["object"].update(path=relative, sha256=content_hash, bytes=len(replacement))
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        self.store.rebuild()
        with self.assertRaises(KeyError):
            self.store.get_capture(first["capture_id"], expected_hash=first["content_hash"])
        self.assertIsNone(self.store.lookup(self.query, 3))
        with self.assertRaisesRegex(ValueError, "evidence changed"):
            with self.store.evidence_guard([first]):
                self.fail("Changed historical evidence must not enter an answer commit.")

    def test_metadata_replacement_is_rejected_before_and_after_index_rebuild(self):
        self.enable()
        first = self.capture()["sources"][0]
        path = self.manifests()[0]
        original = json.loads(path.read_text())
        original_at = original["retrieved_at"]
        for field, replacement in (("title", "Replaced historical title"), ("retrieved_at", original_at[:8] + ("02" if original_at[8:10] != "02" else "03") + original_at[10:])):
            manifest = {**original, field: replacement}
            path.write_text(json.dumps(manifest), encoding="utf-8")
            with self.subTest(field=field):
                with self.assertRaises(KeyError):
                    self.store.get_capture(first["capture_id"])
                self.store.rebuild()
                with self.assertRaises(KeyError):
                    self.store.get_capture(first["capture_id"], expected_hash=first["content_hash"], expected_manifest_hash=first["manifest_hash"])
                self.assertIsNone(self.store.lookup(self.query, 3))
                with self.assertRaisesRegex(ValueError, "evidence changed"):
                    with self.store.evidence_guard([first]):
                        self.fail("Changed capture metadata cannot enter an answer commit.")
            path.write_text(json.dumps(original), encoding="utf-8")
            self.store.rebuild()

    def test_interrupted_tmp_files_count_toward_archive_budgets(self):
        self.config = {**self.config, "max_bytes": 1024 * 1024}
        self.enable()
        residue = self.archive / "interrupted.tmp"
        residue.write_bytes(b"x" * self.config["max_bytes"])
        result = self.capture()
        self.assertEqual(result["sources"][0]["archive_status"], "not_saved")
        self.assertIn("archive_warning", result)
        self.assertEqual(self.manifests(), [])
        residue.unlink()
        for i in range(100):
            # A smaller valid item allowance still exercises the real scan.
            (self.archive / (str(i) + ".tmp")).write_bytes(b"residue")
        self.store.configure({**self.config, "max_items": 100})
        self.assertIn("archive_warning", self.capture())
        self.assertEqual(self.manifests(), [])

    def test_guard_blocks_deletion_and_scope_changes_until_answer_commit_completes(self):
        self.enable()
        for operation in ("delete", "disable"):
            self.store.configure(self.config)
            saved = self.capture()["sources"][0]
            attempted, completed = threading.Event(), threading.Event()
            errors = []
            def mutation():
                attempted.set()
                try:
                    if operation == "delete":
                        self.store.delete_capture(saved["capture_id"])
                    else:
                        self.store.configure({**self.config, "enabled": False})
                except Exception as error:
                    errors.append(error)
                completed.set()
            with self.subTest(operation=operation):
                with self.store.archive_owner(), self.store.evidence_guard([saved]):
                    thread = threading.Thread(target=mutation, daemon=True)
                    thread.start()
                    self.assertTrue(attempted.wait(2))
                    self.assertFalse(completed.wait(0.05))
                    # Mutation lock then private DB is the provider's commit ordering.
                    with self.store._transaction() as (_, db):
                        db.execute("SELECT 1")
                thread.join(3)
                self.assertTrue(completed.is_set())
                self.assertEqual(errors, [])
                with self.assertRaisesRegex(ValueError, "evidence changed"):
                    with self.store.evidence_guard([saved]):
                        self.fail("Invalidated evidence cannot be committed.")


    def test_keyword_search_reuses_changed_query_and_requires_every_literal_term(self):
        self.enable()
        saved = self.capture()["sources"][0]
        self.assertIsNone(self.store.lookup("distinct evidence", 3))
        result = self.store.search("  distinct   evidence ")
        self.assertEqual(result["query"], "distinct evidence")
        self.assertEqual(result["sources"], [{**saved, "stale": False}])
        self.assertEqual(result["at"], saved["retrieved_at"])
        self.assertEqual(result["retrieval"], "keyword")
        self.assertTrue(result["from_cache"])
        self.assertFalse(result["stale"])
        self.assertFalse(result["search_limited"])
        for query in ("unrelated topic", "historical missingkeyword", "histor*"):
            self.assertEqual(self.store.search(query)["sources"], [])
        for query in ('historical OR missingkeyword', 'content:historical', '"historical" NOT evidence',
                      'historical NEAR(evidence)', 'historical) OR (absent'):
            with self.subTest(query=query):
                self.assertEqual(self.store.search(query)["sources"], [])
        literal = self.capture(sources=[{**self.source, "url": self.source["url"] + "/literal",
                                        "content": "literal alpha OR beta terminology"}])["sources"][0]
        self.assertEqual(self.store.search("alpha OR beta")["sources"], [{**literal, "stale": False}])

    def test_keyword_search_unicode_and_title_weight_have_deterministic_order(self):
        self.enable()
        now = self.store.now()
        title = self.capture(at=now.isoformat(), sources=[{**self.source, "title": "Archive quasar", "content": "ordinary body",
                                                        "url": self.source["url"] + "/title"}])["sources"][0]
        body = self.capture(at=(now - timedelta(seconds=1)).isoformat(), sources=[{**self.source, "title": "Ordinary archive", "content": "body quasar",
                                                                               "url": self.source["url"] + "/body"}])["sources"][0]
        for _ in range(2):
            self.assertEqual([item["capture_id"] for item in self.store.search("quasar", 2)["sources"]], [title["capture_id"], body["capture_id"]])
        unicode = self.capture(sources=[{**self.source, "url": self.source["url"] + "/unicode",
                                        "title": "Räksmörgås café", "content": "日本語 München cafés"}])["sources"][0]
        for query in ("räksmörgås CAFÉ", "日本語 münchen", "cafe\u0301s"):
            self.assertEqual(self.store.search(query)["sources"], [{**unicode, "stale": False}])

    def test_keyword_search_validates_bounds_and_filters_before_any_storage_access(self):
        cases = [({"query": ""}, "query"), ({"query": "a" * 513}, "query"),
                 ({"query": "???"}, "keywords"), ({"query": " ".join("word" for _ in range(17))}, "keywords")]
        cases.extend((({"query": "word", "limit": limit}, "results") for limit in (True, 0, 21, 1.0, "3")))
        cases.extend((({"query": "word", "domain": domain}, "hostname") for domain in (
            "https://docs.example.org", "docs.example.org/path", "docs.example.org:443", "*.example.org", " docs.example.org",
            "docs.example.org.", "localhost", "127.0.0.1", "1.1.1.1", "-bad.example.org", "bad-.example.org", "a" * 64 + ".example.org")))
        cases.extend((({"query": "word", "retrieved_from": value}, "dates") for value in ("2026-2-03", "2026-02-30", "2026-01-01T00:00:00Z", 20260101)))
        cases.append(({"query": "word", "retrieved_from": "2026-02-01", "retrieved_to": "2026-01-01"}, "start date"))
        with patch.object(self.store, "_state", side_effect=AssertionError("Invalid searches must not access storage")):
            for arguments, message in cases:
                with self.subTest(arguments=arguments), self.assertRaisesRegex(ValueError, message):
                    self.store.search(**arguments)
        self.assertFalse(self.database.exists())
        self.assertEqual(validate_search("  public  facts ", 20, domain="BÜCHER.Example", retrieved_from="2024-02-29"),
                         {"query": "public facts", "limit": 20, "domain": "xn--bcher-kva.example", "retrieved_from": "2024-02-29", "retrieved_to": None})

    def test_keyword_search_exact_domain_and_inclusive_utc_date_filters(self):
        self.config = {**self.config, "public_sources": ["https://docs.example.org/", "https://sub.docs.example.org/", "https://bücher.example/"]}
        self.enable()
        now = datetime(2026, 3, 3, 12, tzinfo=timezone.utc)
        sources = [("https://docs.example.org/first", "2026-03-01T01:00:00+01:00"),
                   ("https://docs.example.org/last", "2026-03-02T23:59:59.999999+00:00"),
                   ("https://sub.docs.example.org/sibling", "2026-03-02T12:00:00+00:00"),
                   ("https://docs.example.org/excluded", "2026-03-03T00:00:00+00:00"),
                   ("https://bücher.example/unicode", "2026-03-02T12:00:00+00:00")]
        saved = [self.capture(at=at, sources=[{**self.source, "url": url}])["sources"][0] for url, at in sources]
        with patch.object(self.store, "now", return_value=now):
            result = self.store.search("historical", 20, domain="DOCS.EXAMPLE.ORG", retrieved_from="2026-03-01", retrieved_to="2026-03-02", allow_stale=True)
            self.assertEqual({source["capture_id"] for source in result["sources"]}, {saved[0]["capture_id"], saved[1]["capture_id"]})
            self.assertEqual(result["at"], saved[1]["retrieved_at"])
            self.assertEqual(self.store.search("historical", domain="sub.docs.example.org")["sources"][0]["capture_id"], saved[2]["capture_id"])
            self.assertEqual(self.store.search("historical", domain="BÜCHER.EXAMPLE")["sources"][0]["capture_id"], saved[4]["capture_id"])

    def test_keyword_search_ttl_is_per_source_and_excludes_future_even_when_historical_allowed(self):
        self.enable()
        now = datetime(2026, 3, 3, 12, tzinfo=timezone.utc)
        times = [now, now - timedelta(hours=24), now - timedelta(hours=24, microseconds=1), now + timedelta(microseconds=1)]
        saved = [self.capture(at=at.isoformat(), sources=[{**self.source, "url": self.source["url"] + "/" + str(i)}])["sources"][0]
                 for i, at in enumerate(times)]
        with patch.object(self.store, "now", return_value=now):
            fresh = self.store.search("historical", 20)
            self.assertEqual({item["capture_id"] for item in fresh["sources"]}, {item["capture_id"] for item in saved[:2]})
            self.assertFalse(fresh["stale"])
            historical = self.store.search("historical", 20, allow_stale=True)
            self.assertEqual({item["capture_id"] for item in historical["sources"]}, {item["capture_id"] for item in saved[:3]})
            self.assertTrue(historical["stale"])
            self.assertEqual({item["capture_id"]: item["stale"] for item in historical["sources"]},
                             {saved[0]["capture_id"]: False, saved[1]["capture_id"]: False, saved[2]["capture_id"]: True})

    def test_keyword_search_uses_newest_eligible_url_version_even_when_older_ranks_better(self):
        self.enable()
        now = datetime(2026, 3, 3, 12, tzinfo=timezone.utc)
        old = self.capture(at=(now - timedelta(days=2)).isoformat(), sources=[{**self.source, "title": "quasar quasar quasar", "content": "quasar previous fact"}])["sources"][0]
        new = self.capture(at=(now - timedelta(hours=1)).isoformat(), sources=[{**self.source, "url": "https://DOCS.EXAMPLE.ORG:443/docs/page",
                                                                               "title": "Current facts", "content": "quasar revised fact"}])["sources"][0]
        with patch.object(self.store, "now", return_value=now):
            self.assertEqual(self.store.search("quasar", allow_stale=True)["sources"], [{**new, "stale": False}])
            self.assertEqual(self.store.search("quasar", retrieved_to="2026-03-01", allow_stale=True)["sources"], [{**old, "stale": True}])
            no_match = self.capture(at=(now - timedelta(microseconds=1)).isoformat(), sources=[{**self.source, "content": "Current version removed the previous term"}])["sources"][0]
            self.assertEqual(self.store.search("quasar", allow_stale=True)["sources"], [])
            self.store.delete_capture(no_match["capture_id"])
            self.assertEqual(self.store.search("quasar", allow_stale=True)["sources"], [{**new, "stale": False}])
            self.store.rebuild()
            self.assertEqual(self.store.search("quasar", allow_stale=True)["sources"], [{**new, "stale": False}])

    def test_keyword_search_never_returns_corrupt_deleted_missing_or_out_of_scope_evidence(self):
        self.enable()
        saved = [self.capture(sources=[{**self.source, "url": self.source["url"] + "/" + str(i), "content": "historical distinct body " + str(i)}])["sources"][0]
                 for i in range(4)]
        self.store.delete_capture(saved[0]["capture_id"])
        for source, replacement in ((saved[1], b"Changed object bytes"), (saved[2], None)):
            path = self.archive / "objects" / "sha256" / source["content_hash"][:2] / (source["content_hash"] + ".txt")
            if replacement is None:
                path.unlink()
            else:
                path.write_bytes(replacement)
        self.assertEqual(self.store.search("historical")["sources"], [{**saved[3], "stale": False}])
        manifest = next(path for path in self.manifests() if path.stem == saved[3]["capture_id"])
        original = json.loads(manifest.read_text())
        manifest.write_text(json.dumps({**original, "title": "Metadata replaced"}))
        self.assertEqual(self.store.search("historical")["sources"], [])
        manifest.write_text(json.dumps(original))
        self.store.configure({**self.config, "public_sources": ["https://other.example.org/"]})
        self.assertEqual(self.store.search("historical")["sources"], [])

    def test_keyword_search_is_read_only_and_disabled_unconfigured_or_missing_archive_is_empty(self):
        self.assertEqual(self.store.search("historical")["sources"], [])
        self.assertFalse(self.database.exists())
        self.assertFalse(self.archive.exists())
        self.enable()
        self.capture()
        def files():
            return {str(path): (path.read_bytes(), path.stat().st_mtime_ns) for path in self.root.rglob("*") if path.is_file()}
        before = files()
        with patch.object(self.store, "archive_owner", side_effect=AssertionError("Search must not claim a writer")), patch.object(self.store, "initialize", side_effect=AssertionError("Search must not initialize storage")):
            self.assertEqual(len(self.store.search("evidence")["sources"]), 1)
            self.assertEqual(self.store.search("missing")["sources"], [])
        self.assertEqual(files(), before)
        other = ContextStore(self.database, archive_dir="")
        self.assertEqual(other.search("historical")["sources"], [])
        missing = ContextStore(self.database, self.root / "missing-archive")
        self.assertEqual(missing.search("historical")["sources"], [])
        self.store.configure({**self.config, "enabled": False})
        self.assertEqual(self.store.search("historical")["sources"], [])

    def test_keyword_search_reports_corrupt_index_and_recovers_only_after_explicit_rebuild(self):
        self.enable()
        saved = self.capture()["sources"][0]
        self.store.index.write_bytes(b"Synthetic corrupt index")
        with self.assertRaisesRegex(ValueError, "local source index could not be searched"):
            self.store.search("historical")
        self.assertEqual(self.store.index.read_bytes(), b"Synthetic corrupt index")
        self.store.rebuild()
        self.assertEqual(self.store.search("historical")["sources"], [{**saved, "stale": False}])
        with closing(sqlite3.connect(self.store.index)) as db, db:
            db.execute("UPDATE captures SET content='Synthetic incorrect indexed evidence'")
        self.assertEqual(self.store.search("historical")["sources"], [])

    def test_keyword_search_bounds_candidate_validation_and_sql_work(self):
        self.enable()
        sources = [{**self.source, "url": self.source["url"] + "/" + str(i)} for i in range(5)]
        for source in sources:
            self.capture(sources=[source])
        original = self.store.get_capture
        with patch("backend.context_store.SEARCH_CANDIDATES", 2), patch.object(self.store, "get_capture", wraps=original) as get:
            result = self.store.search("historical", 20)
            self.assertLessEqual(len(result["sources"]), 2)
            self.assertLessEqual(get.call_count, 2)
            self.assertTrue(result["search_limited"])
        with patch("backend.context_store.SEARCH_SECONDS", -1):
            with self.assertRaisesRegex(ValueError, "exceeded its work allowance"):
                self.store.search("historical")

    def test_keyword_search_bounds_distinct_urls_without_repeated_versions_starving_other_evidence(self):
        self.enable()
        now = self.store.now()
        for i in range(6):
            self.capture(at=(now - timedelta(seconds=10 - i)).isoformat(),
                         sources=[{**self.source, "title": "quasar quasar quasar", "content": "quasar recurring facts"}])
        other = self.capture(at=(now - timedelta(seconds=1)).isoformat(),
                             sources=[{**self.source, "url": self.source["url"] + "/other",
                                       "title": "Public source", "content": "quasar distinct fact"}])["sources"][0]
        with patch("backend.context_store.SEARCH_CANDIDATES", 4):
            result = self.store.search("quasar")
            self.assertEqual(len(result["sources"]), 2)
            self.assertFalse(result["search_limited"])
            self.capture(at=now.isoformat(), sources=[{**self.source, "title": "Revised source", "content": "Previous term removed"}])
            result = self.store.search("quasar")
            self.assertEqual(result["sources"], [{**other, "stale": False}])
            self.assertFalse(result["search_limited"])

    def test_keyword_search_marks_empty_partial_result_when_unvalidated_versions_hit_allowance(self):
        self.enable()
        for i in range(4):
            self.capture(at=(self.store.now() - timedelta(seconds=i)).isoformat())
        for manifest in self.manifests():
            value = json.loads(manifest.read_text())
            manifest.write_text(json.dumps({**value, "title": "Synthetic tampered metadata"}))
        with patch("backend.context_store.SEARCH_CANDIDATES", 2):
            result = self.store.search("historical")
        self.assertEqual(result["sources"], [])
        self.assertTrue(result["search_limited"])


if __name__ == "__main__":
    unittest.main()

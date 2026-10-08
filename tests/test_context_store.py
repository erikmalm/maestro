"""Synthetic capture-core fixtures; no real archive, network, keys or model calls."""
from datetime import timedelta
from contextlib import closing, contextmanager
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

from backend.context_policy import SCHEMA, decoded_url_variants, public_url
from backend.context_store import ContextStore, DEFAULT, ROOT
from backend.storage import archive_inventory, publish_archive


class ContextStoreTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory(prefix="maestro-context-test-")))
        self.archive = self.root / "public-archive"
        self.database = self.root / "private" / "workspace.sqlite3"
        self.store = ContextStore(self.database, self.archive)
        self.config = {**DEFAULT, "enabled": True, "capture_policy": "approved_sources",
                       "public_sources": ["https://docs.example.org/docs/"]}
        self.query = "Synthetic 2026 documentation"
        self.source = {"title": "Synthetic public documentation", "url": "https://docs.example.org/docs/page",
                       "content": "Public excerpt with distinct historical evidence.", "maestro_truncated": True}

    def enable(self):
        self.store.configure(self.config)

    def capture(self, **changes):
        return self.store.capture(changes.get("query", self.query), changes.get("at", self.store.now().isoformat()),
                                  changes.get("sources", [self.source]), changes.get("max_results", 3), changes.get("secrets", ()))

    def seed_source(self):
        self.enable()
        return self.capture()["sources"][0]

    def assert_missing_capture(self, capture_id):
        with self.assertRaises(KeyError):
            self.store.get_capture(capture_id)

    def manifests(self):
        return list(self.archive.glob("records/captures/*/*.json"))

    def test_constructor_status_and_disabled_capture_do_not_create_storage(self):
        with patch.object(Path, "is_dir", side_effect=PermissionError("Synthetic archive attribute denial")):
            self.assertFalse(self.store.status()["available"])
        self.assertFalse(self.store.status()["available"])
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
        self.assertEqual(self.store.get_capture(result["sources"][0]["capture_id"]), result["sources"][0])

    def test_cleanup_failures_leave_inventory_beyond_confirmed_publication_receipts(self):
        for kind, segment in (("object", "objects"), ("manifest", "records")):
            with self.subTest(kind=kind):
                archive = self.root / kind
                store = ContextStore(self.root / "private" / kind / "workspace.sqlite3", archive)
                store.configure(self.config)
                unlink = Path.unlink
                def fail_cleanup(path, *args, **kwargs):
                    if path.suffix == ".tmp" and archive / segment in path.parents:
                        raise PermissionError("Synthetic staging cleanup failure")
                    return unlink(path, *args, **kwargs)
                with store.archive_owner():
                    before = archive_inventory(archive, self.config["max_items"])[0]
                    with patch.object(Path, "unlink", fail_cleanup):
                        result = store.capture(self.query, store.now().isoformat(), [self.source], 3)
                    status = store.status()
                    receipt = status["last_capture"]
                    confirmed = len(self.source["content"].encode()) if kind == "manifest" else 0
                    self.assertEqual((receipt["object_bytes"], receipt["manifest_bytes"], receipt["new_bytes"]), (confirmed, 0, confirmed))
                    self.assertEqual((receipt["sources_saved"], receipt["excerpt_bytes"], status["indexed_count"]), (0, 0, 0))
                    self.assertIn("archive_warning", result)
                    self.assertEqual(result["sources"][0]["archive_status"], "not_saved")
                    residue = list((archive / segment).rglob("*.tmp"))
                    self.assertEqual(len(residue), 1)
                    destination = residue[0].with_name(residue[0].name.rsplit(".", 2)[0])
                    self.assertEqual(destination.read_bytes(), residue[0].read_bytes())
                    self.assertEqual(status["archive_bytes"], archive_inventory(archive, self.config["max_items"])[0])
                    self.assertGreater(status["archive_bytes"] - before, receipt["new_bytes"])

    def test_failed_capture_recovery_cannot_overwrite_concurrent_capture_usage(self):
        self.enable()
        scanned, release, checked = threading.Event(), threading.Event(), threading.Event()
        results, errors, scans = {}, [], 0
        original = self.store._index_capture
        def index(*args):
            if threading.current_thread() is first:
                raise sqlite3.OperationalError("Synthetic failed index")
            return original(*args)
        def inventory(*args):
            nonlocal scans
            value = archive_inventory(*args)
            if threading.current_thread() is first:
                scans += 1
                if scans == 2:
                    scanned.set()
                    if not release.wait(5):
                        raise TimeoutError("The recovery scan test was not released.")
            return value
        def capture(label, source):
            try:
                if label == "second":
                    results["lock_available"] = self.store._write_lock.acquire(blocking=False)
                    if results["lock_available"]:
                        self.store._write_lock.release()
                    checked.set()
                results[label] = self.capture(query=label, sources=[source])
            except Exception as error:
                errors.append(error)
        first = threading.Thread(target=capture, args=("first", self.source), daemon=True)
        second = threading.Thread(target=capture, args=("second", {
            **self.source, "url": self.source["url"] + "/second", "content": "Second public evidence."}), daemon=True)
        with self.store.archive_owner(), patch.object(self.store, "_index_capture", side_effect=index), \
                patch("backend.context_store.archive_inventory", side_effect=inventory):
            try:
                first.start()
                self.assertTrue(scanned.wait(5))
                second.start()
                self.assertTrue(checked.wait(5))
                self.assertFalse(results["lock_available"])
                self.assertNotIn("second", results)
            finally:
                release.set()
                first.join(5)
                if second.ident is not None:
                    second.join(5)
            self.assertFalse(first.is_alive() or second.is_alive())
            self.assertEqual(errors, [])
            self.assertIn("archive_warning", results["first"])
            actual = archive_inventory(self.archive, self.config["max_items"])
            status = self.store.status()
            self.assertEqual((status["archive_bytes"], status["archive_items"]), actual[:2])
            self.assertEqual(status["last_capture"]["sources_saved"], 1)
            source = results["second"]["sources"][0]
            self.assertEqual(self.store.get_capture(source["capture_id"], source["content_hash"], source["manifest_hash"]), source)

    def test_capture_writer_exit_keeps_recovery_ahead_of_a_successful_retry(self):
        self.enable()
        exited, release, checked = threading.Event(), threading.Event(), threading.Event()
        results, errors = {}, []
        original_writer, original_index = self.store._writer, self.store._index_capture

        @contextmanager
        def paused_writer():
            try:
                with original_writer():
                    yield
            except sqlite3.Error:
                if threading.current_thread() is first:
                    # Pause after the original writer has exited, before capture
                    # enters its recovery handler: a normal preemption point.
                    exited.set()
                    if not release.wait(5):
                        raise TimeoutError("The writer-exit recovery test was not released.")
                raise

        def index(*args):
            if threading.current_thread() is first:
                raise sqlite3.OperationalError("Synthetic first-capture index failure")
            return original_index(*args)

        def capture(label, at):
            try:
                if label == "retry":
                    acquired = self.store._write_lock.acquire(blocking=False)
                    results["lock_available"] = acquired
                    if acquired:
                        self.store._write_lock.release()
                    checked.set()
                results[label] = self.capture(query=label, at=at, sources=[{
                    **self.source, "url": self.source["url"] + "/" + label,
                    "content": label + " public evidence."}])
            except Exception as error:
                errors.append(error)

        first = threading.Thread(target=capture, args=("first", "2026-10-07T10:00:00+00:00"), daemon=True)
        retry = threading.Thread(target=capture, args=("retry", "2026-10-07T10:00:01+00:00"), daemon=True)
        with self.store.archive_owner(), patch.object(self.store, "_writer", side_effect=paused_writer), \
                patch.object(self.store, "_index_capture", side_effect=index):
            try:
                first.start()
                self.assertTrue(exited.wait(5))
                retry.start()
                self.assertTrue(checked.wait(5))
                self.assertFalse(results["lock_available"])
                self.assertNotIn("retry", results)
            finally:
                release.set()
                first.join(5)
                if retry.ident is not None:
                    retry.join(5)
            self.assertFalse(first.is_alive() or retry.is_alive())
            self.assertEqual(errors, [])
            self.assertIn("archive_warning", results["first"])
            self.assertNotIn("archive_warning", results["retry"])
            status = self.store.status()
            self.assertIsNone(status["last_error"])
            self.assertEqual((status["last_capture"]["retrieved_at"], status["last_capture"]["sources_saved"]),
                             ("2026-10-07T10:00:01+00:00", 1))
            self.assertEqual((status["archive_bytes"], status["archive_items"]),
                             archive_inventory(self.archive, self.config["max_items"])[:2])
            source = results["retry"]["sources"][0]
            self.assertEqual(self.store.get_capture(source["capture_id"], source["content_hash"], source["manifest_hash"]), source)

    def test_initialization_recovery_does_not_restore_concurrently_revoked_permissions(self):
        source = self.seed_source()
        paused, release, ordered = threading.Event(), threading.Event(), threading.Event()
        errors = []
        original = self.store.configure
        revoked = {**self.config, "enabled": False, "public_sources": ["https://docs.example.org/new/"]}
        def configure(config):
            if threading.current_thread() is initializing:
                paused.set()
                if not release.wait(5):
                    raise TimeoutError("The initialization test was not released.")
            return original(config)
        def work(initial):
            acquired = False
            try:
                if initial:
                    self.store.initialize()
                else:
                    # Finish revocation first if initialization left its snapshot unguarded.
                    acquired = self.store._write_lock.acquire(blocking=False)
                    if not acquired:
                        ordered.set()
                    original(revoked)
            except Exception as error:
                errors.append(error)
            finally:
                if acquired:
                    self.store._write_lock.release()
                if not initial:
                    ordered.set()
        initializing = threading.Thread(target=work, args=(True,), daemon=True)
        revoking = threading.Thread(target=work, args=(False,), daemon=True)
        with patch.object(self.store, "configure", side_effect=configure):
            try:
                initializing.start()
                self.assertTrue(paused.wait(5))
                revoking.start()
                self.assertTrue(ordered.wait(5))
            finally:
                release.set()
                initializing.join(5)
                if revoking.ident is not None:
                    revoking.join(5)
        self.assertFalse(initializing.is_alive() or revoking.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(self.store.status()["config"], revoked)
        with closing(sqlite3.connect(self.database)) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM context_queries").fetchone()[0], 0)
        with self.assertRaises(KeyError):
            self.store.get_capture(source["capture_id"], source["content_hash"], source["manifest_hash"])

    def test_archive_must_be_outside_checkout_database_and_private_index(self):
        for path in (ROOT / "tests" / "synthetic-context", self.database.parent, self.store.index.parent):
            with self.subTest(path=path), self.assertRaises(ValueError):
                ContextStore(self.database, path)

    def test_failed_configuration_cannot_overwrite_a_successful_retry(self):
        source = self.seed_source()
        with closing(sqlite3.connect(self.database)) as db:
            queries = db.execute("SELECT * FROM context_queries").fetchall()
        original_format, original_error = self.store._format, self.store.record_error
        for failure in (PermissionError("Synthetic archive denial"), sqlite3.OperationalError("Synthetic database failure")):
            with self.subTest(failure=type(failure).__name__):
                paused, release = threading.Event(), threading.Event()
                errors = []
                def format_archive():
                    if threading.current_thread() is failing:
                        raise failure
                    return original_format()
                def record_error(*args, **kwargs):
                    if threading.current_thread() is failing:
                        paused.set()
                        if not release.wait(5):
                            raise TimeoutError("The configuration recovery test was not released.")
                    return original_error(*args, **kwargs)
                def configure():
                    try:
                        self.store.configure(self.config)
                    except Exception as error:
                        errors.append(error)
                failing = threading.Thread(target=configure, daemon=True)
                acquired = False
                with patch.object(self.store, "_format", side_effect=format_archive), \
                        patch.object(self.store, "record_error", side_effect=record_error):
                    try:
                        failing.start()
                        self.assertTrue(paused.wait(5))
                        # Retry first only if failed configuration released its mutation lock.
                        acquired = self.store._write_lock.acquire(blocking=False)
                        if acquired:
                            self.assertTrue(self.store.configure(self.config)["available"])
                    finally:
                        if acquired:
                            self.store._write_lock.release()
                        release.set()
                        failing.join(5)
                    self.assertFalse(failing.is_alive())
                    self.assertEqual(len(errors), 1)
                    self.assertIsInstance(errors[0], ValueError)
                    if not acquired:
                        self.assertTrue(self.store.configure(self.config)["available"])
                status = self.store.status()
                self.assertEqual((status["available"], status["last_error"], status["config"]), (True, None, self.config))
                with closing(sqlite3.connect(self.database)) as db:
                    self.assertEqual(db.execute("SELECT * FROM context_queries").fetchall(), queries)
                self.assertEqual(self.store.get_capture(source["capture_id"], source["content_hash"], source["manifest_hash"]), source)

    def test_writer_claim_creation_failure_marks_seeded_archive_unavailable_until_retry(self):
        source = self.seed_source()
        before = self.store.status()
        with closing(sqlite3.connect(self.database)) as db:
            queries = db.execute("SELECT * FROM context_queries").fetchall()
        # Invalid settings must remain a validation error, without revoking a
        # healthy archive or changing its permissions and existing query pins.
        with self.assertRaises(ValueError):
            self.store.configure({**self.config, "reuse_hours": 0})
        self.assertEqual(self.store.status(), before)
        original_open = os.open

        def denied_claim(path, *args, **kwargs):
            if Path(path) == self.archive / "writer-owner.tmp":
                raise PermissionError("Synthetic private writer-claim creation denial")
            return original_open(path, *args, **kwargs)

        with patch("backend.storage.os.open", side_effect=denied_claim), self.assertRaises(ValueError):
            self.store.configure(self.config)
        unavailable = self.store.status()
        self.assertFalse(unavailable["available"])
        self.assertEqual(unavailable["config"], self.config)
        self.assertTrue(unavailable["last_error"])
        self.assertNotIn("private writer-claim", unavailable["last_error"])
        self.assertFalse((self.archive / "writer-owner.tmp").exists())
        self.assertEqual(self.store.get_capture(source["capture_id"], source["content_hash"], source["manifest_hash"]), source)
        recovered = self.store.configure(self.config)
        self.assertTrue(recovered["available"])
        self.assertIsNone(recovered["last_error"])
        self.assertEqual(recovered["config"], self.config)
        with closing(sqlite3.connect(self.database)) as db:
            self.assertEqual(db.execute("SELECT * FROM context_queries").fetchall(), queries)
        self.assertEqual(self.store.get_capture(source["capture_id"], source["content_hash"], source["manifest_hash"]), source)

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

    def test_query_manifest_tampering_cannot_change_an_existing_citation(self):
        first_url = "https://weather.example.org/forecast?date=2026-10-05"
        second_url = first_url.replace("2026-10-05", "2026-10-06")
        self.config = {**self.config, "public_sources": [first_url, second_url]}
        self.enable()
        saved = self.capture(sources=[{**self.source, "url": first_url}])["sources"][0]
        path = self.manifests()[0]
        manifest = json.loads(path.read_text(encoding="utf-8"))
        manifest["source_url"] = second_url
        for digest in (hashlib.sha256(second_url.encode()).hexdigest(), manifest["source_id"]):
            manifest["source_id"] = digest
            path.write_text(json.dumps(manifest), encoding="utf-8")
            self.assert_missing_capture(saved["capture_id"])

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

    def test_identical_bytes_share_an_object_but_keep_capture_versions(self):
        first = self.seed_source()
        second = self.capture()["sources"][0]
        third = self.capture(sources=[{**self.source, "content": "A changed public version."}])["sources"][0]
        self.assertNotEqual(first["capture_id"], second["capture_id"])
        self.assertEqual(first["content_hash"], second["content_hash"])
        self.assertNotEqual(first["content_hash"], third["content_hash"])
        self.assertEqual(len(list(self.archive.glob("objects/sha256/*/*.txt"))), 2)
        self.assertEqual(self.store.get_capture(first["capture_id"]), first)
        for pins in ({"expected_hash": "0" * 64}, {"expected_manifest_hash": "0" * 64}):
            with self.subTest(pins=pins), self.assertRaises(KeyError):
                self.store.get_capture(first["capture_id"], **pins)
        publish_archive(self.archive, "records/deletions/" + first["capture_id"] + ".json",
                        json.dumps({"schema_version": SCHEMA, "capture_id": first["capture_id"]}).encode())
        self.assert_missing_capture(first["capture_id"])
        self.assertEqual(self.store.get_capture(second["capture_id"]), second)

    def test_hash_tampering_and_missing_objects_never_reuse_saved_evidence(self):
        saved = self.seed_source()
        stat = Path.stat
        def denied_stat(path, *args, **kwargs):
            if path == self.store.index:
                raise PermissionError("Synthetic index attribute denial")
            return stat(path, *args, **kwargs)
        with patch.object(Path, "stat", denied_stat):
            self.assertEqual((self.store.status()["indexed_count"], self.store.status()["last_error"]), (0, "The local source index needs rebuilding."))
            self.assert_missing_capture(saved["capture_id"])
        self.assertEqual(self.store.get_capture(saved["capture_id"]), saved)
        obj = next(self.archive.glob("objects/sha256/*/*.txt"))
        obj.write_text("Different bytes", encoding="utf-8")
        self.assert_missing_capture(saved["capture_id"])
        obj.unlink()
        self.assert_missing_capture(saved["capture_id"])

    def test_invalid_format_is_a_managed_error_and_preserves_existing_captures(self):
        saved = self.seed_source()
        with self.assertRaises(ValueError):
            self.store.configure({**self.config, "public_sources": ["https://127.0.0.1/"]})
        self.assertTrue(self.store.status()["available"])
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
                self.assertFalse(self.store.status()["available"])
                self.assertEqual(self.store.status()["config"], self.config)
                self.assertEqual(self.store.get_capture(saved["capture_id"]), saved)
                self.assertEqual(path.read_bytes(), invalid)
                self.assertEqual({entry.relative_to(self.archive): entry.read_bytes()
                                  for entry in self.archive.rglob("*") if entry.is_file() and entry != path}, files)
        path.write_bytes(original)
        self.store.configure(changed)
        self.assertTrue(self.store.status()["available"])
        with patch("backend.context_store.archive_inventory", side_effect=ValueError("Synthetic scan allowance")), self.assertRaises(ValueError):
            self.store.configure(changed)
        self.assertFalse(self.store.status()["available"])
        self.assertEqual(self.store.status()["config"], changed)
        self.assertEqual(self.store.get_capture(saved["capture_id"]), saved)
        self.store.configure(changed)
        self.assertEqual((self.store.status()["available"], self.store.status()["last_error"]), (True, None))
        self.assertEqual(self.store.get_capture(saved["capture_id"]), saved)

    def test_byte_item_and_free_space_limits_do_not_damage_existing_capture(self):
        saved = self.seed_source()
        for metric in ("items", "bytes", "free"):
            with self.subTest(metric=metric):
                inventory = (self.config["max_bytes"] if metric == "bytes" else 100, self.config["max_items"] if metric == "items" else 5, [])
                with patch("backend.context_store.archive_inventory", return_value=inventory), patch("backend.context_store.shutil.disk_usage") as usage:
                    usage.return_value.free = 0 if metric == "free" else 10 ** 10
                    result = self.capture(query="New query", sources=[{**self.source, "content": "A fresh version"}])
                self.assertIn("archive_warning", result)
                self.assertEqual(result["sources"][0]["archive_status"], "not_saved")
                self.assertEqual(self.store.status()["last_capture"]["sources_saved"], 0)
                self.assertEqual(self.store.get_capture(saved["capture_id"]), saved)
                self.assertEqual(len(self.manifests()), 1)

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
        claim.unlink()
        self.assertTrue(self.store.initialize()["available"])

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
        first = self.seed_source()
        second = self.capture(query="Second source", sources=[{
            **self.source, "url": self.source["url"] + "/second", "content": "Different public evidence."}])["sources"][0]
        with closing(sqlite3.connect(self.store.index)) as db, db:
            row = db.execute("SELECT manifest,manifest_hash FROM captures WHERE id=?", (second["capture_id"],)).fetchone()
            db.execute("UPDATE captures SET manifest=?,manifest_hash=? WHERE id=?", (*row, first["capture_id"]))
        self.assert_missing_capture(first["capture_id"])
        self.assertFalse((self.archive / "records" / "deletions" / (first["capture_id"] + ".json")).exists())
        self.assertEqual(self.store.get_capture(second["capture_id"]), second)

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

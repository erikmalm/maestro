"""Verified local candidates from disposable public-safe fixtures; no inference or hosted searches."""
from contextlib import closing
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3
import tempfile
import unicodedata
import unittest
from unittest.mock import patch

from backend.context_policy import DEFAULT, MANIFEST_BYTES, SOURCE_BYTES
from backend.context_store import CANDIDATE_MAP_BYTES, ContextStore, validate_candidate_lookup


class ContextCandidateTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory(prefix="maestro-candidates-")))
        self.archive = self.root / "public"
        self.database = self.root / "private" / "workspace.sqlite3"
        self.store = ContextStore(self.database, self.archive)
        self.config = {**DEFAULT, "enabled": True, "capture_policy": "all_public", "public_sources": []}
        self.now = datetime(2026, 10, 19, 12, tzinfo=timezone.utc)
        self.enterContext(patch.object(self.store, "now", return_value=self.now))
        self.number = 0

    def seed(self, *, query="public evidence", content="Public evidence with verified quantities.",
             title="Synthetic public source", url=None, at=None, maximum=3):
        if not self.database.exists():
            self.store.configure(self.config)
        self.number += 1
        source = {"title": title, "url": url or f"https://docs.example.org/reports/{self.number}", "content": content}
        result = self.store.capture(query, at or self.now.isoformat(), [source], maximum)
        self.assertEqual(result["sources"][0]["archive_status"], "saved")
        return result["sources"][0]

    def lookup(self, *args, **kwargs):
        with (patch("backend.web_search.WebSearch.search", side_effect=AssertionError("Hosted search")),
              patch.object(self.store, "initialize", side_effect=AssertionError("Initialization")),
              patch.object(self.store, "_index_connection", side_effect=AssertionError("Index writes"))):
            return self.store.lookup_candidates(*args, **kwargs)

    def snapshot(self):
        return {str(path.relative_to(self.root)): path.read_bytes()
                for path in self.root.rglob("*") if path.is_file()}

    def manifest(self, source):
        return self.archive / "records" / "captures" / source["retrieved_at"][:7] / (source["capture_id"] + ".json")

    def body(self, source):
        return self.archive / "objects" / "sha256" / source["content_hash"][:2] / (source["content_hash"] + ".txt")

    def edit_index(self, statement, parameters=()):
        with closing(sqlite3.connect(self.store.index)) as db, db:
            db.execute(statement, parameters)

    def test_cold_disabled_and_unconfigured_lookup_creates_nothing(self):
        self.assertEqual(self.lookup(), {"state": "disabled", "candidates": [], "skipped_count": 0, "search_limited": False})
        self.assertEqual(ContextStore(self.database, "").lookup_candidates()["state"], "unconfigured")
        self.assertEqual(self.snapshot(), {})
        self.assertFalse(self.database.parent.exists())
        self.assertFalse(self.archive.exists())

    def test_empty_match_is_distinct_from_missing_or_corrupt_storage(self):
        self.store.configure(self.config)
        empty = self.lookup("missing keyword")
        self.assertEqual(empty, {"state": "ready", "candidates": [], "skipped_count": 0, "search_limited": False})
        self.store.index.unlink()
        before = self.snapshot()
        missing = self.lookup()
        self.assertEqual(missing["state"], "unavailable")
        self.assertIn("incomplete", missing["message"])
        self.assertEqual(self.snapshot(), before)
        self.store.index.write_bytes(b"Synthetic corrupt local index")
        self.assertEqual(self.lookup()["state"], "unavailable")
        self.store.archive = self.root / "not-present"
        self.assertEqual(self.lookup()["state"], "unavailable")
        self.assertFalse(self.store.archive.exists())

    def test_exact_mappings_for_all_result_counts_precede_keyword_fallback_and_deduplicate(self):
        sources = [self.seed(query="rare requested query", content=f"Mapped body {number}.", maximum=number)
                   for number in (1, 2, 3)]
        keyword = self.seed(query="different request", content="Rare requested query in a keyword-only source.")
        result = self.lookup("  rare requested   query ", limit=20)
        self.assertEqual(result["state"], "ready")
        self.assertEqual({source["capture_id"] for source in result["candidates"][:3]},
                         {source["capture_id"] for source in sources})
        self.assertEqual([source["match_kind"] for source in result["candidates"]], ["exact_query"] * 3 + ["keyword"])
        self.assertEqual(result["candidates"][-1]["capture_id"], keyword["capture_id"])
        self.assertTrue(all(source["body_verified"] for source in result["candidates"]))
        self.assertEqual(len({source["capture_id"] for source in result["candidates"]}), 4)
        self.assertEqual(len(self.lookup("rare requested query", limit=1)["candidates"]), 1)

    def test_keyword_operators_are_literal_words_and_all_terms_must_match(self):
        wanted = self.seed(query="other", title="Market publication", content="OR revenue figure is 17 EUR.")
        self.seed(query="another", title="Market publication", content="Revenue figure is 23 EUR.")
        result = self.lookup('market OR "revenue"')
        self.assertEqual([source["capture_id"] for source in result["candidates"]], [wanted["capture_id"]])
        self.assertEqual(result["candidates"][0]["match_kind"], "keyword")
        self.assertEqual(self.lookup("market NEAR revenue")["candidates"], [])

    def test_swedish_nfc_nfd_case_and_diacritic_queries_share_sqlite_keyword_semantics(self):
        source = self.seed(query="another request", title="Årsta årsrapport", content="Verifierad offentlig rapport.")
        for query in ("Årsta årsrapport", unicodedata.normalize("NFD", "Årsta årsrapport"), "ARSTA ARSRAPPORT"):
            with self.subTest(query=query):
                result = self.lookup(query)
                self.assertEqual([candidate["capture_id"] for candidate in result["candidates"]], [source["capture_id"]])
                self.assertEqual(result["candidates"][0]["match_kind"], "keyword")

    def test_stale_versions_remain_candidates_with_original_dates_and_pins(self):
        old = self.seed(query="historic request", content="Historical figure 17 EUR.",
                        url="https://docs.example.org/reports/repeated", at=(self.now - timedelta(days=4)).isoformat())
        new = self.seed(query="other request", content="New current source without the earlier keyword.", url=old["url"])
        before = self.snapshot()
        exact = self.lookup("historic request")["candidates"][0]
        self.assertEqual(exact, {**old, "match_kind": "exact_query", "body_verified": True, "stale": True})
        keyword = self.lookup("historical figure")["candidates"][0]
        self.assertEqual(keyword["capture_id"], old["capture_id"])
        versions = self.lookup()["candidates"]
        self.assertEqual([source["capture_id"] for source in versions], [new["capture_id"], old["capture_id"]])
        self.assertEqual([source["stale"] for source in versions], [False, True])
        self.assertEqual(self.snapshot(), before)

    def test_domain_is_exact_normalized_and_retrieval_dates_are_inclusive_utc(self):
        source = self.seed(url="https://DOCS.EXAMPLE.ORG/reports/day", at="2026-10-16T23:30:00-02:00")
        self.seed(url="https://child.docs.example.org/reports/day", at=source["retrieved_at"])
        self.seed(url="https://docs.example.org/reports/earlier", at="2026-10-16T23:59:59+00:00")
        for query in ("", "public evidence"):
            result = self.lookup(query, domain="DOCS.EXAMPLE.ORG", retrieved_from="2026-10-17", retrieved_to="2026-10-17")
            self.assertEqual([item["capture_id"] for item in result["candidates"]], [source["capture_id"]])
            self.assertEqual(result["candidates"][0]["retrieved_at"], "2026-10-17T01:30:00+00:00")
        self.assertEqual(self.lookup(domain="example.org")["candidates"], [])

    def test_poisoned_fts_cannot_claim_a_keyword_match_for_other_verified_text(self):
        source = self.seed(query="unrelated request", content="Original public text without the requested word.")
        self.edit_index("UPDATE captures_fts SET content=? WHERE id=?", ("poisonneedle", source["capture_id"]))
        result = self.lookup("poisonneedle")
        self.assertEqual(result["candidates"], [])
        self.assertEqual(result["skipped_count"], 1)
        self.assertIn("incomplete", result["message"])

    def test_stale_fts_postings_cannot_claim_terms_absent_from_verified_source(self):
        source = self.seed(query="unrelated request", content="Canonical body without the requested keyword.")
        self.edit_index("UPDATE captures_fts SET content=? WHERE id=?", ("postingsneedle", source["capture_id"]))
        # Deliberately desynchronize disposable postings and their stored text.
        # Comparing FTS text alone to the body would incorrectly accept this.
        self.edit_index("UPDATE captures_fts_content SET c2=? WHERE c0=?", (source["content"], source["capture_id"]))
        result = self.lookup("postingsneedle")
        self.assertEqual(result["candidates"], [])
        self.assertEqual(result["skipped_count"], 1)
        self.assertIn("incomplete", result["message"])

    def test_index_metadata_and_content_must_equal_verified_evidence(self):
        source = self.seed()
        for field, wrong in (("title", "Wrong indexed title"), ("url", "https://other.example.org/reports"),
                             ("at", "2026-10-18T12:00:00+00:00"), ("manifest_hash", "0" * 64),
                             ("content", "Wrong indexed body"), ("manifest", "objects/not-a-manifest.txt")):
            with self.subTest(field=field):
                with closing(sqlite3.connect(self.store.index)) as db:
                    original = db.execute(f"SELECT {field} FROM captures").fetchone()[0]
                self.edit_index(f"UPDATE captures SET {field}=?", (wrong,))
                result = self.lookup("public evidence")
                self.assertEqual(result["candidates"], [])
                self.assertGreater(result["skipped_count"], 0)
                self.edit_index(f"UPDATE captures SET {field}=?", (original,))
        self.assertEqual(self.lookup()["candidates"][0]["content_hash"], source["content_hash"])

    def test_corrupt_exact_pin_can_only_return_independent_keyword_match(self):
        source = self.seed()
        with closing(sqlite3.connect(self.database)) as db, db:
            pins = [{"capture_id": source["capture_id"], "content_hash": "0" * 64, "manifest_hash": source["manifest_hash"]}]
            db.execute("UPDATE context_queries SET captures=?", (json.dumps(pins),))
        result = self.lookup("public evidence")
        self.assertEqual(result["candidates"], [{**source, "match_kind": "keyword", "body_verified": True, "stale": False}])
        self.assertEqual(result["skipped_count"], 1)
        self.assertIn("incomplete", result["message"])

    def test_malformed_or_oversized_exact_mapping_is_not_trusted_or_repaired(self):
        source = self.seed()
        for raw in ("{", "[]", "[" * 1100 + "]" * 1100, json.dumps(["x" * (CANDIDATE_MAP_BYTES + 1)])):
            with self.subTest(length=len(raw)):
                with closing(sqlite3.connect(self.database)) as db, db:
                    db.execute("UPDATE context_queries SET captures=?", (raw,))
                before = self.snapshot()
                result = self.lookup("public evidence")
                self.assertEqual(result["candidates"][0]["capture_id"], source["capture_id"])
                self.assertEqual(result["candidates"][0]["match_kind"], "keyword")
                self.assertEqual(result["skipped_count"], 1)
                self.assertEqual(self.snapshot(), before)

    def test_oversized_exact_mapping_metadata_cannot_grant_exact_query_status(self):
        source = self.seed()
        for field in ("query", "at"):
            with self.subTest(field=field), closing(sqlite3.connect(self.database)) as db:
                original = db.execute(f"SELECT {field} FROM context_queries").fetchone()[0]
                with db:
                    db.execute(f"UPDATE context_queries SET {field}=?", ("X" * CANDIDATE_MAP_BYTES,))
                before = self.snapshot()
                result = self.lookup("public evidence")
                self.assertEqual(result["candidates"][0]["capture_id"], source["capture_id"])
                self.assertEqual(result["candidates"][0]["match_kind"], "keyword")
                self.assertEqual(result["skipped_count"], 1)
                self.assertEqual(self.snapshot(), before)
                with db:
                    db.execute(f"UPDATE context_queries SET {field}=?", (original,))

    def test_missing_tampered_deleted_and_revoked_captures_are_skipped(self):
        sources = [self.seed(content=f"Public evidence distinct body {number}.") for number in range(4)]
        self.manifest(sources[0]).unlink()
        raw = self.body(sources[1]).read_bytes()
        self.body(sources[1]).write_bytes(raw[:-1] + b"!")
        marker = self.archive / "records" / "deletions" / (sources[2]["capture_id"] + ".json")
        marker.parent.mkdir(parents=True)
        marker.write_text("{}", encoding="utf-8")
        remaining = self.lookup()
        self.assertEqual([source["capture_id"] for source in remaining["candidates"]], [sources[3]["capture_id"]])
        self.assertEqual(remaining["skipped_count"], 3)
        self.store.configure({**self.config, "capture_policy": "approved_sources", "public_sources": ["https://other.example.org/"]})
        before = self.snapshot()
        result = self.lookup()
        self.assertEqual(result["candidates"], [])
        self.assertEqual(result["skipped_count"], 4)
        self.assertIn("incomplete", result["message"])
        self.assertEqual(self.snapshot(), before)

    def test_new_credentials_are_screened_in_raw_encoded_and_normalized_evidence(self):
        secret = "rotated-private-key"
        encoded = "".join(f"%{ord(character):02X}" for character in secret)
        escaped = "".join(f"&#{ord(character)};" for character in secret)
        self.seed(content="Public evidence " + encoded)
        self.seed(title="Public evidence " + escaped)
        self.seed(url="https://docs.example.org/reports/" + encoded)
        self.seed(url="https://SENSITIVE.EXAMPLE.ORG/reports/public")
        before = self.snapshot()
        result = self.lookup(secrets=(secret, "sensitive.example.org"))
        self.assertEqual(result["candidates"], [])
        self.assertEqual(result["skipped_count"], 4)
        self.assertIn("incomplete", result["message"])
        self.assertEqual(self.snapshot(), before)

    def test_all_connections_use_readonly_selects_and_lookup_never_changes_state(self):
        self.seed()
        before = self.snapshot()
        connect, statements = sqlite3.connect, []

        def readonly(database, **kwargs):
            self.assertTrue(database.endswith("?mode=ro"))
            self.assertTrue(kwargs["uri"])
            db = connect(database, **kwargs)
            db.set_trace_callback(statements.append)
            return db

        with patch("backend.context_store.sqlite3.connect", side_effect=readonly):
            result = self.lookup("public evidence")
        self.assertEqual(result["state"], "ready")
        self.assertTrue(statements)
        self.assertTrue(all(statement.lstrip().upper().startswith(("SELECT ", "-- "))
                            or statement == "PRAGMA 'main'.data_version" for statement in statements), statements)
        self.assertEqual(self.snapshot(), before)

    def test_scan_limit_retains_partial_results_and_bounds_body_reads(self):
        for number in range(5):
            self.seed(content=f"Public body {number}.")
        original = self.store._manifest
        with patch("backend.context_store.CANDIDATE_SCAN_LIMIT", 2), patch.object(self.store, "_manifest", wraps=original) as read:
            result = self.lookup(limit=20)
        self.assertEqual(result["state"], "limited")
        self.assertTrue(result["search_limited"])
        self.assertEqual(len(result["candidates"]), 2)
        self.assertEqual(read.call_count, 2)

    def test_duplicate_fts_nominations_consume_the_same_scan_allowance(self):
        source = self.seed(query="another request")
        with closing(sqlite3.connect(self.store.index)) as db, db:
            db.executemany("INSERT INTO captures_fts(id,title,content) VALUES(?,?,?)",
                           [(source["capture_id"], source["title"], source["content"])] * 3)
        original = self.store._manifest
        with patch("backend.context_store.CANDIDATE_SCAN_LIMIT", 2), patch.object(self.store, "_manifest", wraps=original) as read:
            result = self.lookup("public evidence", limit=20)
        self.assertEqual(result["state"], "limited")
        self.assertTrue(result["search_limited"])
        self.assertEqual(len(result["candidates"]), 1)
        self.assertEqual(read.call_count, 1)

    def test_deadline_retains_only_evidence_verified_before_work_expires(self):
        for number in range(3):
            self.seed(content=f"Public body {number}.")
        clock, count = [0.0], [0]
        original = self.store._manifest

        def read(*args):
            value = original(*args)
            count[0] += 1
            if count[0] == 2:
                clock[0] = 3.0
            return value

        with (patch("backend.context_store.time.monotonic", side_effect=lambda: clock[0]),
              patch.object(self.store, "_manifest", side_effect=read)):
            result = self.lookup()
        self.assertEqual(result["state"], "limited")
        self.assertTrue(result["search_limited"])
        self.assertEqual(len(result["candidates"]), 1)

    def test_sql_work_limit_is_reported_without_repair_or_exhaustive_miss(self):
        self.seed()
        # A large disposable FTS posting list forces SQLite's progress handler,
        # without opening any real archive or querying a hosted service.
        with closing(sqlite3.connect(self.store.index)) as db, db:
            db.executemany("INSERT INTO captures_fts(id,title,content) VALUES(?,?,?)",
                           [(f"missing-{number}", "Public evidence", "Public evidence") for number in range(2000)])
        before = self.snapshot()
        with patch("backend.context_store.CANDIDATE_SQL_STEPS", 1000):
            result = self.lookup("public evidence", limit=20)
        self.assertEqual(result["state"], "limited")
        self.assertTrue(result["search_limited"])
        self.assertEqual(len(result["candidates"]), 1)  # Exact evidence survives later SQL interruption.
        self.assertEqual(self.snapshot(), before)

    def test_sql_allowance_is_shared_between_private_mapping_and_fts_connections(self):
        source = self.seed()
        # Both connections do real work individually below the eventual cap.
        # A larger private schema makes exact-map discovery consume VM steps;
        # duplicate FTS nominations then consume the remainder in another DB.
        with closing(sqlite3.connect(self.database)) as db, db:
            state = db.execute("SELECT value FROM context_state WHERE id=1").fetchone()[0]
            mappings = db.execute("SELECT key,query,at,captures FROM context_queries").fetchall()
            db.execute("DROP TABLE context_state")
            db.execute("DROP TABLE context_queries")
            for number in range(600):
                db.execute(f"CREATE VIEW synthetic_{number} AS SELECT 1")
            db.execute("CREATE TABLE context_state(id INTEGER PRIMARY KEY,value TEXT NOT NULL)")
            db.execute("INSERT INTO context_state VALUES(1,?)", (state,))
            db.execute("CREATE TABLE context_queries(key TEXT PRIMARY KEY,query TEXT,at TEXT,captures TEXT)")
            db.executemany("INSERT INTO context_queries VALUES(?,?,?,?)", mappings)
        with closing(sqlite3.connect(self.store.index)) as db, db:
            db.executemany("INSERT INTO captures_fts(id,title,content) VALUES(?,?,?)",
                           [(source["capture_id"], source["title"], source["content"])] * 80)
        connect, counts = sqlite3.connect, {}

        class TrackedConnection(sqlite3.Connection):
            def set_progress_handler(connection, callback, interval):
                def counted():
                    counts[connection.label] = counts.get(connection.label, 0) + 1
                    return callback()
                return super().set_progress_handler(counted, interval)

        def tracked(database, **kwargs):
            connection = connect(database, factory=TrackedConnection, **kwargs)
            connection.label = database
            return connection

        with patch("backend.context_store.sqlite3.connect", side_effect=tracked):
            self.assertEqual(self.lookup("public evidence")["state"], "ready")
            self.assertEqual(len(counts), 2)
            self.assertTrue(all(count > 0 for count in counts.values()))
            allowance = max(counts.values()) + 1
            counts.clear()
            with patch("backend.context_store.CANDIDATE_SQL_STEPS", allowance * 1000):
                limited = self.lookup("public evidence")
        self.assertEqual(limited["state"], "limited")
        self.assertTrue(limited["search_limited"])
        self.assertTrue(all(count < allowance for count in counts.values()))
        self.assertGreaterEqual(sum(counts.values()), allowance)

    def test_large_disposable_index_values_are_skipped_before_python_fetch(self):
        source = self.seed(query="different request")
        for field in ("title", "url", "content", "manifest", "at", "manifest_hash"):
            with self.subTest(field=field):
                with closing(sqlite3.connect(self.store.index)) as db:
                    original = db.execute(f"SELECT {field} FROM captures").fetchone()[0]
                self.edit_index(f"UPDATE captures SET {field}=?", ("X" * (SOURCE_BYTES * 2),))
                result = self.lookup()
                self.assertEqual(result["candidates"], [])
                self.assertEqual(result["skipped_count"], 1)
                self.edit_index(f"UPDATE captures SET {field}=?", (original,))
        self.assertEqual(self.lookup()["candidates"][0]["capture_id"], source["capture_id"])

    def test_writer_unavailability_does_not_prevent_verified_local_reads(self):
        source = self.seed()
        self.store.record_error()
        result = self.lookup()
        self.assertEqual(result["state"], "ready")
        self.assertEqual(result["candidates"][0]["capture_id"], source["capture_id"])

    def test_validation_rejects_bad_inputs_before_any_storage_access(self):
        for kwargs in ({"query": None}, {"query": "x" * 513}, {"query": "!"}, {"query": "word " * 17},
                       {"limit": True}, {"limit": 0}, {"limit": 21}, {"limit": 2.0},
                       {"domain": "https://example.org"}, {"domain": "*.example.org"}, {"domain": "127.0.0.1"},
                       {"domain": "docs.example.org:443"}, {"domain": "docs.example.org/"},
                       {"retrieved_from": "2026-2-01"}, {"retrieved_to": "2026-02-30"},
                       {"retrieved_from": "2026-10-19", "retrieved_to": "2026-10-18"}):
            with self.subTest(kwargs=kwargs), patch.object(Path, "is_file", side_effect=AssertionError("Storage inspection")):
                with self.assertRaises(ValueError):
                    self.lookup(**kwargs)
        self.assertEqual(validate_candidate_lookup("  ")["query"], "")
        self.assertEqual(validate_candidate_lookup(domain="DOCS.EXAMPLE.ORG")["domain"], "docs.example.org")
        self.assertEqual(self.snapshot(), {})


if __name__ == "__main__":
    unittest.main()

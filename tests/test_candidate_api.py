"""Local candidate API uses real disposable evidence without acquisition or writes."""
from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient

from backend import app as backend
from backend.web_search import ENDPOINT


class CandidateApiTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory(prefix="maestro-candidate-api-"))).resolve()
        self.private, self.archive = self.root / "private", self.root / "public"
        self.private.mkdir()
        self.enterContext(patch.object(backend, "DATABASE", self.private / "workspace.sqlite3"))
        self.enterContext(patch.dict("os.environ", {"MAESTRO_CONTEXT_ARCHIVE_DIR": str(self.archive)}))
        self.keys = {}
        self.enterContext(patch.object(backend.credentials, "read", side_effect=lambda endpoint:
                                      (self.keys.get(endpoint), "synthetic" if endpoint in self.keys else "missing")))
        for transport in (httpx.HTTPTransport, httpx.AsyncHTTPTransport):
            self.enterContext(patch.object(transport, "handle_request" if transport is httpx.HTTPTransport
                                          else "handle_async_request", side_effect=AssertionError("External network")))
        self.enterContext(patch.object(backend.WebSearch, "search", side_effect=AssertionError("Hosted acquisition")))
        self.enterContext(patch.object(backend.Provider, "generate", side_effect=AssertionError("Model inference")))
        self.enterContext(patch.object(backend.Provider, "generate_context", side_effect=AssertionError("Model stage")))
        self.client = self.enterContext(TestClient(backend.app))
        self.headers = {"X-Maestro-CSRF": self.client.get("/api/session").json()["csrf"]}
        self.number = 0

    def enable(self):
        config = self.client.get("/api/context").json()["config"]
        response = self.client.put("/api/context", json={**config, "enabled": True}, headers=self.headers)
        self.assertEqual(response.status_code, 200, response.text)

    def capture(self, query="Synthetic saved report", *, content="Evidence about aurora.",
                title="Synthetic report", url=None, at="2026-10-16T12:00:00+00:00"):
        self.number += 1
        source = {"title": title, "url": url or f"https://reports.example.org/report/{self.number}",
                  "content": content}
        saved = backend.context_store().capture(query, at, [source], 1)["sources"][0]
        self.assertEqual(saved["archive_status"], "saved", saved)
        return saved

    def search(self, **fields):
        return self.client.post("/api/context/search", json=fields, headers=self.headers)

    def snapshot(self):
        return {str(path.relative_to(self.root)): path.read_bytes() for path in self.root.rglob("*")
                if path.is_file() and path.name != "runtime.lock"}

    def test_exact_keyword_and_filtered_candidates_preserve_full_text_pins_and_dates_without_writes(self):
        self.enable()
        original = self.capture(content="Aurora report. " + "Historical details. " * 90 + " Exact final value 77.")
        other = self.capture("Another report", content="Evidence about coastal weather.",
                             url="https://weather.example.org/report", at="2026-10-17T12:00:00+00:00")
        before = self.snapshot()
        exact = self.search(query="  Synthetic   saved report  ")
        keyword = self.search(query="aurora")
        filtered = self.search(domain="REPORTS.EXAMPLE.ORG", retrieved_from="2026-10-16", retrieved_to="2026-10-16")
        for response, kind in ((exact, "exact_query"), (keyword, "keyword"), (filtered, "filtered")):
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.headers["cache-control"], "no-store")
            result = response.json()
            self.assertEqual(result["state"], "ready", result)
            self.assertFalse(result["search_limited"])
            self.assertEqual(len(result["candidates"]), 1, result)
            candidate = result["candidates"][0]
            for field, value in original.items():
                self.assertEqual(candidate[field], value, field)
            self.assertEqual(candidate["match_kind"], kind)
            self.assertTrue(candidate["body_verified"])
            self.assertIsInstance(candidate["stale"], bool)
            self.assertNotIn(other["capture_id"], response.text)
        self.assertEqual(self.snapshot(), before)

    def test_empty_query_is_local_bounded_filtering_and_stale_is_not_a_new_retrieval(self):
        self.enable()
        source = self.capture(at="2020-01-02T12:00:00+00:00")
        before = self.snapshot()
        response = self.search()
        self.assertEqual(response.status_code, 200, response.text)
        candidate = response.json()["candidates"][0]
        self.assertTrue(candidate["stale"])
        self.assertEqual(candidate["retrieved_at"], source["retrieved_at"])
        self.assertEqual(candidate["match_kind"], "filtered")
        self.assertEqual(self.snapshot(), before)

    def test_lookup_requires_session_csrf_and_local_origin(self):
        anonymous = TestClient(backend.app)
        self.addCleanup(anonymous.close)
        self.assertEqual(anonymous.post("/api/context/search", json={}).status_code, 401)
        self.assertEqual(self.client.post("/api/context/search", json={}).status_code, 403)
        self.assertEqual(self.client.post("/api/context/search", json={}, headers={
            **self.headers, "Origin": "https://unrelated.example"}).status_code, 403)

    def test_invalid_fields_return_generic_422_without_echoing_inputs_or_mutating_storage(self):
        self.enable()
        before = self.snapshot()
        cases = [{"query": None}, {"query": 17}, {"query": "sensitive-query-" * 50},
                 {"limit": 0}, {"limit": 21}, {"limit": True}, {"limit": "2"},
                 {"domain": "https://sensitive.example/path"}, {"domain": "127.0.0.1"},
                 {"domain": "localhost"}, {"retrieved_from": "2026-02-30"},
                 {"retrieved_from": "2026-10-18", "retrieved_to": "2026-10-17"},
                 {"retrieved_to": "private-date"}, {"context_mode": "refresh"}, {"fresh": True}]
        for fields in cases:
            with self.subTest(fields=fields):
                response = self.search(**fields)
                self.assertEqual(response.status_code, 422, response.text)
                self.assertEqual(set(response.json()), {"detail"})
                for value in ("sensitive", "private-date", "refresh"):
                    self.assertNotIn(value, response.text)
        self.assertEqual(self.snapshot(), before)

    def test_disabled_missing_index_and_missing_archive_are_explicit_without_initialization(self):
        before = self.snapshot()
        self.assertEqual(self.search(query="aurora").json()["state"], "disabled")
        self.assertEqual(self.snapshot(), before)
        self.assertFalse(self.archive.exists())
        self.enable()
        store = backend.context_store()
        store.index.unlink()
        before = self.snapshot()
        response = self.search(query="aurora")
        self.assertEqual(response.json()["state"], "unavailable", response.text)
        self.assertEqual(response.json()["candidates"], [])
        self.assertEqual(self.snapshot(), before)
        self.assertFalse(store.index.exists())
        self.archive.rename(self.root / "offline-public")
        before = self.snapshot()
        response = self.search(query="aurora")
        self.assertEqual(response.json()["state"], "unavailable", response.text)
        self.assertEqual(self.snapshot(), before)
        self.assertFalse(self.archive.exists())

    def test_current_provider_and_search_keys_withhold_encoded_candidates_and_unsafe_queries(self):
        self.enable()
        clean = self.capture(content="Evidence about aurora, safe original content.")
        provider_url = "https://provider.example.org/v1"
        with backend.provider().transaction() as state:
            state["config"].update(base_url=provider_url, protocol="responses", model="synthetic")
        keys = {provider_url: "providersecretq7", ENDPOINT: "searchsecretp8"}
        for key in keys.values():
            for encoding in (lambda value: value, lambda value: "".join(f"%{ord(char):02X}" for char in value),
                             lambda value: "".join(f"&#{ord(char)};" for char in value)):
                self.capture(content="Evidence about aurora. " + "Historical details. " * 80 + encoding(key))
        self.keys.update(keys)
        before = self.snapshot()
        response = self.search(query="aurora", limit=20)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual([row["capture_id"] for row in response.json()["candidates"]], [clean["capture_id"]])
        self.assertGreaterEqual(response.json()["skipped_count"], 6)
        for key in keys.values():
            for query in (key, "".join(f"%{ord(char):02X}" for char in key),
                          "".join(f"&#{ord(char)};" for char in key)):
                rejected = self.search(query=query)
                self.assertEqual(rejected.status_code, 422, rejected.text)
                self.assertNotIn(key, rejected.text)
        self.assertEqual(self.snapshot(), before)

    def test_key_rotation_after_lookup_is_screened_without_redaction_or_changes_to_pins(self):
        self.enable()
        key = "latersearchsecretp8"
        original = self.capture(content="Evidence about aurora and " + key)
        store = backend.context_store()
        lookup = store.lookup_candidates

        def rotate(*args, **kwargs):
            result = lookup(*args, **kwargs)
            self.keys[ENDPOINT] = key
            return result

        before = self.snapshot()
        with patch.object(store, "lookup_candidates", side_effect=rotate):
            response = self.search(query="aurora")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["candidates"], [])
        self.assertEqual(response.json()["state"], "limited")
        self.assertTrue(response.json()["search_limited"])
        self.assertNotIn(key, response.text)
        self.keys.clear()
        restored = self.search(query="aurora").json()["candidates"][0]
        for field, value in original.items():
            self.assertEqual(restored[field], value)
        self.assertEqual(self.snapshot(), before)

    def test_key_rotation_during_later_source_screening_rechecks_the_whole_response(self):
        self.enable()
        key = "latebatchcredential"
        first = self.capture(content="Evidence about aurora and " + key, at="2026-10-17T12:00:00+00:00")
        second = self.capture("Another saved report", content="Evidence about aurora and safe dates.")
        screen = backend.safe_archived_source

        def rotate_after_second(source, **kwargs):
            checked = screen(source, **kwargs)
            if source["capture_id"] == second["capture_id"] and ENDPOINT not in self.keys:
                self.keys[ENDPOINT] = key
            return checked

        before = self.snapshot()
        with patch.object(backend, "safe_archived_source", side_effect=rotate_after_second):
            response = self.search(query="aurora")
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()
        self.assertEqual([row["capture_id"] for row in result["candidates"]], [second["capture_id"]])
        self.assertNotIn(first["capture_id"], response.text)
        self.assertNotIn(key, response.text)
        self.assertEqual(result["state"], "limited")
        self.assertTrue(result["search_limited"])
        self.assertEqual(self.snapshot(), before)

    def test_permission_changes_during_lookup_cannot_return_a_previously_allowed_packet(self):
        self.enable()
        self.capture()
        store = backend.context_store()
        lookup, original = store.lookup_candidates, store.status()["config"]
        for update, expected in (({"enabled": False}, "disabled"),
                                 ({"capture_policy": "approved_sources", "public_sources": ["https://other.example.org/"]},
                                  "unavailable")):
            with self.subTest(update=update):
                store.configure(original)
                after_change = []

                def revoke(*args, **kwargs):
                    result = lookup(*args, **kwargs)
                    store.configure({**original, **update})
                    after_change.append(self.snapshot())
                    return result

                with patch.object(store, "lookup_candidates", side_effect=revoke):
                    response = self.search(query="aurora")
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(response.json()["state"], expected)
                self.assertEqual(response.json()["candidates"], [])
                self.assertEqual(self.snapshot(), after_change[0])

    def test_revoked_source_scope_and_withheld_configuration_cannot_return_candidates(self):
        self.enable()
        source = self.capture()
        config = backend.context_store().status()["config"]
        revoked = {**config, "capture_policy": "approved_sources", "public_sources": ["https://other.example.org/"]}
        self.client.put("/api/context", json=revoked, headers=self.headers)
        before = self.snapshot()
        response = self.search(query="aurora")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["candidates"], [])
        self.assertNotIn(source["capture_id"], response.text)
        self.assertEqual(self.snapshot(), before)
        key = "laterscopecredential"
        permitted = {**config, "public_sources": ["https://scopes.example.org/" + key + "/"]}
        self.client.put("/api/context", json=permitted, headers=self.headers)
        self.keys[ENDPOINT] = key
        before = self.snapshot()
        self.assertEqual(self.search(query="aurora").json()["state"], "unavailable")
        self.assertEqual(self.snapshot(), before)

    def test_cold_unconfigured_or_invalid_paths_do_not_create_private_storage(self):
        for archive_root, state in (("", "unconfigured"), (str(self.root / "unused-archive"), "disabled"),
                                    ("relative/private-selector", "unavailable")):
            with self.subTest(archive_root=archive_root), patch.object(backend, "DATABASE", self.root / "cold-private" / "workspace.sqlite3"), \
                    patch.dict("os.environ", {"MAESTRO_CONTEXT_ARCHIVE_DIR": archive_root}):
                before = self.snapshot()
                response = self.search(query="aurora")
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(response.json()["state"], state, response.text)
                self.assertEqual(self.snapshot(), before)
                self.assertFalse(backend.DATABASE.exists())
        self.assertFalse((self.root / "unused-archive").exists())

    def test_oversized_provider_state_is_unavailable_without_writes(self):
        self.enable()
        self.capture()
        with backend.provider().transaction() as state:
            state["ledger"] = [{"synthetic": "large accounting record" * 10}]
        before = self.snapshot()
        with patch.object(backend, "SOURCE_CREDENTIAL_STATE_BYTES", 64):
            response = self.search(query="aurora")
            self.assertEqual(backend.safe_private_content("Unrelated existing private content."),
                             "Unrelated existing private content.")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["state"], "unavailable")
        self.assertEqual(response.json()["candidates"], [])
        self.assertEqual(self.snapshot(), before)

    def test_deeply_nested_private_state_is_unavailable_without_writes(self):
        self.enable()
        with closing(sqlite3.connect(backend.DATABASE)) as db, db:
            db.execute("UPDATE context_state SET value=? WHERE id=1", ("[" * 1500 + "0" + "]" * 1500,))
        before = self.snapshot()
        response = self.search(query="aurora")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["state"], "unavailable")
        self.assertEqual(response.json()["candidates"], [])
        self.assertEqual(self.snapshot(), before)


if __name__ == "__main__":
    unittest.main()

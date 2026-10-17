"""Credentials added after capture must never escape through Evidence reads."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import quote

from fastapi.testclient import TestClient

from backend import app as backend
from backend.web_search import ENDPOINT


class EvidenceCredentialTests(unittest.TestCase):
    def setUp(self):
        root = Path(self.enterContext(tempfile.TemporaryDirectory(prefix="maestro-evidence-credentials-")))
        self.archive = root / "public"
        (root / "private").mkdir()
        self.enterContext(patch.object(backend, "DATABASE", root / "private" / "workspace.sqlite3"))
        self.enterContext(patch.dict("os.environ", {"MAESTRO_CONTEXT_ARCHIVE_DIR": str(self.archive)}))
        self.keys = {}
        self.enterContext(patch.object(backend.credentials, "read", side_effect=lambda endpoint:
                                      (self.keys.get(endpoint), "synthetic" if endpoint in self.keys else "missing")))
        self.client = self.enterContext(TestClient(backend.app))
        csrf = self.client.get("/api/session").json()["csrf"]
        self.headers = {"X-Maestro-CSRF": csrf}
        config = self.client.get("/api/context").json()["config"]
        enabled = self.client.put("/api/context", json={**config, "enabled": True},
                                  headers=self.headers)
        self.assertEqual(enabled.status_code, 200, enabled.text)
        self.provider_url = "https://provider.example.org/v1"
        with backend.provider().transaction() as state:
            state["config"].update(base_url=self.provider_url, protocol="responses", model="synthetic-model")
        self.credentials = {self.provider_url: "providersecretq7", ENDPOINT: "searchsecretp8"}
        self.number = 0

    def capture(self, field=None, secret="", form="raw", at="2026-10-16T12:00:00+00:00"):
        self.number += 1
        encoded = {
            "raw": secret,
            "percent": "".join(f"%{ord(character):02X}" for character in secret),
            "html": "".join(f"&#{ord(character)};" for character in secret),
        }.get(form, secret)
        source = {"title": f"Synthetic report {self.number}",
                  "url": f"https://reports.example.org/report/{self.number}",
                  "content": f"Original archived excerpt {self.number}."}
        if field == "url":
            source[field] = (f"https://{secret.upper()}.example.org/report" if form == "host"
                             else f"https://reports.example.org/report/{quote(encoded, safe='') if form == 'html' else encoded}")
        elif field:
            if field == "content":
                source[field] += " Historical context." * 80
            source[field] += " " + encoded
        saved = backend.context_store().capture("Synthetic public report", at, [source], 1)["sources"][0]
        self.assertEqual(saved["archive_status"], "saved", saved)
        return saved

    def open_source(self, source, **pins):
        return self.client.get("/api/context/sources/" + source["capture_id"], params={
            "content_hash": source["content_hash"], "manifest_hash": source["manifest_hash"], **pins,
        })

    def snapshot(self):
        store = backend.context_store()
        return (backend.DATABASE.read_bytes(), store.index.read_bytes(),
                {str(path.relative_to(self.archive)): path.read_bytes()
                 for path in self.archive.rglob("*") if path.is_file()})

    def test_inventory_omits_current_provider_and_search_credentials_without_body_reads_or_writes(self):
        control = self.capture()
        blocked = []
        for secret in self.credentials.values():
            for field in ("title", "url"):
                for form in (("raw", "percent", "html", "host") if field == "url"
                             else ("raw", "percent", "html")):
                    blocked.append(self.capture(field, secret, form))
        self.keys.update(self.credentials)
        before = self.snapshot()
        store = backend.context_store()
        with (patch.object(store, "get_capture", side_effect=AssertionError("Inventory body verification")),
              patch("backend.context_store.read_archive", side_effect=AssertionError("Inventory body read"))):
            response = self.client.get("/api/context/sources", params={"limit": 50})
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()
        self.assertEqual([source["capture_id"] for source in result["sources"]], [control["capture_id"]])
        self.assertEqual(result["skipped_count"], len(blocked))
        self.assertEqual(self.snapshot(), before)

    def test_filtered_metadata_page_keeps_its_cursor_so_the_next_safe_source_is_reachable(self):
        control = self.capture(at="2026-10-15T12:00:00+00:00")
        self.capture("title", self.credentials[ENDPOINT])
        self.keys.update(self.credentials)
        before = self.snapshot()
        first = self.client.get("/api/context/sources", params={"limit": 1}).json()
        self.assertEqual(first["state"], "ready")
        self.assertEqual(first["sources"], [])
        self.assertEqual(first["skipped_count"], 1)
        self.assertIsNotNone(first["next_cursor"])
        second = self.client.get("/api/context/sources", params={"limit": 1, "cursor": first["next_cursor"]}).json()
        self.assertEqual([source["capture_id"] for source in second["sources"]], [control["capture_id"]])
        self.assertIsNone(second["next_cursor"])
        self.assertEqual(self.snapshot(), before)

    def test_pinned_reads_fail_with_generic_404_for_each_field_and_encoding_instead_of_redaction(self):
        control = self.capture()
        blocked = []
        for secret in self.credentials.values():
            for field in ("title", "url", "content"):
                for form in (("raw", "percent", "html", "host") if field == "url"
                             else ("raw", "percent", "html")):
                    blocked.append((field, form, self.capture(field, secret, form)))
        self.keys.update(self.credentials)
        unavailable = self.open_source(control, content_hash="0" * 64)
        self.assertEqual(unavailable.status_code, 404)
        before = self.snapshot()
        for field, form, source in blocked:
            with self.subTest(field=field, form=form, capture_id=source["capture_id"]):
                response = self.open_source(source)
                self.assertEqual(response.status_code, 404, response.text)
                self.assertEqual(response.json(), unavailable.json())
                self.assertNotIn(source["content_hash"], response.text)
                self.assertNotIn(source["manifest_hash"], response.text)
        self.assertEqual(self.open_source(control).json(), control)
        self.assertEqual(self.snapshot(), before)

    def test_removing_current_keys_restores_original_text_and_hash_pins_without_rewriting_capture(self):
        originals = [(endpoint, self.capture("content", secret, "html"))
                     for endpoint, secret in self.credentials.items()]
        self.keys.update(self.credentials)
        before = self.snapshot()
        for _, original in originals:
            self.assertEqual(self.open_source(original).status_code, 404)
        for endpoint, original in originals:
            del self.keys[endpoint]
            opened = self.open_source(original)
            self.assertEqual(opened.status_code, 200, opened.text)
            self.assertEqual(opened.json(), original)
        self.assertEqual(self.snapshot(), before)

    def test_archive_status_withholds_scopes_matching_later_credentials_without_mutation(self):
        self.capture()
        config = backend.context_store().status()["config"]
        errors = []
        for endpoint, secret in self.credentials.items():
            forms = {
                "raw": secret,
                "percent": "".join(f"%{ord(character):02X}" for character in secret),
                "html": quote("".join(f"&#{ord(character)};" for character in secret), safe=""),
            }
            for form, value in forms.items():
                with self.subTest(endpoint=endpoint, form=form):
                    # The scope is legitimate when stored; the key only exists later.
                    self.keys.clear()
                    scope = "https://scopes.example.org/public/" + value + "/"
                    stored = {**config, "capture_policy": "approved_sources",
                              "public_sources": ["https://reports.example.org/", scope]}
                    saved = self.client.put("/api/context", json=stored, headers=self.headers)
                    self.assertEqual(saved.status_code, 200, saved.text)
                    safe_status = self.client.get("/api/context").json()
                    self.assertEqual(safe_status["config"], stored)
                    self.assertIsNone(safe_status.get("config_error"))
                    before = self.snapshot()
                    self.keys[endpoint] = secret
                    for path in ("/api/context", "/api/workspace"):
                        response = self.client.get(path)
                        self.assertEqual(response.status_code, 200, response.text)
                        status = response.json() if path == "/api/context" else response.json()["context_archive"]
                        self.assertIsNone(status["config"])
                        self.assertIsInstance(status["config_error"], str)
                        self.assertTrue(status["config_error"])
                        errors.append(status["config_error"])
                        self.assertNotIn(secret, response.text)
                        self.assertNotIn(value, response.text)
                        self.assertNotIn(scope, response.text)
                        expected = {field: item for field, item in safe_status.items()
                                    if field not in ("config", "config_error")}
                        self.assertEqual({field: item for field, item in status.items()
                                          if field not in ("config", "config_error")}, expected)
                    self.assertEqual(backend.context_store().status()["config"], stored)
                    self.assertEqual(self.snapshot(), before)
                    del self.keys[endpoint]
                    for path in ("/api/context", "/api/workspace"):
                        response = self.client.get(path)
                        self.assertEqual(response.status_code, 200, response.text)
                        restored = response.json() if path == "/api/context" else response.json()["context_archive"]
                        self.assertEqual(restored, safe_status)
                        self.assertIn("config_error", restored)
                        self.assertIsNone(restored["config_error"])
                    self.assertEqual(self.snapshot(), before)
        self.assertEqual(len(set(errors)), 1, "Credential errors must use one fixed safe message.")

    def test_large_safe_scope_list_stays_visible_when_credentials_are_added(self):
        self.capture()
        scopes = [f"https://scopes.example.org/public/{number}/" + "a" * 1400 + "/" for number in range(100)]
        self.assertGreater(sum(map(len, scopes)), 131072)
        self.assertTrue(all(len(scope) <= 2048 for scope in scopes))
        config = {**backend.context_store().status()["config"], "public_sources": scopes}
        saved = self.client.put("/api/context", json=config, headers=self.headers)
        self.assertEqual(saved.status_code, 200, saved.text)
        before_status = self.client.get("/api/context").json()
        self.assertEqual(before_status["config"], config)
        before = self.snapshot()
        self.keys.update(self.credentials)
        for path in ("/api/context", "/api/workspace"):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200, response.text)
                status = response.json() if path == "/api/context" else response.json()["context_archive"]
                self.assertEqual(status, before_status)
                self.assertIsNone(status["config_error"])
        self.assertEqual(self.snapshot(), before)


if __name__ == "__main__":
    unittest.main()

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
        config = self.client.get("/api/context").json()["config"]
        enabled = self.client.put("/api/context", json={**config, "enabled": True},
                                  headers={"X-Maestro-CSRF": csrf})
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


if __name__ == "__main__":
    unittest.main()

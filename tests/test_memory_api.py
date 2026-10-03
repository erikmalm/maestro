"""Private memory APIs use explicit inputs, local access checks and atomic deletion."""
from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend import app as backend
from backend.memory import MAX_CONTENT, MemoryStore
from backend.web_search import ENDPOINT as SEARCH_ENDPOINT


class MemoryAPITests(unittest.TestCase):
    def setUp(self):
        directory = Path(self.enterContext(tempfile.TemporaryDirectory(prefix="maestro-memory-api-")))
        self.enterContext(patch.object(backend, "DATABASE", directory / "workspace.sqlite3"))
        self.enterContext(patch.object(backend.credentials, "read", return_value=(None, "missing")))
        self.client = TestClient(backend.app)
        self.addCleanup(self.client.close)
        csrf = self.client.get("/api/session").json()["csrf"]
        self.headers = {"X-Maestro-CSRF": csrf, "Origin": "http://127.0.0.1:8765"}

    def remember(self, content="Synthetic concise preference.", **fields):
        response = self.client.post("/api/memory", headers=self.headers, json={"content": content, **fields})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def create_chat(self):
        response = self.client.post("/api/chats", headers=self.headers)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["active_chat_id"]

    def add_sources(self, chat_id):
        with backend.workspace_transaction() as state:
            backend.selected_chat(state, chat_id)["messages"] = [
                {"id": "synthetic-user", "role": "user", "text": "A synthetic confirmed preference."},
                {"id": "synthetic-assistant", "role": "assistant", "text": "An unconfirmed synthetic inference."},
            ]

    def test_read_does_not_initialize_database_and_crud_persists_without_inference(self):
        self.assertEqual(self.client.get("/api/memory").json(), [])
        self.assertFalse(backend.DATABASE.exists())
        with patch("backend.provider.network", side_effect=AssertionError("Explicit memory must not trigger inference")):
            original = self.remember()
            self.assertEqual(original["origin"], "explicit")
            self.assertEqual(original["scope"], "workspace")
            self.assertIsNone(original["chat_id"])
            updated = self.client.patch(f"/api/memory/{original['id']}", headers=self.headers,
                                        json={"content": "Synthetic revised preference."})
            self.assertEqual(updated.status_code, 200, updated.text)
            self.assertEqual(updated.json()["created_at"], original["created_at"])
            fresh = TestClient(backend.app)
            try:
                fresh.get("/api/session")
                self.assertEqual(fresh.get("/api/memory").json(), [updated.json()])
            finally:
                fresh.close()
            snapshot = self.client.get("/api/workspace").json()
            self.assertEqual(snapshot["memories"], [updated.json()])
            self.assertEqual(snapshot["usage"]["calls"], 0)
            deleted = self.client.delete(f"/api/memory/{original['id']}", headers=self.headers)
            self.assertEqual(deleted.status_code, 200, deleted.text)
            self.assertEqual(self.client.get("/api/memory").json(), [])
            self.assertEqual(self.client.get("/api/workspace").json()["usage"]["calls"], 0)

    def test_memory_endpoints_require_session_csrf_and_local_origin(self):
        anonymous = TestClient(backend.app)
        try:
            self.assertEqual(anonymous.get("/api/memory").status_code, 401)
            self.assertEqual(anonymous.post("/api/memory", headers=self.headers, json={"content": "Untrusted"}).status_code, 401)
        finally:
            anonymous.close()
        memory = self.remember()
        for method, url, data in (
            ("POST", "/api/memory", {"content": "Untrusted"}),
            ("PATCH", f"/api/memory/{memory['id']}", {"content": "Untrusted"}),
            ("DELETE", f"/api/memory/{memory['id']}", None),
        ):
            with self.subTest(method=method):
                self.assertEqual(self.client.request(method, url, json=data).status_code, 403)
                self.assertEqual(self.client.request(method, url, json=data,
                    headers={**self.headers, "Origin": "https://unrelated.example"}).status_code, 403)
        self.assertEqual(self.client.get("/api/memory", headers={"Origin": "https://unrelated.example"}).status_code, 403)
        self.assertEqual(self.client.get("/api/memory").json(), [memory])

    def test_long_unicode_memory_fits_request_limit_and_round_trips_unchanged(self):
        content = "记忆" * (MAX_CONTENT // 2)
        memory = self.remember(content)
        self.assertEqual(len(memory["content"]), MAX_CONTENT)
        self.assertEqual(self.client.get("/api/memory").json()[0]["content"], content)

    def test_scope_and_provenance_are_validated_and_isolate_recall(self):
        first, second = self.create_chat(), self.create_chat()
        self.add_sources(first)
        linked = self.remember("Synthetic project source preference.", chat_id=first, source_message_id="synthetic-user")
        scoped = self.remember("Synthetic project scoped preference.", scope="conversation", chat_id=first)
        store = MemoryStore(backend.DATABASE, backend.TIMEZONE)
        self.assertEqual({item["id"] for item in store.recall("Synthetic project", first)}, {linked["id"], scoped["id"]})
        self.assertEqual(store.recall("Synthetic project", second), [linked])
        for fields in (
            {"scope": "conversation"}, {"chat_id": "missing"}, {"source_message_id": "synthetic-user"},
            {"chat_id": second, "source_message_id": "synthetic-user"},
            {"chat_id": first, "source_message_id": "synthetic-assistant"},
        ):
            response = self.client.post("/api/memory", headers=self.headers, json={"content": "Unconfirmed", **fields})
            self.assertEqual(response.status_code, 409, response.text)
            self.assertNotIn("Unconfirmed", response.text)
        self.assertEqual(len(self.client.get("/api/memory").json()), 2)

    def test_validation_errors_and_missing_ids_never_echo_submitted_content(self):
        private = "synthetic-private-do-not-echo"
        invalid = [
            {"content": ""}, {"content": "x" * (MAX_CONTENT + 1)}, {"content": private, "scope": "reflection"},
            {"content": private, "origin": "inferred"}, {"content": private, "local": False},
        ]
        for body in invalid:
            response = self.client.post("/api/memory", headers=self.headers, json=body)
            self.assertEqual(response.status_code, 422, response.text)
            self.assertNotIn(private, response.text)
        for response in (
            self.client.patch("/api/memory/missing", headers=self.headers, json={"content": private}),
            self.client.delete("/api/memory/missing", headers=self.headers),
        ):
            self.assertEqual(response.status_code, 409, response.text)
            self.assertNotIn(private, response.text)
        self.assertEqual(self.client.get("/api/memory").json(), [])

    def test_configured_provider_and_search_keys_cannot_be_saved_or_added_by_edit(self):
        memory = self.remember()
        provider_key, search_key = "synthetic-provider-secret", "synthetic-search-secret"
        def read_key(endpoint):
            return (search_key if endpoint == SEARCH_ENDPOINT else provider_key, "session")
        with patch.object(backend.credentials, "read", side_effect=read_key):
            for key in (provider_key, search_key):
                for method, url in (("POST", "/api/memory"), ("PATCH", f"/api/memory/{memory['id']}")):
                    response = self.client.request(method, url, headers=self.headers, json={"content": "Keep " + key})
                    self.assertEqual(response.status_code, 409, response.text)
                    self.assertNotIn(key, response.text)
                self.assertNotIn(key.encode(), backend.DATABASE.read_bytes())
        self.assertEqual(self.client.get("/api/memory").json(), [memory])
        with patch.object(backend.credentials, "read", side_effect=ValueError("Synthetic inaccessible secret")):
            ordinary = self.remember("Synthetic ordinary preference without credentials.")
        self.assertEqual(len(self.client.get("/api/memory").json()), 2)
        self.assertEqual(ordinary["origin"], "explicit")

    def test_chat_deletion_removes_source_and_conversation_memory_but_keeps_independent_memory(self):
        chat_id = self.create_chat()
        self.add_sources(chat_id)
        independent = self.remember("Synthetic independent project preference.")
        self.remember("Synthetic source project preference.", chat_id=chat_id, source_message_id="synthetic-user")
        self.remember("Synthetic scoped project preference.", scope="conversation", chat_id=chat_id)
        response = self.client.delete(f"/api/chats/{chat_id}", headers=self.headers)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["chats"], [])
        self.assertEqual(response.json()["memories"], [independent])
        with closing(sqlite3.connect(backend.DATABASE)) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM private_memories").fetchone()[0], 1)
        self.assertNotIn(b"source project preference", backend.DATABASE.read_bytes())

    def test_inflight_chat_deletion_cannot_remove_its_memory(self):
        chat_id = self.create_chat()
        memory = self.remember("Synthetic scoped project preference.", scope="conversation", chat_id=chat_id)
        with backend.provider().transaction() as state:
            state["ledger"].append({"id": "synthetic-reservation", "at": backend.now(), "cost": 0,
                                     "status": "reserved", "thread_id": chat_id, "protocol": "ollama"})
        response = self.client.delete(f"/api/chats/{chat_id}", headers=self.headers)
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(self.client.get("/api/memory").json(), [memory])
        self.assertEqual(len(self.client.get("/api/workspace").json()["chats"]), 1)


if __name__ == "__main__":
    unittest.main()

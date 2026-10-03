"""Private proposals require user approval and preserve their real source."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend import app as backend
from backend.memory import MemoryStore
from backend.provider import DEFAULT
from backend.reflection import ReflectionStore, ReflectionWorker
from backend.work_config import WorkConfig


class ReflectionAPITests(unittest.TestCase):
    def setUp(self):
        directory = Path(self.enterContext(tempfile.TemporaryDirectory(prefix="maestro-reflection-api-")))
        self.enterContext(patch.object(backend, "DATABASE", directory / "workspace.sqlite3"))
        self.enterContext(patch.object(backend.credentials, "read", return_value=(None, "missing")))
        self.client = TestClient(backend.app)
        self.addCleanup(self.client.close)
        self.headers = {"X-Maestro-CSRF": self.client.get("/api/session").json()["csrf"]}
        self.store = ReflectionStore(backend.DATABASE, backend.TIMEZONE)
        self.memory = MemoryStore(backend.DATABASE, backend.TIMEZONE)

    def proposal(self, content="I prefer concise project updates."):
        backend.read_workspace()
        self.memory.initialize()
        self.store.initialize()
        backend.provider().configure({**DEFAULT, "protocol": "ollama", "model": "synthetic:7b",
                                      "base_url": "http://127.0.0.1:11434", "ollama_context_tokens": 8192}, "", False)
        with backend.workspace_transaction(with_db=True) as (workspace, db):
            workspace["work_config"] = WorkConfig(enabled=True, debounce_seconds=0, idle_seconds=0, max_output_tokens=512).model_dump()
            chat = backend.create_chat_record("Synthetic source", [
                {"id": "source-user", "role": "user", "text": content},
                {"id": "source-assistant", "role": "assistant", "text": "Synthetic reply."},
            ])
            workspace["chats"].append(chat)
            self.store.enqueue(chat["id"], workspace, db)

        def network(config, path, payload, deadline):
            if path == "/api/show":
                return {"details": {"format": "gguf"}, "capabilities": ["completion"]}
            return {"done": True, "message": {"content": json.dumps({"memories": [{
                "content": content, "evidence": content, "source_message_id": "source-user"}]})},
                "prompt_eval_count": 25, "eval_count": 12}

        with patch("backend.provider.background_network", side_effect=network):
            ReflectionWorker(self.store, backend.provider(), backend.safe_private_content).step()
        proposals = self.client.get("/api/reflection").json()["candidates"]
        self.assertEqual(len(proposals), 1)
        return proposals[0]

    def test_acceptance_preserves_scope_provenance_and_only_then_enables_recall(self):
        candidate = self.proposal()
        self.assertEqual(self.memory.list(), [])
        self.assertEqual(self.memory.recall("project updates", candidate["chat_id"]), [])
        response = self.client.post(f"/api/reflection/candidates/{candidate['id']}/accept",
                                    headers=self.headers, json={"scope": "workspace"})
        self.assertEqual(response.status_code, 200, response.text)
        saved = response.json()
        self.assertEqual(saved["origin"], "explicit")
        self.assertEqual(saved["scope"], "workspace")
        self.assertEqual(saved["source_message_id"], candidate["source_message_id"])
        self.assertEqual(saved["chat_id"], candidate["chat_id"])
        self.assertEqual(self.memory.recall("project updates", "another-chat"), [saved])
        self.assertEqual(self.client.get("/api/reflection").json()["candidates"], [])
        self.assertEqual(self.client.post(f"/api/reflection/candidates/{candidate['id']}/accept",
                                         headers=self.headers, json={}).status_code, 409)

    def test_rejection_removes_proposal_and_never_creates_active_memory(self):
        candidate = self.proposal()
        response = self.client.delete(f"/api/reflection/candidates/{candidate['id']}", headers=self.headers)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.store.status()["candidates"], [])
        self.assertEqual(self.memory.list(), [])
        self.assertEqual(self.client.post(f"/api/reflection/candidates/{candidate['id']}/accept",
                                         headers=self.headers, json={}).status_code, 409)

    def test_deleting_source_chat_removes_pending_and_accepted_source_linked_memory(self):
        candidate = self.proposal()
        response = self.client.delete(f"/api/chats/{candidate['chat_id']}", headers=self.headers)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.store.status()["candidates"], [])
        candidate = self.proposal("I prefer detailed project updates.")
        self.assertEqual(self.client.post(f"/api/reflection/candidates/{candidate['id']}/accept",
                                         headers=self.headers, json={"scope": "workspace"}).status_code, 200)
        self.assertEqual(len(self.memory.list()), 1)
        self.client.delete(f"/api/chats/{candidate['chat_id']}", headers=self.headers)
        self.assertEqual(self.memory.list(), [])

    def test_known_credential_added_after_proposal_cannot_be_accepted(self):
        candidate = self.proposal("Synthetic-private-value")
        with patch.object(backend.credentials, "read", return_value=("Synthetic-private-value", "session")):
            response = self.client.post(f"/api/reflection/candidates/{candidate['id']}/accept",
                                        headers=self.headers, json={})
        self.assertEqual(response.status_code, 409, response.text)
        self.assertNotIn("Synthetic-private-value", response.text)
        self.assertEqual(self.memory.list(), [])

    def test_access_and_strict_validation_precede_mutation(self):
        candidate = self.proposal()
        url = f"/api/reflection/candidates/{candidate['id']}"
        anonymous = TestClient(backend.app)
        try:
            self.assertEqual(anonymous.get("/api/reflection").status_code, 401)
            self.assertEqual(anonymous.post(url + "/accept", headers=self.headers, json={}).status_code, 401)
        finally:
            anonymous.close()
        self.assertEqual(self.client.get("/api/reflection", headers={"Origin": "https://example.invalid"}).status_code, 403)
        for method, path, body in (("POST", url + "/accept", {}), ("DELETE", url, None)):
            self.assertEqual(self.client.request(method, path, json=body).status_code, 403)
            self.assertEqual(self.client.request(method, path, json=body,
                headers={**self.headers, "Origin": "https://example.invalid"}).status_code, 403)
        response = self.client.post(url + "/accept", headers=self.headers,
                                    json={"scope": "remote", "content": "private value"})
        self.assertEqual(response.status_code, 422, response.text)
        self.assertNotIn("private value", response.text)
        self.assertEqual(self.store.status()["candidates"], [candidate])

    def test_status_polling_is_read_only_after_initialization(self):
        self.proposal()
        from contextlib import closing
        import sqlite3
        with closing(sqlite3.connect(backend.DATABASE)) as monitor:
            version = monitor.execute("PRAGMA data_version").fetchone()[0]
            for _ in range(3):
                response = self.client.get("/api/reflection")
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()["memories"], self.memory.list())
            self.assertEqual(monitor.execute("PRAGMA data_version").fetchone()[0], version)

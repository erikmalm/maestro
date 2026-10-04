"""Answer feedback stays private, editable and separate from generation."""
from contextlib import closing
from pathlib import Path
import json
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend import app as backend
from backend.memory import MemoryStore, assistant_revision, revision
from backend.reflection import ReflectionStore


class FeedbackAPITests(unittest.TestCase):
    def setUp(self):
        directory = Path(self.enterContext(tempfile.TemporaryDirectory(prefix="maestro-feedback-")))
        self.enterContext(patch.object(backend, "DATABASE", directory / "workspace.sqlite3"))
        self.enterContext(patch.object(backend.credentials, "read", return_value=(None, "missing")))
        self.client = TestClient(backend.app)
        self.addCleanup(self.client.close)
        self.headers = {"X-Maestro-CSRF": self.client.get("/api/session").json()["csrf"]}
        self.chat_id = self.client.post("/api/chats", headers=self.headers).json()["active_chat_id"]
        with backend.workspace_transaction() as state:
            backend.selected_chat(state, self.chat_id)["messages"] = [
                {"id": "user", "role": "user", "text": "Explain a synthetic test."},
                {"id": "answer", "role": "assistant", "text": "A synthetic answer."},
            ]
        self.url = f"/api/chats/{self.chat_id}/messages/answer/feedback"

    def save(self, rating="negative", comment="The synthetic example is unclear."):
        response = self.client.patch(self.url, headers=self.headers, json={"rating": rating, "comment": comment})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["feedback"]

    def test_save_edit_clear_persist_without_inference_or_new_messages(self):
        before = self.client.get("/api/workspace").json()
        with patch("backend.provider.network", side_effect=AssertionError("Feedback must not infer")):
            first = self.save()
            self.assertEqual(self.save(), first)  # Idempotent saves retain the revision.
            updated = self.save("positive", "The revised synthetic example helps.")
            self.assertEqual(updated["rating"], "positive")
            self.assertIn("updated_at", updated)
            fresh = TestClient(backend.app)
            try:
                fresh.get("/api/session")
                self.assertEqual(fresh.get("/api/workspace").json()["messages"][-1]["feedback"], updated)
            finally:
                fresh.close()
            after = self.client.get("/api/workspace").json()
            self.assertEqual(after["usage"], before["usage"])
            self.assertEqual(after["chats"], before["chats"])
            self.assertEqual(len(after["messages"]), 2)
            self.assertEqual(after["messages"][-1]["text"], before["messages"][-1]["text"])
            self.assertIsNone(self.save(None, ""))
        self.assertNotIn("feedback", self.client.get("/api/workspace").json()["messages"][-1])

    def test_only_existing_assistant_in_the_selected_chat_can_receive_feedback(self):
        other = self.client.post("/api/chats", headers=self.headers).json()["active_chat_id"]
        for chat_id, message_id in ((self.chat_id, "user"), (self.chat_id, "missing"), (other, "answer"), ("missing", "answer")):
            response = self.client.patch(f"/api/chats/{chat_id}/messages/{message_id}/feedback",
                                         headers=self.headers, json={"rating": "negative"})
            self.assertEqual(response.status_code, 404, response.text)
        self.save()
        self.assertEqual(self.client.delete(f"/api/chats/{self.chat_id}", headers=self.headers).status_code, 200)
        self.assertEqual(self.client.patch(self.url, headers=self.headers, json={"rating": "positive"}).status_code, 404)
        with closing(sqlite3.connect(backend.DATABASE)) as db:
            saved = db.execute("SELECT value FROM workspace WHERE id=1").fetchone()[0]
        self.assertNotIn("The synthetic example is unclear.", saved)

    def test_access_validation_and_active_credentials_are_protected(self):
        body = {"rating": "negative", "comment": "Synthetic private feedback"}
        anonymous = TestClient(backend.app)
        try:
            self.assertEqual(anonymous.patch(self.url, headers=self.headers, json=body).status_code, 401)
        finally:
            anonymous.close()
        self.assertEqual(self.client.patch(self.url, json=body).status_code, 403)
        self.assertEqual(self.client.patch(self.url, headers={**self.headers, "Origin": "https://unrelated.example"}, json=body).status_code, 403)
        for invalid in ({"rating": "unknown"}, {"rating": True}, {"rating": "negative", "comment": "x" * 4001},
                        {**body, "private": "Synthetic private feedback"}):
            response = self.client.patch(self.url, headers=self.headers, json=invalid)
            self.assertEqual(response.status_code, 422, response.text)
            self.assertNotIn("Synthetic private feedback", response.text)
        for invalid in ({"rating": None, "comment": "Unrated comment"}, {"rating": "positive", "comment": "a\u0000b"}):
            self.assertEqual(self.client.patch(self.url, headers=self.headers, json=invalid).status_code, 409)
        key = "synthetic-configured-key"
        with patch.object(backend.credentials, "read", return_value=(key, "session")):
            response = self.client.patch(self.url, headers=self.headers, json={"rating": "negative", "comment": key})
            self.assertEqual(response.status_code, 409, response.text)
            self.assertNotIn(key, response.text)
        self.assertNotIn(key.encode(), backend.DATABASE.read_bytes())

    def test_changed_feedback_invalidates_inflight_work_and_erases_stale_journal(self):
        self.client.get("/api/reflection")
        store = ReflectionStore(backend.DATABASE, backend.TIMEZONE)
        self.save()
        with store.transaction() as db:
            epoch = db.execute("SELECT epoch FROM reflection_meta WHERE id=1").fetchone()[0]
            db.execute("INSERT INTO reflection_jobs VALUES ('job',?,'reviewing',?)", (self.chat_id, json.dumps({"id": "job", "epoch": epoch})))
            db.execute("INSERT INTO reflection_journal VALUES ('journal',?)", (json.dumps({"id": "journal", "summary": "Obsolete feedback"}),))
        self.save("negative", "Updated synthetic correction.")
        with closing(sqlite3.connect(backend.DATABASE)) as db:
            self.assertEqual(db.execute("SELECT state FROM reflection_jobs WHERE id='job'").fetchone()[0], "cancelled")
            self.assertEqual(db.execute("SELECT epoch FROM reflection_meta WHERE id=1").fetchone()[0], epoch + 1)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM reflection_journal").fetchone()[0], 0)
        self.save("negative", "Updated synthetic correction.")  # No-op leaves background work alone.
        with closing(sqlite3.connect(backend.DATABASE)) as db:
            self.assertEqual(db.execute("SELECT epoch FROM reflection_meta WHERE id=1").fetchone()[0], epoch + 1)

    def test_clearing_feedback_removes_derived_notes_and_returns_current_memory_when_paused(self):
        feedback = self.save()
        store = MemoryStore(backend.DATABASE, backend.TIMEZONE)
        pinned = store.remember("Synthetic pinned user preference.")
        note = {**pinned, "id": "lesson", "content": "Explain synthetic examples more clearly.",
                "origin": "reflective", "kind": "lesson", "provenance": [{"chat_id": self.chat_id,
                "message_id": "answer", "role": "assistant", "hash": assistant_revision("A synthetic answer.", feedback)}]}
        dependent = {**note, "id": "dependent", "provenance": [{"memory_id": "lesson", "hash": revision(note)}]}
        with store.transaction() as db:
            store.insert(db, note)
            store.insert(db, dependent)
        response = self.client.patch(self.url, headers=self.headers, json={"rating": None})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIsNone(response.json()["feedback"])
        self.assertFalse(response.json()["work"]["config"]["enabled"])
        self.assertEqual(response.json()["memories"], [pinned])
        with closing(sqlite3.connect(backend.DATABASE)) as db:
            self.assertEqual(db.execute("SELECT id FROM private_memories").fetchall(), [(pinned["id"],)])
            self.assertEqual(db.execute("SELECT COUNT(*) FROM memory_tombstones").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()

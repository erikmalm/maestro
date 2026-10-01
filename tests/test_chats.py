"""Persistent chat isolation and bounded, accounted naming with synthetic models."""
from contextlib import closing, contextmanager
from pathlib import Path
import json
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient

from backend import app as backend, credentials
from backend.provider import DEFAULT, Provider, TITLE_INSTRUCTIONS


class ChatTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="maestro-threads-test-")
        self.database_patch = patch.object(backend, "DATABASE", Path(self.directory.name) / "workspace.sqlite3")
        self.database_patch.start()
        self.key_patch = patch.object(credentials, "read", return_value=("synthetic-key", "session"))
        self.read_key = self.key_patch.start()
        self.client = TestClient(backend.app)
        self.headers = {"X-Maestro-CSRF": self.client.get("/api/session").json()["csrf"]}
        self.config = {**DEFAULT, "protocol": "ollama", "base_url": "http://127.0.0.1:11434",
                       "model": "synthetic-chat", "max_output_tokens": 256}
        self.configure(self.config)
        self.requests = []

    def tearDown(self):
        self.client.close()
        self.key_patch.stop()
        self.database_patch.stop()
        self.directory.cleanup()

    def configure(self, config):
        result = self.client.put("/api/provider", headers=self.headers, json={"config": config, "persist": False})
        self.assertEqual(result.status_code, 200, result.text)

    def workspace(self, chat_id=None):
        result = self.client.get("/api/workspace", params={"chat_id": chat_id} if chat_id else {})
        self.assertEqual(result.status_code, 200, result.text)
        return result.json()

    def create_chat(self):
        result = self.client.post("/api/chats", headers=self.headers)
        self.assertEqual(result.status_code, 200, result.text)
        return result.json()["active_chat_id"]

    def send(self, chat_id, text="Plan a quiet weekend"):
        return self.client.post("/api/chat", headers=self.headers, json={"chat_id": chat_id, "text": text})

    def is_title(self, payload):
        return payload.get("instructions", (payload.get("messages") or [{}])[0].get("content")) == TITLE_INSTRUCTIONS

    def response(self, title=False, local=True, output=None):
        reply = "Quiet weekend plans" if title else "Synthetic useful reply"
        input_tokens, output_tokens = (20, 5) if title else (100, 20)
        output_tokens = output_tokens if output is None else output
        if local:
            return httpx.Response(200, json={"done": True, "message": {"role": "assistant", "content": reply},
                                             "prompt_eval_count": input_tokens, "eval_count": output_tokens})
        return httpx.Response(200, json={"output": [{"type": "message", "content": [{"type": "output_text", "text": reply}]}],
                                         "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens}})

    def handler(self, request):
        payload = json.loads(request.content)
        self.requests.append((request.url.path, payload))
        if request.url.path == "/api/show":
            return httpx.Response(200, json={"details": {"format": "gguf"}, "capabilities": ["completion"]})
        return self.response(self.is_title(payload), request.url.path == "/api/chat")

    def mock_http(self, handler=None):
        real_client = httpx.Client
        return patch("backend.provider.httpx.Client", side_effect=lambda **kwargs:
                     real_client(transport=httpx.MockTransport(handler or self.handler), **kwargs))

    def test_create_switch_isolated_context_and_title_accounting(self):
        self.assertIsNone(self.workspace()["active_chat_id"])
        first, second = self.create_chat(), self.create_chat()
        with self.mock_http():
            response = self.send(first, "First thread secret").json()
            self.assertEqual(response["active_chat_id"], first)
            self.assertEqual(response["chats"][0]["title"], "Quiet weekend plans")
            self.send(second, "Second thread context")
            self.send(first, "Continue first thread")
        reply_payloads = [payload for path, payload in self.requests if path == "/api/chat" and not self.is_title(payload)]
        self.assertNotIn("First thread secret", json.dumps(reply_payloads[1]))
        self.assertNotIn("Second thread context", json.dumps(reply_payloads[2]))
        self.assertEqual([message["content"] for message in reply_payloads[2]["messages"][1:]],
                         ["First thread secret", "Synthetic useful reply", "Continue first thread"])
        titles = [payload for path, payload in self.requests if path == "/api/chat" and self.is_title(payload)]
        self.assertEqual(len(titles), 2)
        self.assertTrue(all("tools" not in payload and payload["options"]["num_predict"] == 64 for payload in titles))
        self.assertEqual(titles[0]["messages"][1]["content"], "First thread secret")
        self.assertNotIn("Synthetic useful reply", json.dumps(titles[0]))
        self.assertEqual(len(self.workspace(first)["messages"]), 4)
        self.assertEqual(len(self.workspace(second)["messages"]), 2)
        usage = self.workspace()["usage"]
        self.assertEqual((usage["input_tokens"], usage["output_tokens"], usage["calls"]), (340, 70, 5))
        ledger = Provider(backend.DATABASE, backend.TIMEZONE).read_state()["ledger"]
        self.assertEqual([(entry["thread_id"], entry["kind"]) for entry in ledger],
                         [(first, "chat"), (first, "title"), (second, "chat"), (second, "title"), (first, "chat")])
        self.assertEqual(ledger[1]["parent_id"], ledger[0]["id"])
        self.assertEqual(self.workspace()["active_chat_id"], first)
        with TestClient(backend.app) as fresh:
            fresh.get("/api/session")
            restored = fresh.get("/api/workspace", params={"chat_id": second}).json()
        self.assertEqual(restored["messages"], self.workspace(second)["messages"])

    def test_legacy_live_history_migrates_exactly_once_without_title_call(self):
        self.workspace()
        messages = [{"id": "one", "role": "user", "text": "Retained question", "demo": False},
                    {"id": "two", "role": "assistant", "text": "Retained answer", "model": "old-model", "cost": 0.01,
                     "input_tokens": 30, "output_tokens": 10,
                     "web_search": {"query": "retained query", "sources": [{"title": "Source", "url": "https://example.org"}]}}]
        legacy = {"tasks": [], "messages": messages, "limits": backend.seed_workspace()["limits"], "working_core_migrated": True}
        with closing(sqlite3.connect(backend.DATABASE)) as db, db:
            db.execute("UPDATE workspace SET value=? WHERE id=1", (json.dumps(legacy),))
        migrated = self.workspace()
        self.assertEqual(migrated["messages"], messages)
        self.assertEqual(migrated["chats"][0]["title"], "Previous conversation")
        self.assertEqual(self.workspace(), migrated)
        with closing(sqlite3.connect(backend.DATABASE)) as db:
            stored = json.loads(db.execute("SELECT value FROM workspace WHERE id=1").fetchone()[0])
        self.assertNotIn("messages", stored)
        with self.mock_http():
            self.send(migrated["active_chat_id"], "Follow up")
        self.assertFalse(any(self.is_title(payload) for _, payload in self.requests))

    def test_demo_cleanup_precedes_chat_migration(self):
        state = {"tasks": [], "messages": [{"id": "demo", "role": "assistant", "text": "Old demo", "demo": True},
                                            {"id": "live", "role": "user", "text": "Real history", "demo": False}],
                 "limits": backend.seed_workspace()["limits"], "live_chat_migrated": True}
        backend.migrate_workspace(state)
        self.assertEqual(state["chats"][0]["messages"], [{"id": "live", "role": "user", "text": "Real history", "demo": False}])
        self.assertEqual(state["legacy_preview"]["messages"][0]["text"], "Old demo")
        self.assertNotIn("messages", state)

    def test_invalid_ids_and_titles_cannot_mutate_workspace(self):
        chat_id = self.create_chat()
        before = self.workspace()
        self.assertEqual(self.client.get("/api/workspace", params={"chat_id": "missing"}).status_code, 404)
        self.assertEqual(self.send("missing").status_code, 404)
        self.assertEqual(self.client.patch("/api/chats/missing", headers=self.headers, json={"title": "Missing"}).status_code, 404)
        self.assertEqual(self.client.delete("/api/chats/missing", headers=self.headers).status_code, 404)
        for title in ("", "   ", "a" * 121):
            self.assertEqual(self.client.patch(f"/api/chats/{chat_id}", headers=self.headers, json={"title": title}).status_code, 422)
        self.assertEqual(self.workspace(), before)

    def test_manual_rename_before_first_send_skips_naming_and_delete_keeps_usage(self):
        chat_id = self.create_chat()
        renamed = self.client.patch(f"/api/chats/{chat_id}", headers=self.headers, json={"title": "My chosen title"}).json()
        self.assertEqual(renamed["chats"][0]["title"], "My chosen title")
        with self.mock_http():
            response = self.send(chat_id)
        self.assertFalse(any(self.is_title(payload) for _, payload in self.requests))
        usage = response.json()["usage"]
        deleted = self.client.delete(f"/api/chats/{chat_id}", headers=self.headers).json()
        self.assertEqual(deleted["chats"], [])
        self.assertIsNone(deleted["active_chat_id"])
        self.assertEqual(deleted["usage"], usage)
        self.assertEqual(self.client.get("/api/workspace", params={"chat_id": chat_id}).status_code, 404)
        self.assertEqual(self.client.delete("/api/chat", headers=self.headers).status_code, 405)

    def test_reply_settles_to_original_thread_when_another_is_created(self):
        first = self.create_chat()
        entered, release = threading.Event(), threading.Event()
        responses = []
        def handler(request):
            if request.url.path == "/api/chat" and not self.is_title(json.loads(request.content)):
                entered.set()
                self.assertTrue(release.wait(5))
            return self.handler(request)
        with self.mock_http(handler):
            worker = threading.Thread(target=lambda: responses.append(self.send(first)))
            worker.start()
            try:
                self.assertTrue(entered.wait(5))
                second = self.create_chat()
                self.assertEqual(self.workspace()["active_chat_id"], second)
                self.assertEqual(self.client.delete(f"/api/chats/{first}", headers=self.headers).status_code, 409)
                self.assertEqual(self.client.delete(f"/api/chats/{second}", headers=self.headers).status_code, 200)
                second = self.create_chat()
            finally:
                release.set()
                worker.join(5)
        self.assertFalse(worker.is_alive())
        self.assertEqual(responses[0].json()["active_chat_id"], first)
        self.assertEqual(len(self.workspace(first)["messages"]), 2)
        self.assertEqual(self.workspace(second)["messages"], [])

    def test_manual_rename_wins_while_title_is_running(self):
        chat_id = self.create_chat()
        entered, release = threading.Event(), threading.Event()
        responses = []
        def handler(request):
            if self.is_title(json.loads(request.content)):
                entered.set()
                self.assertTrue(release.wait(5))
            return self.handler(request)
        with self.mock_http(handler):
            worker = threading.Thread(target=lambda: responses.append(self.send(chat_id)))
            worker.start()
            try:
                self.assertTrue(entered.wait(5))
                self.assertEqual(len(self.workspace(chat_id)["messages"]), 2)
                renamed = self.client.patch(f"/api/chats/{chat_id}", headers=self.headers, json={"title": "Keep my title"})
                self.assertEqual(renamed.status_code, 200)
                self.assertEqual(self.client.delete(f"/api/chats/{chat_id}", headers=self.headers).status_code, 409)
            finally:
                release.set()
                worker.join(5)
        self.assertFalse(worker.is_alive())
        self.assertEqual(responses[0].status_code, 200)
        self.assertEqual(self.workspace(chat_id)["chats"][0]["title"], "Keep my title")
        self.assertEqual(self.workspace()["usage"]["calls"], 2)

    def test_paid_title_failure_keeps_reply_and_unknown_charge_reconciles(self):
        remote = {**DEFAULT, "base_url": "https://synthetic.example/v1", "model": "synthetic-paid",
                  "input_usd_per_million": 1, "output_usd_per_million": 2, "pricing_verified": True}
        self.configure(remote)
        chat_id = self.create_chat()
        def handler(request):
            payload = json.loads(request.content)
            self.requests.append((request.url.path, payload))
            return httpx.Response(200, json={"output": []}) if self.is_title(payload) else self.response(local=False)
        with self.mock_http(handler):
            result = self.send(chat_id).json()
        self.assertEqual(result["messages"][-1]["text"], "Synthetic useful reply")
        self.assertEqual(result["chats"][0]["title"], "Plan a quiet weekend")
        self.assertEqual(len(result["usage"]["uncertain"]), 1)
        uncertain = result["usage"]["uncertain"][0]
        resolved = self.client.post(f"/api/provider/charges/{uncertain['id']}/reconcile", headers=self.headers, json={"billed_usd": 0.001})
        self.assertEqual(resolved.status_code, 200)
        with self.mock_http():
            self.assertEqual(self.send(chat_id, "Continue after reconciliation").status_code, 200)
        self.assertEqual(sum(self.is_title(payload) for _, payload in self.requests), 1)
        self.assertAlmostEqual(self.workspace()["usage"]["today_usd"], 0.00128)

    def test_title_reservation_lock_failure_keeps_paid_reply_successful_once(self):
        self.configure({**DEFAULT, "base_url": "https://synthetic.example/v1", "model": "synthetic-paid",
                        "input_usd_per_million": 1, "output_usd_per_million": 2, "pricing_verified": True})
        chat_id = self.create_chat()
        real_transaction, real_connect = Provider.transaction, sqlite3.connect
        transaction_count = 0
        def no_wait_connect(*args, **kwargs):
            return real_connect(*args, **{**kwargs, "timeout": 0})
        @contextmanager
        def block_title_reservation(service, with_db=False):
            nonlocal transaction_count
            transaction_count += 1
            if transaction_count == 3:  # Main reservation and settlement already committed.
                with closing(real_connect(backend.DATABASE)) as writer:
                    writer.execute("BEGIN IMMEDIATE")
                    try:
                        with patch("backend.provider.sqlite3.connect", side_effect=no_wait_connect):
                            with real_transaction(service, with_db) as state:
                                yield state
                    finally:
                        writer.rollback()
            else:
                with real_transaction(service, with_db) as state:
                    yield state
        with self.mock_http(), patch.object(Provider, "transaction", block_title_reservation):
            result = self.send(chat_id, "Preserve this paid reply")
            self.assertEqual(result.status_code, 200, result.text)
            self.assertEqual(transaction_count, 3)
            self.assertEqual(result.json()["messages"][-1]["text"], "Synthetic useful reply")
            self.assertEqual(result.json()["chats"][0]["title"], "Preserve this paid reply")
            usage = result.json()["usage"]
            self.assertEqual((usage["input_tokens"], usage["output_tokens"], usage["calls"]), (100, 20, 1))
            self.assertAlmostEqual(usage["today_usd"], 0.00014)
            self.assertEqual(usage["reserved_usd"], 0)
            self.assertEqual(usage["uncertain"], [])
            ledger = Provider(backend.DATABASE, backend.TIMEZONE).read_state()["ledger"]
            self.assertEqual([(entry["kind"], entry["status"]) for entry in ledger], [("chat", "settled")])
            self.assertEqual(len(self.requests), 1)
            Provider(backend.DATABASE, backend.TIMEZONE).recover()
            self.assertEqual(self.send(chat_id, "Continue after naming failed").status_code, 200)
        self.assertFalse(any(self.is_title(payload) for _, payload in self.requests))
        messages = self.workspace(chat_id)["messages"]
        self.assertEqual(sum(message["text"] == "Preserve this paid reply" for message in messages), 1)
        self.assertEqual(len(messages), 4)
        self.assertAlmostEqual(self.workspace()["usage"]["today_usd"], 0.00028)

    def test_local_title_failure_is_not_retried_after_restart(self):
        chat_id = self.create_chat()
        def handler(request):
            payload = json.loads(request.content)
            if self.is_title(payload):
                self.requests.append((request.url.path, payload))
                return httpx.Response(500, json={"error": "synthetic local failure"})
            return self.handler(request)
        with self.mock_http(handler):
            result = self.send(chat_id)
        self.assertEqual(result.status_code, 200)
        self.assertEqual(self.workspace()["usage"]["uncertain"], [])
        Provider(backend.DATABASE, backend.TIMEZONE).recover()
        with self.mock_http():
            self.assertEqual(self.send(chat_id, "Continue").status_code, 200)
        self.assertEqual(sum(self.is_title(payload) for _, payload in self.requests), 1)

    def test_output_token_and_spend_bounds_skip_optional_title(self):
        self.configure({**self.config, "max_output_tokens": 64})
        first = self.create_chat()
        def full_reply(request):
            if request.url.path == "/api/show":
                return self.handler(request)
            payload = json.loads(request.content)
            self.requests.append((request.url.path, payload))
            return self.response(output=64)
        with self.mock_http(full_reply):
            self.assertEqual(self.send(first).status_code, 200)
        self.assertFalse(any(self.is_title(payload) for _, payload in self.requests))
        self.requests.clear()
        self.configure(self.config)
        limits = self.workspace()["limits"]
        self.client.put("/api/limits", headers=self.headers, json={**limits, "max_tokens": 2800})
        second = self.create_chat()
        def large_usage(request):
            if request.url.path == "/api/show":
                return self.handler(request)
            self.requests.append((request.url.path, json.loads(request.content)))
            data = self.response().json()
            data["prompt_eval_count"] = 1000
            return httpx.Response(200, json=data)
        with self.mock_http(large_usage):
            self.assertEqual(self.send(second).status_code, 200)
        self.assertFalse(any(self.is_title(payload) for _, payload in self.requests))
        self.requests.clear()
        remote = {**DEFAULT, "base_url": "https://synthetic.example/v1", "model": "synthetic-paid",
                  "input_usd_per_million": 1, "output_usd_per_million": 2, "pricing_verified": True,
                  "max_output_tokens": 64}
        self.configure(remote)
        self.client.put("/api/limits", headers=self.headers, json={**limits, "run_usd": 0.00242})
        third = self.create_chat()
        with self.mock_http():
            self.assertEqual(self.send(third).status_code, 200)
        self.assertFalse(any(self.is_title(payload) for _, payload in self.requests))

    def test_title_uses_remaining_output_and_skips_changed_endpoint(self):
        self.configure({**self.config, "max_output_tokens": 64})
        first = self.create_chat()
        def bounded_reply(request):
            payload = json.loads(request.content)
            if request.url.path == "/api/chat" and not self.is_title(payload):
                self.requests.append((request.url.path, payload))
                return self.response(output=50)
            return self.handler(request)
        with self.mock_http(bounded_reply):
            self.assertEqual(self.send(first).status_code, 200)
        title_payload = next(payload for _, payload in self.requests if self.is_title(payload))
        self.assertEqual(title_payload["options"]["num_predict"], 14)
        self.requests.clear()
        self.configure(self.config)
        second = self.create_chat()
        def changed_settings(request):
            payload = json.loads(request.content)
            if request.url.path == "/api/chat" and not self.is_title(payload):
                self.configure({**DEFAULT, "base_url": "https://different.example/v1", "model": "different-model",
                                "pricing_verified": True})
            return self.handler(request)
        with self.mock_http(changed_settings):
            result = self.send(second)
        self.assertEqual(result.status_code, 200)
        self.assertFalse(any(self.is_title(payload) for _, payload in self.requests))
        self.assertEqual(self.workspace(second)["messages"][-1]["model"], "synthetic-chat")

    def test_title_uses_original_key_and_bounded_first_message(self):
        self.configure({**DEFAULT, "base_url": "https://synthetic.example/v1", "model": "synthetic-paid",
                        "input_usd_per_million": 1, "output_usd_per_million": 2, "pricing_verified": True})
        chat_id = self.create_chat()
        original_key = self.read_key.return_value[0]
        first_message = "Bounded title context " * 150
        def handler(request):
            self.assertEqual(request.headers["Authorization"], "Bearer " + original_key)
            payload = json.loads(request.content)
            self.requests.append((request.url.path, payload))
            title = self.is_title(payload)
            if not title:
                self.read_key.return_value = ("synthetic-replacement-key", "session")
            return self.response(title=title, local=False)
        with self.mock_http(handler):
            response = self.send(chat_id, first_message)
        self.assertEqual(response.status_code, 200, response.text)
        title_payload = next(payload for _, payload in self.requests if self.is_title(payload))
        self.assertEqual(title_payload["input"], [{"role": "user", "content": first_message[:400]}])
        self.assertNotIn("tools", title_payload)
        self.assertNotIn(original_key, response.text)
        self.assertNotIn(original_key.encode(), backend.DATABASE.read_bytes())


if __name__ == "__main__":
    unittest.main()

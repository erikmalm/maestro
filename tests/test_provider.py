"""Live-chat application paths with synthetic keys and mocked provider HTTP."""
from pathlib import Path
import json
import os
import tempfile
import threading
import unittest
import uuid
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient

from backend import app as backend, credentials
from backend.provider import DEFAULT, Provider


class ProviderTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="maestro-chat-test-")
        self.database_patch = patch.object(backend, "DATABASE", Path(self.directory.name) / "workspace.sqlite3")
        self.database_patch.start()
        self.read_patch = patch.object(credentials, "read", side_effect=lambda url: (credentials.session_keys.get(url), "session"))
        self.read_patch.start()
        self.base = "http://127.0.0.1:9876/v1"
        self.key = "synthetic-chat-test-key"
        self.client = TestClient(backend.app)
        self.headers = {"X-Maestro-CSRF": self.client.get("/api/session").json()["csrf"]}
        self.client.get("/api/workspace")
        self.config = {**DEFAULT, "base_url": self.base, "model": "synthetic-chat-model",
                       "input_usd_per_million": 1.0, "output_usd_per_million": 2.0, "pricing_verified": True}
        self.requests = []
        self.setup_provider()

    def tearDown(self):
        credentials.session_keys.pop(self.base, None)
        self.client.close()
        self.read_patch.stop()
        self.database_patch.stop()
        self.directory.cleanup()

    def setup_provider(self, config=None):
        result = self.client.put("/api/provider", headers=self.headers, json={"config": config or self.config, "api_key": self.key, "persist": False})
        self.assertEqual(result.status_code, 200, result.text)

    def provider_http(self, request):
        self.requests.append(request)
        self.assertEqual(request.headers["Authorization"], "Bearer " + self.key)
        if request.method == "GET":
            return httpx.Response(200, json={"data": [{"id": "synthetic-chat-model"}]})
        return httpx.Response(200, json={"status": "completed", "output": [{"type": "message", "content": [{"type": "output_text", "text": "Synthetic provider answer"}]}], "usage": {"input_tokens": 1000, "output_tokens": 200}})

    def mock_http(self, handler=None):
        real_client = httpx.Client
        return patch("backend.provider.httpx.Client", side_effect=lambda **kwargs: real_client(transport=httpx.MockTransport(handler or self.provider_http), **kwargs))

    def test_setup_test_chat_usage_and_context_survive_reload(self):
        with self.mock_http():
            tested = self.client.post("/api/provider/test", headers=self.headers, json={})
            self.assertEqual(tested.status_code, 200)
            self.assertEqual(tested.json()["models"], ["synthetic-chat-model"])
            first = self.client.post("/api/chat", headers=self.headers, json={"text": "Remember the synthetic codeword."})
            self.assertEqual(first.status_code, 200, first.text)
            self.assertEqual(first.json()["messages"][-1]["text"], "Synthetic provider answer")
            self.assertAlmostEqual(first.json()["usage"]["today_usd"], 0.0014)
            self.assertEqual(first.json()["usage"]["input_tokens"], 1000)
            second = self.client.post("/api/chat", headers=self.headers, json={"text": "What was it?"})
            self.assertEqual(second.status_code, 200)
        sent = json.loads(self.requests[-1].content)
        self.assertFalse(sent["store"])
        self.assertEqual(sent["model"], self.config["model"])
        self.assertEqual(sent["input"][0]["content"], "Remember the synthetic codeword.")
        restored = self.client.get("/api/workspace").json()
        self.assertEqual(len(restored["messages"]), 4)
        self.assertAlmostEqual(restored["usage"]["month_usd"], 0.0028)
        self.assertNotIn(self.key, json.dumps(restored))
        self.assertNotIn(self.key.encode(), backend.DATABASE.read_bytes())
        cleared = self.client.delete("/api/chat", headers=self.headers).json()
        self.assertEqual(cleared["messages"], [])
        self.assertEqual(cleared["usage"], restored["usage"])

    def test_no_credentials_and_zero_budget_cannot_dispatch(self):
        self.client.delete("/api/provider/key", headers=self.headers)
        with self.mock_http():
            self.assertEqual(self.client.post("/api/chat", headers=self.headers, json={"text": "Synthetic test"}).status_code, 409)
        self.setup_provider()
        limits = self.client.get("/api/workspace").json()["limits"]
        self.client.put("/api/limits", headers=self.headers, json={**limits, "daily_usd": 0})
        with self.mock_http():
            self.assertEqual(self.client.post("/api/chat", headers=self.headers, json={"text": "Synthetic test"}).status_code, 409)
        self.assertEqual(self.requests, [])

    def test_provider_auth_error_is_redacted_and_does_not_reserve_charge(self):
        with self.mock_http(lambda request: httpx.Response(401, json={"error": self.key})):
            response = self.client.post("/api/chat", headers=self.headers, json={"text": "Synthetic test"})
        self.assertEqual(response.status_code, 409)
        self.assertNotIn(self.key, response.text)
        usage = self.client.get("/api/workspace").json()["usage"]
        self.assertEqual(usage["reserved_usd"], 0)
        self.assertEqual(usage["today_usd"], 0)

    def test_unknown_charge_blocks_retry_and_survives_recovery(self):
        with self.mock_http(lambda request: httpx.Response(200, json={"output": []})):
            self.assertEqual(self.client.post("/api/chat", headers=self.headers, json={"text": "Synthetic test"}).status_code, 409)
        service = Provider(backend.DATABASE, backend.TIMEZONE)
        service.recover()
        before = service.usage()
        self.assertGreater(before["reserved_usd"], 0)
        with self.mock_http():
            self.assertEqual(self.client.post("/api/chat", headers=self.headers, json={"text": "Retry"}).status_code, 409)
        entry = before["uncertain"][0]
        resolved = self.client.post(f"/api/provider/charges/{entry['id']}/reconcile", headers=self.headers, json={"billed_usd": 0.002})
        self.assertEqual(resolved.status_code, 200)
        self.assertEqual(resolved.json()["usage"]["reserved_usd"], 0)
        self.assertAlmostEqual(resolved.json()["usage"]["today_usd"], 0.002)

    def test_endpoint_changes_cannot_receive_previous_key(self):
        changed = {**self.config, "base_url": "https://different.example/v1"}
        response = self.client.put("/api/provider", headers=self.headers, json={"config": changed, "api_key": "", "persist": False})
        self.assertFalse(response.json()["credentials_present"])
        for url in ("http://remote.example/v1", "https://secret@remote.example/v1", "https://remote.example/v1?key=secret", "https://remote.example:bad/v1", "https://remote.example:99999/v1"):
            response = self.client.put("/api/provider", headers=self.headers, json={"config": {**self.config, "base_url": url}, "persist": False})
            self.assertEqual(response.status_code, 409)

    def test_chat_completion_protocol_reports_actual_usage(self):
        self.setup_provider({**self.config, "protocol": "chat_completions"})
        def handler(request):
            payload = json.loads(request.content)
            self.assertEqual(request.url.path, "/v1/chat/completions")
            self.assertEqual(payload["max_tokens"], 1024)
            return httpx.Response(200, json={"choices": [{"message": {"content": "Compatible model reply"}}], "usage": {"prompt_tokens": 80, "completion_tokens": 20}})
        with self.mock_http(handler):
            result = self.client.post("/api/chat", headers=self.headers, json={"text": "Hello"})
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(result.json()["usage"]["input_tokens"], 80)

    def test_concurrent_request_cannot_dispatch_or_clear_chat(self):
        entered, release = threading.Event(), threading.Event()
        def handler(request):
            entered.set()
            self.assertTrue(release.wait(3))
            return self.provider_http(request)
        result = []
        def send():
            result.append(self.client.post("/api/chat", headers=self.headers, json={"text": "First"}))
        with self.mock_http(handler):
            thread = threading.Thread(target=send)
            thread.start()
            self.assertTrue(entered.wait(3))
            self.assertEqual(self.client.post("/api/chat", headers=self.headers, json={"text": "Second"}).status_code, 409)
            self.assertEqual(self.client.delete("/api/chat", headers=self.headers).status_code, 409)
            release.set()
            thread.join(3)
        self.assertEqual(result[0].status_code, 200)
        self.assertEqual(len(self.requests), 1)


@unittest.skipUnless(os.name == "nt", "Windows credential integration")
class WindowsCredentialTests(unittest.TestCase):
    def test_real_windows_store_roundtrip_and_removal_with_synthetic_key(self):
        endpoint = "https://synthetic-" + uuid.uuid4().hex + ".invalid/v1"
        try:
            credentials.save(endpoint, "synthetic-vault-test-value", True)
            self.assertEqual(credentials.read(endpoint), ("synthetic-vault-test-value", "Windows Credential Manager"))
            credentials.delete(endpoint)
            self.assertEqual(credentials.read(endpoint)[0], None)
        finally:
            credentials.delete(endpoint)


if __name__ == "__main__":
    unittest.main()

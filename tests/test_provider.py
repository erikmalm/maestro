"""Live-chat application paths with synthetic keys and mocked provider HTTP."""
from pathlib import Path
import json
import os
import sqlite3
import tempfile
import threading
import unittest
import uuid
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient

from backend import app as backend, credentials
from backend.provider import DEFAULT, Provider, TITLE_INSTRUCTIONS
from backend.web_search import ENDPOINT as SEARCH_ENDPOINT, WebSearch


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

    def setup_provider(self, config=None, key=None):
        result = self.client.put("/api/provider", headers=self.headers, json={"config": config or self.config, "api_key": self.key if key is None else key, "persist": False})
        self.assertEqual(result.status_code, 200, result.text)

    def local_config(self, **changes):
        return {**self.config, "protocol": "ollama", "base_url": "http://127.0.0.1:11434",
                "model": "synthetic-local:latest", "orchestrator_model": "synthetic-coordinator:latest",
                "max_output_tokens": 256, **changes}

    def local_provider_http(self, request):
        self.requests.append(request)
        self.assertEqual(request.url.host, "127.0.0.1")
        self.assertNotIn("authorization", request.headers)
        payload = json.loads(request.content)
        if request.url.path == "/api/show":
            return httpx.Response(200, json={"details": {"format": "gguf"}, "capabilities": ["completion", "tools"]})
        self.assertEqual(request.url.path, "/api/chat")
        return httpx.Response(200, json={"done": True, "message": {"role": "assistant", "content": "Synthetic local reply"},
                                         "prompt_eval_count": 37, "eval_count": 11})

    def provider_http(self, request):
        self.requests.append(request)
        self.assertEqual(request.headers["Authorization"], "Bearer " + self.key)
        if request.method == "GET":
            return httpx.Response(200, json={"data": [{"id": "synthetic-chat-model"}]})
        if json.loads(request.content).get("instructions") == TITLE_INSTRUCTIONS:
            return httpx.Response(200, json={"output": [{"type": "message", "content": [{"type": "output_text", "text": "Synthetic chat title"}]}], "usage": {"input_tokens": 100, "output_tokens": 5}})
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
            self.assertAlmostEqual(first.json()["usage"]["today_usd"], 0.00151)
            self.assertEqual(first.json()["usage"]["input_tokens"], 1100)
            second = self.client.post("/api/chat", headers=self.headers, json={"text": "What was it?"})
            self.assertEqual(second.status_code, 200)
        sent = json.loads(self.requests[-1].content)
        self.assertNotIn("options", sent)
        self.assertNotIn("keep_alive", sent)
        self.assertFalse(sent["store"])
        self.assertEqual(sent["model"], self.config["model"])
        self.assertEqual(sent["input"][0]["content"], "Remember the synthetic codeword.")
        restored = self.client.get("/api/workspace").json()
        self.assertEqual(len(restored["messages"]), 4)
        self.assertAlmostEqual(restored["usage"]["month_usd"], 0.00291)
        self.assertNotIn(self.key, json.dumps(restored))
        self.assertNotIn(self.key.encode(), backend.DATABASE.read_bytes())
        new_chat = self.client.post("/api/chats", headers=self.headers).json()
        self.assertEqual(new_chat["messages"], [])
        self.assertEqual(new_chat["usage"], restored["usage"])
        self.assertEqual(len(new_chat["chats"]), 2)
        self.assertEqual(self.client.get("/api/workspace", params={"chat_id": restored["active_chat_id"]}).json()["messages"], restored["messages"])

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

    def test_model_list_survives_model_price_and_protocol_changes(self):
        with self.mock_http():
            tested = self.client.post("/api/provider/test", headers=self.headers).json()
        saved = self.client.put("/api/provider", headers=self.headers, json={
            "config": {**self.config, "model": "another-chat-model", "protocol": "chat_completions",
                       "input_usd_per_million": 3, "max_output_tokens": 2048}, "api_key": "", "persist": False})
        self.assertEqual(saved.status_code, 200)
        self.assertEqual(saved.json()["models"], tested["models"])
        self.assertEqual(saved.json()["tested_at"], tested["tested_at"])
        # Replacing the key must discard the old key's model-list verification.
        self.setup_provider()
        self.assertEqual(self.client.get("/api/provider").json()["models"], [])
        self.assertIsNone(self.client.get("/api/provider").json()["tested_at"])

    def test_local_coordinator_selection_overrides_fallback_titles_and_actual_accounting(self):
        service = Provider(backend.DATABASE, backend.TIMEZONE)
        local = self.local_config()
        for position, (route, coordinator, override, expected, recorded) in enumerate((
                ("orchestrator", local["orchestrator_model"], "", local["orchestrator_model"], "orchestrator"),
                ("orchestrator", local["orchestrator_model"], "synthetic-override:latest", "synthetic-override:latest", "explicit"),
                ("orchestrator", "", "", local["model"], "orchestrator"),
                ("direct", local["orchestrator_model"], "", local["model"], "direct"))):
            with self.subTest(route=route, override=override, coordinator=coordinator):
                self.setup_provider({**local, "chat_routing": route, "orchestrator_model": coordinator}, key="")
                self.requests.clear()
                with self.mock_http(self.local_provider_http):
                    reply = self.client.post("/api/chat", headers=self.headers, json={"text": "Synthetic routing check", "model": override})
                self.assertEqual(reply.status_code, 200, reply.text)
                answer = reply.json()["messages"][-1]
                self.assertEqual((answer["kind"], answer["chat_routing"], answer["model"]), ("chat", recorded, expected))
                self.assertTrue(reply.json()["messages"][-2]["reflection_eligible"])
                ledger = service.read_state()["ledger"]
                entry = next(entry for entry in reversed(ledger) if entry["kind"] == "chat")
                self.assertEqual((entry["status"], entry["chat_routing"], entry["model"], entry["input_tokens"], entry["output_tokens"]),
                                 ("settled", recorded, expected, 37, 11))
                self.assertEqual(entry["connection_config"]["model"], local["model"])
                payloads = [json.loads(request.content) for request in self.requests]
                self.assertEqual(len(ledger), position + 2)
                self.assertEqual(len(payloads), 3 if position == 0 else 2)
                self.assertTrue(all(payload["model"] == expected for payload in payloads))
                self.assertEqual("Coordinate this reply" in payloads[1]["messages"][0]["content"], recorded == "orchestrator")
                if position == 0:
                    self.assertEqual((ledger[-1]["kind"], ledger[-1]["model"]), ("title", expected))
                    self.assertNotIn("chat_routing", ledger[-1])
                    self.assertEqual(payloads[-1]["messages"][0]["content"], TITLE_INSTRUCTIONS)

    def test_legacy_route_defaults_direct_and_invalid_routes_preserve_state_and_credentials(self):
        local = self.local_config()
        self.setup_provider(local, key="")
        service = Provider(backend.DATABASE, backend.TIMEZONE)
        with service.transaction() as state:
            state["config"].pop("chat_routing")
        self.assertEqual(service.read_state()["config"]["chat_routing"], "direct")
        with self.mock_http(self.local_provider_http):
            reply = self.client.post("/api/chat", headers=self.headers, json={"text": "Legacy route"})
        self.assertEqual(reply.status_code, 200, reply.text)
        self.assertEqual(reply.json()["messages"][-1]["model"], local["model"])
        before = service.read_state()
        for route, status in (("orchestrator", 409), ("delegate", 422)):
            with self.subTest(route=route), patch.object(credentials, "save") as save:
                response = self.client.put("/api/provider", headers=self.headers, json={
                    "config": {**self.config, "chat_routing": route}, "api_key": "synthetic-replacement-key", "persist": False})
                self.assertEqual(response.status_code, status, response.text)
                save.assert_not_called()
                self.assertEqual(service.read_state(), before)
                self.assertEqual(credentials.read(self.base)[0], self.key)
        with self.assertRaisesRegex(ValueError, "Choose direct or orchestrator"):
            service.configure({**local, "chat_routing": "delegate"}, "", False)
        self.assertEqual(service.read_state(), before)

    def test_coordinator_still_requires_installed_completion_model_before_inference(self):
        self.setup_provider(self.local_config(chat_routing="orchestrator"), key="")
        service = Provider(backend.DATABASE, backend.TIMEZONE)
        with service.transaction() as state:
            state["models"] = ["synthetic-local:latest"]
        for status, metadata in ((200, {"details": {"format": "gguf"}, "remote_host": "cloud.invalid", "capabilities": ["completion"]}),
                                 (200, {"details": {"format": "gguf"}, "capabilities": []}), (404, {"error": "Model not found"})):
            with self.subTest(metadata=metadata):
                self.requests.clear()
                def unavailable(request):
                    self.local_provider_http(request)
                    return httpx.Response(status, json=metadata)
                with self.mock_http(unavailable):
                    reply = self.client.post("/api/chat", headers=self.headers, json={"text": "Blocked coordinator"})
                self.assertEqual(reply.status_code, 409, reply.text)
                self.assertEqual([request.url.path for request in self.requests], ["/api/show"])
                entry = Provider(backend.DATABASE, backend.TIMEZONE).read_state()["ledger"][-1]
                self.assertEqual((entry["status"], entry["chat_routing"], entry["model"]),
                                 ("failed", "orchestrator", "synthetic-coordinator:latest"))
        # A stale inventory does not override fresh installed-model metadata.
        with self.mock_http(self.local_provider_http):
            reply = self.client.post("/api/chat", headers=self.headers, json={"text": "Newly installed coordinator"})
        self.assertEqual(reply.status_code, 200, reply.text)
        self.assertEqual(reply.json()["messages"][-1]["model"], "synthetic-coordinator:latest")
        self.assertEqual(service.read_state()["models"], ["synthetic-local:latest"])

    def test_coordinator_reuses_existing_search_round_and_shared_reply_allowance(self):
        local = self.local_config(chat_routing="orchestrator")
        self.setup_provider(local, key="")
        chat_id = self.client.post("/api/chats", headers=self.headers).json()["active_chat_id"]
        named = self.client.patch(f"/api/chats/{chat_id}", headers=self.headers, json={"title": "Synthetic search"})
        self.assertEqual(named.status_code, 200, named.text)
        credentials.session_keys[SEARCH_ENDPOINT] = "synthetic-routing-search-key"
        self.addCleanup(credentials.session_keys.pop, SEARCH_ENDPOINT, None)
        search = WebSearch(backend.DATABASE, backend.TIMEZONE)
        with search.transaction() as state:
            state["config"]["enabled"] = True
            state["tested_at"] = search.now().isoformat()
        def use_search(request):
            response = self.local_provider_http(request)
            payload = json.loads(request.content)
            if request.url.path == "/api/chat" and "tools" in payload:
                response = httpx.Response(200, json={"done": True, "message": {"role": "assistant", "content": "", "tool_calls": [{"function": {
                    "name": "web_search", "arguments": {"query": "public documentation"}}}]}, "prompt_eval_count": 37, "eval_count": 11})
            return response
        with self.mock_http(use_search), patch.object(WebSearch, "search", return_value={"query": "public documentation", "at": search.now().isoformat(),
                "sources": [{"title": "Synthetic source", "url": "https://docs.example.org/guide", "content": "Public documentation."}]}) as hosted:
            reply = self.client.post("/api/chat", headers=self.headers, json={"text": "Consult public documentation"})
        self.assertEqual(reply.status_code, 200, reply.text)
        hosted.assert_called_once_with("public documentation")
        payloads = [json.loads(request.content) for request in self.requests if request.url.path == "/api/chat"]
        self.assertEqual([payload["model"] for payload in payloads], [local["orchestrator_model"]] * 2)
        self.assertEqual(payloads[0]["tools"][0]["function"]["name"], "web_search")
        self.assertNotIn("tools", payloads[1])
        self.assertEqual(payloads[1]["options"]["num_predict"], 245)
        self.assertNotIn(credentials.session_keys[SEARCH_ENDPOINT], json.dumps(payloads))
        self.assertFalse(reply.json()["messages"][-2]["reflection_eligible"])
        entry = Provider(backend.DATABASE, backend.TIMEZONE).read_state()["ledger"][-1]
        self.assertEqual((entry["status"], entry["model_calls"], entry["model"], entry["input_tokens"], entry["output_tokens"]),
                         ("settled", 2, local["orchestrator_model"], 74, 22))

    def test_provider_auth_error_is_redacted_and_does_not_reserve_charge(self):
        with self.mock_http(lambda request: httpx.Response(401, json={"error": self.key})):
            response = self.client.post("/api/chat", headers=self.headers, json={"text": "Synthetic test"})
        self.assertEqual(response.status_code, 409)
        self.assertNotIn(self.key, response.text)
        usage = self.client.get("/api/workspace").json()["usage"]
        self.assertEqual(usage["reserved_usd"], 0)
        self.assertEqual(usage["today_usd"], 0)

    def test_model_test_cannot_verify_a_replaced_or_removed_key(self):
        for replace in (True, False):
            with self.subTest(replace=replace):
                self.setup_provider()
                def handler(request):
                    response = self.provider_http(request)
                    if replace:
                        updated = self.client.put("/api/provider", headers=self.headers, json={
                            "config": self.config, "api_key": "synthetic-replacement-key", "persist": False})
                    else:
                        updated = self.client.delete("/api/provider/key", headers=self.headers)
                    self.assertEqual(updated.status_code, 200)
                    return response
                with self.mock_http(handler):
                    result = self.client.post("/api/provider/test", headers=self.headers)
                self.assertEqual(result.status_code, 409)
                status = self.client.get("/api/provider").json()
                self.assertEqual(status["models"], [])
                self.assertIsNone(status["tested_at"])
                self.assertNotIn("synthetic-replacement-key", result.text)

    def test_model_test_rejects_echoed_credentials_before_persistence(self):
        with self.mock_http():
            before = self.client.post("/api/provider/test", headers=self.headers).json()
        with self.mock_http(lambda request: httpx.Response(200, json={
                "data": [{"id": "safe-model"}, {"id": "model-" + self.key + "-suffix"}]})):
            response = self.client.post("/api/provider/test", headers=self.headers)
        self.assertEqual(response.status_code, 409)
        self.assertNotIn(self.key, response.text)
        self.assertEqual(self.client.get("/api/provider").json(), before)
        self.assertNotIn(self.key.encode(), backend.DATABASE.read_bytes())

    def test_manual_model_rejects_current_and_effective_keys_before_changing_state(self):
        with self.mock_http():
            before = self.client.post("/api/provider/test", headers=self.headers).json()
        for protocol, submitted, exposed in (("responses", "", self.key),
                                            ("responses", "synthetic-replacement-key", "synthetic-replacement-key"),
                                            ("responses", "synthetic-replacement-key", self.key), ("ollama", "", self.key)):
            with self.subTest(protocol=protocol, submitted=bool(submitted), exposed=exposed), patch.object(credentials, "save", wraps=credentials.save) as save:
                response = self.client.put("/api/provider", headers=self.headers, json={
                    "config": {**self.config, "protocol": protocol, "model": "model-" + exposed + "-suffix",
                               "base_url": "http://127.0.0.1:11434" if protocol == "ollama" else self.base},
                    "api_key": submitted, "persist": False})
                self.assertEqual(response.status_code, 409)
                self.assertNotIn(exposed, response.text)
                save.assert_not_called()
                self.assertEqual(credentials.read(self.base)[0], self.key)
                self.assertEqual(self.client.get("/api/provider").json(), before)
                self.assertNotIn(exposed.encode(), backend.DATABASE.read_bytes())

    def test_manual_model_uses_target_endpoint_and_replacement_key_scope(self):
        target = "https://different.example/v1"
        target_key = "synthetic-target-key"
        credentials.session_keys[target] = target_key
        try:
            for submitted, model, expected in (("", "model-" + target_key, 409),
                                               ("synthetic-new-key", "model-" + self.key, 409),
                                               ("", "safe-target-model", 200),
                                               ("synthetic-new-key", "safe-replacement-model", 200)):
                with self.subTest(submitted=bool(submitted), model=model):
                    response = self.client.put("/api/provider", headers=self.headers, json={
                        "config": {**self.config, "base_url": target, "model": model},
                        "api_key": submitted, "persist": False})
                    self.assertEqual(response.status_code, expected, response.text)
            self.assertEqual(credentials.read(target)[0], "synthetic-new-key")
            self.assertEqual(credentials.read(self.base)[0], self.key)
            self.assertNotIn(target_key.encode(), backend.DATABASE.read_bytes())
        finally:
            credentials.session_keys.pop(target, None)

    def test_base_url_rejects_known_keys_before_changing_configuration_or_credentials(self):
        with self.mock_http():
            before = self.client.post("/api/provider/test", headers=self.headers).json()
        for submitted, exposed, target_key in (("", self.key, self.key),
                                               ("synthetic-replacement-key", "synthetic-replacement-key", self.key),
                                               ("", self.key, None), ("synthetic-replacement-key", self.key, None)):
            for encoded in (False, True):
                path = "".join(f"%{ord(char):02X}" for char in exposed) if encoded else exposed
                target = self.base + "/" + path
                if target_key:
                    credentials.session_keys[target] = target_key
                try:
                    with self.subTest(submitted=bool(submitted), target_key=bool(target_key), encoded=encoded), patch.object(credentials, "save", wraps=credentials.save) as save:
                        response = self.client.put("/api/provider", headers=self.headers, json={
                            "config": {**self.config, "base_url": target}, "api_key": submitted, "persist": False})
                        self.assertEqual(response.status_code, 409)
                        self.assertNotIn(exposed, response.text)
                        self.assertNotIn(path, response.text)
                        save.assert_not_called()
                        self.assertEqual(credentials.read(target)[0], target_key)
                        self.assertEqual(credentials.read(self.base)[0], self.key)
                        self.assertEqual(self.client.get("/api/provider").json(), before)
                        self.assertNotIn(exposed.encode(), backend.DATABASE.read_bytes())
                        self.assertNotIn(path.encode(), backend.DATABASE.read_bytes())
                finally:
                    credentials.session_keys.pop(target, None)

    def test_successful_reply_refusal_and_title_redact_dispatched_credentials(self):
        for protocol, kind, empty in (("responses", "output_text", None), ("responses", "refusal", None),
                                     ("chat_completions", "content", None), ("chat_completions", "refusal", None),
                                     ("chat_completions", "refusal", "")):
            with self.subTest(protocol=protocol, kind=kind, content=empty):
                self.setup_provider({**self.config, "protocol": protocol})
                self.client.post("/api/chats", headers=self.headers)
                def handler(request):
                    self.assertEqual(request.headers["Authorization"], "Bearer " + self.key)
                    payload = json.loads(request.content)
                    if payload.get("instructions", (payload.get("messages") or [{}])[0].get("content")) == TITLE_INSTRUCTIONS:
                        credentials.session_keys[self.base] = "synthetic-replacement-key"
                    reply = "Provider echo " + self.key
                    if protocol == "responses":
                        field = "refusal" if kind == "refusal" else "text"
                        # The echoed key straddles two output parts.
                        content = [{"type": kind, field: part} for part in (reply[:-5], reply[-5:])]
                        data = {"output": [{"type": "message", "content": content}],
                                "usage": {"input_tokens": 100, "output_tokens": 5}}
                    else:
                        message = {kind: reply}
                        if kind == "refusal":
                            message["content"] = empty
                        data = {"choices": [{"message": message}],
                                "usage": {"prompt_tokens": 100, "completion_tokens": 5}}
                    return httpx.Response(200, json=data)
                with self.mock_http(handler):
                    response = self.client.post("/api/chat", headers=self.headers, json={"text": "Synthetic echo check"})
                self.assertEqual(response.status_code, 200, response.text)
                workspace = response.json()
                self.assertEqual(workspace["messages"][-1]["text"], "Provider echo [redacted]")
                self.assertEqual(next(x["title"] for x in workspace["chats"] if x["id"] == workspace["active_chat_id"]), "Provider echo [redacted]")
                self.assertEqual(workspace["usage"]["reserved_usd"], 0)
                self.assertAlmostEqual(workspace["messages"][-1]["cost"], 0.00011)
                self.assertEqual(workspace["messages"][-1]["input_tokens"], 100)
                self.assertEqual(workspace["messages"][-1]["output_tokens"], 5)
                self.assertNotIn(self.key, response.text)
                self.assertNotIn(self.key, self.client.get("/api/workspace").text)
                self.assertNotIn(self.key.encode(), backend.DATABASE.read_bytes())

    def test_redaction_marker_cannot_contain_the_dispatched_key(self):
        for key, prefix, suffix in (("redacted", "", ""), ("[redacted]", "", ""),
                                    ("x[redacted]", "x", ""), ("[redacted]x", "", "x")):
            with self.subTest(key=key):
                self.key = key
                self.setup_provider()
                def handler(request):
                    data = self.provider_http(request).json()
                    data["output"][0]["content"][0]["text"] = "Provider echo " + prefix + self.key + suffix
                    return httpx.Response(200, json=data)
                with self.mock_http(handler):
                    response = self.client.post("/api/chat", headers=self.headers, json={"text": "Synthetic marker check"})
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(response.json()["messages"][-1]["text"], "Provider echo " + prefix + "\u2588" + suffix)
                self.assertNotIn(self.key, response.text)
                self.assertNotIn(self.key.encode(), backend.DATABASE.read_bytes())

    def test_title_normalization_cannot_recreate_the_dispatched_key(self):
        self.key = "synthetic title key"
        self.setup_provider()
        def handler(request):
            data = self.provider_http(request).json()
            if json.loads(request.content).get("instructions") == TITLE_INSTRUCTIONS:
                data["output"][0]["content"][0]["text"] = "synthetic  title  key"
            return httpx.Response(200, json=data)
        with self.mock_http(handler):
            response = self.client.post("/api/chat", headers=self.headers, json={"text": "Synthetic title check"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["chats"][0]["title"], "[redacted]")
        self.assertNotIn(self.key, response.text)
        self.assertNotIn(self.key.encode(), backend.DATABASE.read_bytes())

    def test_overrun_settles_reply_without_invalidating_another_configuration(self):
        for change_model in (False, True):
            with self.subTest(change_model=change_model):
                self.setup_provider({**self.config, "max_output_tokens": 1024})
                def handler(request):
                    if change_model:
                        self.setup_provider({**self.config, "model": "new-chat-model"})
                    return httpx.Response(200, json={
                        "output": [{"type": "message", "content": [{"type": "output_text", "text": "Completed reply"}]}],
                        "usage": {"input_tokens": 100, "output_tokens": 2000}})
                original = Provider.transaction
                calls = 0
                def fail_after_settlement(service, with_db=False):
                    nonlocal calls
                    calls += 1
                    if calls > 2:
                        raise sqlite3.OperationalError("Synthetic subsequent write failure")
                    return original(service, with_db)
                with self.mock_http(handler):
                    # A post-settlement failure must never replace the completed reply.
                    if change_model:
                        result = self.client.post("/api/chat", headers=self.headers, json={"text": "Check configuration change"})
                    else:
                        with patch.object(Provider, "transaction", fail_after_settlement):
                            result = self.client.post("/api/chat", headers=self.headers, json={"text": "Check overrun"})
                self.assertEqual(result.status_code, 200, result.text)
                self.assertEqual(result.json()["messages"][-1]["text"], "Completed reply")
                self.assertEqual(result.json()["provider"]["config"]["pricing_verified"], change_model)
                self.assertEqual(result.json()["usage"]["reserved_usd"], 0)

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

    def test_quota_and_rate_errors_are_distinct_redacted_and_release_reservations(self):
        cases = [("credit_balance_exhausted", "insufficient_quota", "credits are exhausted"),
                 ("project_spend_limit_exceeded", "insufficient_quota", "project spending limit"),
                 ("organization_spend_limit_exceeded", "insufficient_quota", "organization spending limit"),
                 ("organization_usage_limit_exceeded", "insufficient_quota", "usage limit"),
                 ("insufficient_quota", "insufficient_quota", "quota is unavailable"),
                 ("rate_limit_exceeded", "rate_limit_error", "request rate limit"),
                 ("slow_down", "rate_limit_error", "slower traffic"),
                 (["malformed"], None, "Provider limit reached")]
        for code, kind, expected in cases:
            with self.subTest(code=code), self.mock_http(lambda request: httpx.Response(429, json={
                    "error": {"code": code, "type": kind, "message": self.key}})):
                response = self.client.post("/api/chat", headers=self.headers, json={"text": "Synthetic error check"})
                self.assertEqual(response.status_code, 409)
                self.assertIn(expected, response.json()["detail"])
                self.assertNotIn(self.key, response.text)
        usage = self.client.get("/api/workspace").json()["usage"]
        self.assertEqual(usage["reserved_usd"], 0)
        self.assertEqual(usage["today_usd"], 0)
        self.assertEqual(usage["input_tokens"], 0)

    def test_endpoint_changes_cannot_receive_previous_key(self):
        changed = {**self.config, "base_url": "https://different.example/v1"}
        response = self.client.put("/api/provider", headers=self.headers, json={"config": changed, "api_key": "", "persist": False})
        self.assertFalse(response.json()["credentials_present"])
        for url in ("http://remote.example/v1", "https://secret@remote.example/v1", "https://remote.example/v1?key=secret", "https://remote.example:bad/v1", "https://remote.example:99999/v1"):
            response = self.client.put("/api/provider", headers=self.headers, json={"config": {**self.config, "base_url": url}, "persist": False})
            self.assertEqual(response.status_code, 409)

    def test_invalid_keys_never_dispatch_or_create_uncertain_charges(self):
        for key in ("synthetic-\u2603-key", "synthetic-\n-key", "synthetic-\x7f-key"):
            with self.subTest(key=repr(key)):
                with self.assertRaises(ValueError):
                    credentials.save(self.base, key, False)
                # Also reject invalid credentials read from an existing vault/environment.
                credentials.session_keys[self.base] = key
                with self.mock_http():
                    for path, body in (("/api/provider/test", {}), ("/api/chat", {"text": "Synthetic check"})):
                        response = self.client.post(path, headers=self.headers, json=body)
                        self.assertEqual(response.status_code, 409)
                self.assertEqual(self.client.get("/api/workspace").json()["usage"]["uncertain"], [])
        self.assertEqual(self.requests, [])

    def test_chat_completion_protocol_reports_actual_usage(self):
        self.setup_provider({**self.config, "protocol": "chat_completions"})
        def handler(request):
            payload = json.loads(request.content)
            self.assertEqual(request.url.path, "/v1/chat/completions")
            title = payload["messages"][0]["content"] == TITLE_INSTRUCTIONS
            self.assertEqual(payload["max_tokens"], 64 if title else self.config["max_output_tokens"])
            self.assertNotIn("options", payload)
            self.assertNotIn("keep_alive", payload)
            self.assertNotIn("service_tier", payload)  # Compatible providers need not implement OpenAI tiers.
            return httpx.Response(200, json={"choices": [{"message": {"content": "Compatible chat" if title else "Compatible model reply"}}], "usage": {"prompt_tokens": 20 if title else 80, "completion_tokens": 5 if title else 20}})
        with self.mock_http(handler):
            result = self.client.post("/api/chat", headers=self.headers, json={"text": "Hello"})
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(result.json()["usage"]["input_tokens"], 100)

    def test_openai_requests_use_standard_pricing_tier(self):
        official = "https://api.openai.com/v1"
        try:
            self.setup_provider({**self.config, "base_url": official})
            with self.mock_http():
                result = self.client.post("/api/chat", headers=self.headers, json={"text": "Synthetic tier check"})
            self.assertEqual(result.status_code, 200, result.text)
            self.assertEqual(json.loads(self.requests[-1].content)["service_tier"], "default")
        finally:
            credentials.session_keys.pop(official, None)

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
            chat_id = self.client.get("/api/workspace").json()["active_chat_id"]
            self.assertEqual(self.client.delete(f"/api/chats/{chat_id}", headers=self.headers).status_code, 409)
            release.set()
            thread.join(3)
        self.assertEqual(result[0].status_code, 200)
        self.assertEqual(len(self.requests), 2)


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

"""Bounded hosted search and local tool calls with synthetic keys and mocked HTTP."""
import asyncio
from contextlib import contextmanager
from datetime import timedelta
from pathlib import Path
import json
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient

from backend import app as backend, credentials
from backend.provider import DEFAULT as PROVIDER_DEFAULT, TITLE_INSTRUCTIONS
from backend.web_search import DEFAULT, ENDPOINT, WebSearch, fit_sources, safe_url


class WebSearchTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="maestro-search-test-")
        self.database_patch = patch.object(backend, "DATABASE", Path(self.directory.name) / "workspace.sqlite3")
        self.database_patch.start()
        self.keys = {}
        self.key = "synthetic-hosted-search-key"
        self.patches = [
            patch.object(credentials, "read", side_effect=lambda endpoint: (self.keys.get(endpoint), "synthetic session")),
            patch.object(credentials, "save", side_effect=lambda endpoint, key, persist: self.keys.__setitem__(endpoint, key)),
            patch.object(credentials, "delete", side_effect=lambda endpoint: self.keys.pop(endpoint, None)),
        ]
        for item in self.patches:
            item.start()
        self.client = TestClient(backend.app)
        self.headers = {"X-Maestro-CSRF": self.client.get("/api/session").json()["csrf"]}
        self.client.get("/api/workspace")
        self.provider_config = {
            **PROVIDER_DEFAULT, "protocol": "ollama", "base_url": "http://127.0.0.1:11434",
            "model": "synthetic-tools:latest", "ollama_context_tokens": 8192, "max_output_tokens": 256,
        }
        self.set_provider(self.provider_config)
        self.service = WebSearch(backend.DATABASE, backend.TIMEZONE)
        self.requests = []
        self.mode = "search"
        self.query = "Ollama official current capabilities"
        self.snippet = "Synthetic public documentation excerpt."
        self.tool_calls = [{"function": {"name": "web_search", "arguments": {"query": self.query}}}]

    def tearDown(self):
        self.client.close()
        for item in reversed(self.patches):
            item.stop()
        self.database_patch.stop()
        self.directory.cleanup()

    def set_provider(self, config):
        response = self.client.put("/api/provider", headers=self.headers,
                                   json={"config": config, "api_key": "", "persist": False})
        self.assertEqual(response.status_code, 200, response.text)

    def configure(self, enabled=False, key="", **settings):
        return self.client.put("/api/web-search", headers=self.headers,
                               json={"config": {**DEFAULT, "enabled": enabled, **settings},
                                     "api_key": key, "persist": False})

    def age_attempts(self):
        with self.service.transaction() as state:
            for entry in state["ledger"]:
                entry["at"] = (self.service.now() - timedelta(seconds=10)).isoformat()

    def enable(self):
        self.assertEqual(self.configure(key=self.key).status_code, 200)
        with self.mock_http():
            tested = self.client.post("/api/web-search/test", headers=self.headers)
        self.assertEqual(tested.status_code, 200, tested.text)
        self.assertFalse(tested.json()["config"]["enabled"])
        self.age_attempts()
        enabled = self.configure(enabled=True)
        self.assertEqual(enabled.status_code, 200, enabled.text)
        self.requests.clear()

    def model_response(self, message, output=10):
        return httpx.Response(200, json={"done": True, "message": message,
                                         "prompt_eval_count": 100, "eval_count": output})

    def search_response(self, sources=None):
        return httpx.Response(200, json={"results": sources or [{
            "title": "Synthetic public source", "url": "https://docs.ollama.com/capabilities/web-search",
            "content": self.snippet,
        }]})

    def provider_http(self, request):
        self.requests.append(request)
        if request.url.host == "ollama.com":
            self.assertEqual(str(request.url), ENDPOINT)
            self.assertEqual(request.method, "POST")
            self.assertEqual(request.headers["authorization"], "Bearer " + self.key)
            payload = json.loads(request.content)
            self.assertEqual(set(payload), {"query", "max_results"})
            self.assertLessEqual(payload["max_results"], 3)
            return self.search_response()
        self.assertEqual(request.url.host, "127.0.0.1")
        self.assertNotIn("authorization", request.headers)
        self.assertNotIn(self.key.encode(), request.content)
        if request.url.path == "/api/show":
            return httpx.Response(200, json={"details": {"format": "gguf"},
                                            "capabilities": ["completion", "tools"]})
        self.assertEqual(request.url.path, "/api/chat")
        payload = json.loads(request.content)
        self.assertFalse(payload["stream"])
        if payload["messages"][0]["content"] == TITLE_INSTRUCTIONS:
            self.assertEqual(payload["options"]["num_predict"], 64)
            self.assertNotIn("tools", payload)
            return httpx.Response(200, json={"done": True, "message": {"role": "assistant", "content": "Synthetic research chat"},
                                             "prompt_eval_count": 20, "eval_count": 5})
        self.assertEqual(payload["options"]["num_predict"], 256 if "tools" in payload else 246)
        if "tools" in payload:
            self.assertEqual(payload["tools"][0]["function"]["name"], "web_search")
            if self.mode == "reply":
                return self.model_response({"role": "assistant", "content": "Synthetic ordinary reply"}, 20)
            return self.model_response({"role": "assistant", "content": "", "tool_calls": self.tool_calls})
        return self.model_response({"role": "assistant", "content": "Synthetic sourced answer [1]"}, 20)

    @contextmanager
    def mock_http(self, handler=None):
        real_client, real_async_client = httpx.Client, httpx.AsyncClient
        transport = httpx.MockTransport(handler or self.provider_http)
        with patch("backend.provider.httpx.Client", side_effect=lambda **kwargs: real_client(transport=transport, **kwargs)), \
                patch("backend.web_search.httpx.AsyncClient", side_effect=lambda **kwargs: real_async_client(transport=transport, **kwargs)):
            yield

    def search_requests(self):
        return [request for request in self.requests if request.url.host == "ollama.com"]

    def model_requests(self):
        return [request for request in self.requests if request.url.path == "/api/chat"
                and json.loads(request.content)["messages"][0]["content"] != TITLE_INSTRUCTIONS]

    def ordinary_http(self, request):
        self.requests.append(request)
        self.assertEqual(request.url.host, "127.0.0.1")
        if request.url.path == "/api/show":
            return httpx.Response(200, json={"details": {"format": "gguf"}, "capabilities": ["completion", "tools"]})
        self.assertNotIn("tools", json.loads(request.content))
        return self.model_response({"role": "assistant", "content": "Synthetic ordinary reply"}, 20)

    def test_verified_key_enable_gates_and_deletion(self):
        self.assertEqual(self.configure(enabled=True).status_code, 409)
        self.assertEqual(self.configure(key=self.key).status_code, 200)
        self.assertEqual(self.configure(enabled=True).status_code, 409)
        with self.mock_http():
            tested = self.client.post("/api/web-search/test", headers=self.headers)
        self.assertEqual(tested.status_code, 200, tested.text)
        self.assertFalse(tested.json()["config"]["enabled"])
        self.assertIsNotNone(tested.json()["tested_at"])
        self.assertEqual(tested.json()["searches_today"], 1)
        self.assertEqual(json.loads(self.search_requests()[0].content)["query"],
                         "Ollama official web search documentation")
        for config in ({**self.provider_config, "ollama_context_tokens": 4096},
                       {**self.provider_config, "protocol": "responses", "base_url": "https://synthetic.invalid/v1"}):
            self.set_provider(config)
            self.assertEqual(self.configure(enabled=True).status_code, 409)
        self.set_provider(self.provider_config)
        self.assertEqual(self.configure(enabled=True).status_code, 200)
        removed = self.client.delete("/api/web-search/key", headers=self.headers)
        self.assertEqual(removed.status_code, 200, removed.text)
        self.assertFalse(removed.json()["config"]["enabled"])
        self.assertFalse(removed.json()["credentials_present"])
        self.assertIsNone(removed.json()["tested_at"])
        self.assertEqual(removed.json()["searches_today"], 1)
        self.assertNotIn(self.key, removed.text)
        self.assertNotIn(self.key.encode(), backend.DATABASE.read_bytes())

    def test_automatic_search_two_model_calls_sources_and_usage_persist(self):
        self.enable()
        private_context = "Synthetic private chat detail excluded from the public search query"
        with self.mock_http():
            response = self.client.post("/api/chat", headers=self.headers, json={"text": private_context})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(len(self.model_requests()), 2)
        self.assertEqual(len(self.search_requests()), 1)
        sent_query = json.loads(self.search_requests()[0].content)
        self.assertEqual(sent_query["query"], self.query)
        self.assertNotIn(private_context, json.dumps(sent_query))
        final = json.loads(self.model_requests()[-1].content)
        self.assertNotIn("tools", final)
        self.assertTrue(any(message["role"] == "tool" for message in final["messages"]))
        self.assertIn(self.snippet, json.dumps(final))
        with TestClient(backend.app) as fresh:
            fresh.get("/api/session")
            restored = fresh.get("/api/workspace").json()
            search_status = fresh.get("/api/web-search").json()
        self.assertEqual(restored["messages"][-1]["text"], "Synthetic sourced answer [1]")
        evidence = restored["messages"][-1]["web_search"]
        self.assertEqual(evidence["query"], self.query)
        self.assertIsInstance(evidence["at"], str)
        self.assertEqual(evidence["sources"], [{"title": "Synthetic public source",
                                              "url": "https://docs.ollama.com/capabilities/web-search"}])
        self.assertNotIn("content", json.dumps(evidence))
        self.assertNotIn(self.snippet.encode(), backend.DATABASE.read_bytes())
        self.assertEqual(restored["usage"]["input_tokens"], 220)
        self.assertEqual(restored["usage"]["output_tokens"], 35)
        self.assertEqual(restored["usage"]["calls"], 3)
        self.assertEqual(restored["usage"]["today_usd"], 0)
        self.assertEqual(restored["usage"]["uncertain"], [])
        self.assertEqual(search_status["searches_today"], 2)
        self.assertNotIn(self.key, json.dumps(restored))
        self.assertNotIn(self.key.encode(), backend.DATABASE.read_bytes())

    def test_ordinary_reply_does_not_search(self):
        self.enable()
        self.mode = "reply"
        with self.mock_http():
            response = self.client.post("/api/chat", headers=self.headers, json={"text": "Hello locally"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(len(self.model_requests()), 1)
        self.assertEqual(self.search_requests(), [])
        self.assertNotIn("web_search", response.json()["messages"][-1])
        self.assertEqual(self.service.status()["searches_today"], 1)
        self.assertEqual(response.json()["usage"]["calls"], 2)

    def test_lost_readiness_quota_and_cooldown_allow_ordinary_local_replies(self):
        self.enable()
        for reason in ("missing key after restart", "failed retest", "daily cap", "cooldown"):
            with self.subTest(reason=reason):
                self.keys.clear()
                if reason != "missing key after restart":
                    self.keys[ENDPOINT] = self.key
                with self.service.transaction() as state:
                    state["tested_at"] = None if reason == "failed retest" else self.service.now().isoformat()
                    state["config"]["daily_limit"] = 1 if reason == "daily cap" else 20
                    state["paused_until"] = (self.service.now() + timedelta(seconds=60)).isoformat() if reason == "cooldown" else None
                self.requests.clear()
                with self.mock_http(self.ordinary_http):
                    response = self.client.post("/api/chat", headers=self.headers, json={"text": reason})
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(self.search_requests(), [])
                self.assertEqual(self.service.status()["searches_today"], 1)

    def test_failed_retest_blocks_automatic_dispatch_without_blocking_local_reply(self):
        self.enable()
        with self.mock_http(lambda request: httpx.Response(500)):
            rejected = self.client.post("/api/web-search/test", headers=self.headers)
        self.assertEqual(rejected.status_code, 409, rejected.text)
        self.assertTrue(self.service.status()["config"]["enabled"])
        self.assertIsNone(self.service.status()["tested_at"])
        with self.assertRaisesRegex(ValueError, "Test the Ollama search key"):
            self.service.search(self.query)
        with self.mock_http(self.ordinary_http):
            response = self.client.post("/api/chat", headers=self.headers, json={"text": "Reply after a failed retest"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.search_requests(), [])

    def test_multiple_unknown_and_invalid_tool_requests_cannot_dispatch(self):
        self.enable()
        valid = self.tool_calls[0]
        cases = ([valid, valid], [{"function": {"name": "web_fetch", "arguments": {"url": "https://synthetic.invalid"}}}],
                 [{"function": {"name": "web_search", "arguments": {"query": " "}}}],
                 [{"function": {"name": "web_search", "arguments": {"query": "x" * 513}}}],
                 [{"function": {"name": "web_search", "arguments": {"query": ["invalid"]}}}],
                 [{"function": {"name": "web_search", "arguments": {"query": self.query, "extra": "invalid"}}}])
        for calls in cases:
            with self.subTest(calls=calls):
                self.tool_calls = calls
                self.requests.clear()
                with self.mock_http():
                    response = self.client.post("/api/chat", headers=self.headers, json={"text": "Synthetic invalid tool request"})
                self.assertEqual(response.status_code, 409, response.text)
                self.assertEqual(len(self.model_requests()), 1)
                self.assertEqual(self.search_requests(), [])
                self.assertEqual(self.client.get("/api/workspace").json()["messages"], [])
        self.assertEqual(self.service.status()["searches_today"], 1)

    def test_limits_and_missing_tools_capability_block_next_dispatch(self):
        self.enable()
        limits = self.client.get("/api/workspace").json()["limits"]
        self.client.put("/api/limits", headers=self.headers, json={**limits, "max_tokens": 0})
        with self.mock_http():
            blocked = self.client.post("/api/chat", headers=self.headers, json={"text": "No token allowance"})
        self.assertEqual(blocked.status_code, 409)
        self.assertEqual(self.requests, [])
        self.client.put("/api/limits", headers=self.headers, json=limits)

        def unsupported(request):
            self.requests.append(request)
            self.assertEqual(request.url.path, "/api/show")
            return httpx.Response(200, json={"details": {"format": "gguf"}, "capabilities": ["completion"]})

        with self.mock_http(unsupported):
            blocked = self.client.post("/api/chat", headers=self.headers, json={"text": "Tool support missing"})
        self.assertEqual(blocked.status_code, 409, blocked.text)
        self.assertEqual(self.model_requests(), [])
        self.requests.clear()

        def reduced_allowance(request):
            response = self.provider_http(request)
            if request.url.path == "/api/chat" and "tools" in json.loads(request.content):
                with backend.workspace_transaction() as state:
                    state["limits"]["max_tokens"] = 110
            return response

        with self.mock_http(reduced_allowance):
            blocked = self.client.post("/api/chat", headers=self.headers, json={"text": "Allowance changes before next call"})
        self.assertEqual(blocked.status_code, 409, blocked.text)
        self.assertEqual(len(self.model_requests()), 1)
        usage = self.client.get("/api/workspace").json()["usage"]
        self.assertEqual(usage["input_tokens"], 100)
        self.assertEqual(usage["output_tokens"], 10)
        self.assertEqual(usage["reserved_usd"], 0)
        self.assertEqual(usage["uncertain"], [])

    def test_malformed_oversized_and_unsafe_sources_are_counted_without_leaking(self):
        self.assertEqual(self.configure(key=self.key).status_code, 200)
        secret = "synthetic-private-service-error"
        cases = (
            lambda: httpx.Response(200, text=secret),
            lambda: httpx.Response(200, content=b"x" * 131073),
            lambda: httpx.Response(200, json={"results": "invalid"}),
            lambda: self.search_response([{"title": "unsafe", "content": secret, "url": url}
                                         for url in ("javascript:alert(1)", "http://127.0.0.1/private", "http://localhost/private")]),
            lambda: httpx.Response(401, json={"error": secret + self.key}),
        )
        for index, make_response in enumerate(cases, 1):
            self.age_attempts()
            calls = []

            def handler(request):
                calls.append(request)
                self.assertEqual(str(request.url), ENDPOINT)
                return make_response()

            with self.subTest(index=index), self.mock_http(handler):
                rejected = self.client.post("/api/web-search/test", headers=self.headers)
            self.assertEqual(rejected.status_code, 409, rejected.text)
            self.assertNotIn(secret, rejected.text)
            self.assertNotIn(self.key, rejected.text)
            self.assertEqual(len(calls), 1)
            self.assertEqual(self.service.status()["searches_today"], index)
            self.assertNotIn(secret.encode(), backend.DATABASE.read_bytes())
        self.assertIsNone(self.service.status()["tested_at"])

    def test_source_urls_block_browser_private_host_aliases(self):
        for host in ("127.1", "0x7f.0.0.1", "0177.0.0.1", "2130706433", "%31%32%37.0.0.1",
                     "１２７.0.0.1", "localhost。", "10.1", "[::1]", "example.local"):
            with self.subTest(host=host):
                self.assertFalse(safe_url("https://" + host + "/"))
        for url in ("https://docs.ollama.com/", "https://8.8.8.8/", "https://[2606:4700:4700::1111]/", "https://例え.テスト/"):
            with self.subTest(url=url):
                self.assertTrue(safe_url(url))

    def test_escaped_source_excerpts_fit_the_byte_allowance(self):
        for content in ('"' * 1200, "\\" * 1200, "\x00" * 1200, "\u00e9" * 1200):
            with self.subTest(content=content[:1]):
                source = {"title": "Synthetic source", "url": "https://docs.ollama.com/", "content": content}
                fitted = fit_sources([source], 256)
                self.assertTrue(fitted[0]["content"])
                self.assertTrue(content.startswith(fitted[0]["content"]))
                self.assertLessEqual(len(json.dumps(fitted, ensure_ascii=False).encode("utf-8")), 256)

    def test_invalid_stored_search_key_never_dispatches_or_reserves(self):
        for key in ("synthetic-\u2603-key", "synthetic-\n-key"):
            with self.subTest(key=repr(key)), self.mock_http():
                self.keys[ENDPOINT] = key
                result = self.client.post("/api/web-search/test", headers=self.headers)
                self.assertEqual(result.status_code, 409)
                self.assertIn("valid API key", result.json()["detail"])
                self.assertEqual(self.service.read_state()["ledger"], [])
        self.assertEqual(self.requests, [])

    def test_total_deadline_cancels_dns_headers_and_continuously_trickling_body(self):
        self.assertEqual(self.configure(key=self.key).status_code, 200)
        for stage in ("dns", "headers", "body"):
            with self.subTest(stage=stage):
                self.age_attempts()
                calls, closed = [], []
                release = threading.Event()

                class Trickle(httpx.AsyncByteStream):
                    async def __aiter__(self):
                        for _ in range(100):
                            await asyncio.sleep(0.01)
                            yield b" "

                    async def aclose(self):
                        closed.append(True)

                async def handler(request):
                    calls.append(request)
                    if stage != "body":
                        try:
                            if stage == "dns":
                                await asyncio.get_running_loop().run_in_executor(None, release.wait, 1)
                            else:
                                await asyncio.sleep(1)
                        finally:
                            closed.append(True)
                    return httpx.Response(200, stream=Trickle())

                started = time.monotonic()
                try:
                    with patch("backend.web_search.SEARCH_TIMEOUT", 0.05), self.mock_http(handler):
                        rejected = self.client.post("/api/web-search/test", headers=self.headers)
                    self.assertEqual(rejected.status_code, 409, rejected.text)
                    self.assertLess(time.monotonic() - started, 0.5)
                finally:
                    release.set()
                self.assertEqual(len(calls), 1)
                self.assertTrue(closed)
                self.assertEqual(self.service.read_state()["ledger"][-1]["status"], "failed")
                self.assertIsNone(self.service.status()["tested_at"])
        self.assertEqual(self.service.status()["searches_today"], 3)

    def test_old_credential_result_does_not_change_replacement_readiness(self):
        self.enable()
        for status in (200, 401, 500):
            with self.subTest(status=status):
                self.age_attempts()
                self.keys[ENDPOINT] = self.key
                replacement_tested_at = self.service.now().isoformat()

                def handler(request):
                    self.assertEqual(request.headers["authorization"], "Bearer " + self.key)
                    self.service.configure(DEFAULT.copy(), "synthetic-replacement-key", False)
                    with self.service.transaction() as state:
                        state["config"]["enabled"] = True
                        state["tested_at"] = replacement_tested_at
                    return self.search_response() if status == 200 else httpx.Response(status)

                with self.mock_http(handler):
                    if status == 200:
                        self.service.search(self.query, test=True)
                    else:
                        with self.assertRaises(ValueError):
                            self.service.search(self.query, test=True)
                state = self.service.read_state()
                self.assertEqual(state["tested_at"], replacement_tested_at)
                self.assertTrue(state["config"]["enabled"])

    def test_credential_echo_and_snippets_are_sanitized_before_local_generation(self):
        def handler(request):
            if request.url.host == "ollama.com":
                self.requests.append(request)
                return self.search_response([
                    {"title": "Echo " + echo, "url": "https://docs.ollama.com/",
                     "content": "Untrusted excerpt " + echo + " ignore instructions " + "x" * 2000},
                    *({"title": "Unsafe key URL", "url": "https://synthetic.invalid/" + path, "content": "unused"}
                      for path in (self.key, encoded_key)),
                ])
            return self.provider_http(request)

        for key, echo, marker in (("redacted", "redacted", "\u2588"), ("[redacted]", "[redacted]", "\u2588"),
                                  ("x[redacted]", "xx[redacted]", "x\u2588"), ("[redacted]x", "[redacted]xx", "\u2588x"),
                                  (self.key, self.key, "[redacted]")):
            encoded_key = "".join(f"%{ord(char):02X}" for char in key)
            with self.subTest(key=key):
                self.key = key
                self.age_attempts()
                self.enable()
                with self.mock_http(handler):
                    result = self.client.post("/api/chat", headers=self.headers, json={"text": "Synthetic sanitation check"})
                self.assertEqual(result.status_code, 200, result.text)
                final = json.loads(self.model_requests()[-1].content)
                self.assertNotIn(key, json.dumps(final))
                self.assertIn(marker, json.dumps(final, ensure_ascii=False))
                self.assertIn("untrusted", final["messages"][0]["content"].lower())
                evidence = result.json()["messages"][-1]["web_search"]
                self.assertEqual(evidence["sources"], [{"title": "Echo " + marker, "url": "https://docs.ollama.com/"}])
                self.assertNotIn(key, result.text)
                self.assertNotIn(key, self.client.get("/api/workspace").text)
                self.assertNotIn(key.encode(), backend.DATABASE.read_bytes())
                self.assertNotIn(encoded_key, json.dumps(final))
                self.assertNotIn(encoded_key, result.text)
                self.assertNotIn(encoded_key.encode(), backend.DATABASE.read_bytes())

    def test_daily_quota_spacing_rate_cooldown_and_recovery_persist_attempts(self):
        self.assertEqual(self.configure(key=self.key, daily_limit=1).status_code, 200)
        with self.mock_http():
            self.assertEqual(self.client.post("/api/web-search/test", headers=self.headers).status_code, 200)
            blocked = self.client.post("/api/web-search/test", headers=self.headers)
        self.assertEqual(blocked.status_code, 409)
        self.assertIn("daily", blocked.text.lower())
        self.assertEqual(len(self.search_requests()), 1)
        self.assertEqual(self.configure(daily_limit=20).status_code, 200)
        with self.mock_http():
            blocked = self.client.post("/api/web-search/test", headers=self.headers)
        self.assertEqual(blocked.status_code, 409)
        self.assertIn("five seconds", blocked.text)
        self.assertEqual(len(self.search_requests()), 1)
        self.age_attempts()
        calls = []

        def rate_limit(request):
            calls.append(request)
            return httpx.Response(429, headers={"Retry-After": "30"}, json={"error": self.key})

        with self.mock_http(rate_limit):
            limited = self.client.post("/api/web-search/test", headers=self.headers)
            blocked = self.client.post("/api/web-search/test", headers=self.headers)
        self.assertEqual(limited.status_code, 409)
        self.assertEqual(blocked.status_code, 409)
        self.assertEqual(len(calls), 1)
        self.assertNotIn(self.key, limited.text)
        self.assertIsNotNone(self.service.status()["paused_until"])
        self.assertEqual(self.service.status()["searches_today"], 2)
        with self.service.transaction() as state:
            state["ledger"].append({"id": "synthetic-interrupted-search", "at": self.service.now().isoformat(), "status": "reserved"})
        restored = WebSearch(backend.DATABASE, backend.TIMEZONE)
        restored.recover()
        self.assertEqual(restored.status()["searches_today"], 3)
        self.assertEqual(restored.read_state()["ledger"][-1]["status"], "interrupted")

    def test_search_failure_retains_planning_tokens_without_inventing_final_reply(self):
        self.enable()

        def handler(request):
            if request.url.host == "ollama.com":
                self.requests.append(request)
                return httpx.Response(401, json={"error": "synthetic-error " + self.key})
            return self.provider_http(request)

        with self.mock_http(handler):
            result = self.client.post("/api/chat", headers=self.headers, json={"text": "Search fails safely"})
        self.assertEqual(result.status_code, 409, result.text)
        self.assertNotIn(self.key, result.text)
        self.assertEqual(len(self.model_requests()), 1)
        self.assertEqual(len(self.search_requests()), 1)
        restored = self.client.get("/api/workspace").json()
        self.assertEqual(restored["messages"], [])
        self.assertEqual(restored["usage"]["input_tokens"], 100)
        self.assertEqual(restored["usage"]["output_tokens"], 10)
        self.assertEqual(restored["usage"]["calls"], 1)
        self.assertEqual(restored["usage"]["reserved_usd"], 0)
        self.assertEqual(restored["usage"]["uncertain"], [])
        self.assertEqual(self.service.status()["searches_today"], 2)
        self.assertFalse(self.service.status()["config"]["enabled"])
        self.assertIsNone(self.service.status()["tested_at"])
        self.requests.clear()

        with self.mock_http(self.ordinary_http):
            ordinary = self.client.post("/api/chat", headers=self.headers, json={"text": "A normal follow-up"})
        self.assertEqual(ordinary.status_code, 200, ordinary.text)
        self.assertEqual(self.search_requests(), [])

    def test_inflight_search_holds_chat_slot_and_cannot_be_repeated(self):
        self.enable()
        entered, release = threading.Event(), threading.Event()
        result = []

        def handler(request):
            if request.url.host == "ollama.com":
                entered.set()
                self.assertTrue(release.wait(3))
            return self.provider_http(request)

        def send():
            result.append(self.client.post("/api/chat", headers=self.headers, json={"text": "Single reserved search"}))

        with self.mock_http(handler):
            thread = threading.Thread(target=send)
            thread.start()
            try:
                self.assertTrue(entered.wait(3))
                self.assertEqual(self.client.post("/api/chat", headers=self.headers, json={"text": "Concurrent request"}).status_code, 409)
                chat_id = self.client.get("/api/workspace").json()["active_chat_id"]
                self.assertEqual(self.client.delete(f"/api/chats/{chat_id}", headers=self.headers).status_code, 409)
                self.assertEqual(self.client.post("/api/web-search/test", headers=self.headers).status_code, 409)
            finally:
                release.set()
                thread.join(3)
        self.assertFalse(thread.is_alive())
        self.assertEqual(result[0].status_code, 200, result[0].text)
        self.assertEqual(len(self.search_requests()), 1)
        self.assertEqual(len(self.model_requests()), 2)


if __name__ == "__main__":
    unittest.main()

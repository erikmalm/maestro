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
        self.directory = self.enterContext(tempfile.TemporaryDirectory(prefix="maestro-search-test-"))
        self.enterContext(patch.object(backend, "DATABASE", Path(self.directory) / "workspace.sqlite3"))
        self.keys = {}
        self.key = "synthetic-hosted-search-key"
        self.enterContext(patch.object(credentials, "read",
                                      side_effect=lambda endpoint: (self.keys.get(endpoint), "synthetic session")))
        self.enterContext(patch.object(credentials, "save",
                                      side_effect=lambda endpoint, key, persist: self.keys.__setitem__(endpoint, key)))
        self.enterContext(patch.object(credentials, "delete",
                                      side_effect=lambda endpoint: self.keys.pop(endpoint, None)))
        self.client = TestClient(backend.app)
        self.addCleanup(self.client.close)
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

    def send(self, text, handler=None):
        with self.mock_http(handler):
            return self.client.post("/api/chat", headers=self.headers, json={"text": text})

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

    def test_readiness_reports_static_settings_reasons_without_revealing_credentials(self):
        state = {"config": DEFAULT.copy(), "tested_at": None, "paused_until": None, "ledger": []}
        missing = self.service.status(state)
        self.assertFalse(missing["ready"])
        self.assertEqual(missing["unavailable_reason"], "Add and test an Ollama search API key in Settings.")
        self.keys[ENDPOINT] = self.key
        disabled = self.service.status(state)
        self.assertFalse(disabled["ready"])
        self.assertEqual(disabled["unavailable_reason"], "Enable Optional web search in Settings.")
        state["config"]["enabled"] = True
        untested = self.service.status(state)
        self.assertFalse(untested["ready"])
        self.assertEqual(untested["unavailable_reason"], "Test the Ollama search key in Settings before using web search.")
        state["tested_at"] = self.service.now().isoformat()
        ready = self.service.status(state)
        self.assertTrue(ready["ready"])
        self.assertIsNone(ready["unavailable_reason"])
        state["config"]["enabled"] = False
        self.assertFalse(self.service.status(state)["ready"])  # A tested key never enables search by itself.
        state["config"]["enabled"] = True
        self.keys.clear()
        lost = self.service.status(state)
        self.assertFalse(lost["ready"])
        self.assertIsNone(lost["tested_at"])
        for result in (missing, disabled, untested, ready, lost):
            self.assertNotIn(self.key, json.dumps(result))

    def test_readiness_matches_quota_cooldown_inflight_and_spacing_gates(self):
        self.keys[ENDPOINT] = self.key
        now = self.service.now()
        base = {"config": {**DEFAULT, "enabled": True}, "tested_at": now.isoformat(), "paused_until": None, "ledger": []}
        cases = [
            ({**base, "config": {**base["config"], "daily_limit": 1},
              "ledger": [{"id": "synthetic-completed", "at": (now - timedelta(seconds=10)).isoformat(), "status": "completed"}]},
             "Daily web search limit reached. Adjust the search allowance in Settings."),
            ({**base, "paused_until": (now + timedelta(seconds=30)).isoformat()},
             "Ollama search is paused after a rate limit. Wait until the cooldown ends."),
            ({**base, "ledger": [{"id": "synthetic-active", "at": (now - timedelta(seconds=10)).isoformat(), "status": "reserved"}]},
             "A web search is already running. Wait for it to finish."),
            ({**base, "ledger": [{"id": "synthetic-recent", "at": (now - timedelta(seconds=4, microseconds=999999)).isoformat(), "status": "failed"}]},
             "Search requests are spaced at least five seconds apart. Wait before trying again."),
        ]
        with patch.object(self.service, "now", return_value=now):
            for state, reason in cases:
                original = json.dumps(state, sort_keys=True)
                with self.subTest(reason=reason):
                    result = self.service.status(state)
                    self.assertFalse(result["ready"])
                    self.assertEqual(result["unavailable_reason"], reason)
                    self.assertEqual(json.dumps(state, sort_keys=True), original)
                    self.assertNotIn(self.key, json.dumps(result))
            boundary = {**base, "ledger": [{"id": "synthetic-boundary", "at": (now - timedelta(seconds=5)).isoformat(), "status": "failed"}]}
            self.assertTrue(self.service.status(boundary)["ready"])

    def test_readiness_cooldown_and_daily_reset_are_computed_without_rewriting_state(self):
        self.keys[ENDPOINT] = self.key
        now = self.service.now()
        state = {"config": {**DEFAULT, "enabled": True, "daily_limit": 1}, "tested_at": now.isoformat(),
                 "paused_until": (now - timedelta(seconds=1)).isoformat(),
                 "ledger": [{"id": "synthetic-yesterday", "at": (now - timedelta(days=1)).isoformat(), "status": "interrupted"}]}
        original = json.dumps(state, sort_keys=True)
        with patch.object(self.service, "now", return_value=now):
            result = self.service.status(state)
        self.assertTrue(result["ready"])
        self.assertIsNone(result["unavailable_reason"])
        self.assertIsNone(result["paused_until"])
        self.assertEqual(result["searches_today"], 0)
        self.assertEqual(result["remaining_today"], 1)
        self.assertEqual(json.dumps(state, sort_keys=True), original)

    def test_status_is_read_only_on_existing_and_absent_databases_and_makes_no_requests(self):
        fresh = Path(self.directory) / "not-created" / "workspace.sqlite3"
        absent = WebSearch(fresh, backend.TIMEZONE)
        before = backend.DATABASE.read_bytes()
        with patch.object(httpx, "AsyncClient", side_effect=AssertionError("Status must not send requests")), patch.object(WebSearch, "transaction", side_effect=AssertionError("Status must not initialize or update SQLite")):
            default = absent.status()
            existing = self.service.status()
            endpoint = self.client.get("/api/web-search")
        self.assertFalse(fresh.parent.exists())
        self.assertFalse(default["ready"])
        self.assertFalse(existing["ready"])
        self.assertEqual(endpoint.status_code, 200)
        self.assertFalse(endpoint.json()["ready"])
        self.assertEqual(backend.DATABASE.read_bytes(), before)
        self.assertEqual(self.requests, [])
        self.enable()
        before = backend.DATABASE.read_bytes()
        self.assertTrue(self.service.status()["ready"])
        self.assertEqual(backend.DATABASE.read_bytes(), before)

    def test_credential_read_failure_reports_not_ready_without_search_or_state_mutation(self):
        with self.service.transaction() as state:
            state["config"]["enabled"] = True
            state["tested_at"] = self.service.now().isoformat()
        before = backend.DATABASE.read_bytes()
        with patch.object(credentials, "read", side_effect=ValueError("The server's mounted API key is missing or invalid. Check its secret configuration.")):
            result = self.service.status()
        self.assertFalse(result["ready"])
        self.assertFalse(result["credentials_present"])
        self.assertIsNone(result["tested_at"])
        self.assertEqual(result["unavailable_reason"], "Add and test an Ollama search API key in Settings.")
        self.assertEqual(backend.DATABASE.read_bytes(), before)
        self.assertEqual(self.requests, [])

    def test_automatic_search_two_model_calls_sources_and_usage_persist(self):
        self.enable()
        private_context = "Synthetic private chat detail excluded from the public search query"
        response = self.send(private_context)
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
        response = self.send("Hello locally")
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
                response = self.send(reason, self.ordinary_http)
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
        response = self.send("Reply after a failed retest", self.ordinary_http)
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
                response = self.send("Synthetic invalid tool request")
                self.assertEqual(response.status_code, 409, response.text)
                self.assertEqual(len(self.model_requests()), 1)
                self.assertEqual(self.search_requests(), [])
                self.assertEqual(self.client.get("/api/workspace").json()["messages"], [])
        self.assertEqual(self.service.status()["searches_today"], 1)

    def test_limits_and_missing_tools_capability_block_next_dispatch(self):
        self.enable()
        limits = self.client.get("/api/workspace").json()["limits"]
        self.client.put("/api/limits", headers=self.headers, json={**limits, "max_tokens": 0})
        blocked = self.send("No token allowance")
        self.assertEqual(blocked.status_code, 409)
        self.assertEqual(self.requests, [])
        self.client.put("/api/limits", headers=self.headers, json=limits)

        def unsupported(request):
            self.requests.append(request)
            self.assertEqual(request.url.path, "/api/show")
            return httpx.Response(200, json={"details": {"format": "gguf"}, "capabilities": ["completion"]})

        blocked = self.send("Tool support missing", unsupported)
        self.assertEqual(blocked.status_code, 409, blocked.text)
        self.assertEqual(self.model_requests(), [])
        self.requests.clear()

        def reduced_allowance(request):
            response = self.provider_http(request)
            if request.url.path == "/api/chat" and "tools" in json.loads(request.content):
                with backend.workspace_transaction() as state:
                    state["limits"]["max_tokens"] = 110
            return response

        blocked = self.send("Allowance changes before next call", reduced_allowance)
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
                encoded = json.dumps(fitted, ensure_ascii=False)
                self.assertLessEqual(len(encoded.encode("utf-8")), 256)
                self.assertLessEqual(len(json.dumps(encoded, ensure_ascii=False).encode("utf-8")) - 2, 256)

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
                result = self.send("Use search for this synthetic sanitation check", handler)
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

    def test_rotated_request_credential_is_excluded_from_layered_source_urls_before_archiving(self):
        from backend.context_store import ContextStore, DEFAULT as ARCHIVE_DEFAULT

        self.enable()
        original_key = self.key
        replacement_key = "synthetic-replacement-search-key"
        encoded = "".join(f"%{ord(char):02X}" for char in original_key)
        unsafe_urls = ["https://public.example.org/weather?value=" + encoded.replace("%", "%25"),
                       "https://public.example.org/weather?value=" + encoded.replace("%", "%2525")]
        safe_url = "https://public.example.org/weather?date=2026-10-05"

        def handler(request):
            self.assertEqual(request.headers["authorization"], "Bearer " + original_key)
            self.service.configure(DEFAULT.copy(), replacement_key, False)
            return self.search_response([
                *({"title": "Credential URL", "url": url, "content": "Do not retain this source."}
                  for url in unsafe_urls),
                {"title": "Public forecast", "url": safe_url, "content": "Synthetic dated public excerpt."},
            ])

        with self.mock_http(handler):
            result = self.service.search(self.query)
        self.assertEqual(self.keys[ENDPOINT], replacement_key)
        self.assertEqual([source["url"] for source in result["sources"]], [safe_url])
        self.assertEqual(len(result["completeness"]), 1)
        self.assertEqual(self.service.read_state()["ledger"][-1]["status"], "completed")
        self.assertIsNone(self.service.status()["tested_at"])

        with tempfile.TemporaryDirectory(prefix="maestro-public-archive-test-") as archive:
            store = ContextStore(backend.DATABASE, archive)
            store.configure({**ARCHIVE_DEFAULT, "enabled": True})
            captured = store.capture(self.query, result["at"], result["sources"], 3, secrets=(replacement_key,))
            self.assertEqual(len(captured["sources"]), 1)
            self.assertEqual(captured["sources"][0]["archive_status"], "saved")
            self.assertEqual(store.get_capture(captured["sources"][0]["capture_id"])["url"], safe_url)
            exported = b"".join(path.read_bytes() for path in Path(archive).rglob("*") if path.is_file())
            for leaked in (original_key, *unsafe_urls):
                self.assertNotIn(leaked.encode(), exported)

    def test_encoded_credentials_in_titles_and_bodies_are_excluded_before_chat_and_archive(self):
        from backend.context_store import ContextStore, DEFAULT as ARCHIVE_DEFAULT

        self.enable()
        encoded = "".join(f"%{ord(char):02X}" for char in self.key)
        entities = "".join(f"&#x{ord(char):x};" for char in self.key)
        safe_content = "Normal escaped source text: %41 &amp; &lt;code&gt;. " + "x" * 1300

        def handler(request):
            if request.url.host == "ollama.com":
                self.requests.append(request)
                return self.search_response([
                    {"title": "Encoded credential " + encoded, "url": "https://public.example.org/title", "content": "Unused."},
                    {"title": "Public-looking title", "url": "https://public.example.org/body", "content": "Encoded credential " + entities},
                    {"title": "Safe escaped source", "url": "https://public.example.org/safe", "content": safe_content},
                ])
            if request.url.path == "/api/chat" and "tools" in json.loads(request.content):
                self.requests.append(request)
                return self.model_response({"role": "assistant", "content": "", "tool_calls": [{"function": {
                    "name": "search_context", "arguments": {"query": self.query, "freshness": "stable"}}}]})
            return self.provider_http(request)

        with tempfile.TemporaryDirectory(prefix="maestro-encoded-archive-test-") as archive, \
                patch.dict("os.environ", {"MAESTRO_CONTEXT_ARCHIVE_DIR": archive}):
            store = ContextStore(backend.DATABASE, archive)
            store.configure({**ARCHIVE_DEFAULT, "enabled": True})
            response = self.send("Search for this synthetic credential check", handler)
            self.assertEqual(response.status_code, 200, response.text)
            search = response.json()["messages"][-1]["web_search"]
            self.assertEqual(len(search["sources"]), 1)
            source = search["sources"][0]
            self.assertEqual(source["url"], "https://public.example.org/safe")
            self.assertEqual(source["archive_status"], "saved")
            self.assertEqual(source["completeness"], {"maestro_truncated": True, "full_page": False})
            capture = store.get_capture(source["capture_id"])
            self.assertEqual(capture["content"], safe_content[:1200])
            exported = b"".join(path.read_bytes() for path in Path(archive).rglob("*") if path.is_file())
            final = self.model_requests()[-1].content
            for value in (self.key, encoded, entities):
                self.assertNotIn(value.encode(), exported)
                self.assertNotIn(value.encode(), final)
                self.assertNotIn(value, response.text)
            self.assertEqual(len(self.search_requests()), 1)

    def test_rotated_request_credential_is_excluded_from_encoded_titles_and_content(self):
        self.enable()
        original_key = self.key
        encoded = "".join(f"%{ord(char):02X}" for char in original_key).replace("%", "%25")
        entities = "".join(f"&#{ord(char)};" for char in original_key).replace("&", "%26")

        def handler(request):
            self.assertEqual(request.headers["authorization"], "Bearer " + original_key)
            self.service.configure(DEFAULT.copy(), "synthetic-replacement-search-key", False)
            return self.search_response([
                {"title": encoded, "url": "https://public.example.org/title", "content": "Unused."},
                {"title": "Public title", "url": "https://public.example.org/body", "content": entities},
                {"title": "Safe source", "url": "https://public.example.org/safe", "content": "Normal source."},
            ])

        with self.mock_http(handler):
            result = self.service.search(self.query)
        self.assertEqual([source["url"] for source in result["sources"]], ["https://public.example.org/safe"])
        self.assertEqual(result["completeness"], [{"maestro_truncated": False, "full_page": False}])
        for value in (original_key, encoded, entities):
            self.assertNotIn(value, json.dumps(result))
        self.assertIsNone(self.service.status()["tested_at"])

    def test_known_remote_provider_credential_is_excluded_from_public_result_fields(self):
        self.enable()
        provider_url = "https://synthetic-provider.example.org/v1"
        provider_key = "synthetic-known-remote-provider-key"
        self.keys[provider_url] = provider_key
        with backend.provider().transaction() as state:
            state["config"].update(protocol="responses", base_url=provider_url)
        encoded = "".join(f"%{ord(char):02X}" for char in provider_key)
        entities = "".join(f"&#x{ord(char):x};" for char in provider_key)

        def handler(request):
            return self.search_response([
                {"title": encoded, "url": "https://public.example.org/title", "content": "Unused."},
                {"title": "Public title", "url": "https://public.example.org/body", "content": entities},
                {"title": "Safe source", "url": "https://public.example.org/safe", "content": "Normal source."},
            ])

        with self.mock_http(handler):
            result = self.service.search(self.query)
        self.assertEqual([source["url"] for source in result["sources"]], ["https://public.example.org/safe"])
        for value in (provider_key, encoded, entities):
            self.assertNotIn(value, json.dumps(result))

    def test_only_encoded_credential_titles_and_content_fail_without_inference_or_echo(self):
        self.enable()
        encoded = "".join(f"%{ord(char):02X}" for char in self.key)
        entities = "".join(f"&#x{ord(char):x};" for char in self.key)

        def handler(request):
            self.requests.append(request)
            return self.search_response([
                {"title": encoded, "url": "https://public.example.org/title", "content": "Unused."},
                {"title": "Public title", "url": "https://public.example.org/body", "content": entities},
            ])

        with self.mock_http(handler), self.assertRaisesRegex(ValueError, "no usable public sources") as rejected:
            self.service.search(self.query)
        self.assertEqual(self.model_requests(), [])
        self.assertEqual(len(self.search_requests()), 1)
        for value in (self.key, encoded, entities):
            self.assertNotIn(value, str(rejected.exception))
        self.assertEqual(self.service.read_state()["ledger"][-1]["status"], "failed")

    def test_rotated_request_with_only_encoded_credential_sources_fails_without_exposing_key(self):
        self.enable()
        original_key = self.key
        encoded = "".join(f"%{ord(char):02X}" for char in original_key).replace("%", "%2525")

        def handler(request):
            self.assertEqual(request.headers["authorization"], "Bearer " + original_key)
            self.service.configure(DEFAULT.copy(), "synthetic-replacement-search-key", False)
            return self.search_response([{"title": "Credential URL", "url": "https://public.example.org/?value=" + encoded,
                                          "content": "Unusable synthetic result."}])

        with self.mock_http(handler), self.assertRaisesRegex(ValueError, "no usable public sources") as rejected:
            self.service.search(self.query)
        self.assertNotIn(original_key, str(rejected.exception))
        self.assertNotIn(encoded, str(rejected.exception))
        self.assertEqual(self.service.read_state()["ledger"][-1]["status"], "failed")
        self.assertIsNone(self.service.status()["tested_at"])

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

        result = self.send("Search fails safely", handler)
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

        ordinary = self.send("A normal follow-up", self.ordinary_http)
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

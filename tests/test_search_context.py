"""Dated archive reuse through real chat routes with synthetic HTTP and sources."""
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import hashlib
import json
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient

from backend import app as backend, credentials
from backend.provider import DEFAULT as PROVIDER_DEFAULT, TITLE_INSTRUCTIONS
from backend.context_store import ContextStore
from backend.context_tools import explicit_search_request, requested_relative_dates, dated_search_query
from backend.web_search import DEFAULT as SEARCH_DEFAULT, ENDPOINT, WebSearch


class RelativeSearchDateTests(unittest.TestCase):
    def test_relative_days_handle_local_calendar_boundaries_and_language_variants(self):
        for text in ("tomorrow", "tomorrows weather", "tomorrow's forecast", "tomorrow’s forecast", "imorgon", "i morgon"):
            with self.subTest(text=text):
                self.assertEqual(requested_relative_dates(text, date(2026, 12, 31)), ("2027-01-01",))
        self.assertEqual(requested_relative_dates("Today, tonight, idag, ikväll and tomorrow", date(2024, 2, 28)),
                         ("2024-02-28", "2024-02-29"))
        self.assertEqual(requested_relative_dates("Tomorrow and today", date(2024, 2, 29)),
                         ("2024-02-29", "2024-03-01"))
        self.assertEqual(requested_relative_dates("Explain historical weather", date(2026, 10, 4)), ())

    def test_absolute_query_anchors_preserve_subject_and_reject_overflow_without_truncation(self):
        query = "Stockholm Årsta weather 2026-10-05"
        self.assertEqual(dated_search_query(" " + query + " ", ("2026-10-05",)), query)
        self.assertEqual(dated_search_query(query, ("2026-10-05", "2026-10-06")), query + " 2026-10-06")
        self.assertEqual(len(dated_search_query("q" * 501, ("2026-10-05",))), 512)
        with self.assertRaises(ValueError):
            dated_search_query("q" * 502, ("2026-10-05",))
        with self.assertRaises(ValueError):
            dated_search_query("Stockholm forecast", ("2026-02-30",))


class SearchContextTests(unittest.TestCase):
    def setUp(self):
        self.directory = self.enterContext(tempfile.TemporaryDirectory(prefix="maestro-context-chat-"))
        self.archive = Path(self.directory) / "public-archive"
        self.archive.mkdir()
        private = Path(self.directory) / "private"
        private.mkdir()
        self.enterContext(patch.object(backend, "DATABASE", private / "workspace.sqlite3"))
        self.enterContext(patch.dict("os.environ", {"MAESTRO_CONTEXT_ARCHIVE_DIR": str(self.archive)}))
        self.keys = {ENDPOINT: "synthetic-search-context-key"}
        self.enterContext(patch.object(credentials, "read", side_effect=lambda url: (self.keys.get(url), "synthetic")))
        self.requests = []
        self.query = "Ollama tool documentation"
        self.freshness = "stable"
        self.snippet = "Synthetic public tool interface documentation."
        self.sources = None
        self.url = "https://docs.ollama.com/capabilities/web-search"
        self.fail_final = False
        self.extra_args = {}
        self.client = TestClient(backend.app)
        self.addCleanup(self.client.close)
        self.headers = {"X-Maestro-CSRF": self.client.get("/api/session").json()["csrf"]}
        self.client.get("/api/workspace")
        self.config = {**PROVIDER_DEFAULT, "protocol": "ollama", "base_url": "http://127.0.0.1:11434",
                       "model": "synthetic-tools:latest", "ollama_context_tokens": 8192, "max_output_tokens": 256,
                       "pricing_verified": True}
        self.assertEqual(self.client.put("/api/provider", headers=self.headers,
                         json={"config": self.config, "api_key": "", "persist": False}).status_code, 200)
        self.search = WebSearch(backend.DATABASE, backend.TIMEZONE)
        with self.search.transaction() as state:
            state["config"] = {**SEARCH_DEFAULT, "enabled": True}
            state["tested_at"] = self.search.now().isoformat()
        config = self.client.get("/api/context").json()["config"]
        configured = self.client.put("/api/context", headers=self.headers,
                                     json={**config, "enabled": True, "capture_policy": "approved_sources", "public_sources": ["https://docs.ollama.com/"]})
        self.assertEqual(configured.status_code, 200, configured.text)

    def http(self, request):
        self.requests.append(request)
        if request.url.host == "ollama.com":
            self.assertEqual(str(request.url), ENDPOINT)
            return httpx.Response(200, json={"results": self.sources if self.sources is not None else [{
                "title": "Synthetic source", "url": self.url, "content": self.snippet}]})
        self.assertEqual(request.url.host, "127.0.0.1")
        self.assertNotIn("authorization", request.headers)
        if request.url.path == "/api/show":
            return httpx.Response(200, json={"details": {"format": "gguf"}, "capabilities": ["completion", "tools"]})
        payload = json.loads(request.content)
        if payload["messages"][0]["content"] == TITLE_INSTRUCTIONS:
            message = {"role": "assistant", "content": "Synthetic source conversation"}
            counts = (20, 5)
        elif "tools" in payload:
            self.assertEqual(payload["tools"][0]["function"]["name"], "search_context")
            message = {"role": "assistant", "content": "", "tool_calls": [{"function": {
                "name": "search_context", "arguments": {"query": self.query, "freshness": self.freshness, **self.extra_args}}}]}
            counts = (100, 10)
        else:
            if self.fail_final:
                return httpx.Response(500)
            self.assertNotIn("tools", payload)
            self.assertEqual(payload["options"]["num_predict"], 246)
            message = {"role": "assistant", "content": "Synthetic dated answer [1]"}
            counts = (100, 20)
        return httpx.Response(200, json={"done": True, "message": message,
                                         "prompt_eval_count": counts[0], "eval_count": counts[1]})

    @contextmanager
    def mocked_http(self):
        sync, async_client = httpx.Client, httpx.AsyncClient
        transport = httpx.MockTransport(self.http)
        with patch("backend.provider.httpx.Client", side_effect=lambda **kwargs: sync(transport=transport, **kwargs)), \
                patch("backend.web_search.httpx.AsyncClient", side_effect=lambda **kwargs: async_client(transport=transport, **kwargs)):
            yield

    def send(self, text="Explain the tool interface", mode="prefer_saved"):
        with self.mocked_http():
            return self.client.post("/api/chat", headers=self.headers, json={"text": text, "context_mode": mode})

    def answer(self, response):
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["messages"][-1]

    def age_searches(self):
        with self.search.transaction() as state:
            for entry in state["ledger"]:
                entry["at"] = (self.search.now() - timedelta(seconds=10)).isoformat()

    def hosted_requests(self):
        return [r for r in self.requests if r.url.host == "ollama.com"]

    def seed_answer(self):
        reply = self.send()
        source = self.answer(reply)["web_search"]["sources"][0]
        self.assertEqual(source["archive_status"], "saved")
        self.assertEqual(len(self.hosted_requests()), 1)
        self.age_searches()
        self.requests.clear()
        return source

    def test_exact_approved_parameterized_source_is_saved_then_reused_without_hosted_search(self):
        self.url = "https://docs.ollama.com/public/weather?date=2026-10-05"
        self.query = "Stockholm forecast 2026-10-05"
        self.snippet = "Stockholm forecast for 2026-10-05: synthetic dated public evidence."
        self.freshness = "current"
        config = self.client.get("/api/context").json()["config"]
        configured = self.client.put("/api/context", headers=self.headers,
                                     json={**config, "public_sources": [self.url]})
        self.assertEqual(configured.status_code, 200, configured.text)
        first = self.send("Use a fresh search for the Stockholm forecast on 2026-10-05", mode="refresh")
        original = self.answer(first)["web_search"]["sources"][0]
        self.assertEqual(original["archive_status"], "saved")
        self.assertEqual(original["url"], self.url)
        self.assertEqual(len(self.hosted_requests()), 1)
        self.requests.clear()
        self.keys.clear()
        reused = self.send("Use the saved Stockholm forecast on 2026-10-05", mode="saved_only")
        evidence = self.answer(reused)["web_search"]
        self.assertTrue(evidence["from_cache"])
        self.assertEqual(evidence["sources"][0]["capture_id"], original["capture_id"])
        self.assertEqual(evidence["sources"][0]["retrieved_at"], original["retrieved_at"])
        self.assertEqual(self.hosted_requests(), [])
        payloads = [json.loads(request.content) for request in self.requests if request.url.path == "/api/chat"]
        self.assertEqual(len(payloads), 2)
        self.assertNotIn("tools", payloads[-1])
        self.assertEqual(evidence["forecast_date_check"]["supported_source_count"], 1)
        self.assertLessEqual(len(json.dumps(payloads[-1]["messages"], ensure_ascii=False).encode("utf-8"))
                             + 2048 + payloads[-1]["options"]["num_predict"], self.config["ollama_context_tokens"])
        viewed = self.client.get("/api/context/sources/" + original["capture_id"],
                                params={"expected_hash": original["content_hash"],
                                        "expected_manifest_hash": original["manifest_hash"]})
        self.assertEqual(viewed.status_code, 200)
        self.assertEqual(viewed.json()["content"], self.snippet)

    def test_repeat_uses_original_source_without_network_or_search_allowance(self):
        source = self.seed_answer()
        before = self.search.status()["searches_today"]
        reply = self.send()
        evidence = self.answer(reply)["web_search"]
        self.assertTrue(evidence["from_cache"])
        self.assertEqual(evidence["sources"][0]["retrieved_at"], source["retrieved_at"])
        self.assertEqual(evidence["sources"][0]["content_hash"], source["content_hash"])
        self.assertEqual(self.hosted_requests(), [])
        self.assertEqual(self.search.status()["searches_today"], before)
        fitted = evidence["evidence"][0]
        self.assertEqual(fitted["sha256"], hashlib.sha256(fitted["content"].encode()).hexdigest())
        self.assertEqual(reply.json()["usage"]["calls"], 5)

    def test_explicit_search_action_detection_does_not_promote_recency_or_algorithm_discussion(self):
        for text in ("I want you to try the search function. Give me information on tomorrows weather in Stockholm Årsta.",
                     "Please search for public documentation", "Use web search to find sources", "Look up the forecast",
                     "Prova sökfunktionen", "Sök på webben efter väder"):
            with self.subTest(text=text):
                self.assertTrue(explicit_search_request(text))
        for text in ("Explain the search function", "Use search algorithms to solve the puzzle", "Explain current settings",
                     "Release the GPU", "Search memory is a data structure", "Tell me what web search means",
                     "Do not use web search for this answer", "Don't search for this", "Never look up private information",
                     "How do I use the search function?", "Can you explain how to use web search?", "How to look up sources?"):
            with self.subTest(text=text):
                self.assertFalse(explicit_search_request(text))

    def test_explicit_weather_search_unavailable_fails_before_recall_and_inference(self):
        status = self.client.get("/api/context").json()
        self.client.put("/api/context", headers=self.headers, json={**status["config"], "enabled": False})
        self.keys.clear()
        with self.search.transaction() as state:
            state["config"]["enabled"] = False
        text = "I want you to try the search function. Give me information on tomorrows weather in Stockholm Årsta."
        with patch("backend.memory.MemoryStore.recall", side_effect=AssertionError("Unavailable search fell back to memory")):
            rejected = self.send(text)
        self.assertEqual(rejected.status_code, 409, rejected.text)
        self.assertIn("Add and test", rejected.json()["detail"])
        self.assertEqual(self.requests, [])
        workspace = self.client.get("/api/workspace").json()
        self.assertEqual(workspace["messages"], [])
        self.assertEqual(workspace["usage"]["calls"], 0)

    def test_explicit_search_cannot_save_an_unsourced_direct_model_reply(self):
        original = self.http

        def no_tool(request):
            if request.url.path == "/api/chat" and "tools" in json.loads(request.content):
                payload = json.loads(request.content)
                self.assertIn("You must request the available source tool", payload["messages"][0]["content"])
                self.assertIn("Today's local date is", payload["messages"][0]["content"])
                self.requests.append(request)
                return httpx.Response(200, json={"done": True, "message": {"role": "assistant", "content": "I cannot access forecasts."},
                                                 "prompt_eval_count": 100, "eval_count": 10})
            return original(request)

        self.http = no_tool
        rejected = self.send("Use search to check tomorrow's weather in Stockholm")
        self.assertEqual(rejected.status_code, 409, rejected.text)
        self.assertIn("did not request evidence", rejected.json()["detail"])
        workspace = self.client.get("/api/workspace").json()
        self.assertEqual(workspace["messages"], [])
        self.assertEqual(workspace["usage"]["input_tokens"], 100)
        self.assertEqual(workspace["usage"]["output_tokens"], 10)
        self.assertEqual(self.hosted_requests(), [])

    def test_relative_weather_search_anchors_actual_hosted_query_to_local_calendar_date(self):
        self.query = "tomorrow's weather in Stockholm Årsta"
        self.freshness = "stable"  # The current user request overrides the model's classification.
        self.snippet = "Stockholm forecast for 2026-10-05: cloudy, maximum 15 C and minimum 11 C."
        # The local day is already October 4 while UTC is still October 3.
        local_now = datetime(2026, 10, 3, 22, 30, tzinfo=timezone.utc).astimezone(backend.TIMEZONE)
        with patch("backend.provider.datetime", wraps=datetime) as clock:
            clock.now.return_value = local_now
            reply = self.send("I want you to try the search function. Give me information on tomorrows weather in Stockholm Årsta.")
        self.assertEqual(reply.status_code, 200, reply.text)
        self.assertEqual(len(self.hosted_requests()), 1)
        dispatched_query = json.loads(self.hosted_requests()[0].content)["query"]
        self.assertIn("2026-10-05", dispatched_query)
        self.assertNotIn("2026-10-04", dispatched_query)
        self.assertIn("Stockholm Årsta", dispatched_query)
        evidence = reply.json()["messages"][-1]["web_search"]
        self.assertEqual(evidence["query"], dispatched_query)
        self.assertFalse(evidence["from_cache"])
        chat_payloads = [json.loads(request.content) for request in self.requests if request.url.path == "/api/chat"]
        first, final = [payload for payload in chat_payloads if payload["messages"][0]["content"] != TITLE_INSTRUCTIONS]
        self.assertIn("Today's local date is 2026-10-04", first["messages"][0]["content"])
        self.assertIn("The requested calendar dates are 2026-10-05", final["messages"][0]["content"])
        self.assertIn("the excerpt itself must explicitly support that day", final["messages"][0]["content"])
        self.assertNotIn("tools", final)
        self.assertEqual(reply.json()["usage"]["calls"], 3)  # Two answer stages plus the existing title call.

    def test_relative_date_anchor_and_grounding_also_apply_without_enabled_archive(self):
        config = self.client.get("/api/context").json()["config"]
        self.assertEqual(self.client.put("/api/context", headers=self.headers,
                                        json={**config, "enabled": False}).status_code, 200)
        self.query = "Stockholm weather tomorrow"
        self.snippet = "Stockholm forecast 2026-10-05: cloudy, 15 C."
        original = self.http

        def legacy_search_tool(request):
            if request.url.path == "/api/chat":
                payload = json.loads(request.content)
                if "tools" in payload:
                    self.requests.append(request)
                    self.assertEqual(payload["tools"][0]["function"]["name"], "web_search")
                    return httpx.Response(200, json={"done": True, "message": {"role": "assistant", "content": "",
                        "tool_calls": [{"function": {"name": "web_search", "arguments": {"query": self.query}}}]},
                        "prompt_eval_count": 100, "eval_count": 10})
            return original(request)

        self.http = legacy_search_tool
        with patch("backend.provider.datetime", wraps=datetime) as clock:
            clock.now.return_value = datetime(2026, 10, 4, 10, tzinfo=backend.TIMEZONE)
            reply = self.send("Use web search to check tomorrow's weather in Stockholm")
        self.assertEqual(reply.status_code, 200, reply.text)
        self.assertEqual(len(self.hosted_requests()), 1)
        dispatched_query = json.loads(self.hosted_requests()[0].content)["query"]
        self.assertIn("2026-10-05", dispatched_query)
        self.assertEqual(reply.json()["messages"][-1]["web_search"]["query"], dispatched_query)
        final = next(json.loads(request.content) for request in self.requests if request.url.path == "/api/chat"
                     and "tools" not in json.loads(request.content)
                     and json.loads(request.content)["messages"][0]["content"] != TITLE_INSTRUCTIONS)
        self.assertIn("The requested calendar dates are 2026-10-05", final["messages"][0]["content"])
        self.assertIn("fresh search retrieval timestamp does not establish the date", final["messages"][0]["content"])

    def test_saved_only_relative_date_request_preserves_original_query_and_evidence(self):
        self.query = "Stockholm forecast"
        self.snippet = "Stockholm forecast for 2026-10-05: cloudy, 15 C."
        source = self.seed_answer()
        self.keys.clear()
        self.freshness = "current"
        with patch("backend.provider.datetime", wraps=datetime) as clock:
            clock.now.return_value = datetime(2026, 10, 4, 10, tzinfo=backend.TIMEZONE)
            reply = self.send("Use search to check tomorrow's weather in Stockholm", "saved_only")
        self.assertEqual(reply.status_code, 200, reply.text)
        self.assertEqual(self.hosted_requests(), [])
        evidence = reply.json()["messages"][-1]["web_search"]
        self.assertEqual(evidence["query"], self.query)
        self.assertTrue(evidence["from_cache"])
        self.assertEqual(evidence["sources"][0]["capture_id"], source["capture_id"])
        self.assertEqual(evidence["sources"][0]["retrieved_at"], source["retrieved_at"])
        final = next(json.loads(request.content) for request in self.requests if request.url.path == "/api/chat"
                     and "tools" not in json.loads(request.content))
        self.assertIn("The requested calendar dates are 2026-10-05", final["messages"][0]["content"])

    def test_undated_search_excerpts_are_saved_but_cannot_reach_final_forecast_generation(self):
        self.query = "Stockholm weather tomorrow"
        self.snippet = "Stockholm weather: sunny, high 29 C and low 21 C."
        original = self.http

        def grounded_final(request):
            if request.url.path == "/api/chat":
                payload = json.loads(request.content)
                if "tools" not in payload and payload["messages"][0]["content"] != TITLE_INSTRUCTIONS:
                    self.fail("Undated excerpts reached a final model call")
            return original(request)

        self.http = grounded_final
        with patch("backend.provider.datetime", wraps=datetime) as clock:
            clock.now.return_value = datetime(2026, 10, 4, 10, tzinfo=backend.TIMEZONE)
            reply = self.send("Use search to check tomorrow's weather in Stockholm Årsta")
        answer = self.answer(reply)
        self.assertIn("cannot verify", answer["text"])
        self.assertNotIn("29", answer["text"])
        self.assertEqual(answer["reply_origin"], "source_validation")
        self.assertEqual(answer["web_search"]["forecast_date_check"], {"requested_dates": ["2026-10-05"],
                         "supported_source_count": 0, "excluded_source_count": 1})
        self.assertEqual(answer["web_search"]["evidence"], [])
        self.assertEqual(answer["web_search"]["sources"][0]["archive_status"], "saved")
        self.assertEqual(self.client.get("/api/context/sources/" + answer["web_search"]["sources"][0]["capture_id"]).json()["content"], self.snippet)
        self.assertEqual(len(self.hosted_requests()), 1)
        self.assertEqual(reply.json()["usage"]["calls"], 2)  # Planning plus the existing first-chat title.
        self.assertEqual(reply.json()["usage"]["input_tokens"], 120)
        self.assertEqual(reply.json()["usage"]["output_tokens"], 15)

    def test_dated_weather_requires_evidence_even_without_explicit_search_wording(self):
        original = self.http

        def unsupported_direct_answer(request):
            if request.url.path == "/api/chat" and "tools" in json.loads(request.content):
                self.requests.append(request)
                return httpx.Response(200, json={"done": True, "message": {"role": "assistant", "content": "15 C, rain 1.5 mm [1]"},
                                                 "prompt_eval_count": 100, "eval_count": 10})
            return original(request)

        self.http = unsupported_direct_answer
        reply = self.send("What is tomorrow's weather in Stockholm?")
        self.assertEqual(reply.status_code, 409, reply.text)
        self.assertIn("did not request evidence", reply.json()["detail"])
        self.assertEqual(self.client.get("/api/workspace").json()["messages"], [])
        self.assertEqual(self.hosted_requests(), [])
        usage = self.client.get("/api/workspace").json()["usage"]
        self.assertEqual((usage["calls"], usage["input_tokens"], usage["output_tokens"]), (1, 100, 10))
        self.keys.clear()
        self.requests.clear()
        blocked = self.send("What is tomorrow's weather in Stockholm?")
        self.assertEqual(blocked.status_code, 409, blocked.text)
        self.assertIn("unavailable", blocked.json()["detail"])
        self.assertEqual(self.requests, [])

    def test_nonweather_invalid_iso_example_remains_an_ordinary_question(self):
        reply = self.send("Explain why 2026-99-99 is an invalid ISO date example")
        self.assertEqual(reply.status_code, 200, reply.text)
        self.assertNotIn("forecast_date_check", reply.json()["messages"][-1]["web_search"])
        self.assertEqual(len(self.hosted_requests()), 1)

    def test_all_stale_forecasts_are_captured_before_swedish_server_refusal_without_final_model_call(self):
        self.query = "Stockholm weather 2026-10-05"
        self.freshness = "current"
        self.sources = [
            {"title": "Stale forecast", "url": "https://docs.ollama.com/weather/may", "content": "Forecast 2026-05-02: max 23 C, rain 3.5 mm."},
            {"title": "October 5", "url": "https://docs.ollama.com/weather/october", "content": "October 5: max 15 C, rain 1.5 mm."},
            {"title": "Forecast 2026-10-05", "url": "https://docs.ollama.com/weather/current?date=2026-10-05",
             "content": "Tomorrow: max 29 C, min 21 C."},
        ]
        config = self.client.get("/api/context").json()["config"]
        self.assertEqual(self.client.put("/api/context", headers=self.headers,
            json={**config, "capture_policy": "all_public", "public_sources": []}).status_code, 200)
        self.assertEqual(self.client.post("/api/chats", headers=self.headers).status_code, 200)
        with backend.workspace_transaction() as state:
            state["chats"][0]["title_source"] = "manual"
        reply = self.send("Sammanfatta vädret i Stockholm 2026-10-05", mode="refresh")
        answer = self.answer(reply)
        self.assertEqual(answer["reply_origin"], "source_validation")
        self.assertIn("kan inte verifiera", answer["text"])
        self.assertNotIn("23", answer["text"])
        self.assertNotIn("29", answer["text"])
        self.assertEqual(answer["web_search"]["evidence"], [])
        self.assertEqual(answer["web_search"]["forecast_date_check"]["excluded_source_count"], 3)
        self.assertEqual(len(answer["web_search"]["sources"]), 3)
        self.assertTrue(all(source["archive_status"] == "saved" for source in answer["web_search"]["sources"]))
        for source, original in zip(answer["web_search"]["sources"], self.sources):
            viewed = self.client.get("/api/context/sources/" + source["capture_id"])
            self.assertEqual(viewed.json()["content"], original["content"])
        status = self.client.get("/api/context").json()
        self.assertEqual(status["indexed_count"], 3)
        self.assertEqual(status["last_capture"]["sources_received"], 3)
        self.assertEqual(status["last_capture"]["sources_saved"], 3)
        self.assertEqual(len(self.hosted_requests()), 1)
        self.assertEqual(reply.json()["usage"]["calls"], 1)
        self.assertEqual((reply.json()["usage"]["input_tokens"], reply.json()["usage"]["output_tokens"]), (100, 10))

    def test_partial_dated_forecast_gate_captures_all_sources_but_synthesizes_only_supported_body(self):
        self.query = "Stockholm forecast 2026-10-05"
        self.freshness = "current"
        self.sources = [
            {"title": "May", "url": "https://docs.ollama.com/weather/may", "content": "Forecast 2026-05-02: rain 3.5 mm."},
            {"title": "Supported", "url": "https://docs.ollama.com/weather/october", "content": "Forecast 2026-10-05: rain 1.5 mm."},
            {"title": "Undated", "url": "https://docs.ollama.com/weather/relative", "content": "Tomorrow: rain 5.0 mm."},
        ]
        reply = self.send("Use search for Stockholm weather on 2026-10-05", mode="refresh")
        answer = self.answer(reply)
        self.assertNotIn("reply_origin", answer)
        evidence = answer["web_search"]
        self.assertEqual(evidence["forecast_date_check"], {"requested_dates": ["2026-10-05"], "supported_source_count": 1, "excluded_source_count": 2})
        self.assertEqual([source["title"] for source in evidence["sources"]], ["Supported"])
        final = next(json.loads(request.content) for request in self.requests if request.url.path == "/api/chat"
                     and json.loads(request.content)["messages"][-1]["role"] == "tool")
        fitted = json.loads(final["messages"][-1]["content"])
        self.assertEqual(len(fitted), 1)
        self.assertEqual(fitted[0]["source"], 1)
        self.assertEqual(fitted[0]["content"], self.sources[1]["content"])
        self.assertEqual(self.client.get("/api/context").json()["last_capture"]["sources_saved"], 3)
        self.assertEqual(len(self.hosted_requests()), 1)

    def test_date_lost_from_exact_fitted_prefix_causes_server_refusal_after_full_capture(self):
        from backend.web_search import fit_sources

        self.query = "Stockholm forecast 2026-10-05"
        self.freshness = "current"
        self.snippet = "Synthetic context. " * 58 + " Forecast 2026-10-05: rain 1.5 mm."
        with patch("backend.provider.fit_sources", side_effect=lambda sources, bound: fit_sources(sources, min(bound, 1200))):
            reply = self.send("Use search for Stockholm weather on 2026-10-05", mode="refresh")
        answer = self.answer(reply)
        self.assertEqual(answer["reply_origin"], "source_validation")
        self.assertEqual(answer["web_search"]["forecast_date_check"]["supported_source_count"], 0)
        self.assertEqual(answer["web_search"]["evidence"], [])
        source = answer["web_search"]["sources"][0]
        self.assertEqual(self.client.get("/api/context/sources/" + source["capture_id"]).json()["content"], self.snippet)
        self.assertFalse(any(json.loads(request.content)["messages"][-1]["role"] == "tool"
                             for request in self.requests if request.url.path == "/api/chat"))
        self.assertEqual(len(self.hosted_requests()), 1)

    def test_cached_wrong_day_forecast_is_preserved_but_refused_without_hosted_search(self):
        self.query = "Stockholm forecast 2026-10-05"
        saved = backend.context_store().capture(self.query, backend.context_store().now().isoformat(), [{
            "title": "Wrong day", "url": self.url, "content": "Forecast 2026-05-02: max 23 C."}], 3)["sources"][0]
        self.keys.clear()
        reply = self.send("Use saved sources for Stockholm weather on 2026-10-05", mode="saved_only")
        answer = self.answer(reply)
        self.assertEqual(answer["reply_origin"], "source_validation")
        self.assertTrue(answer["web_search"]["from_cache"])
        self.assertEqual(answer["web_search"]["sources"][0]["capture_id"], saved["capture_id"])
        self.assertEqual(answer["web_search"]["evidence"], [])
        self.assertEqual(self.hosted_requests(), [])

    def test_date_anchoring_overflow_retains_first_usage_without_hosted_dispatch(self):
        self.query = "q" * 502
        self.freshness = "current"
        with patch("backend.provider.datetime", wraps=datetime) as clock:
            clock.now.return_value = datetime(2026, 10, 4, 10, tzinfo=backend.TIMEZONE)
            rejected = self.send("Use search to check tomorrow's weather in Stockholm")
            workspace = self.client.get("/api/workspace").json()
        self.assertEqual(rejected.status_code, 409, rejected.text)
        self.assertIn("after adding its requested dates", rejected.json()["detail"])
        self.assertEqual(self.hosted_requests(), [])
        self.assertEqual(workspace["messages"], [])
        self.assertEqual(workspace["usage"]["calls"], 1)
        self.assertEqual(workspace["usage"]["input_tokens"], 100)
        self.assertEqual(workspace["usage"]["output_tokens"], 10)

    def test_explicit_current_search_cannot_export_memory_derived_chat(self):
        self.seed_answer()
        with backend.workspace_transaction() as state:
            state["chats"][0]["messages"][-1]["memory_ids"] = ["synthetic-private-memory"]
        rejected = self.send("Use web search to check tomorrow's weather in Stockholm")
        self.assertEqual(rejected.status_code, 409, rejected.text)
        self.assertIn("Start a new chat", rejected.json()["detail"])
        self.assertEqual(self.requests, [])

    def test_explicit_stable_search_and_saved_only_can_use_local_evidence_without_hosted_key(self):
        source = self.seed_answer()
        self.keys.clear()
        for mode in ("prefer_saved", "saved_only"):
            with self.subTest(mode=mode):
                evidence = self.send("Use search to explain the tool interface", mode)
                self.assertEqual(evidence.status_code, 200, evidence.text)
                self.assertTrue(evidence.json()["messages"][-1]["web_search"]["from_cache"])
                self.assertEqual(evidence.json()["messages"][-1]["web_search"]["sources"][0]["capture_id"], source["capture_id"])
                self.assertEqual(self.hosted_requests(), [])

    def test_local_cache_remains_available_without_hosted_readiness(self):
        self.seed_answer()
        for reason in ("key", "quota", "cooldown", "test"):
            with self.subTest(reason=reason):
                self.keys[ENDPOINT] = "synthetic-search-context-key"
                with self.search.transaction() as state:
                    state["tested_at"] = self.search.now().isoformat()
                    state["config"]["daily_limit"] = 20
                    state["paused_until"] = None
                    if reason == "quota":
                        state["config"]["daily_limit"] = 0
                    elif reason == "cooldown":
                        state["paused_until"] = (self.search.now() + timedelta(seconds=60)).isoformat()
                    elif reason == "test":
                        state["tested_at"] = None
                if reason == "key":
                    self.keys.clear()
                reply = self.send()
                self.assertEqual(reply.status_code, 200, reply.text)
                self.assertTrue(reply.json()["messages"][-1]["web_search"]["from_cache"])
                self.assertEqual(self.hosted_requests(), [])

    def test_related_keywords_reuse_saved_evidence_without_hosted_readiness(self):
        source = self.seed_answer()
        self.query = "interface tool"
        self.keys.clear()
        with self.search.transaction() as state:
            state["config"]["daily_limit"] = 0
        before = self.client.get("/api/workspace").json()["usage"]
        reply = self.send()
        evidence = self.answer(reply)["web_search"]
        self.assertEqual(evidence["retrieval"], "keyword")
        self.assertTrue(evidence["from_cache"])
        self.assertEqual(evidence["sources"][0]["capture_id"], source["capture_id"])
        self.assertEqual(evidence["sources"][0]["manifest_hash"], source["manifest_hash"])
        self.assertEqual(evidence["at"], source["retrieved_at"])
        self.assertEqual(self.hosted_requests(), [])
        self.assertEqual(reply.json()["usage"]["input_tokens"] - before["input_tokens"], 200)
        exported = b"".join(p.read_bytes() for p in self.archive.rglob("*") if p.is_file())
        self.assertNotIn(self.query.encode(), exported)

    def test_manual_keyword_search_filters_are_read_only_and_session_protected(self):
        source = self.seed_answer()
        query = {"query": "tool interface", "domain": "DOCS.OLLAMA.COM",
                 "retrieved_from": source["retrieved_at"][:10], "retrieved_to": source["retrieved_at"][:10]}
        before = {str(p): p.read_bytes() for p in Path(self.directory).rglob("*") if p.is_file()}
        searched = self.client.post("/api/context/search", headers=self.headers, json=query)
        self.assertEqual(searched.status_code, 200, searched.text)
        evidence = searched.json()
        self.assertEqual(evidence["retrieval"], "keyword")
        self.assertEqual(evidence["sources"][0]["capture_id"], source["capture_id"])
        self.assertEqual(evidence["sources"][0]["retrieved_at"], source["retrieved_at"])
        after = {str(p): p.read_bytes() for p in Path(self.directory).rglob("*") if p.is_file()}
        self.assertEqual(before, after)
        self.assertEqual(self.requests, [])
        empty = self.client.post("/api/context/search", headers=self.headers,
                                 json={**query, "domain": "other.example.org"})
        self.assertEqual(empty.status_code, 200, empty.text)
        self.assertEqual(empty.json()["sources"], [])
        self.assertIsNone(empty.json()["at"])
        self.assertEqual(self.client.post("/api/context/search", json=query).status_code, 403)
        unauthenticated = TestClient(backend.app)
        self.addCleanup(unauthenticated.close)
        self.assertEqual(unauthenticated.post("/api/context/search", json=query).status_code, 401)
        for change in ({"domain": "https://docs.ollama.com/"}, {"retrieved_to": "2026-02-30"},
                       {"retrieved_from": "2026-10-05", "retrieved_to": "2026-10-01"},
                       {"limit": True}, {"allow_stale": "true"}, {"query": " ".join(str(i) for i in range(17))},
                       {"query": "***"}, {"unknown": True}):
            with self.subTest(change=change):
                rejected = self.client.post("/api/context/search", headers=self.headers, json={**query, **change})
                self.assertEqual(rejected.status_code, 422, rejected.text)
        configured = self.client.get("/api/context").json()["config"]
        self.client.put("/api/context", headers=self.headers, json={**configured, "enabled": False})
        self.assertEqual(self.client.post("/api/context/search", headers=self.headers, json=query).status_code, 409)

    def test_saved_tool_filters_never_use_unfiltered_exact_hit_or_online_fallback(self):
        source = self.seed_answer()
        self.query = "tool interface"
        self.extra_args = {"domain": "DOCS.OLLAMA.COM", "retrieved_from": source["retrieved_at"][:10]}
        hit = self.send(mode="saved_only")
        evidence = self.answer(hit)["web_search"]
        self.assertEqual(evidence["retrieval"], "keyword")
        self.assertEqual(evidence["filters"]["domain"], "docs.ollama.com")
        self.query = "Ollama tool documentation"  # An exact hit exists, but is outside the filter.
        self.extra_args = {"domain": "other.example.org"}
        for mode in ("prefer_saved", "saved_only", "refresh"):
            with self.subTest(mode=mode):
                blocked = self.send(mode=mode)
                self.assertEqual(blocked.status_code, 409, blocked.text)
                self.assertEqual(self.hosted_requests(), [])
        self.freshness = "current"
        self.extra_args = {"domain": "docs.ollama.com"}
        blocked = self.send()
        self.assertEqual(blocked.status_code, 409, blocked.text)
        self.assertEqual(self.hosted_requests(), [])

    def test_empty_optional_tool_filters_are_omitted_before_fresh_search(self):
        self.query = "Uppsala forecast"
        self.freshness = "current"
        self.extra_args = {"domain": "", "retrieved_from": "", "retrieved_to": ""}
        reply = self.send("Summarize the latest Uppsala forecast", mode="refresh")
        evidence = self.answer(reply)["web_search"]
        self.assertFalse(evidence["from_cache"])
        self.assertNotIn("filters", evidence)
        self.assertEqual(len(self.hosted_requests()), 1)

    def test_blank_optional_tool_filters_preserve_saved_lookup_without_a_search_key(self):
        source = self.seed_answer()
        self.keys.clear()
        self.extra_args = {"domain": "  ", "retrieved_from": "", "retrieved_to": "\t"}
        reply = self.send(mode="saved_only")
        evidence = self.answer(reply)["web_search"]
        self.assertEqual(evidence["retrieval"], "exact_query")
        self.assertEqual(evidence["sources"][0]["capture_id"], source["capture_id"])
        self.assertNotIn("filters", evidence)
        self.assertEqual(self.hosted_requests(), [])

    def test_current_uncertain_and_refresh_requests_do_not_consult_keyword_index(self):
        self.seed_answer()
        self.query = "tool interface"
        with patch.object(backend.context_store(), "search", side_effect=AssertionError("Fresh request consulted saved keywords")):
            for freshness, mode in (("current", "prefer_saved"), ("unspecified", "prefer_saved"), ("stable", "refresh")):
                with self.subTest(freshness=freshness, mode=mode):
                    self.freshness = freshness
                    self.requests.clear()
                    reply = self.send("Use search to explain the tool interface", mode=mode)
                    self.assertEqual(reply.status_code, 200, reply.text)
                    self.assertFalse(reply.json()["messages"][-1]["web_search"]["from_cache"])
                    self.assertEqual(len(self.hosted_requests()), 1)
                    self.age_searches()

    def test_stale_keyword_evidence_requires_saved_only_and_keeps_original_dates(self):
        self.seed_answer()
        store = backend.context_store()
        old_at = (store.now() - timedelta(hours=25)).isoformat()
        captured = store.capture("Different historical acquisition query", old_at,
                                [{"title": "Historical compiler", "url": self.url + "/older",
                                  "content": "Archived compiler evidence."}], 3)["sources"][0]
        self.query = "archived compiler"
        self.keys.clear()
        recent_only = self.send()
        self.assertEqual(recent_only.status_code, 409, recent_only.text)
        historical = self.send(mode="saved_only")
        evidence = self.answer(historical)["web_search"]
        self.assertEqual(evidence["retrieval"], "keyword")
        self.assertTrue(evidence["stale"])
        self.assertTrue(evidence["sources"][0]["stale"])
        self.assertEqual(evidence["sources"][0]["capture_id"], captured["capture_id"])
        self.assertEqual(evidence["sources"][0]["retrieved_at"], old_at)
        self.assertEqual(evidence["at"], old_at)
        self.assertEqual(self.hosted_requests(), [])

    def test_memory_derived_chat_can_reuse_related_keywords_without_export(self):
        source = self.seed_answer()
        with backend.workspace_transaction() as state:
            state["chats"][0]["messages"][-1]["memory_ids"] = ["synthetic-private-memory"]
        self.query = "tool interface"
        reply = self.send()
        evidence = self.answer(reply)["web_search"]
        self.assertEqual(evidence["retrieval"], "keyword")
        self.assertEqual(evidence["sources"][0]["capture_id"], source["capture_id"])
        self.assertEqual(self.hosted_requests(), [])

    def test_keyword_evidence_deleted_during_completion_cannot_commit_an_answer(self):
        source = self.seed_answer()
        self.query = "tool interface"
        before = self.client.get("/api/workspace").json()["usage"]
        original = self.http

        def delete_before_completion(request):
            if request.url.path == "/api/chat":
                payload = json.loads(request.content)
                if "tools" not in payload and payload["messages"][-1]["role"] == "tool":
                    self.assertEqual(self.client.delete("/api/context/sources/" + source["capture_id"],
                                                       headers=self.headers).status_code, 200)
            return original(request)

        self.http = delete_before_completion
        reply = self.send(mode="saved_only")
        self.assertEqual(reply.status_code, 409, reply.text)
        workspace = self.client.get("/api/workspace").json()
        self.assertEqual(len(workspace["messages"]), 2)
        self.assertEqual(workspace["usage"]["input_tokens"] - before["input_tokens"], 200)
        self.assertEqual(workspace["usage"]["output_tokens"] - before["output_tokens"], 30)
        self.assertEqual(self.hosted_requests(), [])

    def test_corrupt_keyword_index_is_explicit_locally_and_can_use_ready_fresh_search(self):
        self.seed_answer()
        self.query = "tool interface"
        store = backend.context_store()
        store.index.write_bytes(b"Synthetic corrupted index")
        manual = self.client.post("/api/context/search", headers=self.headers, json={"query": self.query})
        self.assertEqual(manual.status_code, 409, manual.text)
        self.assertIn("Rebuild", manual.json()["detail"])
        self.keys.clear()
        local = self.send(mode="saved_only")
        self.assertEqual(local.status_code, 409, local.text)
        self.assertEqual(self.hosted_requests(), [])
        self.keys[ENDPOINT] = "synthetic-search-context-key"
        fresh = self.send("Use search to explain the tool interface")
        evidence = self.answer(fresh)["web_search"]
        self.assertFalse(evidence["from_cache"])
        self.assertIn("archive_warning", evidence)
        self.assertEqual(len(self.hosted_requests()), 1)

    def test_bounded_keyword_results_are_disclosed_and_limited_misses_are_explicit(self):
        self.seed_answer()
        self.query = "tool interface"
        store = backend.context_store()
        partial = {**store.search(self.query, 3), "search_limited": True}
        with patch.object(store, "search", return_value=partial):
            answer = self.send(mode="saved_only")
        evidence = self.answer(answer)["web_search"]
        self.assertTrue(evidence["search_limited"])
        self.assertEqual(evidence["retrieval"], "keyword")
        self.assertEqual(self.hosted_requests(), [])
        empty = {**partial, "sources": [], "at": None}
        with patch.object(store, "search", return_value=empty):
            blocked = self.send(mode="saved_only")
            self.assertEqual(blocked.status_code, 409, blocked.text)
            self.assertIn("Narrow", blocked.json()["detail"])
            self.assertEqual(self.hosted_requests(), [])
            fresh = self.send("Use search to explain the tool interface")
        evidence = self.answer(fresh)["web_search"]
        self.assertFalse(evidence["from_cache"])
        self.assertTrue(evidence["search_limited"])
        self.assertIn("work limit", evidence["archive_warning"])
        self.assertEqual(len(self.hosted_requests()), 1)

    def test_explicit_refresh_and_current_text_bypass_saved_results(self):
        self.seed_answer()
        for text, mode in (("Explain the tool interface", "refresh"), ("Explain the latest interface", "prefer_saved")):
            with self.subTest(mode=mode, text=text):
                self.requests.clear()
                reply = self.send(text, mode)
                self.assertEqual(reply.status_code, 200, reply.text)
                self.assertFalse(reply.json()["messages"][-1]["web_search"]["from_cache"])
                self.assertEqual(len(self.hosted_requests()), 1)
                self.age_searches()

    def test_saved_only_never_sends_a_missing_query_online(self):
        self.seed_answer()
        self.query = "An unseen public documentation query"
        reply = self.send(mode="saved_only")
        self.assertEqual(reply.status_code, 409, reply.text)
        self.assertIn("No suitable saved evidence", reply.json()["detail"])
        self.assertEqual(self.hosted_requests(), [])

    def test_refresh_without_key_is_explicitly_unavailable(self):
        self.seed_answer()
        self.keys.clear()
        reply = self.send(mode="refresh")
        self.assertEqual(reply.status_code, 409, reply.text)
        self.assertIn("Fresh web search is unavailable", reply.json()["detail"])
        self.assertEqual(self.requests, [])

    def test_test_connection_always_bypasses_archive(self):
        self.seed_answer()
        with self.mocked_http():
            tested = self.client.post("/api/web-search/test", headers=self.headers)
        self.assertEqual(tested.status_code, 200, tested.text)
        self.assertEqual(len(self.hosted_requests()), 1)

    def test_answer_failure_keeps_capture_and_known_model_usage(self):
        self.fail_final = True
        reply = self.send()
        self.assertEqual(reply.status_code, 409, reply.text)
        status = self.client.get("/api/context").json()
        self.assertEqual(status["indexed_count"], 1)
        state = self.client.get("/api/workspace").json()
        self.assertEqual(state["usage"]["input_tokens"], 100)
        self.assertEqual(state["messages"], [])
        self.assertEqual(self.search.status()["searches_today"], 1)

    def test_capture_is_public_only_and_source_view_is_authenticated(self):
        self.snippet += " " + self.keys[ENDPOINT]
        reply = self.send("Synthetic private chat detail")
        source = self.answer(reply)["web_search"]["sources"][0]
        viewed = self.client.get("/api/context/sources/" + source["capture_id"])
        self.assertEqual(viewed.status_code, 200, viewed.text)
        self.assertIsNone(viewed.json()["published_at"])
        exported = b"".join(p.read_bytes() for p in self.archive.rglob("*") if p.is_file())
        for private in (b"Synthetic private chat detail", self.query.encode(), self.keys[ENDPOINT].encode()):
            self.assertNotIn(private, exported)
        unauthenticated = TestClient(backend.app)
        self.addCleanup(unauthenticated.close)
        self.assertEqual(unauthenticated.get("/api/context/sources/" + source["capture_id"]).status_code, 401)

    def test_unapproved_source_is_transient_with_no_snapshot_id(self):
        self.url = "https://other.example.org/public"
        reply = self.send()
        source = self.answer(reply)["web_search"]["sources"][0]
        self.assertIn(source["archive_status"], ("skipped", "not_saved"))
        self.assertNotIn("capture_id", source)
        self.assertEqual(self.client.get("/api/context").json()["indexed_count"], 0)

    def test_delete_invalidates_saved_view_and_query_mapping(self):
        source = self.seed_answer()
        removed = self.client.delete("/api/context/sources/" + source["capture_id"], headers=self.headers)
        self.assertEqual(removed.status_code, 200, removed.text)
        self.assertEqual(self.client.get("/api/context/sources/" + source["capture_id"]).status_code, 404)
        reply = self.send(mode="saved_only")
        self.assertEqual(reply.status_code, 409, reply.text)
        self.assertEqual(self.hosted_requests(), [])

    def test_deleted_or_out_of_scope_evidence_rejects_late_model_result(self):
        original = self.http

        def invalidate_before_completion(request):
            if request.url.path == "/api/chat":
                payload = json.loads(request.content)
                if "tools" not in payload and payload["messages"][-1]["role"] == "tool":
                    sources = json.loads(payload["messages"][-1]["content"])
                    if self.invalidate == "delete":
                        changed = self.client.delete("/api/context/sources/" + sources[0]["capture_id"], headers=self.headers)
                    else:
                        config = self.client.get("/api/context").json()["config"]
                        changed = self.client.put("/api/context", headers=self.headers,
                                                  json={**config, "public_sources": ["https://different.example.org/"]})
                    self.assertEqual(changed.status_code, 200, changed.text)
            return original(request)

        self.http = invalidate_before_completion
        for action in ("delete", "scope"):
            with self.subTest(action=action):
                config = self.client.get("/api/context").json()["config"]
                self.client.put("/api/context", headers=self.headers,
                                json={**config, "public_sources": ["https://docs.ollama.com/"]})
                self.invalidate = action
                reply = self.send()
                self.assertEqual(reply.status_code, 409, reply.text)
                self.assertEqual(self.client.get("/api/workspace").json()["messages"], [])
                self.age_searches()
        usage = self.client.get("/api/workspace").json()["usage"]
        self.assertEqual(usage["input_tokens"], 400)
        self.assertEqual(usage["output_tokens"], 60)

    def test_memory_derived_chat_can_reuse_but_cannot_dispatch_hosted_search(self):
        self.seed_answer()
        with backend.workspace_transaction() as state:
            state["chats"][0]["messages"][-1]["memory_ids"] = ["synthetic-private-memory"]
        cached = self.send()
        self.assertEqual(cached.status_code, 200, cached.text)
        self.assertTrue(cached.json()["messages"][-1]["web_search"]["from_cache"])
        self.query = "A missing source query"
        blocked = self.send()
        self.assertEqual(blocked.status_code, 409, blocked.text)
        self.assertEqual(self.hosted_requests(), [])

    def test_invalid_freshness_and_additional_tool_arguments_cannot_dispatch(self):
        for freshness, extra in (("invented", {}), ("stable", {"override_network": True}),
                                 ("stable", {"domain": True}), ("stable", {"domain": None}),
                                 ("stable", {"domain": []}), ("stable", {"domain": {}}),
                                 ("stable", {"retrieved_from": 0}),
                                 ("stable", {"domain": "https://docs.ollama.com/"}),
                                 ("stable", {"retrieved_to": "2026-02-30"})):
            with self.subTest(freshness=freshness, extra=extra):
                self.freshness, self.extra_args = freshness, extra
                reply = self.send()
                self.assertEqual(reply.status_code, 409, reply.text)
                self.assertEqual(self.hosted_requests(), [])

    def test_explicit_source_modes_reject_an_unsupported_direct_model_answer(self):
        original = self.http

        def no_tool(request):
            if request.url.path == "/api/chat" and "tools" in json.loads(request.content):
                self.requests.append(request)
                return httpx.Response(200, json={"done": True, "message": {"role": "assistant", "content": "Unverified direct answer"},
                                                 "prompt_eval_count": 100, "eval_count": 10})
            return original(request)

        self.http = no_tool
        for mode in ("refresh", "saved_only"):
            with self.subTest(mode=mode):
                reply = self.send(mode=mode)
                self.assertEqual(reply.status_code, 409, reply.text)
                self.assertIn("did not request evidence", reply.json()["detail"])
                self.assertEqual(self.hosted_requests(), [])
        self.assertEqual(self.client.get("/api/workspace").json()["messages"], [])

    def test_context_mutations_require_csrf_and_reject_invalid_modes(self):
        self.assertEqual(self.client.post("/api/context/rebuild", json={"limit": 1000}).status_code, 403)
        self.assertEqual(self.client.post("/api/chat", headers=self.headers,
                         json={"text": "Synthetic request", "context_mode": "unrestricted"}).status_code, 422)

    def test_lifespan_owner_allows_threaded_chat_and_enable_disable(self):
        claim = self.archive / "writer-owner.tmp"
        with TestClient(backend.app) as live:
            live_headers = {"X-Maestro-CSRF": live.get("/api/session").json()["csrf"]}
            self.assertTrue(claim.is_file())
            with self.mocked_http():
                reply = live.post("/api/chat", headers=live_headers, json={"text": "Explain the tool interface"})
            self.assertEqual(reply.status_code, 200, reply.text)
            competitor = ContextStore(Path(self.directory) / "other-private" / "workspace.sqlite3", self.archive)
            with self.assertRaises(ValueError):
                with competitor.archive_owner():
                    self.fail("A second workspace acquired the live archive")
            config = live.get("/api/context").json()["config"]
            disabled = live.put("/api/context", headers=live_headers, json={**config, "enabled": False})
            self.assertEqual(disabled.status_code, 200, disabled.text)
            self.assertFalse(claim.exists())
            enabled = live.put("/api/context", headers=live_headers, json=config)
            self.assertEqual(enabled.status_code, 200, enabled.text)
            self.assertTrue(claim.exists())
        self.assertFalse(claim.exists())

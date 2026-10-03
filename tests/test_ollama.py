"""Native loopback Ollama chat with synthetic fixtures and no real model calls."""
from pathlib import Path
import json
import os
import tempfile
import unittest
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient

from backend import app as backend, credentials
from backend.web_search import ENDPOINT
from backend.provider import DEFAULT, Provider, TITLE_INSTRUCTIONS


class OllamaTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="maestro-ollama-test-")
        self.database_patch = patch.object(
            backend, "DATABASE", Path(self.directory.name) / "workspace.sqlite3"
        )
        self.database_patch.start()
        self.read_patch = patch.object(credentials, "read", return_value=(None, "session"))
        self.save_patch = patch.object(credentials, "save")
        self.read_key = self.read_patch.start()
        self.save_key = self.save_patch.start()
        self.client = TestClient(backend.app)
        self.headers = {"X-Maestro-CSRF": self.client.get("/api/session").json()["csrf"]}
        self.client.get("/api/workspace")
        self.base = "http://127.0.0.1:11434"
        self.model = "synthetic-local:latest"
        self.config = {
            **DEFAULT,
            "base_url": self.base,
            "protocol": "ollama",
            "model": self.model,
            "input_usd_per_million": 19.0,
            "output_usd_per_million": 27.0,
            "pricing_verified": False,
            "max_output_tokens": 256,
        }
        self.requests = []
        self.read_key.reset_mock()

    def tearDown(self):
        self.client.close()
        self.save_patch.stop()
        self.read_patch.stop()
        self.database_patch.stop()
        self.directory.cleanup()

    def configure(self, config=None, key="", persist=False):
        response = self.client.put(
            "/api/provider",
            headers=self.headers,
            json={"config": config or self.config, "api_key": key, "persist": persist},
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def local_model(self, **overrides):
        return {
            "name": self.model,
            "details": {"format": "gguf"},
            "capabilities": ["completion"],
            **overrides,
        }

    def provider_http(self, request):
        self.requests.append(request)
        self.assertEqual(request.url.host, "127.0.0.1")
        self.assertEqual(request.url.scheme, "http")
        self.assertNotIn("authorization", request.headers)
        self.assertNotIn("x-api-key", request.headers)
        if request.method == "GET" and request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [self.local_model()]})
        payload = json.loads(request.content)
        if request.method == "POST" and request.url.path == "/api/show":
            self.assertEqual(payload["model"], self.model)
            return httpx.Response(
                200, json={"details": {"format": "gguf"}, "capabilities": ["completion"]}
            )
        self.assertEqual(request.method, "POST")
        self.assertEqual(request.url.path, "/api/chat")
        self.assertEqual(payload["model"], self.model)
        self.assertFalse(payload["stream"])
        title = payload["messages"][0]["content"] == TITLE_INSTRUCTIONS
        self.assertEqual(payload["options"]["num_predict"], 64 if title else 256)
        self.assertEqual(payload["messages"][0]["role"], "system")
        self.assertNotIn("api_key", payload)
        self.assertNotIn("store", payload)
        return httpx.Response(
            200,
            json={
                "done": True,
                "message": {"role": "assistant", "content": "Synthetic local chat" if title else "Synthetic local answer"},
                "prompt_eval_count": 20 if title else 100,
                "eval_count": 5 if title else 20,
            },
        )

    def mock_http(self, handler=None):
        real_client = httpx.Client
        return patch(
            "backend.provider.httpx.Client",
            side_effect=lambda **kwargs: real_client(
                transport=httpx.MockTransport(handler or self.provider_http), **kwargs
            ),
        )

    def reply_requests(self):
        return [request for request in self.requests if request.url.path == "/api/chat"
                and json.loads(request.content)["messages"][0]["content"] != TITLE_INSTRUCTIONS]

    def test_setup_rejects_credentials_and_forces_zero_prices(self):
        synthetic_key = "synthetic-accidental-local-key"
        status = self.configure()
        self.read_key.assert_called_once_with(DEFAULT["base_url"])
        self.read_key.reset_mock()
        rejected = self.client.put(
            "/api/provider",
            headers=self.headers,
            json={"config": self.config, "api_key": synthetic_key, "persist": True},
        )
        self.assertEqual(rejected.status_code, 409, rejected.text)
        self.assertNotIn(synthetic_key, rejected.text)
        self.assertEqual(self.client.get("/api/provider").json()["config"], status["config"])
        self.assertFalse(status["credentials_required"])
        self.assertFalse(status["credentials_present"])
        self.assertEqual(status["config"]["input_usd_per_million"], 0)
        self.assertEqual(status["config"]["output_usd_per_million"], 0)
        self.assertTrue(status["config"]["pricing_verified"])
        with self.mock_http():
            tested = self.client.post("/api/provider/test", headers=self.headers)
            self.assertEqual(tested.status_code, 200, tested.text)
            reply = self.client.post(
                "/api/chat", headers=self.headers, json={"text": "Synthetic local greeting"}
            )
            self.assertEqual(reply.status_code, 200, reply.text)
        self.client.get("/api/provider")
        restored = self.client.get("/api/workspace").json()
        self.assertEqual({call.args for call in self.read_key.call_args_list}, {(ENDPOINT,)})
        self.save_key.assert_not_called()
        self.assertNotIn(synthetic_key, json.dumps(restored))
        self.assertNotIn(synthetic_key.encode(), backend.DATABASE.read_bytes())
        self.assertTrue(all(synthetic_key.encode() not in request.content for request in self.requests))

    def test_native_model_test_and_history_usage_survive_fresh_read(self):
        self.configure()
        self.read_key.reset_mock()
        with self.mock_http():
            tested = self.client.post("/api/provider/test", headers=self.headers)
            self.assertEqual(tested.status_code, 200, tested.text)
            self.assertEqual(tested.json()["models"], [self.model])
            for text in ("Remember the synthetic codeword.", "What did I just ask?"):
                result = self.client.post("/api/chat", headers=self.headers, json={"text": text})
                self.assertEqual(result.status_code, 200, result.text)
        paths = [request.url.path for request in self.requests]
        self.assertEqual(paths, ["/api/tags", "/api/show", "/api/chat", "/api/chat", "/api/show", "/api/chat"])
        payload = json.loads(self.requests[-1].content)
        self.assertEqual(
            [(message["role"], message["content"]) for message in payload["messages"][1:]],
            [
                ("user", "Remember the synthetic codeword."),
                ("assistant", "Synthetic local answer"),
                ("user", "What did I just ask?"),
            ],
        )
        with TestClient(backend.app) as fresh:
            fresh.get("/api/session")
            restored = fresh.get("/api/workspace").json()
        self.assertEqual(len(restored["messages"]), 4)
        self.assertEqual(restored["messages"][-1]["text"], "Synthetic local answer")
        usage = restored["usage"]
        self.assertEqual(usage["input_tokens"], 220)
        self.assertEqual(usage["output_tokens"], 45)
        self.assertEqual(usage["calls"], 3)
        self.assertEqual(usage["today_usd"], 0)
        self.assertEqual(usage["month_usd"], 0)
        self.assertEqual(usage["reserved_usd"], 0)
        self.assertEqual(usage["uncertain"], [])
        self.assertEqual(restored["provider"]["models"], [self.model])
        self.assertEqual({call.args for call in self.read_key.call_args_list}, {(ENDPOINT,)})

    def test_model_list_excludes_cloud_and_non_completion_entries(self):
        self.configure()
        models = [
            self.local_model(),
            self.local_model(name="synthetic-local-alias:latest", capabilities=None),
            self.local_model(name="synthetic-remote:latest", remote_host="synthetic.invalid"),
            self.local_model(name="synthetic-remote-model:latest", remote_model="synthetic-cloud"),
            self.local_model(name="synthetic-model:cloud"),
            self.local_model(name="synthetic-embed:latest", capabilities=["embedding"]),
            self.local_model(name="synthetic-empty:latest", capabilities=[]),
        ]
        # Older tags responses can omit capabilities; completion restrictions apply when present.
        models[1].pop("capabilities")
        with self.mock_http(lambda request: httpx.Response(200, json={"models": models})):
            tested = self.client.post("/api/provider/test", headers=self.headers)
        self.assertEqual(tested.status_code, 200, tested.text)
        self.assertEqual(tested.json()["models"], ["synthetic-local-alias:latest", self.model])

    def test_worker_settings_persist_keep_models_and_control_native_request(self):
        self.configure()
        with self.mock_http():
            self.client.post("/api/provider/test", headers=self.headers)
        config = {**self.config, "ollama_context_tokens": 8192,
                  "ollama_threads": 2, "ollama_keep_alive_minutes": 0}
        status = self.configure(config)
        self.assertEqual(status["models"], [self.model])
        restored = Provider(backend.DATABASE, backend.TIMEZONE).status()
        self.assertEqual(restored["config"], status["config"])
        with self.mock_http():
            reply = self.client.post("/api/chat", headers=self.headers,
                                     json={"text": "Check saved local worker settings"})
        self.assertEqual(reply.status_code, 200, reply.text)
        payload = json.loads(self.reply_requests()[-1].content)
        self.assertEqual(payload["options"], {"num_ctx": 8192, "num_thread": 2, "num_predict": 256})
        self.assertEqual(payload["keep_alive"], 0)
        self.assertEqual(reply.json()["usage"]["today_usd"], 0)

    def test_existing_settings_gain_worker_defaults_without_losing_state(self):
        self.configure()
        service = Provider(backend.DATABASE, backend.TIMEZONE)
        with service.transaction() as state:
            for key in ("ollama_context_tokens", "ollama_threads", "ollama_keep_alive_minutes", "orchestrator_model"):
                del state["config"][key]
            state["models"] = [self.model]
            state["tested_at"] = "synthetic-previous-test"
        status = self.client.get("/api/provider").json()
        self.assertEqual(status["config"]["ollama_context_tokens"], DEFAULT["ollama_context_tokens"])
        self.assertEqual(status["config"]["ollama_threads"], 0)
        self.assertEqual(status["config"]["ollama_keep_alive_minutes"], 5)
        self.assertEqual(status["config"]["orchestrator_model"], "")
        self.assertEqual(status["config"]["model"], self.model)
        self.assertEqual(status["models"], [self.model])
        self.assertEqual(status["tested_at"], "synthetic-previous-test")
        with self.mock_http():
            reply = self.client.post("/api/chat", headers=self.headers,
                                     json={"text": "Check old settings still generate"})
        self.assertEqual(reply.status_code, 200, reply.text)
        payload = json.loads(self.reply_requests()[-1].content)
        self.assertEqual(payload["options"], {"num_ctx": DEFAULT["ollama_context_tokens"], "num_thread": 0, "num_predict": 256})
        self.assertEqual(payload["keep_alive"], "5m")
        self.assertEqual(service.read_state()["config"], status["config"])

    def test_local_context_guard_stops_before_dispatch_and_preserves_chat(self):
        self.configure({**self.config, "ollama_context_tokens": 4096})
        before = self.client.get("/api/workspace").json()
        with self.mock_http():
            rejected = self.client.post("/api/chat", headers=self.headers,
                                        json={"text": "Synthetic oversized context " * 100})
        self.assertEqual(rejected.status_code, 409, rejected.text)
        self.assertIn("context allowance", rejected.json()["detail"])
        self.assertEqual(self.requests, [])
        after = self.client.get("/api/workspace").json()
        self.assertEqual(after["messages"], before["messages"])
        self.assertEqual(after["usage"], before["usage"])
        self.configure({**self.config, "ollama_context_tokens": 16384})
        with self.mock_http():
            allowed = self.client.post("/api/chat", headers=self.headers,
                                       json={"text": "Synthetic oversized context " * 100})
        self.assertEqual(allowed.status_code, 200, allowed.text)

    def test_output_and_context_capacity_rejects_invalid_saves_and_legacy_settings_can_be_fixed(self):
        saved = self.configure()["config"]
        service = Provider(backend.DATABASE, backend.TIMEZONE)
        incompatible = {**saved, "ollama_context_tokens": 8192, "max_output_tokens": 8192}
        with patch("backend.provider.network") as network:
            for output in (8192, 6144):
                response = self.client.put("/api/provider", headers=self.headers, json={
                    "config": {**incompatible, "max_output_tokens": output}})
                self.assertEqual(response.status_code, 409, response.text)
                self.assertIn("output limit and chat prompt", response.json()["detail"])
                self.assertEqual(service.read_state()["config"], saved)
            network.assert_not_called()
        with service.transaction() as state:
            state["config"] = incompatible  # A previously saved configuration remains editable.
        self.assertEqual(self.client.get("/api/provider").json()["config"], incompatible)
        with self.mock_http():
            rejected = self.client.post("/api/chat", headers=self.headers, json={"text": "Synthetic greeting"})
        self.assertEqual(rejected.status_code, 409, rejected.text)
        self.assertEqual(self.requests, [])
        repaired = self.configure({**incompatible, "max_output_tokens": 256})
        self.assertEqual(repaired["config"]["ollama_context_tokens"], 8192)
        with self.mock_http():
            reply = self.client.post("/api/chat", headers=self.headers, json={"text": "Synthetic greeting"})
        self.assertEqual(reply.status_code, 200, reply.text)

    def test_worker_resource_values_reject_invalid_input_without_overwriting_settings(self):
        saved = self.configure()["config"]
        for key, values in (
            ("ollama_context_tokens", (1023, 131073, 4096.5)),
            ("ollama_threads", (-1, 257, 1.5)),
            ("ollama_keep_alive_minutes", (-1, 121, 0.5)),
        ):
            for value in values:
                with self.subTest(key=key, value=value):
                    rejected = self.client.put("/api/provider", headers=self.headers,
                                               json={"config": {**self.config, key: value}})
                    self.assertEqual(rejected.status_code, 422, rejected.text)
                    self.assertEqual(self.client.get("/api/provider").json()["config"], saved)

    def test_zero_dollar_limits_allow_local_generation_and_token_limits_still_block(self):
        self.configure()
        limits = self.client.get("/api/workspace").json()["limits"]
        zero = {**limits, "run_usd": 0, "daily_usd": 0, "monthly_usd": 0}
        self.client.put("/api/limits", headers=self.headers, json=zero)
        with self.mock_http():
            result = self.client.post("/api/chat", headers=self.headers, json={"text": "Local generation"})
            self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(result.json()["usage"]["today_usd"], 0)
        self.client.put("/api/limits", headers=self.headers, json={**zero, "max_tokens": 0})
        before = len(self.requests)
        with self.mock_http():
            result = self.client.post("/api/chat", headers=self.headers, json={"text": "Blocked by tokens"})
        self.assertEqual(result.status_code, 409, result.text)
        self.assertIn("token", result.text.lower())
        self.assertEqual(len(self.requests), before)

    def test_remote_or_non_native_urls_rejected_without_saving_or_dispatch(self):
        self.configure()
        self.read_key.reset_mock()
        invalid = (
            "http://synthetic.invalid:11434",
            "https://synthetic.invalid",
            "http://0.0.0.0:11434",
            "http://192.0.2.1:11434",
            "http://127.0.0.1:11434/v1",
            "http://localhost:11434/proxy",
            "http://secret@127.0.0.1:11434",
            "http://127.0.0.1:11434?key=synthetic",
            "http://127.0.0.1:11434#synthetic",
            "http://127.0.0.1:99999",
        )
        with self.mock_http():
            for base in invalid:
                with self.subTest(base=base):
                    response = self.client.put(
                        "/api/provider",
                        headers=self.headers,
                        json={"config": {**self.config, "base_url": base}, "persist": False},
                    )
                    self.assertEqual(response.status_code, 409, response.text)
                    self.assertEqual(self.client.get("/api/provider").json()["config"]["base_url"], self.base)
        self.assertEqual(self.requests, [])
        self.save_key.assert_not_called()
        self.read_key.assert_not_called()

    @patch.dict(os.environ, {"MAESTRO_OLLAMA_URL": ""})
    def test_container_endpoint_requires_exact_server_opt_in_and_never_sends_credentials(self):
        self.configure()
        for host in ("host.containers.internal", "host.docker.internal"):
            endpoint = f"http://{host}:11434"
            with self.subTest(host=host):
                rejected = self.client.put("/api/provider", headers=self.headers,
                                           json={"config": {**self.config, "base_url": endpoint}})
                self.assertEqual(rejected.status_code, 409, rejected.text)
                with patch.dict(os.environ, {"MAESTRO_OLLAMA_URL": endpoint}):
                    self.configure({**self.config, "base_url": endpoint})
                    calls = []
                    def handler(request):
                        calls.append(request)
                        self.assertEqual(request.url.host, host)
                        self.assertNotIn("authorization", request.headers)
                        if request.url.path == "/api/show":
                            return httpx.Response(200, json={"details": {"format": "gguf"}, "capabilities": ["completion"]})
                        return httpx.Response(200, json={"done": True, "message": {"content": "Synthetic container reply"},
                                                         "prompt_eval_count": 100, "eval_count": 20})
                    with self.mock_http(handler):
                        allowed = self.client.post("/api/chat", headers=self.headers, json={"text": "Container host model"})
                    self.assertEqual(allowed.status_code, 200, allowed.text)
                    self.assertEqual(calls[0].url.path, "/api/show")
                    for invalid in (endpoint + "/v1", endpoint.replace(":11434", ":11435"),
                                    endpoint + "?key=synthetic", endpoint.replace(host, "synthetic.invalid")):
                        denied = self.client.put("/api/provider", headers=self.headers,
                                                 json={"config": {**self.config, "base_url": invalid}})
                        self.assertEqual(denied.status_code, 409, denied.text)
                self.configure()
        for invalid in ("http://synthetic.invalid:11434", "https://synthetic.invalid", "http://host.containers.internal:11434/v1",
                        "http://user:synthetic-env-key@host.containers.internal:11434", "http://host.containers.internal:11434?key=synthetic-env-key"):
            with patch.dict(os.environ, {"MAESTRO_OLLAMA_URL": invalid}):
                status = self.client.get("/api/provider")
                self.assertEqual(status.json()["ollama_base_url"], self.base)
                self.assertNotIn("synthetic-env-key", status.text)
                rejected = self.client.put("/api/provider", headers=self.headers,
                                           json={"config": {**self.config, "base_url": invalid}})
            self.assertEqual(rejected.status_code, 409, rejected.text)

    def test_show_rejects_cloud_models_and_unsupported_capabilities_before_generation(self):
        self.configure()
        for show in (
            {"details": {"format": "gguf"}, "capabilities": ["completion"], "remote_host": "synthetic.invalid"},
            {"details": {"format": "gguf"}, "capabilities": ["completion"], "remote_model": "synthetic-cloud"},
            {"details": {"format": "gguf"}, "capabilities": ["embedding"]},
            {"details": {"format": "gguf"}, "capabilities": []},
            {"details": {"format": "gguf"}},
            {"details": {"format": "gguf"}, "capabilities": None},
        ):
            calls = []

            def handler(request):
                calls.append(request)
                self.assertEqual(request.url.path, "/api/show")
                return httpx.Response(200, json=show)

            with self.subTest(show=show), self.mock_http(handler):
                result = self.client.post("/api/chat", headers=self.headers, json={"text": "Must stay local"})
            self.assertEqual(result.status_code, 409, result.text)
            self.assertEqual(len(calls), 1)
            usage = self.client.get("/api/workspace").json()["usage"]
            self.assertEqual(usage["reserved_usd"], 0)
            self.assertEqual(usage["uncertain"], [])
        self.assertEqual(self.client.get("/api/workspace").json()["messages"], [])

    def test_cloud_model_suffix_is_rejected_without_cloud_fallback(self):
        self.configure({**self.config, "model": "synthetic-model:cloud"})
        with self.mock_http():
            response = self.client.post("/api/chat", headers=self.headers, json={"text": "Stay on this computer"})
        self.assertEqual(response.status_code, 409, response.text)
        self.assertTrue(all(request.url.path != "/api/chat" for request in self.requests))
        self.assertTrue(all(request.url.host == "127.0.0.1" for request in self.requests))
        self.assertEqual(self.client.get("/api/workspace").json()["messages"], [])

    def test_local_errors_release_reservations_redact_details_and_allow_retry(self):
        self.configure()
        secret = "synthetic-private-provider-error"
        for failure in ("http", "timeout", "malformed", "missing_usage", "refusal"):
            calls = []

            def handler(request):
                calls.append(request)
                if request.url.path == "/api/show":
                    return httpx.Response(200, json={"details": {"format": "gguf"}, "capabilities": ["completion"]})
                self.assertEqual(request.url.path, "/api/chat")
                if failure == "http":
                    return httpx.Response(500, json={"error": secret})
                if failure == "timeout":
                    raise httpx.ReadTimeout(secret, request=request)
                if failure == "malformed":
                    return httpx.Response(200, text=secret)
                if failure == "refusal":
                    return httpx.Response(200, json={"done": True, "message": {"content": None, "refusal": secret},
                                                     "prompt_eval_count": 100, "eval_count": 5})
                return httpx.Response(200, json={"done": True, "message": {"role": "assistant", "content": secret}})

            with self.subTest(failure=failure), self.mock_http(handler):
                result = self.client.post("/api/chat", headers=self.headers, json={"text": "Synthetic failure check"})
            self.assertEqual(result.status_code, 409, result.text)
            self.assertNotIn(secret, result.text)
            if failure == "missing_usage":
                self.assertNotIn("reconcil", result.text.lower())
            self.assertEqual([request.url.path for request in calls], ["/api/show", "/api/chat"])
            restored = self.client.get("/api/workspace").json()
            self.assertEqual(restored["messages"], [])
            self.assertNotIn(secret, json.dumps(restored))
            self.assertNotIn(secret.encode(), backend.DATABASE.read_bytes())
            self.assertEqual(restored["usage"]["reserved_usd"], 0)
            self.assertEqual(restored["usage"]["uncertain"], [])
            self.assertEqual(restored["usage"]["today_usd"], 0)
        with self.mock_http():
            retried = self.client.post("/api/chat", headers=self.headers, json={"text": "A valid local retry"})
        self.assertEqual(retried.status_code, 200, retried.text)

    def test_recovery_releases_local_reservation_instead_of_creating_uncertain_charge(self):
        self.configure()
        service = Provider(backend.DATABASE, backend.TIMEZONE)
        with service.transaction() as state:
            state["ledger"].append({
                "id": "synthetic-interrupted-local-call",
                "at": service.stamp(),
                "model": self.model,
                "protocol": "ollama",
                "cost": 0,
                "status": "reserved",
            })
        service.recover()
        self.assertEqual(service.usage()["uncertain"], [])
        self.assertEqual(service.usage()["reserved_usd"], 0)
        with self.mock_http():
            response = self.client.post("/api/chat", headers=self.headers, json={"text": "After local restart"})
        self.assertEqual(response.status_code, 200, response.text)

    def test_paid_protocol_still_requires_key(self):
        config = {
            **self.config,
            "protocol": "responses",
            "base_url": "https://synthetic-paid.invalid/v1",
            "input_usd_per_million": 1,
            "output_usd_per_million": 2,
            "pricing_verified": True,
        }
        status = self.configure(config)
        self.assertTrue(status["credentials_required"])
        with self.mock_http():
            response = self.client.post("/api/chat", headers=self.headers, json={"text": "No paid dispatch"})
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(self.requests, [])


if __name__ == "__main__":
    unittest.main()

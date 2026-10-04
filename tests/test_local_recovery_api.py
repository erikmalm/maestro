"""Local recovery requires an explicit restart acknowledgement for its original server."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend import app as backend
from backend.provider import BackgroundUncertain, DEFAULT


ORIGINAL = {**DEFAULT, "protocol": "ollama", "model": "synthetic:7b", "base_url": "http://127.0.0.1:11434"}


class LocalRecoveryAPITests(unittest.TestCase):
    def setUp(self):
        directory = Path(self.enterContext(tempfile.TemporaryDirectory(prefix="maestro-local-recovery-api-")))
        self.enterContext(patch.object(backend, "DATABASE", directory / "workspace.sqlite3"))
        self.enterContext(patch.object(backend.credentials, "read", return_value=(None, "missing")))
        self.client = TestClient(backend.app)
        self.addCleanup(self.client.close)
        self.headers = {"X-Maestro-CSRF": self.client.get("/api/session").json()["csrf"]}
        self.client.post("/api/chats", headers=self.headers)
        self.service = backend.provider()
        self.service.configure(ORIGINAL.copy(), "", False)
        with self.service.transaction() as state:
            state["ledger"].append({"id": "synthetic-unknown", "at": self.service.stamp(), "protocol": "ollama",
                                    "model": "synthetic:7b", "cost": 0, "status": "reserved", "local_unknown": True,
                                    "input_tokens": 100, "output_tokens": 20, "model_calls": 2,
                                    "connection_config": ORIGINAL.copy()})
        self.url = "/api/provider/local-requests/synthetic-unknown/recover"
        self.body = {"restart_confirmed": True, "expected_base_url": ORIGINAL["base_url"]}

    def entry(self):
        return self.service.read_state()["ledger"][0]

    def test_confirmation_access_and_strict_validation_precede_the_server_check(self):
        with patch("backend.provider.background_network") as request:
            anonymous = TestClient(backend.app)
            try:
                self.assertEqual(anonymous.post(self.url, headers=self.headers, json=self.body).status_code, 401)
            finally:
                anonymous.close()
            self.assertEqual(self.client.post(self.url, json=self.body).status_code, 403)
            self.assertEqual(self.client.post(self.url, headers={**self.headers, "Origin": "https://example.invalid"},
                                             json=self.body).status_code, 403)
            for body in ({"expected_base_url": ORIGINAL["base_url"]}, {**self.body, "restart_confirmed": "true"},
                         {**self.body, "restart_confirmed": 1}, {**self.body, "extra": "synthetic-private-value"}):
                response = self.client.post(self.url, headers=self.headers, json=body)
                self.assertEqual(response.status_code, 422, response.text)
                self.assertNotIn("synthetic-private-value", response.text)
            response = self.client.post(self.url, headers=self.headers, json={**self.body, "restart_confirmed": False})
            self.assertEqual(response.status_code, 409, response.text)
            request.assert_not_called()
        self.assertEqual(self.entry()["status"], "reserved")

    def test_recovery_checks_original_endpoint_after_provider_change_and_retains_known_usage(self):
        self.service.configure({**ORIGINAL, "base_url": "http://127.0.0.1:11436"}, "", False)
        before = self.client.get("/api/workspace").json()
        self.assertEqual(before["usage"]["local_requests"][0]["base_url"], ORIGINAL["base_url"])
        with patch("backend.provider.background_network", return_value={"models": []}) as request:
            wrong = self.client.post(self.url, headers=self.headers, json={**self.body, "expected_base_url": "http://127.0.0.1:11436"})
            self.assertEqual(wrong.status_code, 409)
            request.assert_not_called()
            response = self.client.post(self.url, headers=self.headers, json=self.body)
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(request.call_args.args[:3], (ORIGINAL, "/api/ps", None))
        after = response.json()
        for field in ("input_tokens", "output_tokens", "calls"):
            self.assertEqual(after["usage"][field], before["usage"][field])
        for field in ("messages", "chats", "tasks", "provider"):
            self.assertEqual(after[field], before[field])
        self.assertEqual(after["usage"]["local_requests"], [])
        self.assertTrue(self.entry()["local_recovered"])
        with patch("backend.provider.background_network") as request:
            duplicate = self.client.post(self.url, headers=self.headers, json=self.body)
            self.assertEqual(duplicate.status_code, 200, duplicate.text)
            request.assert_not_called()

    def test_failed_verification_retains_the_reservation(self):
        for result in ({}, {"models": None}, {"models": [{"name": "synthetic-resident"}]}):
            with self.subTest(result=result), patch("backend.provider.background_network", return_value=result):
                response = self.client.post(self.url, headers=self.headers, json=self.body)
                self.assertEqual(response.status_code, 409, response.text)
                self.assertEqual(self.entry()["status"], "reserved")
        with patch("backend.provider.background_network", side_effect=BackgroundUncertain("Synthetic offline server")):
            self.assertEqual(self.client.post(self.url, headers=self.headers, json=self.body).status_code, 409)
        self.assertEqual(self.entry()["status"], "reserved")

    def test_legacy_endpoint_is_only_assigned_by_acknowledged_recovery(self):
        with self.service.transaction() as state:
            state["ledger"][0].pop("connection_config")
        self.service.configure({**ORIGINAL, "base_url": "http://127.0.0.1:11436"}, "", False)
        with patch("backend.provider.network", return_value={"models": []}):
            self.assertEqual(self.client.post("/api/provider/test", headers=self.headers).status_code, 200)
        self.assertNotIn("connection_config", self.entry())
        self.assertIsNone(self.client.get("/api/workspace").json()["usage"]["local_requests"][0]["base_url"])
        with patch("backend.provider.background_network", return_value={"models": []}) as request:
            response = self.client.post(self.url, headers=self.headers, json=self.body)
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(request.call_args.args[0]["base_url"], ORIGINAL["base_url"])
        self.assertEqual(self.entry()["connection_config"]["base_url"], ORIGINAL["base_url"])

    def test_active_local_and_paid_requests_cannot_use_this_recovery_action(self):
        with patch("backend.provider.background_network") as request:
            for changes in ({"local_unknown": False}, {"protocol": "responses", "status": "uncertain", "local_unknown": True}):
                with self.subTest(changes=changes):
                    with self.service.transaction() as state:
                        state["ledger"][0].update(changes)
                    self.assertEqual(self.client.post(self.url, headers=self.headers, json=self.body).status_code, 409)
            request.assert_not_called()

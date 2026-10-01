"""Meaningful local storage, security and demo budget checks using synthetic data."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from backend import app as backend


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="maestro-test-")
        self.directory = Path(self.temporary.name).resolve()
        self.database_patch = patch.object(backend, "DATABASE", self.directory / "workspace.sqlite3")
        self.database_patch.start()
        self.client = TestClient(backend.app)
        self.csrf = self.client.get("/api/session").json()["csrf"]
        self.headers = {"X-Maestro-CSRF": self.csrf, "Origin": "http://127.0.0.1:8765"}

    def tearDown(self):
        self.client.close()
        self.database_patch.stop()
        self.assertEqual(self.directory.parent, Path(tempfile.gettempdir()).resolve())
        self.temporary.cleanup()

    def workspace(self):
        response = self.client.get("/api/workspace")
        self.assertEqual(response.status_code, 200)
        return response.json()

    def test_session_csrf_and_unrelated_origin_cannot_read_or_write(self):
        anonymous = TestClient(backend.app)
        self.assertEqual(anonymous.get("/api/workspace").status_code, 401)
        self.assertEqual(self.client.post("/api/tasks", json={"title": "Synthetic task"}).status_code, 403)
        self.assertEqual(self.client.get("/api/workspace", headers={"Origin": "https://unrelated.example"}).status_code, 403)
        self.assertEqual(self.client.post("/api/tasks", json={"title": "Synthetic task"}, headers={**self.headers, "Origin": "https://unrelated.example"}).status_code, 403)
        self.assertEqual(self.client.get("/api/workspace", headers={"Host": "unrelated.example"}).status_code, 400)
        anonymous.close()

    def test_task_creation_persists_without_changing_usage(self):
        before = self.workspace()
        created = self.client.post("/api/tasks", headers=self.headers, json={"title": "Synthetic persistence task", "details": "A test criterion", "priority": "high"})
        self.assertEqual(created.status_code, 200)
        self.assertEqual(created.json()["usage"], before["usage"])
        second = TestClient(backend.app)
        second.get("/api/session")
        self.assertEqual(second.get("/api/workspace").json()["tasks"][0]["title"], "Synthetic persistence task")
        second.close()
        self.assertTrue(backend.DATABASE.is_file())
        self.assertNotIn(backend.ROOT, backend.DATABASE.parents)

    def test_zero_budget_blocks_simulation_without_partial_writes(self):
        before = self.workspace()
        limits = {**before["limits"], "run_usd": 0}
        self.assertEqual(self.client.put("/api/limits", headers=self.headers, json=limits).status_code, 200)
        blocked = self.client.post("/api/tasks/sample-documents/preview", headers=self.headers, json={})
        self.assertEqual(blocked.status_code, 409)
        after = self.workspace()
        self.assertEqual(before["usage"], after["usage"])
        self.assertEqual(before["runs"], after["runs"])

    def test_token_ceiling_blocks_preview(self):
        before = self.workspace()
        self.client.put("/api/limits", headers=self.headers, json={**before["limits"], "max_tokens": 0})
        self.assertEqual(self.client.post("/api/tasks/sample-documents/preview", headers=self.headers, json={}).status_code, 409)
        self.assertEqual(self.workspace()["runs"], before["runs"])

    def test_concurrent_runs_cannot_overspend_shared_daily_allowance(self):
        before = self.workspace()
        self.client.put("/api/limits", headers=self.headers, json={**before["limits"], "daily_usd": 0.44})
        def run():
            with TestClient(backend.app) as client:
                client.get("/api/session")
                return client.post("/api/tasks/sample-documents/preview", headers=self.headers, json={}).status_code
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda _: run(), range(2)))
        self.assertEqual(sorted(results), [200, 409])
        self.assertAlmostEqual(self.workspace()["usage"]["today_usd"], 0.44)

    def test_demo_run_leaves_real_task_open_and_reports_false_capabilities(self):
        result = self.client.post("/api/tasks/sample-documents/preview", headers=self.headers, json={}).json()
        task = next(task for task in result["tasks"] if task["id"] == "sample-documents")
        self.assertFalse(task["done"])
        self.assertFalse(result["capabilities"]["live_ai"])
        self.assertFalse(result["capabilities"]["github_pr"])
        self.assertFalse(result["capabilities"]["secure_credentials"])

    def test_limits_reject_invalid_values_and_do_not_expose_credentials(self):
        original = self.workspace()
        for update in ({"daily_usd": -1}, {"max_refinements": 11}, {"max_minutes": 0}, {"api_key": "synthetic-placeholder"}):
            response = self.client.put("/api/limits", headers=self.headers, json={**original["limits"], **update})
            self.assertEqual(response.status_code, 422)
            self.assertNotIn("synthetic-placeholder", response.text)
        self.assertEqual(self.workspace()["limits"], original["limits"])
        self.assertNotIn("api_key", str(self.workspace()))

    def test_manual_memory_can_be_forgotten(self):
        result = self.client.post("/api/memory", headers=self.headers, json={"text": "Synthetic preference"})
        self.assertEqual(result.status_code, 200)
        memory_id = result.json()["memories"][0]["id"]
        self.assertEqual(self.client.delete(f"/api/memory/{memory_id}", headers=self.headers).status_code, 200)
        self.assertNotIn("Synthetic preference", str(self.workspace()))


if __name__ == "__main__":
    unittest.main()

"""Synthetic evidence and mock providers only; no paid API traffic."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timezone
from pathlib import Path
import json
import tempfile
import threading
import unittest
from unittest.mock import patch

import httpx

from backend.reflection import DEFAULTS, ReflectionEngine, provider_call


class ReflectionTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="maestro-reflection-test-")
        self.key = patch.dict("os.environ", {"OPENAI_API_KEY": "synthetic-test-value"})
        self.key.start()
        self.calls = []
        self.engine = ReflectionEngine(Path(self.directory.name) / "workspace.sqlite3", timezone.utc, self.provider)
        self.config = {**DEFAULTS, "enabled": True, "model": "synthetic-model", "input_usd_per_million": 1,
                       "output_usd_per_million": 2, "pricing_verified": True}
        self.engine.configure(self.config)

    def tearDown(self):
        self.engine.close()
        self.key.stop()
        self.directory.cleanup()

    def provider(self, config, prompt, timeout):
        payload = json.loads(prompt)
        self.calls.append(payload)
        return {"summary": "Grounded synthetic lesson", "lesson": "Attach sources to factual summaries.",
                "critique": "Matches supplied feedback", "approved": True}, {"input_tokens": 100, "output_tokens": 50}

    def prepare(self):
        self.engine.evidence("Synthetic observed missing source", True)
        return self.engine.enqueue()

    def test_only_approved_feedback_leaves_storage_and_idle_does_not_repeat(self):
        self.engine.evidence("Synthetic local-only text", False)
        job_id = self.prepare()
        self.engine.process()
        self.assertEqual(len(self.calls), 2)
        self.assertNotIn("Synthetic local-only text", json.dumps(self.calls))
        job = self.engine.status()["jobs"][0]
        self.assertEqual(job["status"], "needs_review")
        self.assertAlmostEqual(job["cost"], 0.0004)
        self.assertEqual(self.engine.status()["usage"]["tokens"], 300)
        with self.assertRaisesRegex(ValueError, "No new feedback"):
            self.engine.enqueue()
        self.engine.process()
        self.assertEqual(len(self.calls), 2)
        self.engine.review(job_id, True)
        self.prepare()
        self.engine.process()
        self.assertEqual(self.calls[-1]["approved_guidance"], ["Attach sources to factual summaries."])

    def test_cycle_zero_and_global_zero_prevent_provider_dispatch(self):
        self.engine.configure({**self.config, "cycle_usd": 0})
        self.prepare()
        self.engine.process()
        self.assertEqual(self.calls, [])
        self.assertIn("Cycle budget", self.engine.status()["jobs"][0]["reason"])
        self.engine.configure(self.config)
        with patch.object(self.engine, "_limits", return_value={"run_usd": 1, "daily_usd": 0, "monthly_usd": 50, "max_tokens": 100000, "max_minutes": 10, "max_refinements": 2}):
            self.prepare()
            self.engine.process()
        self.assertEqual(self.calls, [])
        self.assertIn("Daily or monthly", self.engine.status()["jobs"][0]["reason"])

    def test_pause_blocks_follow_up_after_inflight_call(self):
        def provider(config, prompt, timeout):
            self.engine.configure({**self.config, "enabled": False})
            return self.provider(config, prompt, timeout)
        self.engine.provider = provider
        self.prepare()
        self.engine.process()
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.engine.status()["jobs"][0]["status"], "stopped")

    def test_repeated_bad_proposal_stops_and_never_self_promotes(self):
        def provider(config, prompt, timeout):
            result, usage = self.provider(config, prompt, timeout)
            return {**result, "approved": False}, usage
        self.engine.provider = provider
        self.prepare()
        self.engine.process()
        self.assertEqual(len(self.calls), 4)
        self.assertEqual(self.engine.status()["jobs"][0]["reason"], "No meaningful change between iterations")
        self.assertEqual(self.engine.status()["jobs"][0]["status"], "needs_review")

    def test_failure_reserves_possible_charge_pauses_and_cannot_reset_budget(self):
        def provider(*args):
            raise RuntimeError("synthetic provider detail must stay private")
        self.engine.provider = provider
        self.prepare()
        self.engine.process()
        before = self.engine.status()
        self.assertFalse(before["config"]["enabled"])
        self.assertGreater(before["usage"]["reserved_usd"], 0)
        self.assertNotIn("synthetic provider detail", json.dumps(before))
        self.engine.clear_history()
        self.assertEqual(self.engine.status()["usage"], before["usage"])
        self.engine.configure(self.config)
        self.engine.evidence("Another synthetic feedback item", True)
        with self.assertRaisesRegex(ValueError, "unknown charges"):
            self.engine.enqueue()
        self.engine.configure({**self.config, "enabled": False})
        entry = self.engine.status()["uncertain_charges"][0]
        self.engine.reconcile(entry["id"], 0.002)
        self.assertFalse(self.engine.status()["usage"]["unresolved"])
        self.assertAlmostEqual(self.engine.status()["usage"]["today_usd"], 0.002)

    def test_concurrent_enqueue_creates_one_cycle(self):
        self.engine.evidence("Synthetic observation", True)
        def enqueue():
            try:
                return self.engine.enqueue()
            except ValueError:
                return None
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda _: enqueue(), range(4)))
        self.assertEqual(sum(bool(x) for x in results), 1)
        self.assertEqual(len(self.engine.status()["jobs"]), 1)
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(lambda _: self.engine.process(), range(4)))
        self.assertEqual(len(self.calls), 2)

    def test_crash_recovery_does_not_replay_running_call(self):
        self.prepare()
        with self.engine.transaction() as state:
            state["jobs"][0]["status"] = "running"
            state["ledger"].append({"id": "synthetic-reservation", "at": self.engine.stamp(), "cost": 0.01, "status": "reserved"})
        self.engine.start()
        self.engine.close()
        status = self.engine.status()
        self.assertEqual(status["jobs"][0]["status"], "interrupted")
        self.assertFalse(status["config"]["enabled"])
        self.assertTrue(status["usage"]["unresolved"])
        self.assertEqual(self.calls, [])

    def test_background_worker_picks_up_feedback_without_manual_dispatch(self):
        finished = threading.Event()
        def provider(config, prompt, timeout):
            result = self.provider(config, prompt, timeout)
            if json.loads(prompt)["role"] == "review":
                finished.set()
            return result
        self.engine.provider = provider
        self.engine.evidence("Synthetic automatic cycle observation", True)
        self.engine.start()
        self.assertTrue(finished.wait(3), "Worker did not dispatch its scheduled cycle")
        self.engine.close()
        self.assertEqual(self.engine.status()["jobs"][0]["status"], "needs_review")
        self.assertEqual(len(self.calls), 2)

    def test_token_ceiling_and_pass_limit_gate_dispatch(self):
        with patch.object(self.engine, "_limits", return_value={"run_usd": 1, "daily_usd": 5, "monthly_usd": 50, "max_tokens": 10, "max_minutes": 10, "max_refinements": 2}):
            self.prepare()
            self.engine.process()
        self.assertEqual(self.calls, [])
        self.assertIn("Token budget", self.engine.status()["jobs"][0]["reason"])
        def provider(config, prompt, timeout):
            result, usage = self.provider(config, prompt, timeout)
            return {**result, "lesson": f"Synthetic lesson {len(self.calls)}", "approved": False}, usage
        self.engine.provider = provider
        self.prepare()
        self.engine.process()
        self.assertEqual(len(self.calls), 6)
        self.assertEqual(self.engine.status()["jobs"][0]["reason"], "Refinement limit reached")

    def test_structured_output_adapter_uses_server_key_and_accounts_incomplete_output(self):
        requests = []
        def transport(request):
            requests.append(request)
            return httpx.Response(200, json={"status": "incomplete", "output": [], "usage": {"input_tokens": 123, "output_tokens": 17}})
        real_client = httpx.Client
        with patch("backend.reflection.httpx.Client", side_effect=lambda **kwargs: real_client(transport=httpx.MockTransport(transport), **kwargs)):
            result, usage = provider_call(self.config, "synthetic evidence", 1)
        self.assertIsNone(result)
        self.assertEqual(usage["output_tokens"], 17)
        payload = json.loads(requests[0].content)
        self.assertFalse(payload["store"])
        self.assertEqual(payload["max_output_tokens"], 1024)
        self.assertTrue(payload["text"]["format"]["strict"])
        self.assertNotIn("synthetic-test-value", json.dumps(payload))


if __name__ == "__main__":
    unittest.main()

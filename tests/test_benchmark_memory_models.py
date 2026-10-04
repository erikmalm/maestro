"""The opt-in public probe must stop before overlapping uncertain inference."""
from contextlib import redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts import benchmark_memory_models as benchmark


class BenchmarkTests(unittest.TestCase):
    def run_probe(self, mode):
        directory = Path(self.enterContext(tempfile.TemporaryDirectory(prefix="maestro-benchmark-test-")))
        output = directory / "report.json"
        calls = []

        def request(path, payload=None, timeout=120):
            if path == "/api/ps":
                return {"models": [{"name": "synthetic-resident"}] if mode == "busy_after_call" and calls else []}
            if path == "/api/tags":
                return {"models": [{"name": model, "digest": "synthetic-digest"} for model in ("synthetic-a", "synthetic-b")]}
            if path == "/api/version":
                return {"version": "synthetic"}
            if path == "/api/show":
                return {"details": {"format": "gguf"}, "capabilities": ["completion"]}
            self.assertEqual(path, "/api/chat")
            calls.append(payload["model"])
            if mode == "timeout":
                raise TimeoutError("Synthetic request timed out while inference could continue.")
            if mode == "incomplete":
                return {"done": False, "message": {"content": ""}}
            case = benchmark.CASES[(len(calls) - 1) % len(benchmark.CASES)]
            content = "invalid JSON" if mode == "bad_completed_json" else json.dumps({"memories": case["expected"]})
            return {"done": True, "message": {"content": content}, "prompt_eval_count": 10, "eval_count": 5}

        exit_code = 0
        with patch.object(benchmark, "request", side_effect=request), patch("sys.argv", [
                "benchmark_memory_models.py", "--models", "synthetic-a", "synthetic-b", "--output", str(output)]), \
                redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            try:
                benchmark.main()
            except SystemExit as error:
                exit_code = error.code
        return exit_code, calls, json.loads(output.read_text(encoding="utf-8"))

    def test_timeout_stops_even_if_loaded_model_metadata_is_empty(self):
        code, calls, report = self.run_probe("timeout")
        self.assertEqual(code, 1)
        self.assertEqual(calls, ["synthetic-a"])
        self.assertTrue(report["results"][0]["inference_uncertain"])
        self.assertIn("uncertain", report["stopped_reason"])

    def test_unconfirmed_completion_stops_before_the_next_case_or_model(self):
        code, calls, report = self.run_probe("incomplete")
        self.assertEqual(code, 1)
        self.assertEqual(calls, ["synthetic-a"])
        self.assertTrue(report["results"][0]["inference_uncertain"])

    def test_resident_model_after_completion_stops_before_another_call(self):
        code, calls, report = self.run_probe("busy_after_call")
        self.assertEqual(code, 1)
        self.assertEqual(calls, ["synthetic-a"])
        self.assertFalse(report["results"][0]["inference_uncertain"])
        self.assertEqual(report["loaded_after"], [{"name": "synthetic-resident"}])

    def test_confirmed_completion_with_invalid_json_can_continue_quality_checks(self):
        code, calls, report = self.run_probe("bad_completed_json")
        self.assertEqual(code, 0)
        self.assertEqual(calls, ["synthetic-a"] * 4 + ["synthetic-b"] * 4)
        self.assertTrue(all(not row["passed"] and not row["inference_uncertain"] for row in report["results"]))
        self.assertNotIn("stopped_reason", report)

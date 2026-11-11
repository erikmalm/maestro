"""No-inference checks for the isolated, seeded final-review trial."""
from contextlib import redirect_stderr, redirect_stdout
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import httpx

from scripts import check_research_final_review as runner


MODEL = "synthetic-reviewer:latest"
ARGS = ["--model", MODEL, "--base-url", "http://127.0.0.1:11434",
        "--context-tokens", "32768", "--output-tokens", "1024"]


class ResearchFinalReviewQualityTests(unittest.TestCase):
    def test_frozen_cases_cover_supported_and_seeded_defect_boundaries(self):
        cases = {case["id"]: case for case in runner.load_cases()}
        self.assertEqual(set(cases), {"supported_fact", "altered_figure", "wrong_period", "wrong_unit",
            "wrong_currency", "stale_current", "conflicting_sources", "missing_mandatory", "calculation_gap"})
        self.assertEqual([case["id"] for case in cases.values() if case["expected_acceptance"]], ["supported_fact"])
        for case in cases.values():
            self.assertTrue(all("example.org" in source["url"] for source in case["sources"]))
            self.assertEqual(len(runner.assessment(case)["items"]), len(case["items"]))
        wrong = cases["altered_figure"]
        self.assertIn("42", wrong["sources"][0]["quote"])
        self.assertIn("43", wrong["items"][0]["draft_text"])

    def trial(self, options=(), *, mode="correct"):
        scratch = Path(self.enterContext(tempfile.TemporaryDirectory(prefix="maestro-review-runner-test-")))
        report, ambient = scratch / "report.json", scratch / "never-open-ambient"
        selected_ids = [options[index + 1] for index, value in enumerate(options) if value == "--case"]
        cases = [case for case in runner.load_cases() if not selected_ids or case["id"] in selected_ids]
        observed = []

        def fake_transport(transport, request):
            if request.url.path == "/api/tags":
                body = {"models": [{"name": MODEL, "details": {"format": "gguf"}}]}
            elif request.url.path == "/api/show":
                body = {"details": {"format": "gguf"}, "capabilities": ["completion"]}
            elif request.url.path == "/api/ps":
                body = {"models": []}
            else:
                self.assertEqual(request.url.path, "/api/chat")
                payload = json.loads(request.content)
                prefix = runner.PREFIXES["reviewer"]
                selected = json.loads(next(message["content"][len(prefix):] for message in payload["messages"]
                                           if message["content"].startswith(prefix)))
                case = cases[len(observed)]
                observed.append(selected)
                # An altered figure reaches the actual review request verbatim.
                for item in case["items"]:
                    if item["draft_text"] and item["coverage"] == "covered":
                        self.assertIn(item["draft_text"], [row["text"] for row in selected["draft"]["items"]])
                if mode == "unknown":
                    raise httpx.ReadTimeout("Synthetic unknown completion", request=request)
                items = {row["item_id"]: {"outcome": "accepted" if row["kind"] != "gap" else "evidence_gap",
                                          "reason": "Frozen synthetic reviewer finding."}
                         for row in selected["draft"]["items"]}
                accepted = case["expected_acceptance"] or mode == "false_accept"
                if not accepted and all(row["outcome"] == "accepted" for row in items.values()):
                    items[next(iter(items))]["outcome"] = "writing_defect"
                raw = {"version": 1, "outcome": "accepted" if accepted else "partial",
                       "reason": "Frozen synthetic reviewer finding.", "items": items}
                body = {"done": True, "message": {"role": "assistant", "content": json.dumps(raw)},
                        "prompt_eval_count": 41, "eval_count": 7}
            return httpx.Response(200, json=body, request=request)

        async def fake_async_transport(transport, request):
            return fake_transport(transport, request)

        with (patch.object(httpx.HTTPTransport, "handle_request", new=fake_transport),
              patch.object(httpx.AsyncHTTPTransport, "handle_async_request", new=fake_async_transport),
              patch.dict(os.environ, {"MAESTRO_DATA_DIR": str(ambient), "OPENAI_API_KEY": "never-read-synthetic-key"}),
              redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO())):
            code = runner.main([*ARGS, *options, "--report", str(report)])
        self.assertFalse(ambient.exists())
        return code, json.loads(report.read_text(encoding="utf-8")), observed

    def test_all_cases_use_owned_api_and_one_actual_transport_call_with_honest_usage(self):
        code, report, observed = self.trial()
        self.assertEqual(code, 0, report)
        self.assertTrue(report["passed"])
        self.assertEqual(len(observed), 9)
        self.assertIn("not an all-real pipeline", report["validation_scope"])
        self.assertEqual((report["false_acceptances"], report["false_rejections"], report["invalid_reviews"]), (0, 0, 0))
        for result in report["results"]:
            self.assertEqual(result["synthetic_upstream_stages"], ["requirements", "evidence_review", "writer"])
            self.assertEqual(result["network_reviewer_requests"], 1)
            self.assertEqual(result["consumed"]["model_calls"], 4)
            self.assertEqual(result["consumed"]["tokens"], 48)
            self.assertFalse(any(result["reserved"].values()))

    def test_false_acceptance_is_reported_instead_of_regrading_the_seed(self):
        code, report, observed = self.trial(["--case", "altered_figure"], mode="false_accept")
        self.assertEqual(code, 1)
        self.assertEqual(report["false_acceptances"], 1)
        self.assertFalse(report["passed"])
        self.assertEqual(report["invalid_reviews"], 0)
        self.assertEqual(observed[0]["draft"]["items"][0]["text"], "Acme revenue for FY2025 was USD 43 million.")

    def test_unknown_completion_stops_before_another_case_and_preserves_reservations(self):
        code, report, observed = self.trial(mode="unknown")
        self.assertEqual(code, 2)
        self.assertEqual(len(observed), 1)
        self.assertEqual(len(report["results"]), 1)
        self.assertTrue(report["recovery_required"])
        self.assertTrue(any(report["results"][0]["reserved"].values()))
        self.assertEqual(report["false_rejections"], 1)
        self.assertEqual(report["invalid_reviews"], 1)

    def test_help_invalid_endpoints_and_existing_report_do_not_open_ambient_storage(self):
        with tempfile.TemporaryDirectory(prefix="maestro-review-cli-test-") as temporary:
            scratch = Path(temporary)
            ambient, existing = scratch / "never-open", scratch / "existing.json"
            existing.write_text("preserve", encoding="utf-8")
            for options, expected in ((["--help"], 0),
                    ([*ARGS, "--base-url", "http://localhost:11434"], 2),
                    ([*ARGS, "--base-url", "https://127.0.0.1:11434"], 2),
                    ([*ARGS, "--report", str(existing)], 2),
                    ([*ARGS, "--case", "supported_fact", "--case", "supported_fact"], 2)):
                result = subprocess.run([sys.executable, "-B", str(runner.ROOT / "scripts" / "check_research_final_review.py"), *options],
                    cwd=runner.ROOT, env={**os.environ, "MAESTRO_DATA_DIR": str(ambient)}, capture_output=True,
                    text=True, encoding="utf-8", timeout=30)
                self.assertEqual(result.returncode, expected, result.stderr)
                self.assertFalse(ambient.exists())
            self.assertEqual(existing.read_text(encoding="utf-8"), "preserve")


if __name__ == "__main__":
    unittest.main()

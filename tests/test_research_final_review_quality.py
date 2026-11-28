"""No-inference checks for the isolated, seeded final-review trial."""
from contextlib import redirect_stderr, redirect_stdout
from copy import deepcopy
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
from backend.research_final_review import review_action


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
                items = {row["item_id"]: {"outcome": case["expected_item_outcomes"][row["item_id"]],
                                          "reason": "Frozen synthetic reviewer finding."}
                         for row in selected["draft"]["items"]}
                accepted = (case["expected_acceptance"] or mode == "false_accept") and mode != "false_reject"
                if mode == "false_accept":
                    for item in items.values():
                        item["outcome"] = "accepted"
                if not accepted and all(row["outcome"] == "accepted" for row in items.values()):
                    items[next(iter(items))]["outcome"] = "writing_defect"
                if mode == "wrong_category":
                    items[next(iter(items))]["outcome"] = "evidence_gap"
                if mode == "mixed_findings":
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

    def test_valid_semantic_rejection_of_supported_fact_counts_as_false_rejection(self):
        code, report, observed = self.trial(["--case", "supported_fact"], mode="false_reject")
        self.assertEqual(code, 1)
        self.assertFalse(report["passed"])
        self.assertEqual((report["false_acceptances"], report["false_rejections"], report["invalid_reviews"]), (0, 1, 0))
        self.assertTrue(report["results"][0]["valid_semantic_review"])
        self.assertEqual(report["results"][0]["final_review"]["items"][0]["outcome"], "writing_defect")
        self.assertEqual(observed[0]["draft"]["items"][0]["text"], "Acme revenue for FY2025 was USD 42 million.")

    def test_wrong_unit_rejection_with_wrong_category_is_reported_as_triage_miss(self):
        code, report, observed = self.trial(["--case", "wrong_unit"], mode="wrong_category")
        self.assertEqual(code, 1, report)
        result = report["results"][0]
        self.assertTrue(result["valid_semantic_review"])
        self.assertFalse(result["false_acceptance"])
        self.assertFalse(result["triage_ok"])
        self.assertEqual(result["observed_item_outcomes"], {"i1": "evidence_gap"})
        self.assertEqual(result["review_action"]["action"], "stop")
        self.assertEqual(observed[0]["draft"]["items"][0]["text"], "Acme revenue for FY2025 was USD 42 billion.")

    def test_calculation_stop_has_priority_and_mixed_findings_do_not_choose_an_action(self):
        code, report, _ = self.trial(["--case", "calculation_gap"])
        self.assertEqual(code, 0, report)
        self.assertEqual(report["results"][0]["review_action"],
                         {"action": "stop", "reason": "calculation_required", "item_ids": ["i2"]})
        code, report, _ = self.trial(["--case", "missing_mandatory"], mode="mixed_findings")
        self.assertEqual(code, 1, report)
        self.assertEqual(report["results"][0]["review_action"],
                         {"action": "stop", "reason": "mixed_or_unresolved_findings", "item_ids": ["i1", "i2"]})

    def test_action_validation_preserves_exact_bindings_and_legacy_unannotated_grading(self):
        code, report, _ = self.trial(["--case", "supported_fact"])
        self.assertEqual(code, 0, report)
        run = report["results"][0]
        changed = deepcopy(run["final_review"])
        changed["draft_hash"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "final research review"):
            review_action(changed, run["assessment"], run["evidence_review"], run["draft"])
        legacy = next(case for case in runner.load_cases() if case["id"] == "supported_fact")
        legacy = {key: value for key, value in legacy.items() if key not in ("expected_item_outcomes", "expected_action")}
        # The normal API path already validated this synthetic packet above.
        with patch.object(runner, "validate_packet"):
            result = runner.grade(legacy, {**run, "result": None, "budget": {"consumed": run["consumed"], "reserved": run["reserved"]}},
                                  real_calls=1, seeded_stages=run["synthetic_upstream_stages"], loader=None)
        self.assertTrue(result["passed"])

    def test_unknown_completion_stops_before_another_case_and_preserves_reservations(self):
        code, report, observed = self.trial(mode="unknown")
        self.assertEqual(code, 2)
        self.assertEqual(len(observed), 1)
        self.assertEqual(len(report["results"]), 1)
        self.assertTrue(report["recovery_required"])
        self.assertTrue(any(report["results"][0]["reserved"].values()))
        self.assertEqual(report["false_acceptances"], 0)
        self.assertEqual(report["false_rejections"], 0)
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

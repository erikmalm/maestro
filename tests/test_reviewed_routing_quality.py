"""Runner and grading checks with fake local transports, never model inference."""
from contextlib import redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit

import httpx
from fastapi.testclient import TestClient

from backend.chat_routing import SCHEMA
from scripts import check_research_requirements as runner
from tests.research_requirements_quality import load_cases, load_routing_cases, routing_failures
from tests.test_research_requirements_quality import canonical


MODEL = "synthetic-routing:latest"


def assessment(case):
    value = canonical(case)
    for item, expected in zip(value["items"], case["items"]):
        item["freshness"] = expected.get("freshness")
    return value


def raw(case):
    if case.get("route") == "direct":
        return {"route": "direct", "assessment": None}
    value = assessment(case)
    value.pop("completion_criterion")
    for item in value["items"]:
        item.pop("mandatory")
    for question in value["clarifications"]:
        question.pop("question")
    return {"route": case["route"], "assessment": value} if "route" in case else value


def recorded(case):
    return {"chat_route": {"decision": case["route"]},
            "state": {"direct": "answered", "research": "partial", "clarify": "clarification"}[case["route"]],
            "assessment": None if case["route"] == "direct" else assessment(case),
            "permissions": {"hosted_allowed": False, "memory_recall": False,
                            "memory_derived": case.get("memory_derived", False)}}


class ReviewedRoutingQualityTests(unittest.TestCase):
    def test_frozen_routes_and_failure_categories_do_not_grade_synthetic_prose(self):
        cases = load_routing_cases()
        self.assertEqual(len(cases), 8)
        for case in cases:
            with self.subTest(case=case["id"]):
                self.assertEqual(routing_failures(case, recorded(case)), [])
        for route in ("direct", "research", "clarify"):
            case = next(case for case in cases if case["route"] != route)
            value = recorded(case)
            value["chat_route"]["decision"] = route
            self.assertIn("false_" + route, routing_failures(case, value))
        case = cases[-1]
        value = recorded(case)
        value["chat_route"]["decision"] = None
        self.assertEqual(routing_failures(case, value), ["invalid_schema"])
        restricted = next(case for case in cases if case["id"] == "historical_finance")
        value = recorded(restricted)
        value["assessment"]["constraints"].clear()
        self.assertIn("restriction_error", routing_failures(restricted, value))
        value = recorded(case)
        value["permissions"]["hosted_allowed"] = True
        self.assertIn("restriction_error", routing_failures(case, value))
        value = recorded(case)
        value["assessment"]["items"][0]["freshness"] = None
        self.assertIn("assessment_error", routing_failures(case, value))

    def test_transport_guard_checks_origin_method_model_schema_credentials_and_one_attempt(self):
        body = {"model": MODEL, "format": SCHEMA, "stream": False, "messages": []}
        target = urlsplit("http://127.0.0.1:11434")
        for changes in ({"url": "https://example.org/api/chat"}, {"method": "GET"},
                        {"url": "http://127.0.0.1:11434/api/chat?extra=1"},
                        {"headers": {"Authorization": "synthetic"}},
                        {"json": {**body, "model": "other"}},
                        {"json": {**body, "format": {}}},
                        {"json": {**body, "messages": [{"content": runner.CANARY}]}},
                        {"json": {**body, "tools": [{"type": "function"}]}}):
            with self.subTest(changes=changes):
                values = {"method": "POST", "url": "http://127.0.0.1:11434/api/chat", "json": body, **changes}
                counts = {"actual": 0}
                with self.assertRaises((AssertionError, ValueError)):
                    runner.allowed_request(httpx.Request(**values), target, MODEL, SCHEMA, counts)
                self.assertEqual(counts["actual"], 0)
        counts = {"actual": 0}
        request = httpx.Request("POST", "http://127.0.0.1:11434/api/chat", json=body)
        runner.allowed_request(request, target, MODEL, SCHEMA, counts)
        self.assertEqual(counts["actual"], 1)
        with self.assertRaisesRegex(AssertionError, "one inference"):
            runner.allowed_request(request, target, MODEL, SCHEMA, counts)

    def run_fake(self, directory, cases, *, routing=True, unknown=False, read_failure=False, invalid=False, context_tokens=None):
        payloads = []

        def response(request):
            if request.url.path == "/api/tags":
                data = {"models": [{"name": MODEL, "details": {"format": "gguf"}}]}
            elif request.url.path == "/api/show":
                data = {"details": {"format": "gguf"}, "capabilities": ["completion"]}
            else:
                payloads.append(json.loads(request.content))
                if unknown:
                    raise httpx.ReadTimeout("Synthetic unknown completion")
                value = {"route": "invalid", "assessment": None} if invalid else raw(cases[len(payloads) - 1])
                data = {"done": True, "message": {"content": json.dumps(value)},
                        "prompt_eval_count": 101, "eval_count": 17}
            return httpx.Response(200, json=data, request=request)

        def sync(transport, request):
            return response(request)

        async def asynchronous(transport, request):
            return response(request)

        original_get = TestClient.get

        def get(client, url, *args, **kwargs):
            if read_failure and url.startswith("/api/research/runs/"):
                raise OSError("Synthetic read failure after dispatch")
            return original_get(client, url, *args, **kwargs)

        arguments = ["quality", "--model", MODEL, *(["--routing"] if routing else [])]
        if context_tokens is not None:
            arguments.extend(["--context-tokens", str(context_tokens)])
        for case in cases:
            arguments.extend(["--case", case["id"]])
        with patch("sys.argv", arguments), patch.object(runner.tempfile, "mkdtemp", return_value=directory), \
                patch.object(httpx.HTTPTransport, "handle_request", new=sync), \
                patch.object(httpx.AsyncHTTPTransport, "handle_async_request", new=asynchronous), \
                patch.object(TestClient, "get", new=get), redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            code = runner.main()
        report = json.loads((Path(directory) / "report.json").read_text(encoding="utf-8"))
        return code, report, payloads

    def test_all_eight_use_one_fake_route_each_with_only_two_synthetic_direct_continuations(self):
        with tempfile.TemporaryDirectory(prefix="maestro-requirements-quality-") as directory:
            code, report, payloads = self.run_fake(directory, load_routing_cases())
            self.assertEqual(code, 0, report)
            self.assertTrue(report["passed"])
            self.assertEqual(report["measurement"], "routing_only")
            self.assertEqual(report["fixture_reference_date"], "2026-11-28")
            self.assertEqual(len(payloads), 8)
            self.assertNotIn(runner.CANARY, json.dumps(payloads))
            self.assertNotIn("USD 42 million", json.dumps(payloads))
            self.assertEqual(sum(row["synthetic_inference_calls"] for row in report["results"]), 2)
            self.assertTrue(all(row["actual_inference_calls"] == 1 and row["actual_tokens"] == {
                "prompt_eval_count": 101, "eval_count": 17} for row in report["results"]))
            self.assertTrue(all(isinstance(row["actual_seconds"], (int, float)) and row["actual_seconds"] >= 0
                                for row in report["results"]))
            self.assertTrue((Path(report["workspace"]) / "private" / "workspace.sqlite3").exists())

    def test_unknown_stops_remaining_cases_and_retains_raw_reservations(self):
        with tempfile.TemporaryDirectory(prefix="maestro-requirements-quality-") as directory:
            code, report, payloads = self.run_fake(directory, load_routing_cases(), unknown=True)
            self.assertEqual((code, len(payloads), len(report["results"])), (2, 1, 1))
            self.assertTrue(report["recovery_required"])
            self.assertEqual(report["results"][0]["failures"], ["unknown_completion"])
            self.assertTrue(any(report["results"][0]["reserved"].values()))
            self.assertTrue(any(row["status"] == "reserved" for row in report["provider_accounting"]))
            self.assertTrue(Path(directory).exists())

    def test_read_exception_preserves_unverifiable_case_and_accounting_without_retry(self):
        with tempfile.TemporaryDirectory(prefix="maestro-requirements-quality-") as directory:
            code, report, payloads = self.run_fake(directory, load_routing_cases(), read_failure=True)
            self.assertEqual((code, len(payloads)), (2, 1))
            self.assertTrue(report["recovery_required"])
            self.assertIn("Synthetic read failure", report["exception"])
            self.assertEqual(report["incomplete_case"]["transport_counts"]["actual"], 1)
            self.assertEqual(len(report["provider_accounting"]), 2)

    def test_invalid_schema_is_a_measured_failure_without_claiming_unknown_completion(self):
        with tempfile.TemporaryDirectory(prefix="maestro-requirements-quality-") as directory:
            code, report, payloads = self.run_fake(directory, load_routing_cases()[:1], invalid=True)
            self.assertEqual((code, len(payloads)), (1, 1))
            self.assertFalse(report["recovery_required"])
            self.assertIn("invalid_schema", report["results"][0]["failures"])

    def test_original_requirements_mode_still_grades_one_owned_call(self):
        with tempfile.TemporaryDirectory(prefix="maestro-requirements-quality-") as directory:
            code, report, payloads = self.run_fake(directory, load_cases()[:1], routing=False)
            self.assertEqual(code, 0, report)
            self.assertEqual(report["measurement"], "requirements")
            self.assertEqual(len(payloads), 1)

    def test_insufficient_context_is_not_misreported_as_a_measured_model_schema_error(self):
        with tempfile.TemporaryDirectory(prefix="maestro-requirements-quality-") as directory:
            code, report, payloads = self.run_fake(directory, load_routing_cases()[:1], context_tokens=16384)
            self.assertEqual((code, len(payloads)), (1, 0))
            self.assertEqual(report["results"][0]["failures"], ["route_not_measured"])
            self.assertFalse(report["recovery_required"])

    def test_existing_report_is_never_overwritten(self):
        with tempfile.TemporaryDirectory(prefix="maestro-quality-report-") as directory:
            report = Path(directory) / "report.json"
            report.write_text("preserve this report", encoding="utf-8")
            with patch("sys.argv", ["quality", "--routing", "--model", MODEL, "--report", str(report)]), \
                    redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                runner.main()
            self.assertEqual(report.read_text(encoding="utf-8"), "preserve this report")


if __name__ == "__main__":
    unittest.main()

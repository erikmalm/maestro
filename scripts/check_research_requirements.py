"""Opt-in local-model quality checks using retained, synthetic workspaces.

This makes real local inference calls. It never reads an existing Maestro
workspace or credentials and never requests hosted search. Normal unit tests do
not invoke it. See tests/fixtures/research_requirements_quality.json.
"""
import argparse
from contextlib import ExitStack
import json
import os
from pathlib import Path
import sys
import tempfile
import time
from unittest.mock import patch
from urllib.parse import urlsplit
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import httpx
from fastapi.testclient import TestClient

from backend import credentials
from backend import provider as provider_module
from backend.chat_routing import DIRECT_SCHEMA, SCHEMA as ROUTING_SCHEMA
from backend.memory import MemoryStore
from backend.provider import DEFAULT, Provider, validate_ollama_url
from backend.research_requirements import SCHEMA as REQUIREMENTS_SCHEMA
from backend.web_search import WebSearch
from tests.research_requirements_quality import check_assessment, load_cases, load_routing_cases, routing_failures

CANARY = "PRIVATE_ROUTING_CANARY_38"


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def allowed_request(request, target, model, schema, counts):
    """Fail closed before any real transport, including an accidental second chat."""
    require((request.url.scheme, request.url.host, request.url.port or 80) == (
        target.scheme, target.hostname, target.port or 80), "Unexpected network destination")
    require(not request.url.query and not request.url.username and not request.url.password,
            "Unexpected URL parameters or credentials")
    require(not any(name in request.headers for name in ("authorization", "cookie", "proxy-authorization")),
            "Unexpected network credentials")
    require((request.method, request.url.path) in (("GET", "/api/tags"), ("POST", "/api/show"),
                                                 ("POST", "/api/chat")), "Unexpected model operation")
    if request.method == "POST":
        body = json.loads(request.content)
        require(body.get("model") == model, "Unexpected model")
        if request.url.path == "/api/chat":
            require(counts is not None and counts["actual"] == 0, "More than one inference per case")
            require(body.get("format") == schema and body.get("stream") is False and not body.get("tools"),
                    "Unexpected inference schema or tools")
            require(CANARY not in request.content.decode("utf-8"), "Private history entered route inference")
            counts["actual"] += 1  # Count attempts before dispatch, including unknown completion.
        else:
            require(set(body) == {"model"}, "Unexpected metadata payload")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", required=True, help="An installed local Ollama model ID")
    parser.add_argument("--base-url", default="http://127.0.0.1:11434")
    parser.add_argument("--context-tokens", type=int, help="Default: 32768 for routing, 16384 for requirements")
    parser.add_argument("--output-tokens", type=int, default=1024)
    parser.add_argument("--routing", action="store_true", help="Measure eight routing cases; direct replies are synthetic")
    parser.add_argument("--case", action="append", dest="case_ids", help="Run only this fixture ID; repeatable")
    parser.add_argument("--report", type=Path, help="Save accepted synthetic assessments and measured results")
    args = parser.parse_args()
    if args.context_tokens is None:
        args.context_tokens = 32768 if args.routing else 16384
    endpoint = validate_ollama_url(args.base_url)
    target = urlsplit(endpoint)
    if target.scheme != "http" or target.hostname not in ("127.0.0.1", "::1"):
        parser.error("Quality checks require an explicit numeric HTTP loopback Ollama endpoint.")
    if not 1024 <= args.context_tokens <= 131072 or not 1 <= args.output_tokens <= 32768:
        parser.error("Use supported context and output limits.")
    if args.report and (args.report.exists() or not args.report.parent.is_dir()):
        parser.error("Report must be a new file in an existing directory; reports are never overwritten.")
    cases = load_routing_cases() if args.routing else load_cases()
    reference_date = next((case["reference_date"] for case in cases if "reference_date" in case), None)
    if args.case_ids:
        if set(args.case_ids) - {case["id"] for case in cases}:
            parser.error("An unknown case ID was requested.")
        cases = [case for case in cases if case["id"] in args.case_ids]

    original_sync = httpx.HTTPTransport.handle_request
    original_async = httpx.AsyncHTTPTransport.handle_async_request
    original_network = provider_module.background_network
    schema = ROUTING_SCHEMA if args.routing else REQUIREMENTS_SCHEMA
    counts = None

    def allowed(request):
        try:
            allowed_request(request, target, args.model, schema, counts)
        except Exception as error:
            if counts is not None:
                counts["restriction_errors"].append(str(error))
            raise

    def guarded_sync(transport, request):
        allowed(request)
        return original_sync(transport, request)

    async def guarded_async(transport, request):
        allowed(request)
        return await original_async(transport, request)

    def inference(config, path, payload, deadline):
        if args.routing and path == "/api/chat" and (payload or {}).get("format") == DIRECT_SCHEMA:
            require(counts is not None and counts["synthetic"] == 0, "Unexpected synthetic continuation")
            counts["synthetic"] += 1
            return {"done": True, "message": {"content": json.dumps({"reply": "Synthetic continuation; routing-only measurement."})},
                    "prompt_eval_count": 0, "eval_count": 0}
        started = time.monotonic()
        try:
            result = original_network(config, path, payload, deadline)
        finally:
            if path == "/api/chat" and counts["actual_seconds"] is None:
                counts["actual_seconds"] = round(time.monotonic() - started, 3)
        if path == "/api/chat":
            counts["actual_tokens"] = {key: result.get(key) for key in ("prompt_eval_count", "eval_count")}
        return result

    def forbidden(operation):
        if counts is not None:
            counts["restriction_errors"].append(operation)
        raise AssertionError(operation)

    results = []
    recovery_required = False
    data = Path(tempfile.mkdtemp(prefix="maestro-requirements-quality-")).resolve()
    require(data.parent == Path(tempfile.gettempdir()).resolve(), "Unexpected temporary directory")
    report_path = args.report or data / "report.json"
    print(json.dumps({"workspace": str(data), "report": str(report_path), "retained": True}), flush=True)
    report = {}
    # Create exclusively before any operation. Even setup/transport exceptions
    # retain both the workspace and a report; no unknown reservation is deleted.
    with report_path.open("x", encoding="utf-8") as report_file, ExitStack() as guards:
        private = data / "private"
        database = private / "workspace.sqlite3"
        try:
            private.mkdir()
            guards.enter_context(patch.dict(os.environ, {
                "MAESTRO_DATA_DIR": str(private), "MAESTRO_CONTEXT_ARCHIVE_DIR": str(data / "archive"),
            }))
            guards.enter_context(patch.object(credentials, "read", return_value=(None, "synthetic-quality")))
            guards.enter_context(patch.object(MemoryStore, "recall", side_effect=lambda *a, **kw: forbidden("Memory recall")))
            guards.enter_context(patch.object(WebSearch, "search", side_effect=lambda *a, **kw: forbidden("Hosted search")))
            guards.enter_context(patch.object(provider_module, "background_network", new=inference))
            guards.enter_context(patch.object(httpx.HTTPTransport, "handle_request", new=guarded_sync))
            guards.enter_context(patch.object(httpx.AsyncHTTPTransport, "handle_async_request", new=guarded_async))
            # app creates its data directory at import time. Import only after the
            # disposable environment and credential/network guards are established.
            from backend import app as backend
            guards.enter_context(patch.object(backend, "DATABASE", database))
            guards.enter_context(patch.object(backend, "DATA_DIR", private))
            if not args.routing:
                guards.enter_context(patch.object(backend, "search_context_candidates", side_effect=AssertionError("Evidence lookup")))
            with TestClient(backend.app) as client:
                headers = {"X-Maestro-CSRF": client.get("/api/session").json()["csrf"]}
                service = Provider(database, backend.TIMEZONE)
                service.configure({**DEFAULT, "protocol": "ollama", "base_url": endpoint,
                                   "model": args.model, "orchestrator_model": args.model,
                                   "chat_routing": "orchestrator", "ollama_context_tokens": args.context_tokens,
                                   "max_output_tokens": args.output_tokens}, "", False)
                status = service.test()
                if args.model not in status["models"]:
                    parser.error("The requested model is not installed locally; no inference was attempted.")
                with backend.workspace_transaction() as workspace:
                    workspace.setdefault("work_config", {}).update(memory_recall_count=0, memory_recall_characters=0)
                for case in cases:
                    counts = {"actual": 0, "synthetic": 0, "actual_seconds": None,
                              "actual_tokens": None, "restriction_errors": []}
                    started = time.monotonic()
                    body = {
                        "submission_id": uuid.uuid4().hex, "request": case["request"], "query": "",
                        "chat_id": None, "workflow": "requirements",
                    }
                    if args.routing:
                        chat = client.post("/api/chats", json={}, headers=headers)
                        chat.raise_for_status()
                        chat_id = chat.json()["active_chat_id"]
                        with backend.workspace_transaction() as workspace:
                            backend.selected_chat(workspace, chat_id)["messages"].append({
                                "id": uuid.uuid4().hex, "role": "assistant", "text": case.get("history", "") + CANARY,
                                "memory_ids": ["synthetic-private-memory"] if case.get("memory_derived") else [],
                            })
                        body = {"submission_id": body["submission_id"], "text": case["request"],
                                "chat_id": chat_id, "model": args.model}
                    response = client.post("/api/chat/reviewed" if args.routing else "/api/research/runs", json=body, headers=headers)
                    response.raise_for_status()
                    response = client.get("/api/research/runs/" + response.json()["run"]["id"])
                    response.raise_for_status()
                    run = response.json()
                    result = {"case": case["id"], "state": run["state"],
                              "seconds": round(time.monotonic() - started, 2),
                              "consumed": run["budget"]["consumed"], "reserved": run["budget"]["reserved"], "passed": False,
                              "assessment": run.get("assessment")}
                    if args.routing:
                        failures = routing_failures(case, run)
                        if (run.get("chat_route") or {}).get("decision") is None:
                            if not counts["actual"]:
                                failures = ["route_not_measured"]
                            elif any(run["budget"]["reserved"].values()):
                                failures = ["unknown_completion"]
                        if counts["restriction_errors"]:
                            failures.append("restriction_error")
                        result.update(measurement="routing_only", expected_route=case["route"],
                                      actual_route=(run.get("chat_route") or {}).get("decision"),
                                      actual_inference_calls=counts["actual"], synthetic_inference_calls=counts["synthetic"],
                                      actual_seconds=counts["actual_seconds"], actual_tokens=counts["actual_tokens"], failures=failures)
                    try:
                        if args.routing:
                            require(not result["failures"], ", ".join(result["failures"]))
                        else:
                            require(run["state"] in ("partial", "clarification"), "Unexpected terminal state")
                            check_assessment(case, run.get("assessment"))
                            require(run["state"] == ("clarification" if run["assessment"]["decision"] == "clarify" else "partial"), "Inconsistent assessment state")
                        require(counts["actual"] == 1, "Expected one actual inference")
                        require(not counts["restriction_errors"], "Unexpected restriction violation")
                        require(result["consumed"]["model_calls"] == 1 + counts["synthetic"], "Unexpected owned call count")
                        require(result["consumed"]["hosted_searches"] == 0, "Unexpected hosted usage")
                        require(all(value == 0 for value in run["budget"]["reserved"].values()), "Unreleased allowance")
                        result["passed"] = True
                    except AssertionError as error:
                        result["failure"] = str(error)
                    result["restriction_errors"] = counts["restriction_errors"]
                    results.append(result)
                    print(json.dumps({key: value for key, value in result.items() if key != "assessment"}), flush=True)
                    # Completion uncertainty requires operator recovery, never a
                    # second inference attempt or a new fixture request.
                    if (any(value for value in run["budget"]["reserved"].values())
                            or any(entry["status"] in ("reserved", "uncertain") for entry in service.read_state()["ledger"])):
                        recovery_required = True
                        break
        except Exception as error:
            # A read/setup failure cannot prove settlement. Preserve raw local
            # accounting and stop; never submit another case or retry inference.
            recovery_required = True
            report["exception"] = type(error).__name__ + ": " + str(error)
            if counts is not None:
                report["incomplete_case"] = {"case": case["id"], "transport_counts": counts}
        finally:
            if "service" in locals():
                try:
                    report["provider_accounting"] = service.read_state()["ledger"]
                    recovery_required |= any(entry["status"] in ("reserved", "uncertain")
                                             for entry in report["provider_accounting"])
                except Exception as error:
                    recovery_required = True
                    report["accounting_unavailable"] = type(error).__name__
            report.update({"model": args.model, "context_tokens": args.context_tokens,
              "output_tokens": args.output_tokens, "results": results,
              "passed": len(results) == len(cases) and all(result["passed"] for result in results),
              "measurement": "routing_only" if args.routing else "requirements",
              "fixture_reference_date": reference_date, "workspace": str(data),
              "recovery_required": recovery_required})
            report["passed"] &= not recovery_required
            report_file.write(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"passed": report["passed"], "cases_run": len(results), "cases_requested": len(cases),
                      "recovery_required": recovery_required}), flush=True)
    if recovery_required:
        print("Completion/accounting needs operator review. Retained workspace: " + str(data), file=sys.stderr)
        return 2
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

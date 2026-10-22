"""Opt-in local-model quality checks using disposable, synthetic workspaces.

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
from backend.memory import MemoryStore
from backend.provider import DEFAULT, Provider, validate_ollama_url
from backend.web_search import WebSearch
from tests.research_requirements_quality import check_assessment, load_cases


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", required=True, help="An installed local Ollama model ID")
    parser.add_argument("--base-url", default="http://127.0.0.1:11434")
    parser.add_argument("--context-tokens", type=int, default=16384)
    parser.add_argument("--output-tokens", type=int, default=1024)
    parser.add_argument("--case", action="append", dest="case_ids", help="Run only this fixture ID; repeatable")
    parser.add_argument("--report", type=Path, help="Save accepted synthetic assessments and measured results")
    args = parser.parse_args()
    endpoint = validate_ollama_url(args.base_url)
    target = urlsplit(endpoint)
    if target.scheme != "http" or target.hostname not in ("127.0.0.1", "::1"):
        parser.error("Quality checks require an explicit numeric HTTP loopback Ollama endpoint.")
    if not 1024 <= args.context_tokens <= 131072 or not 1 <= args.output_tokens <= 32768:
        parser.error("Use supported context and output limits.")
    cases = load_cases()
    if args.case_ids:
        if set(args.case_ids) - {case["id"] for case in cases}:
            parser.error("An unknown case ID was requested.")
        cases = [case for case in cases if case["id"] in args.case_ids]

    original_sync = httpx.HTTPTransport.handle_request
    original_async = httpx.AsyncHTTPTransport.handle_async_request

    def allowed(request):
        require((request.url.scheme, request.url.host, request.url.port or 80) == (
            target.scheme, target.hostname, target.port or 80), "Unexpected network destination")
        require(request.url.path in ("/api/tags", "/api/show", "/api/chat", "/api/ps"), "Unexpected model operation")

    def guarded_sync(transport, request):
        allowed(request)
        return original_sync(transport, request)

    async def guarded_async(transport, request):
        allowed(request)
        return await original_async(transport, request)

    results = []
    recovery_required = False
    with tempfile.TemporaryDirectory(prefix="maestro-requirements-quality-") as temporary, ExitStack() as guards:
        data = Path(temporary).resolve()
        # TemporaryDirectory's recursive cleanup may only target its own new
        # child of the system temporary directory, never an existing workspace.
        require(data.parent == Path(tempfile.gettempdir()).resolve(), "Unexpected temporary directory")
        private = data / "private"
        database = private / "workspace.sqlite3"
        guards.enter_context(patch.dict(os.environ, {
            "MAESTRO_DATA_DIR": str(private), "MAESTRO_CONTEXT_ARCHIVE_DIR": str(data / "archive"),
        }))
        guards.enter_context(patch.object(credentials, "read", return_value=(None, "synthetic-quality")))
        guards.enter_context(patch.object(MemoryStore, "recall", side_effect=AssertionError("Memory recall")))
        guards.enter_context(patch.object(WebSearch, "search", side_effect=AssertionError("Hosted search")))
        guards.enter_context(patch.object(httpx.HTTPTransport, "handle_request", new=guarded_sync))
        guards.enter_context(patch.object(httpx.AsyncHTTPTransport, "handle_async_request", new=guarded_async))
        # app creates its data directory at import time. Import only after the
        # disposable environment and credential/network guards are established.
        from backend import app as backend
        guards.enter_context(patch.object(backend, "DATABASE", database))
        guards.enter_context(patch.object(backend, "DATA_DIR", private))
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
            for case in cases:
                started = time.monotonic()
                response = client.post("/api/research/runs", json={
                    "submission_id": uuid.uuid4().hex, "request": case["request"], "query": "",
                    "chat_id": None, "workflow": "requirements",
                }, headers=headers)
                response.raise_for_status()
                response = client.get("/api/research/runs/" + response.json()["run"]["id"])
                response.raise_for_status()
                run = response.json()
                result = {"case": case["id"], "state": run["state"],
                          "seconds": round(time.monotonic() - started, 2),
                          "consumed": run["budget"]["consumed"], "reserved": run["budget"]["reserved"], "passed": False,
                          "assessment": run.get("assessment")}
                try:
                    require(run["state"] in ("partial", "clarification"), "Unexpected terminal state")
                    check_assessment(case, run.get("assessment"))
                    require(run["state"] == ("clarification" if run["assessment"]["decision"] == "clarify" else "partial"), "Inconsistent assessment state")
                    require(result["consumed"]["model_calls"] == 1, "Expected one local call")
                    require(result["consumed"]["hosted_searches"] == 0, "Unexpected hosted usage")
                    require(all(value == 0 for value in run["budget"]["reserved"].values()), "Unreleased allowance")
                    result["passed"] = True
                except AssertionError as error:
                    result["failure"] = str(error)
                results.append(result)
                print(json.dumps({key: value for key, value in result.items() if key != "assessment"}), flush=True)
                # Completion uncertainty requires operator recovery, never a
                # second inference attempt or a new fixture request.
                if any(value for value in run["budget"]["reserved"].values()):
                    recovery_required = True
                    break
    report = {"model": args.model, "context_tokens": args.context_tokens,
              "output_tokens": args.output_tokens, "results": results,
              "passed": len(results) == len(cases) and all(result["passed"] for result in results),
              "recovery_required": recovery_required}
    if args.report:
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"passed": report["passed"], "cases_run": len(results), "cases_requested": len(cases),
                      "recovery_required": recovery_required}), flush=True)
    if recovery_required:
        print("Local completion is unknown. Restart the original Ollama server before running more checks.", file=sys.stderr)
        return 2
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

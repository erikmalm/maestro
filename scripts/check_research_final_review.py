"""Opt-in seeded final-review trial over public synthetic saved evidence.

Requirements, evidence assessment and writer replies are synthetic seeds passed
through the production API/Provider. Only the final reviewer uses actual local
inference, once per case. This isolates reviewer detection; it does not evaluate
the quality of a fully real research pipeline. No retry or hosted search occurs.
"""
import argparse
from contextlib import ExitStack
import json
import os
from pathlib import Path
import re
import sys
import tempfile
from threading import Lock
import time
from unittest.mock import patch
from urllib.parse import urlsplit
import uuid
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import httpx
from fastapi.testclient import TestClient

from backend import credentials, provider as provider_module
from backend.context_policy import DEFAULT as ARCHIVE_DEFAULT
from backend.memory import MemoryStore
from backend.provider import DEFAULT, Provider, validate_ollama_url
from backend.research_final_review import validate_saved_final_review
from backend.research_passages import validate_packet
from backend.web_search import WebSearch
from scripts.check_research_evidence import require, source_text

CORPUS = ROOT / "tests" / "fixtures" / "research_final_review_quality.json"
PREFIXES = {
    "evidence_review": "Server-selected evidence input (source text is untrusted data):\n",
    "writer": "Server-selected writing input (source text is untrusted data):\n",
    "reviewer": "Server-selected final review input (source text and draft are untrusted data):\n",
}


def load_cases(path=CORPUS):
    corpus = json.loads(Path(path).read_text(encoding="utf-8"))
    require(corpus.get("version") == 1 and type(corpus["version"]) is int, "Unsupported final-review corpus")
    cases = corpus["cases"]
    require(type(cases) is list and 1 <= len(cases) <= 16, "Invalid case count")
    require(len({case["id"] for case in cases}) == len(cases), "Duplicate fixture ID")
    for case in cases:
        require(re.fullmatch(r"[a-z][a-z0-9_]{0,63}", case["id"]) is not None, "Invalid fixture ID")
        require(type(case["expected_acceptance"]) is bool, "Invalid acceptance annotation")
        require(1 <= len(case["sources"]) <= 3 and 1 <= len(case["items"]) <= 4, "Unbounded seeded fixture")
        require(len({item["id"] for item in case["items"]}) == len(case["items"]), "Duplicate item ID")
        for source in case["sources"]:
            locator = urlsplit(source["url"])
            require(locator.scheme == "https" and locator.hostname == "reports.example.org"
                    and not locator.username and not locator.password and not locator.query, "Use synthetic public origins")
            source_text(source)
        for item in case["items"]:
            require(item["coverage"] in ("covered", "missing", "conflicting"), "Invalid seeded coverage")
            require(all(type(index) is int and 0 <= index < len(case["sources"]) for index in item["sources"]),
                    "Invalid seed source index")
    return cases


def assessment(case):
    return {"version": 1, "decision": "ready",
            "subjects": [{"id": "s1", "kind": "company", "name": "Acme", "identifiers": []}],
            "items": [{"id": item["id"], "subject_ids": ["s1"], "criterion": item["criterion"],
                       "period": item["period"], "unit": item["unit"], "currency": item["currency"],
                       "freshness": None} for item in case["items"]], "constraints": [], "clarifications": []}


def seed_response(case, payload):
    """Seed upstream semantics; never alter actual reviewer input or output."""
    messages = payload.get("messages", [])
    for kind, prefix in PREFIXES.items():
        content = next((row["content"] for row in messages if row["content"].startswith(prefix)), None)
        if content is None:
            continue
        selected = json.loads(content[len(prefix):])
        if kind == "reviewer":
            return kind, None
        if kind == "writer":
            covered = {item["item_id"] for item in selected["coverage"]["items"] if item["state"] == "covered"}
            return kind, {"version": 2, "items": {item["id"]: {
                "kind": item.get("draft_kind", "fact"), "text": item["draft_text"],
                "support_indexes": [] if item.get("draft_kind") == "gap" else [0],
                "gap_reason": item.get("gap_reason")} for item in case["items"] if item["id"] in covered}}
        rows = {}
        for item in case["items"]:
            supports = []
            for index in item["sources"]:
                quote = case["sources"][index]["quote"]
                passage = next(part for part in selected["packet"]["passages"] if quote in part["text"])
                supports.append({"passage_id": passage["id"], "quote": quote,
                    "temporal_scope": item.get("temporal_scope", "historical"),
                    "period": item["period"], "observation_date": item.get("observation_date")})
            rows[item["id"]] = {"state": item["coverage"], "reason": "Frozen synthetic upstream assessment.", "supports": supports}
        return kind, {"version": 2, "items": rows}
    require("decision" in payload.get("format", {}).get("properties", {}), "Unexpected upstream stage")
    return "requirements", assessment(case)


def grade(case, run, *, real_calls, seeded_stages, loader):
    """Keep semantic detection, invalid review and false decisions separate."""
    final = run.get("final_review")
    valid = False
    try:
        validate_packet({key: run["evidence_review"][key] for key in
                         ("version", "offset_unit", "lookup", "selection", "sources", "passages")}, loader)
        validate_saved_final_review(final, run["assessment"], run["evidence_review"], run["draft"])
        valid = final["basis"] == "semantic"
    except (ValueError, KeyError, TypeError):
        pass
    accepted = run["state"] == "accepted"
    expected = case["expected_acceptance"]
    accounting = (real_calls == 1 and seeded_stages == ["requirements", "evidence_review", "writer"]
                  and run["budget"]["consumed"]["model_calls"] == 4
                  and run["budget"]["consumed"]["hosted_searches"] == 0
                  and not any(run["budget"]["reserved"].values()) and run["result"] is None)
    return {"passed": valid and accounting and accepted == expected,
            "valid_semantic_review": valid, "accounting_ok": accounting,
            "expected_acceptance": expected, "observed_acceptance": accepted,
            "false_acceptance": valid and accepted and not expected,
            "false_rejection": valid and expected and not accepted,
            "invalid_review": not valid}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", required=True)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--context-tokens", type=int, required=True)
    parser.add_argument("--output-tokens", type=int, required=True)
    parser.add_argument("--timezone", default="UTC")
    parser.add_argument("--case", action="append", dest="case_ids")
    parser.add_argument("--report", type=Path, help="Create a new synthetic-only JSON report; never overwrite")
    args = parser.parse_args(argv)
    try:
        endpoint = validate_ollama_url(args.base_url)
        target = urlsplit(endpoint)
        require(target.scheme == "http" and target.hostname in ("127.0.0.1", "::1"), "Invalid endpoint")
        zone = ZoneInfo(args.timezone)
    except (ValueError, AssertionError, ZoneInfoNotFoundError):
        parser.error("Use an explicit numeric HTTP loopback endpoint and valid timezone.")
    if (not re.fullmatch(r"[A-Za-z0-9_./:-]{1,200}", args.model)
            or not 1024 <= args.context_tokens <= 131072 or not 64 <= args.output_tokens <= 32768):
        parser.error("Use a bounded installed local model and explicit supported context/output limits.")
    if args.report and (args.report.exists() or not args.report.parent.is_dir()):
        parser.error("The report must be a new file in an existing directory.")
    cases = load_cases()
    if args.case_ids:
        if len(set(args.case_ids)) != len(args.case_ids) or set(args.case_ids) - {case["id"] for case in cases}:
            parser.error("Use distinct known fixture IDs.")
        cases = [case for case in cases if case["id"] in args.case_ids]
    original_sync, original_async = httpx.HTTPTransport.handle_request, httpx.AsyncHTTPTransport.handle_async_request
    real_network = provider_module.background_network
    active, lock = {"chats": 0}, Lock()

    def allowed(request):
        require((request.url.scheme, request.url.host, request.url.port or 80) ==
                (target.scheme, target.hostname, target.port or 80), "Unexpected network destination")
        methods = {"/api/tags": "GET", "/api/show": "POST", "/api/chat": "POST", "/api/ps": "GET"}
        require(request.url.path in methods and request.method == methods[request.url.path]
                and not request.url.query, "Unexpected local model operation")
        require(not any(request.headers.get(key) for key in ("Authorization", "Proxy-Authorization", "Cookie")),
                "Credentials must not be sent")
        if request.url.path == "/api/chat":
            with lock:
                require(active["chats"] == 0, "More than one actual inference in a case")
                active["chats"] += 1

    def guarded_sync(transport, request):
        allowed(request)
        return original_sync(transport, request)

    async def guarded_async(transport, request):
        allowed(request)
        return await original_async(transport, request)

    results, recovery_required = [], False
    with tempfile.TemporaryDirectory(prefix="maestro-final-review-quality-") as temporary, ExitStack() as guards:
        scratch = Path(temporary).resolve()
        require(scratch.parent == Path(tempfile.gettempdir()).resolve(), "Unexpected temporary directory")
        guards.enter_context(patch.dict(os.environ, {"MAESTRO_DATA_DIR": str(scratch / "bootstrap"),
            "MAESTRO_CONTEXT_ARCHIVE_DIR": str(scratch / "bootstrap-archive"), "MAESTRO_OLLAMA_URL": endpoint,
            "MAESTRO_TIMEZONE": args.timezone, "MAESTRO_PORT": "8765", "MAESTRO_API_KEY_FILE": "",
            "MAESTRO_SEARCH_KEY_FILE": "", "OPENAI_API_KEY": ""}))
        guards.enter_context(patch.object(credentials, "read", return_value=(None, "synthetic-quality")))
        guards.enter_context(patch.object(credentials, "save", side_effect=AssertionError("Credential writes")))
        guards.enter_context(patch.object(MemoryStore, "recall", side_effect=AssertionError("Memory recall")))
        guards.enter_context(patch.object(WebSearch, "search", side_effect=AssertionError("Hosted acquisition")))
        guards.enter_context(patch.object(httpx.HTTPTransport, "handle_request", new=guarded_sync))
        guards.enter_context(patch.object(httpx.AsyncHTTPTransport, "handle_async_request", new=guarded_async))
        # app creates its configured directory at import time; guard first.
        from backend import app as backend
        for case in cases:
            private, archive = scratch / case["id"] / "private", scratch / case["id"] / "archive"
            private.mkdir(parents=True)
            seeded = []

            def seeded_network(config, path, payload, deadline):
                if path != "/api/chat":
                    return real_network(config, path, payload, deadline)
                kind, raw = seed_response(case, payload)
                if kind == "reviewer":
                    require(seeded == ["requirements", "evidence_review", "writer"], "Reviewer ran before seeded stages")
                    return real_network(config, path, payload, deadline)
                require(len(seeded) < 3 and kind == ("requirements", "evidence_review", "writer")[len(seeded)],
                        "Unexpected seeded inference or retry")
                seeded.append(kind)
                return {"done": True, "message": {"role": "assistant", "content": json.dumps(raw)},
                        "prompt_eval_count": 0, "eval_count": 0}

            with ExitStack() as case_guards:
                case_guards.enter_context(patch.dict(os.environ, {"MAESTRO_DATA_DIR": str(private), "MAESTRO_CONTEXT_ARCHIVE_DIR": str(archive)}))
                for name, value in (("DATABASE", private / "workspace.sqlite3"), ("DATA_DIR", private), ("TIMEZONE", zone)):
                    case_guards.enter_context(patch.object(backend, name, value))
                case_guards.enter_context(patch.object(provider_module, "background_network", side_effect=seeded_network))
                with TestClient(backend.app) as client:
                    headers = {"X-Maestro-CSRF": client.get("/api/session").json()["csrf"]}
                    service = Provider(backend.DATABASE, zone)
                    service.configure({**DEFAULT, "protocol": "ollama", "base_url": endpoint, "model": args.model,
                        "orchestrator_model": args.model, "chat_routing": "orchestrator", "ollama_context_tokens": args.context_tokens,
                        "max_output_tokens": args.output_tokens}, "", False)
                    if args.model not in service.test()["models"]:
                        parser.error("The explicit model is not installed; no inference was attempted.")
                    client.put("/api/context", json={**ARCHIVE_DEFAULT, "enabled": True}, headers=headers).raise_for_status()
                    store = backend.context_store()
                    for source in case["sources"]:
                        captured = store.capture("Acme", source["retrieved_at"], [{"title": source["title"],
                            "url": source["url"], "content": source_text(source)}], 1)
                        require(len(captured["sources"]) == 1 and captured["sources"][0]["archive_status"] == "saved", "Seed source was not saved")
                    active["chats"] = 0
                    started = time.monotonic()
                    response = client.post("/api/research/runs", json={"submission_id": uuid.uuid4().hex,
                        "request": case["request"], "query": "", "chat_id": None, "workflow": "research_answer"}, headers=headers)
                    response.raise_for_status()
                    detail = client.get("/api/research/runs/" + response.json()["run"]["id"])
                    detail.raise_for_status()
                    run = detail.json()
                    result = {"case": case["id"], "state": run["state"], "seconds": round(time.monotonic() - started, 2),
                        "synthetic_upstream_stages": seeded, "network_reviewer_requests": active["chats"],
                        "consumed": run["budget"]["consumed"], "reserved": run["budget"]["reserved"],
                        "assessment": run.get("assessment"), "evidence_review": run.get("evidence_review"),
                        "draft": run.get("draft"), "final_review": run.get("final_review"),
                        **grade(case, run, real_calls=active["chats"], seeded_stages=seeded, loader=store.get_capture)}
                    results.append(result)
                    print(json.dumps({key: value for key, value in result.items() if key not in
                                      ("assessment", "evidence_review", "draft", "final_review")}), flush=True)
                    recovery_required = (any(run["budget"]["reserved"].values()) or any(
                        entry["status"] in ("reserved", "uncertain") for entry in service.read_state()["ledger"]))
            if recovery_required:
                break
    report = {"model": args.model, "base_url": endpoint, "context_tokens": args.context_tokens,
        "output_tokens": args.output_tokens, "timezone": args.timezone, "workflow": "research_answer",
        "reviewer_max_seconds": backend.FINAL_REVIEW_SECONDS,
        "validation_scope": "Seeded-review detection only: three synthetic upstream replies through the production owned API/Provider; "
                            "one actual local final-review inference per case. Synthetic upstream token usage is zero. "
                            "This is not an all-real pipeline evaluation or independent origin verification.",
        "cases_requested": len(cases), "cases_run": len(results),
        "actual_inference_requests": sum(row["network_reviewer_requests"] for row in results),
        "results": results, "false_acceptances": sum(row["false_acceptance"] for row in results),
        "false_rejections": sum(row["false_rejection"] for row in results), "invalid_reviews": sum(row["invalid_review"] for row in results),
        "passed": len(results) == len(cases) and all(row["passed"] for row in results), "recovery_required": recovery_required}
    if args.report:
        with args.report.open("x", encoding="utf-8") as stream:
            stream.write(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({key: value for key, value in report.items() if key not in ("results", "validation_scope")}), flush=True)
    if recovery_required:
        print("Local completion is unknown. Stop checks until the original server has been recovered; no retry was attempted.", file=sys.stderr)
        return 2
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

"""Opt-in real-local evidence quality checks using public synthetic saved sources.

Each case gets a disposable private workspace and sibling archive. No existing
workspace, configuration, credentials, memory or hosted search is used. A case
is attempted once, with at most two local inference requests. Unknown completion
stops the entire run and requires operator recovery of the original server.
"""
import argparse
from contextlib import ExitStack
from datetime import datetime, timezone
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

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import httpx
from fastapi.testclient import TestClient

from backend import credentials
from backend.context_policy import DEFAULT as ARCHIVE_DEFAULT, SOURCE_BYTES
from backend.memory import MemoryStore
from backend.provider import DEFAULT, Provider, validate_ollama_url
from backend.research_evidence import validate_saved_review
from backend.research_passages import validate_packet
from backend.research_requirements import validate_saved_assessment
from backend.web_search import WebSearch


CORPUS = ROOT / "tests" / "fixtures" / "research_evidence_quality.json"


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def source_text(source):
    padding = source.get("padding", {"text": "", "repeat": 0})
    require(type(padding["repeat"]) is int and 0 <= padding["repeat"] <= 100, "Invalid fixture padding")
    content = padding["text"] * padding["repeat"] + source["quote"]
    require(0 < len(content.encode("utf-8")) <= SOURCE_BYTES, "Fixture exceeds saved-source bound")
    return content


def load_cases(path=CORPUS):
    corpus = json.loads(Path(path).read_text(encoding="utf-8"))
    require(type(corpus.get("version")) is int and corpus["version"] == 1, "Unsupported evidence corpus version")
    cases = corpus["cases"]
    require(bool(cases) and len({case["id"] for case in cases}) == len(cases), "Fixture IDs must be unique")
    for case in cases:
        require(re.fullmatch(r"[a-z][a-z0-9_]{0,63}", case["id"]) is not None, "Invalid fixture ID")
        require(1 <= len(case["sources"]) <= 3, "Invalid fixture source count")
        for source in case["sources"]:
            require(urlsplit(source["url"]).hostname == "reports.example.org", "Only synthetic fixture origins are allowed")
            source_text(source)
    return cases


def check_qualifiers(case, assessment):
    """Allow explicit current scope in equivalent structured qualifier fields."""
    require(assessment["decision"] == "ready" and len(assessment["items"]) == 1, "Expected one ready mandatory item")
    item, expected = assessment["items"][0], case["expected"]
    names = {subject["id"]: subject["name"] for subject in assessment["subjects"]}
    require(any(names[identity] == "Acme" for identity in item["subject_ids"]), "Requested subject was not retained")
    require(expected["criterion"] in item["criterion"], "Requested metric was not retained")
    current = expected.get("current_qualifier")

    def has_current(value):
        return isinstance(value, str) and re.search(r"(?<!\w)" + re.escape(current) + r"(?!\w)", value) is not None

    for field in ("period", "currency", "unit"):
        wanted, actual = expected[field], item[field]
        if field == "period" and current and actual is not None:
            require(has_current(actual), "Requested qualifiers were changed")
            continue
        require(actual is None if wanted is None else isinstance(actual, str) and wanted in actual,
                "Requested qualifiers were changed")
    if current:
        preserved = has_current(item["period"]) or has_current(item["freshness"]) or any(
            constraint["kind"] == "freshness" and constraint["strength"] == "require"
            and (not constraint["item_ids"] or item["id"] in constraint["item_ids"])
            and has_current(constraint["criterion"]) for constraint in assessment["constraints"])
        require(preserved, "The explicit current qualifier was omitted")


def check_run(case, run, captured, loader):
    """Typed/pin checks plus narrow semantic expectations for this versioned corpus."""
    require(run["workflow"] == "evidence_review" and run["state"] == "partial", "Expected terminal partial evidence review")
    try:
        assessment = validate_saved_assessment(run.get("assessment"), case["request"])
        review = validate_saved_review(run.get("evidence_review"), assessment)
        packet = {key: review[key] for key in ("version", "offset_unit", "lookup", "selection", "sources", "passages")}
        validate_packet(packet, loader)
    except (ValueError, KeyError, TypeError):
        raise AssertionError("Missing or noncanonical assessment, review or saved-source pins") from None
    check_qualifiers(case, assessment)
    item, expected = assessment["items"][0], case["expected"]
    require(len(review["items"]) == 1 and review["items"][0]["item_id"] == item["id"], "Requirement coverage changed")
    checked = review["items"][0]
    require(checked["state"] in expected["states"], "Unexpected semantic evidence state")
    pins = {source["capture_id"]: source for source in captured}
    selected = {source["id"]: source for source in review["sources"]}
    for source in selected.values():
        original = pins.get(source["capture_id"])
        require(original is not None and all(source[key] == original[key] for key in ("content_hash", "manifest_hash")),
                "A selected source does not belong to this synthetic case")
    supports = checked["supports"]
    require(len({support["source_id"] for support in supports}) >= expected["minimum_sources"], "Too few distinct supporting sources")
    for fragments in expected["support_fragments"]:
        require(any(all(fragment in support["quote"] for fragment in fragments) for support in supports),
                "Expected period/currency/value support was omitted")
    for support in supports:
        source = selected[support["source_id"]]
        body = pins[source["capture_id"]]["content"]
        require(body[support["start"]:support["end"]] == support["quote"], "Support offsets do not reconstruct the saved quote")
    if expected["minimum_support_offset"]:
        require(any(support["start"] > expected["minimum_support_offset"] for support in supports), "Relevant saved tail was not used")
    if expected.get("old_observation"):
        require(any(expected["old_observation"] in passage["text"] for passage in review["passages"]), "Old observation was not inspected")
        require(checked["state"] != "covered", "Old observation was promoted to current coverage")
        require(all(support["suitability"] != "suitable" and support["best_before"] is None for support in supports),
                "Fresh origin verification was fabricated")
    consumed = run["budget"]["consumed"]
    require(consumed["model_calls"] == 2, "Expected exactly two local calls for the populated fixture")
    require(consumed["hosted_searches"] == 0, "Unexpected hosted usage")
    require(all(amount == 0 for amount in run["budget"]["reserved"].values()), "Unreleased allowance")
    require(run["result"] is None, "An ordinary answer or lookup result was produced")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", required=True, help="Explicit installed local model ID; no ambient model selection")
    parser.add_argument("--base-url", default="http://127.0.0.1:11434")
    parser.add_argument("--context-tokens", type=int, default=16384)
    parser.add_argument("--output-tokens", type=int, default=1024)
    parser.add_argument("--case", action="append", dest="case_ids", help="Run this fixture once; repeatable")
    parser.add_argument("--report", type=Path, help="Write a new JSON report containing only synthetic records")
    args = parser.parse_args(argv)
    try:
        endpoint = validate_ollama_url(args.base_url)
    except ValueError:
        parser.error("Use an explicit numeric HTTP loopback Ollama endpoint.")
    target = urlsplit(endpoint)
    if target.scheme != "http" or target.hostname not in ("127.0.0.1", "::1"):
        parser.error("Use an explicit numeric HTTP loopback Ollama endpoint.")
    if not re.fullmatch(r"[A-Za-z0-9_./:-]{1,200}", args.model):
        parser.error("Use a bounded local model ID.")
    if not 1024 <= args.context_tokens <= 131072 or not 64 <= args.output_tokens <= 32768:
        parser.error("Use supported context and output limits.")
    cases = load_cases()
    if args.case_ids:
        if set(args.case_ids) - {case["id"] for case in cases}:
            parser.error("An unknown fixture ID was requested.")
        cases = [case for case in cases if case["id"] in args.case_ids]
    original_sync, original_async = httpx.HTTPTransport.handle_request, httpx.AsyncHTTPTransport.handle_async_request
    active, lock = {"chats": 0}, Lock()

    def allowed(request):
        require((request.url.scheme, request.url.host, request.url.port or 80) ==
                (target.scheme, target.hostname, target.port or 80), "Unexpected network destination")
        operations = {"/api/tags": "GET", "/api/show": "POST", "/api/chat": "POST", "/api/ps": "GET"}
        require(request.url.path in operations and request.method == operations[request.url.path]
                and not request.url.query, "Unexpected local model operation")
        require(not any(request.headers.get(name) for name in ("Authorization", "Proxy-Authorization", "Cookie")),
                "Credentials must not be sent")
        if request.url.path == "/api/chat":
            with lock:
                require(active["chats"] < 2, "A fixture cannot make more than two inference requests")
                active["chats"] += 1

    def guarded_sync(transport, request):
        allowed(request)
        return original_sync(transport, request)

    async def guarded_async(transport, request):
        allowed(request)
        return await original_async(transport, request)

    results, recovery_required = [], False
    with tempfile.TemporaryDirectory(prefix="maestro-evidence-quality-") as temporary, ExitStack() as guards:
        scratch = Path(temporary).resolve()
        require(scratch.parent == Path(tempfile.gettempdir()).resolve(), "Unexpected temporary directory")
        guards.enter_context(patch.dict(os.environ, {
            "MAESTRO_DATA_DIR": str(scratch / "bootstrap"), "MAESTRO_CONTEXT_ARCHIVE_DIR": str(scratch / "bootstrap-archive"),
            "MAESTRO_OLLAMA_URL": endpoint, "MAESTRO_TIMEZONE": "UTC", "MAESTRO_PORT": "8765",
            "MAESTRO_API_KEY_FILE": "", "MAESTRO_SEARCH_KEY_FILE": "", "OPENAI_API_KEY": "",
        }))
        guards.enter_context(patch.object(credentials, "read", return_value=(None, "synthetic-quality")))
        guards.enter_context(patch.object(credentials, "save", side_effect=AssertionError("Credential writes")))
        guards.enter_context(patch.object(MemoryStore, "recall", side_effect=AssertionError("Memory recall")))
        guards.enter_context(patch.object(WebSearch, "search", side_effect=AssertionError("Hosted search")))
        guards.enter_context(patch.object(httpx.HTTPTransport, "handle_request", new=guarded_sync))
        guards.enter_context(patch.object(httpx.AsyncHTTPTransport, "handle_async_request", new=guarded_async))
        # app creates a directory at import time; establish every guard first.
        from backend import app as backend
        for case in cases:
            directory = scratch / case["id"]
            private, archive = directory / "private", directory / "archive"
            private.mkdir(parents=True)
            database = private / "workspace.sqlite3"
            with ExitStack() as case_guards:
                case_guards.enter_context(patch.dict(os.environ, {"MAESTRO_DATA_DIR": str(private), "MAESTRO_CONTEXT_ARCHIVE_DIR": str(archive)}))
                case_guards.enter_context(patch.object(backend, "DATABASE", database))
                case_guards.enter_context(patch.object(backend, "DATA_DIR", private))
                with TestClient(backend.app) as client:
                    headers = {"X-Maestro-CSRF": client.get("/api/session").json()["csrf"]}
                    service = Provider(database, backend.TIMEZONE)
                    service.configure({**DEFAULT, "protocol": "ollama", "base_url": endpoint,
                        "model": args.model, "orchestrator_model": args.model, "chat_routing": "orchestrator",
                        "ollama_context_tokens": args.context_tokens, "max_output_tokens": args.output_tokens}, "", False)
                    if args.model not in service.test()["models"]:
                        parser.error("The model is not installed locally; no inference was attempted.")
                    configured = client.put("/api/context", json={**ARCHIVE_DEFAULT, "enabled": True}, headers=headers)
                    configured.raise_for_status()
                    store, captured = backend.context_store(), []
                    for source in case["sources"]:
                        at = datetime.now(timezone.utc).isoformat() if source["retrieved_at"] == "runtime" else source["retrieved_at"]
                        receipt = store.capture("Acme", at, [{"title": source["title"], "url": source["url"], "content": source_text(source)}], 1)
                        require(len(receipt["sources"]) == 1 and receipt["sources"][0]["archive_status"] == "saved", "Synthetic source was not saved")
                        captured.append(receipt["sources"][0])
                    active["chats"] = 0
                    started = time.monotonic()
                    response = client.post("/api/research/runs", json={"submission_id": uuid.uuid4().hex,
                        "request": case["request"], "query": "", "chat_id": None, "workflow": "evidence_review"}, headers=headers)
                    response.raise_for_status()
                    detail = client.get("/api/research/runs/" + response.json()["run"]["id"])
                    detail.raise_for_status()
                    run = detail.json()
                    result = {"case": case["id"], "state": run["state"], "seconds": round(time.monotonic() - started, 2),
                        "consumed": run["budget"]["consumed"], "reserved": run["budget"]["reserved"],
                        "network_chat_requests": active["chats"], "assessment": run.get("assessment"),
                        "evidence_review": run.get("evidence_review"), "passed": False}
                    try:
                        check_run(case, run, captured, store.get_capture)
                        require(active["chats"] == 2, "Unexpected local inference request count")
                        result["passed"] = True
                    except AssertionError as error:
                        result["failure"] = str(error)
                    results.append(result)
                    print(json.dumps({key: value for key, value in result.items() if key not in ("assessment", "evidence_review")}), flush=True)
                    recovery_required = (any(run["budget"]["reserved"].values()) or
                                         any(entry["status"] in ("reserved", "uncertain") for entry in service.read_state()["ledger"]))
            if recovery_required:
                break
    report = {"model": args.model, "context_tokens": args.context_tokens, "output_tokens": args.output_tokens,
              "results": results, "passed": len(results) == len(cases) and all(result["passed"] for result in results),
              "recovery_required": recovery_required}
    if args.report:
        with args.report.open("x", encoding="utf-8") as stream:
            stream.write(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"passed": report["passed"], "cases_run": len(results), "cases_requested": len(cases),
                      "recovery_required": recovery_required}), flush=True)
    if recovery_required:
        print("Local completion is unknown. Restart the original Ollama server before further checks; no recovery or retry was attempted.", file=sys.stderr)
        return 2
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

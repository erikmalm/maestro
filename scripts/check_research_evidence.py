"""Opt-in real-local evidence quality checks using public synthetic saved sources.

Each case gets a disposable private workspace and sibling archive. No existing
workspace, configuration, credentials, memory or hosted search is used. A case
is attempted once, with at most two evidence calls or three draft calls. Unknown completion
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
import unicodedata
from unittest.mock import patch
from urllib.parse import urlsplit
import uuid
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import httpx
from fastapi.testclient import TestClient

from backend import credentials
from backend.context_policy import DEFAULT as ARCHIVE_DEFAULT, SOURCE_BYTES
from backend.memory import MemoryStore
from backend.provider import DEFAULT, Provider, validate_ollama_url
from backend.research_evidence import validate_saved_review
from backend.research_draft import has_support, validate_saved_draft
from backend.research_passages import validate_packet
from backend.research_requirements import validate_saved_assessment
from backend.web_search import WebSearch


CORPUS = ROOT / "tests" / "fixtures" / "research_evidence_quality.json"
PLAIN_NUMBER = r"[+-]?[0-9]+(?:\.[0-9]+)?"
# Arabic numeric separators stay attached across whitespace. ASCII punctuation
# needs adjacency here so an ordinary sentence ending ". 42" stays valid.
NUMBER_CANDIDATE = r"[+-]?(?:[0-9]+(?:\.[0-9]+)?|[.,][0-9]+|[\u066b\u066c]\s*[0-9]+)"
SCALES = ("thousand", "million", "billion", "trillion")
SCALE_CAPTURE = r"(?i:(" + "|".join(SCALES) + r")s?)"
MAX_CURRENCY_CODES = 32


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def unwrap_code_parentheses(text, currency):
    """Remove only balanced ASCII parentheses containing one code."""
    wrapped = r"\(\s*(" + currency + r")\s*\)"
    while True:
        unwrapped = re.sub(wrapped, r" \1 ", text)
        if unwrapped == text:
            return text
        text = unwrapped


def plain_numbers(text, currency_codes=()):
    """Conservative lexical checks for this ASCII-number fixture evaluation.

    Do not normalize typography into accepted evidence or discard signs/digits.
    Parenthesized FY qualifiers and ordinary sentence punctuation remain valid;
    Accounting amounts, currency glyphs, separated signs, format controls,
    named Unicode math signs and grouped/scientific forms are unsupported.
    """
    unsupported = "Unsupported numeric typography in fixture evaluation"
    numeric_boundaries = []
    for index, character in enumerate(text):
        category = unicodedata.category(character)
        # This view is used only to reject ambiguous numeric envelopes.
        # Original spelling remains authoritative for accepted numbers below.
        projected = unicodedata.normalize("NFKC", character)
        if category.startswith("M"):
            projected = "_"
        elif category == "Ps":
            projected = "("
        elif category == "Pe":
            projected = ")"
        elif any(part in unicodedata.name(character, "") for part in ("SLASH", "SOLIDUS", "DIVISION")):
            projected = "/"
        elif len(projected) != 1 or projected not in ".,/'_eE":
            projected = character
        numeric_boundaries.append(projected)
        require(category != "Cc" or character.isspace(), unsupported)
        require(not character.isnumeric() or character in "0123456789", unsupported)
        # This finite corpus uses currency codes. A glyph cannot acquire its
        # identity from a copied qualifier, even for an ambiguous dollar sign.
        require(category != "Sc", unsupported)
        if not character.isascii():
            name = unicodedata.name(character, "")
            require(category != "Cf" and "MINUS" not in name and "PLUS" not in name, unsupported)
            if category.startswith("M"):
                before, after = text[:index].rstrip(), text[index + 1:].lstrip()
                base = index - 1
                while base >= 0 and unicodedata.category(text[base]).startswith("M"):
                    base -= 1
                word_mark = base >= 0 and text[base].isalpha()
                require(not (before and before[-1] in "0123456789"
                             or after and after[0] in "0123456789" and not word_mark), unsupported)
            if category == "Pd":
                after = text[index + 1:].lstrip()
                require(not after or not after[0].isdigit(), unsupported)
    # Consume unsupported grouping/exponent spellings as a whole. A suffix
    # that also occurs in a copied reporting period cannot authorize it.
    for match in re.finditer(r"[0-9]([\s.,/'_\u2019\u066b\u066c]+)(?=[0-9])", "".join(numeric_boundaries)):
        # Only a single original ASCII decimal point can join digits. The
        # rejection view must not turn Unicode punctuation into a decimal.
        original = text[match.start(1):match.end(1)]
        decimal = match[1] == "." and original == "."
        # An immediate ASCII full stop followed only by whitespace remains a
        # sentence boundary. A detached, repeated or Unicode dot does not.
        sentence = original == match[1] and original.startswith(".") and original[1:].isspace()
        require(decimal or sentence, unsupported)
    # Include a trailing-point mantissa and separator clusters before the
    # exponent. Otherwise its two fragments can borrow an amount and year
    # that happen to be supported independently. Compatibility punctuation
    # and exponent letters are projected only in this rejection view.
    require(re.search(r"[0-9][\s.,/'_\u2019\u066b\u066c]*[eE]\s*[+-]?\s*[0-9]",
                      "".join(numeric_boundaries)) is None, unsupported)
    require(re.search(r"[+-]\s+(?=[.,\u066b\u066c]?[0-9])", text) is None, unsupported)
    # Repeated signs cannot borrow an otherwise supported signed suffix.
    require(re.search(r"[+-]\s*[+-]\s*(?=[.,\u066b\u066c]?[0-9])", text) is None, unsupported)
    # With an explicit sign, an ASCII separator and following whitespace are
    # ambiguous numeric notation rather than ordinary sentence punctuation.
    require(re.search(r"[+-]\s*[.,]\s+[0-9]", text) is None, unsupported)
    # A permitted code-only wrapper does not separate a sign from its amount.
    # Use this rejection-only view for all sign/accounting checks together;
    # Unicode amount brackets must not turn a negative into a positive either.
    envelope = unwrap_code_parentheses("".join(numeric_boundaries), r"(?ai:[A-Z]{3})")
    currency_label = r"[^\W\d_]{1,8}"
    accounting_code = r"(?:\(\s*)*[A-Z]{3}(?:\s*\))*"
    accounting_label = r"(?:(?:in\s+)?" + accounting_code + r"|(?:" + "|".join(SCALES) + r")s?)"
    # Reject an amount inside accounting parentheses in every token order,
    # including code-only wrappers inside the amount's outer parentheses.
    accounting = (r"\(\s*(?:" + accounting_label + r"\s*){0,2}" + PLAIN_NUMBER
                  + r"(?:\s*" + accounting_label + r"){0,2}\s*\)")
    require(re.search(accounting, envelope, re.IGNORECASE) is None
            and re.search(r"[+-]\s*" + currency_label + r"\s*[+-]?[0-9]", envelope) is None, unsupported)
    # The same finite units can appear before the number in any token order.
    # Use declared code variants here so ordinary prose such as "- was USD"
    # is not mistaken for two monetary labels preceding a value.
    currency = currency_pattern(currency_codes) if currency_codes else r"[A-Z]{3}"
    prefix_label = r"(?:" + SCALE_CAPTURE + r"|(?:(?i:in)\s+)?" + currency + r")"
    prefix = r"[+-]\s*(?:" + prefix_label + r"\s*){1,2}[+-]?[0-9]"
    require(re.search(prefix, envelope) is None, unsupported)
    # Both a scale and currency label can intervene before a postfix sign.
    postfix_label = r"(?:" + SCALE_CAPTURE + r"|(?:(?i:in)\s+)?" + currency_label + r")"
    postfix = r"[0-9](?:\s*" + postfix_label + r"\s*){0,2}[+-](?![0-9])"
    # If the units precede the value, no intervening label absorbs whitespace
    # before a trailing sign. Bind the whole numeric token (and finite unit
    # prefixes) so prose dashes after an embedded FY2025 qualifier stay valid.
    bare_postfix = (r"(?<!\w)(?:" + prefix_label + r"\s*){0,2}" + PLAIN_NUMBER
                    + r"\s+[+-](?![0-9])")
    require(re.search(postfix, envelope) is None and re.search(bare_postfix, envelope) is None, unsupported)
    candidates = re.findall(NUMBER_CANDIDATE, text)
    require(all(re.fullmatch(PLAIN_NUMBER, value) is not None for value in candidates), unsupported)
    return set(candidates)


def draft_currency_codes(expected):
    """Validate the fixture's recognition scope, not permission to use a unit."""
    codes = expected.get("draft_currency_codes")
    invalid = "Invalid or missing fixture draft currency vocabulary"
    require(type(codes) is list and 1 <= len(codes) <= MAX_CURRENCY_CODES
            and all(isinstance(code, str) and re.fullmatch(r"[A-Z]{3}", code) is not None for code in codes), invalid)
    require(len(set(codes)) == len(codes)
            and all(item["currency"] in codes for item in expected.get("draft_quantities", [])), invalid)
    return codes


def currency_pattern(currency_codes):
    """Declared case variants plus the existing uppercase-code guard."""
    # Keep the existing uppercase three-letter conflict check. Only declared
    # labels gain ASCII case variants, so ordinary prose such as "was" is not
    # a currency. Values stay unchanged; expected tuples remain authoritative.
    declared = r"(?ai:" + "|".join(re.escape(code) for code in currency_codes) + ")"
    return r"(?:[A-Z]{3}|" + declared + ")"


def currency_text(text, currency_codes):
    """Recognize balanced wrappers around a code, never around an amount."""
    # Each pass removes one pair of parentheses, including nested code-only
    # wrappers. Digits and their spelling are never changed. Accounting
    # parentheses around a value remain unsupported by plain_numbers.
    text = unwrap_code_parentheses(text, currency_pattern(currency_codes))
    # A known code or scale must not disappear from tuple extraction because
    # an unsupported wrapper separates it from its amount. Only balanced
    # ASCII code-only parentheses were accepted above. Refuse other brackets,
    # including Unicode or mixed wrappers, without accepting normalized units.
    currency = currency_pattern(currency_codes)
    unit = r"(?:" + currency + r"|(?i:(?:" + "|".join(SCALES) + r")s?))"
    opening = "".join(sorted({character for character in text if unicodedata.category(character) == "Ps"}))
    closing = "".join(sorted({character for character in text if unicodedata.category(character) == "Pe"}))
    malformed = []
    if opening:
        malformed.append("[" + re.escape(opening) + r"]\s*" + unit + r"(?!\w)")
    if closing:
        malformed.append(r"(?<!\w)" + unit + r"\s*[" + re.escape(closing) + "]")
    require(not malformed or re.search("|".join(malformed), text) is None,
            "Unsupported currency or scale wrapper in fixture evaluation")
    return text


def quantities(text, currency_codes):
    """Recognize all six finite currency/value/scale token orders."""
    currency = r"(?P<currency>" + currency_pattern(currency_codes) + ")"
    value = r"(?P<value>" + PLAIN_NUMBER + ")"
    scale = r"(?i:(?P<scale>" + "|".join(SCALES) + r")s?)"
    code_join = r"(?:\s+(?i:in)\s+|\s*)"
    patterns = (
        r"(?<!\w)" + currency + r"\s*" + value + r"\s*" + scale + r"\b",
        r"(?<![\w.+-])" + value + code_join + currency + r"\s*" + scale + r"\b",
        r"(?<![\w.+-])" + value + r"\s*" + scale + code_join + currency + r"(?!\w)",
        r"(?<!\w)" + currency + r"\s*" + scale + r"\s*" + value + r"(?!\w|\.[0-9])",
        r"(?<!\w)" + scale + code_join + currency + r"\s*" + value + r"(?!\w|\.[0-9])",
        r"(?<!\w)" + scale + r"\s*" + value + code_join + currency + r"(?!\w)",
    )
    text = currency_text(text, currency_codes)
    return {(match["value"], match["currency"].upper(), match["scale"].lower())
            for pattern in patterns for match in re.finditer(pattern, text)}


def currency_values(text, currency_codes):
    """Do not let unscaled explicit currencies inherit a copied row currency."""
    currency = currency_pattern(currency_codes)
    found = {(value, code) for value, code, _ in quantities(text, currency_codes)}
    text = currency_text(text, currency_codes)
    forward = r"(?<!\w)(" + currency + r")\s*(" + PLAIN_NUMBER + r")(?!\w|\.[0-9])"
    reverse = (r"(?<![\w.+-])(" + PLAIN_NUMBER + r")(?:\s+(?i:in)\s+|\s*)("
               + currency + r")(?!\w)")
    found.update((value, code.upper()) for code, value in re.findall(forward, text))
    found.update((value, code.upper()) for value, code in re.findall(reverse, text))
    return found


def check_draft_quantities(expected, row):
    annotations = expected.get("draft_quantities", [])
    if not annotations:
        return
    wanted = {(item["value"], item["currency"], item["scale"]) for item in annotations}
    codes = draft_currency_codes(expected)
    observed = quantities(row["text"], codes)
    # Derive units from the same complete tuples for every explicit ordering;
    # a currency between the value and scale cannot hide a scale change.
    amounts = {(value, scale) for value, _, scale in observed}
    amounts.update((value, scale.lower()) for value, scale in re.findall(
        r"(?<![\w.+-])(" + PLAIN_NUMBER + r")\s*" + SCALE_CAPTURE + r"\b", row["text"]))
    amounts.update((value, scale.lower()) for scale, value in re.findall(
        r"(?<!\w)" + SCALE_CAPTURE + r"\s*(" + PLAIN_NUMBER + r")(?!\w|\.[0-9])", row["text"]))
    # The canonical row also displays copied request qualifiers. An omitted
    # currency in prose can use that row's explicit currency; an explicit
    # conflicting currency in prose still fails below.
    qualified = {(value, currency, scale) for value, currency, scale in wanted
                 if (value, scale) in amounts and re.search(
                     r"(?<!\w)" + re.escape(currency) + r"(?!\w)", row["currency"] or "")}
    allowed = set(wanted)
    calculation = row["calculation"]
    if calculation and calculation["operation"] in ("sum", "difference"):
        # Only these operations preserve a common monetary unit. A server
        # result does not authorize arbitrary currency/scale changes.
        for _, currency, scale in wanted:
            if all((operand["value"], currency, scale) in wanted for operand in calculation["operands"]):
                allowed.add((calculation["result"], currency, scale))
    require(currency_values(row["text"], codes) <= {(value, currency) for value, currency, _ in allowed},
            "Draft introduced a quantity with an unsupported currency")
    require(wanted <= observed | qualified, "Draft omitted an expected fixture quantity or changed its currency/scale")
    require(observed <= allowed, "Draft introduced a quantity with an unsupported currency/scale")
    require(amounts <= {(value, scale) for value, _, scale in allowed},
            "Draft introduced a quantity with an unsupported scale")


def source_text(source):
    padding = source.get("padding", {"text": "", "repeat": 0})
    require(type(padding["repeat"]) is int and 0 <= padding["repeat"] <= 100, "Invalid fixture padding")
    content = padding["text"] * padding["repeat"] + source["quote"]
    require(0 < len(content.encode("utf-8")) <= SOURCE_BYTES, "Fixture exceeds saved-source bound")
    return content


def load_cases(path=CORPUS):
    corpus = json.loads(Path(path).read_text(encoding="utf-8"))
    require(type(corpus.get("version")) is int and corpus["version"] in (1, 2), "Unsupported evidence corpus version")
    cases = corpus["cases"]
    require(bool(cases) and len({case["id"] for case in cases}) == len(cases), "Fixture IDs must be unique")
    for case in cases:
        require(re.fullmatch(r"[a-z][a-z0-9_]{0,63}", case["id"]) is not None, "Invalid fixture ID")
        require(1 <= len(case["sources"]) <= 3, "Invalid fixture source count")
        require(corpus["version"] == 2 or "items" not in case["expected"], "Multiple expected items require corpus version 2")
        for expected in expected_items(case):
            annotations = expected.get("draft_quantities", [])
            require(type(annotations) is list and len(annotations) <= 16, "Invalid fixture draft quantities")
            for item in annotations:
                require(type(item) is dict and set(item) == {"value", "currency", "scale"}
                        and isinstance(item["value"], str) and re.fullmatch(PLAIN_NUMBER, item["value"]) is not None
                        and isinstance(item["currency"], str) and re.fullmatch(r"[A-Z]{3}", item["currency"]) is not None
                        and item["scale"] in SCALES, "Invalid fixture draft quantity")
            codes = draft_currency_codes(expected) if annotations or "draft_currency_codes" in expected else []
            for item in annotations:
                require(any((item["value"], item["currency"], item["scale"]) in quantities(source["quote"], codes)
                            for source in case["sources"]), "Fixture draft quantity lacks source support")
        for source in case["sources"]:
            require(urlsplit(source["url"]).hostname == "reports.example.org", "Only synthetic fixture origins are allowed")
            source_text(source)
    return cases


def expected_items(case):
    """Version 1's single expectation remains strict; version 2 can list items."""
    expected = case["expected"]
    items = expected.get("items", [expected])
    require(type(items) is list and 1 <= len(items) <= 16, "Invalid expected item count")
    require(len({item["criterion"] for item in items}) == len(items), "Expected metrics must remain distinct")
    return items


def check_qualifiers(case, assessment):
    """Allow explicit current scope in equivalent structured qualifier fields."""
    expectations = expected_items(case)
    require(assessment["decision"] == "ready" and len(assessment["items"]) == len(expectations),
            "Expected every ready mandatory item exactly once")
    names = {subject["id"]: subject["name"] for subject in assessment["subjects"]}
    matched, seen = [], set()
    for expected in expectations:
        candidates = [item for item in assessment["items"] if expected["criterion"] in item["criterion"]
                      and not any(other["criterion"] in item["criterion"]
                                  for other in expectations if other is not expected)]
        require(len(candidates) == 1 and candidates[0]["id"] not in seen, "Expected distinct requested metrics on separate item IDs")
        item = candidates[0]
        seen.add(item["id"])
        require(any(names[identity] == "Acme" for identity in item["subject_ids"]), "Requested subject was not retained")
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
        matched.append((expected, item))
    return matched


def check_run(case, run, captured, loader, *, workflow="evidence_review"):
    """Typed/pin checks plus narrow semantic expectations for this versioned corpus."""
    require(workflow in ("evidence_review", "research_draft") and run["workflow"] == workflow,
            "Unexpected research workflow")
    if workflow == "research_draft":
        require(type(run.get("draft")) is dict, "Missing or noncanonical draft")
    require(run["state"] == ("draft" if workflow == "research_draft" and run["draft"].get("state") == "unreviewed"
                              else "partial"), "Expected a terminal partial review or unreviewed draft")
    try:
        assessment = validate_saved_assessment(run.get("assessment"), case["request"])
        review = validate_saved_review(run.get("evidence_review"), assessment)
        packet = {key: review[key] for key in ("version", "offset_unit", "lookup", "selection", "sources", "passages")}
        validate_packet(packet, loader)
    except (ValueError, KeyError, TypeError):
        raise AssertionError("Missing or noncanonical assessment, review or saved-source pins") from None
    matched = check_qualifiers(case, assessment)
    reviewed = {item["item_id"]: item for item in review["items"]}
    require(len(review["items"]) == len(matched) and set(reviewed) == {item["id"] for _, item in matched}, "Requirement coverage changed")
    pins = {source["capture_id"]: source for source in captured}
    selected = {source["id"]: source for source in review["sources"]}
    for source in selected.values():
        original = pins.get(source["capture_id"])
        require(original is not None and all(source[key] == original[key] for key in ("content_hash", "manifest_hash")),
                "A selected source does not belong to this synthetic case")
    for expected, item in matched:
        checked = reviewed[item["id"]]
        require(checked["state"] in expected["states"], "Unexpected semantic evidence state")
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
    calls = 2
    if workflow == "research_draft":
        try:
            draft = validate_saved_draft(run.get("draft"), assessment, review)
        except (ValueError, TypeError, KeyError):
            raise AssertionError("Missing or noncanonical draft") from None
        supported = has_support(review)
        require(draft["state"] == ("unreviewed" if supported else "unavailable"),
                "A supported fixture did not produce an unreviewed draft")
        calls += int(supported)
        drafted = {row["item_id"]: row for row in draft["items"]}
        for expected, item in matched:
            row = drafted[item["id"]]
            require(row["coverage_state"] == reviewed[item["id"]]["state"], "Draft coverage changed")
            if row["coverage_state"] != "covered":
                require(row["kind"] == "gap" and not row["supports"], "Unsupported requirement became factual prose")
                continue
            require(row["kind"] != "gap", "Covered fixture was not drafted")
            visible = " ".join(str(value) for value in (row["text"], row["period"], row["unit"], row["currency"]) if value)
            for fragments in expected["support_fragments"]:
                require(all(fragment.casefold() in visible.casefold() for fragment in fragments),
                        "Draft omitted an expected fixture value or qualifier")
            codes = draft_currency_codes(expected) if expected.get("draft_quantities") or "draft_currency_codes" in expected else ()
            numbers = plain_numbers(row["text"], codes)
            # This is finite numeric provenance, not proof of prose truth.
            # Other packet rows or unrelated request text cannot authorize a
            # figure in this row. Canonical validation already pins citations
            # and copies this requirement's qualifiers from its assessment.
            provenance = " ".join([support["quote"] for support in row["supports"]] +
                                  [row[field] for field in ("period", "unit", "currency", "freshness") if row[field]])
            allowed = plain_numbers(provenance, codes)
            if row["calculation"]:
                allowed.add(row["calculation"]["result"])
            require(numbers <= allowed, "Draft introduced an unsupported numeric literal")
            check_draft_quantities(expected, row)
    require(consumed["model_calls"] == calls, "Unexpected local call count for the populated fixture")
    require(consumed["hosted_searches"] == 0, "Unexpected hosted usage")
    require(all(amount == 0 for amount in run["budget"]["reserved"].values()), "Unreleased allowance")
    require(run["result"] is None, "An ordinary answer or lookup result was produced")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", required=True, help="Explicit installed local model ID; no ambient model selection")
    parser.add_argument("--base-url", default="http://127.0.0.1:11434")
    parser.add_argument("--context-tokens", type=int, default=16384)
    parser.add_argument("--output-tokens", type=int, default=1024)
    parser.add_argument("--timezone", default="UTC", help="Explicit server timezone for this isolated trial")
    parser.add_argument("--workflow", choices=("evidence_review", "research_draft"), default="evidence_review",
                        help="Check coverage alone or add an unreviewed saved-evidence draft")
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
    try:
        ZoneInfo(args.timezone)
    except (ZoneInfoNotFoundError, ValueError):
        parser.error("Use a valid server timezone.")
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
                require(active["chats"] < (3 if args.workflow == "research_draft" else 2),
                        "A fixture exceeded its explicit inference request limit")
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
            "MAESTRO_OLLAMA_URL": endpoint, "MAESTRO_TIMEZONE": args.timezone, "MAESTRO_PORT": "8765",
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
                        "request": case["request"], "query": "", "chat_id": None, "workflow": args.workflow}, headers=headers)
                    response.raise_for_status()
                    detail = client.get("/api/research/runs/" + response.json()["run"]["id"])
                    detail.raise_for_status()
                    run = detail.json()
                    result = {"case": case["id"], "state": run["state"], "seconds": round(time.monotonic() - started, 2),
                        "consumed": run["budget"]["consumed"], "reserved": run["budget"]["reserved"],
                        "network_chat_requests": active["chats"], "assessment": run.get("assessment"),
                        "evidence_review": run.get("evidence_review"), "draft": run.get("draft"), "passed": False}
                    try:
                        check_run(case, run, captured, store.get_capture, workflow=args.workflow)
                        require(active["chats"] == run["budget"]["consumed"]["model_calls"], "Unexpected local inference request count")
                        result["passed"] = True
                    except AssertionError as error:
                        result["failure"] = str(error)
                    results.append(result)
                    print(json.dumps({key: value for key, value in result.items() if key not in ("assessment", "evidence_review", "draft")}), flush=True)
                    recovery_required = (any(run["budget"]["reserved"].values()) or
                                         any(entry["status"] in ("reserved", "uncertain") for entry in service.read_state()["ledger"]))
            if recovery_required:
                break
    report = {"model": args.model, "context_tokens": args.context_tokens, "output_tokens": args.output_tokens,
              "timezone": args.timezone, "workflow": args.workflow,
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

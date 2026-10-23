"""No-inference checks for the opt-in evidence runner and synthetic corpus."""
from copy import deepcopy
import hashlib
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from backend.research_evidence import validate_review
from backend.research_passages import select_packet
from backend.research_requirements import validate_assessment
from scripts.check_research_evidence import check_qualifiers, check_run, expected_items, load_cases, source_text


CHECKED = "2026-10-23T12:00:00+00:00"


def canonical(case, *, current_location="freshness"):
    expectations = expected_items(case)
    raw_assessment = {
        "version": 1, "decision": "ready", "subjects": [
            {"id": "s1", "kind": "company", "name": "Acme", "identifiers": []}],
        "items": [{"id": f"i{index}", "subject_ids": ["s1"], "criterion": expected["criterion"],
                   "period": expected["period"], "currency": expected["currency"], "unit": expected["unit"], "freshness": None}
                  for index, expected in enumerate(expectations, 1)],
        "constraints": [], "clarifications": [],
    }
    for item, expected in zip(raw_assessment["items"], expectations):
        current = expected.get("current_qualifier")
        if current and current_location in ("period", "freshness"):
            item[current_location] = current
        elif current and current_location == "constraint":
            raw_assessment["constraints"].append({"kind": "freshness", "strength": "require",
                                                   "item_ids": [], "criterion": current})
    assessment = validate_assessment(raw_assessment, case["request"])
    sources = []
    for number, fixture in enumerate(case["sources"], 1):
        body = source_text(fixture)
        sources.append({"capture_id": f"{number:032x}", "content_hash": hashlib.sha256(body.encode("utf-8")).hexdigest(),
            "manifest_hash": f"{number:064x}", "title": fixture["title"], "url": fixture["url"],
            "retrieved_at": CHECKED if fixture["retrieved_at"] == "runtime" else fixture["retrieved_at"],
            "published_at": None, "modified_at": None, "stale": fixture["retrieved_at"] != "runtime",
            "completeness": {"maestro_truncated": False, "full_page": False}, "content": body})
    packet = select_packet(sources, assessment, {"state": "ready", "search_limited": False, "skipped_count": 0})
    raw = {"version": 1, "items": []}
    for item, expected in zip(assessment["items"], expectations):
        supports = []
        for fixture in case["sources"]:
            quote = fixture["quote"]
            if not expected.get("old_observation") and not any(
                    all(fragment in quote for fragment in fragments) for fragments in expected["support_fragments"]):
                continue
            passage = next(passage for passage in packet["passages"] if quote in passage["text"])
            supports.append({"passage_id": passage["id"], "quote": quote,
                "temporal_scope": "historical" if expected["period"] else "current",
                "period": expected["period"], "observation_date": expected.get("old_observation")})
        raw["items"].append({"item_id": item["id"], "state": "covered" if expected.get("old_observation") else expected["states"][0],
                             "reason": "Synthetic public fixture assessment.", "supports": supports})
    review = validate_review(raw, assessment, packet, CHECKED)
    amounts = {"model_calls": 2, "tokens": 100, "seconds": 1, "estimated_model_usd": 0, "hosted_searches": 0}
    run = {"workflow": "evidence_review", "state": "partial", "request": case["request"], "assessment": assessment,
           "evidence_review": review, "result": None, "budget": {"consumed": amounts, "reserved": dict.fromkeys(amounts, 0)}}

    def loader(capture_id, content_hash, manifest_hash):
        source = next(source for source in sources if source["capture_id"] == capture_id)
        if (source["content_hash"], source["manifest_hash"]) != (content_hash, manifest_hash):
            raise KeyError("Changed fixture pins")
        return deepcopy(source)

    return run, sources, loader, raw, packet


class ResearchEvidenceQualityTests(unittest.TestCase):
    def setUp(self):
        self.cases = {case["id"]: case for case in load_cases()}

    def test_help_and_invalid_endpoint_never_touch_ambient_private_directory(self):
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(prefix="maestro-evidence-help-") as temporary:
            scratch = Path(temporary).resolve()
            self.assertEqual(scratch.parent, Path(tempfile.gettempdir()).resolve())
            ambient = scratch / "must-not-touch"
            for options in (["--help"],
                            ["--model", "synthetic:latest", "--base-url", "http://localhost:11434"],
                            ["--model", "synthetic:latest", "--timezone", "Not/A-Timezone"]):
                for optimization in ([], ["-O"]):
                    with self.subTest(options=options, optimization=optimization):
                        result = subprocess.run([sys.executable, *optimization, "-B",
                            str(root / "scripts" / "check_research_evidence.py"), *options], cwd=root,
                            env={**os.environ, "MAESTRO_DATA_DIR": str(ambient)}, capture_output=True,
                            text=True, encoding="utf-8", timeout=30)
                        self.assertEqual(result.returncode, 0 if options == ["--help"] else 2, result.stderr)
                        self.assertFalse(ambient.exists())

    def test_versioned_public_synthetic_cases_and_independent_canonical_results_pass(self):
        self.assertEqual(set(self.cases), {"historical_tail", "current_old_observation", "conflicting_fy2025_disclosures", "partial_revenue_only"})
        for case in self.cases.values():
            with self.subTest(case=case["id"]):
                run, sources, loader, _, _ = canonical(case)
                check_run(case, run, sources, loader)
        # No sufficient current support is also a truthful missing gap; the old
        # dated passage stays in the packet and is never accepted as current.
        case = self.cases["current_old_observation"]
        run, sources, loader, raw, packet = canonical(case)
        raw["items"][0].update(state="missing", supports=[], reason="No evidence establishes current headcount.")
        run["evidence_review"] = validate_review(raw, run["assessment"], packet, CHECKED)
        check_run(case, run, sources, loader)
        tail = self.cases["historical_tail"]["sources"][0]
        self.assertGreater(source_text(tail).index(tail["quote"]), 1200)

    def test_semantically_wrong_but_typed_review_results_are_rejected(self):
        case = self.cases["historical_tail"]
        run, sources, loader, raw, packet = canonical(case)
        raw["items"][0]["supports"][0]["quote"] = "Acme revenue for FY2025"
        run["evidence_review"] = validate_review(raw, run["assessment"], packet, CHECKED)
        with self.assertRaisesRegex(AssertionError, "period/currency/value"):
            check_run(case, run, sources, loader)
        case = self.cases["conflicting_fy2025_disclosures"]
        run, sources, loader, raw, packet = canonical(case)
        raw["items"][0].update(state="covered", supports=raw["items"][0]["supports"][:1])
        run["evidence_review"] = validate_review(raw, run["assessment"], packet, CHECKED)
        with self.assertRaisesRegex(AssertionError, "semantic evidence state"):
            check_run(case, run, sources, loader)
        case = self.cases["current_old_observation"]
        run, sources, loader, raw, packet = canonical(case, current_location=None)
        raw["items"][0]["supports"][0].update(temporal_scope="unspecified", observation_date=None)
        run["evidence_review"] = validate_review(raw, run["assessment"], packet, CHECKED)
        with self.assertRaisesRegex(AssertionError, "current qualifier was omitted"):
            check_run(case, run, sources, loader)

    def test_explicit_current_scope_can_use_period_freshness_or_global_constraint_but_cannot_disappear(self):
        case = self.cases["current_old_observation"]
        for location in ("period", "freshness", "constraint"):
            with self.subTest(location=location):
                run, sources, loader, _, _ = canonical(case, current_location=location)
                check_run(case, run, sources, loader)
        run, sources, loader, _, _ = canonical(case, current_location=None)
        with self.assertRaisesRegex(AssertionError, "current qualifier was omitted"):
            check_run(case, run, sources, loader)
        # The exception is fixture-specific: ordinary unspecified periods still
        # have to remain null, including fixtures without a current annotation.
        strict = deepcopy(case)
        strict["expected"].pop("current_qualifier")
        run, _, _, _, _ = canonical(case, current_location="period")
        with self.assertRaisesRegex(AssertionError, "qualifiers were changed"):
            check_qualifiers(strict, run["assessment"])

    def test_wrong_pins_hidden_extra_calls_and_holds_are_rejected(self):
        case = self.cases["historical_tail"]
        for change in (lambda run: run["evidence_review"]["sources"][0].update(content_hash="f" * 64),
                       lambda run: run["budget"]["consumed"].update(model_calls=3),
                       lambda run: run["budget"]["consumed"].update(hosted_searches=1),
                       lambda run: run["budget"]["reserved"].update(tokens=1)):
            run, sources, loader, _, _ = canonical(case)
            change(run)
            with self.subTest(change=change), self.assertRaises(AssertionError):
                check_run(case, run, sources, loader)

    def test_partial_coverage_requires_both_distinct_metrics_and_their_exact_item_states(self):
        case = self.cases["partial_revenue_only"]
        run, sources, loader, raw, packet = canonical(case)
        self.assertEqual([item["state"] for item in run["evidence_review"]["items"]], ["covered", "missing"])
        self.assertEqual(run["evidence_review"]["items"][1]["supports"], [])
        check_run(case, run, sources, loader)
        # IDs and order are model-selected, but every expected metric must bind
        # to its own ID and keep the requested currency/reporting period.
        run["assessment"]["items"].reverse()
        run["evidence_review"] = validate_review(raw, run["assessment"], packet, CHECKED)
        check_run(case, run, sources, loader)
        for change in ("missing_item", "extra_item", "wrong_metric", "merged_metrics"):
            with self.subTest(change=change):
                changed, sources, loader, changed_raw, packet = canonical(case)
                items = changed["assessment"]["items"]
                if change == "missing_item":
                    items.pop()
                    changed_raw["items"].pop()
                elif change == "extra_item":
                    items.append({**deepcopy(items[-1]), "id": "i3", "criterion": "Assess"})
                    changed_raw["items"].append({"item_id": "i3", "state": "missing", "reason": "Synthetic extra item.", "supports": []})
                elif change == "wrong_metric":
                    items[1]["criterion"] = "USD"
                else:
                    for item in items:
                        item["criterion"] = "revenue and operating profit"
                changed["evidence_review"] = validate_review(changed_raw, changed["assessment"], packet, CHECKED)
                with self.assertRaisesRegex(AssertionError, "mandatory item|distinct requested metrics"):
                    check_run(case, changed, sources, loader)
        for change in ("covered_profit", "missing_revenue", "wrong_item_id"):
            with self.subTest(change=change):
                changed, sources, loader, changed_raw, packet = canonical(case)
                if change == "covered_profit":
                    changed_raw["items"][1].update(state="covered", supports=deepcopy(changed_raw["items"][0]["supports"]))
                elif change == "missing_revenue":
                    changed_raw["items"][0].update(state="missing", supports=[])
                else:
                    changed["evidence_review"]["items"][1]["item_id"] = "i3"
                    with self.assertRaises(AssertionError):
                        check_run(case, changed, sources, loader)
                    continue
                changed["evidence_review"] = validate_review(changed_raw, changed["assessment"], packet, CHECKED)
                with self.assertRaisesRegex(AssertionError, "semantic evidence state"):
                    check_run(case, changed, sources, loader)


if __name__ == "__main__":
    unittest.main()

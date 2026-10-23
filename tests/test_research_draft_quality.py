"""The opt-in draft oracle rejects typed but unsupported fixture prose offline."""
from copy import deepcopy
import unittest

from backend.research_draft import gap_draft, has_support, validate_response
from scripts.check_research_evidence import check_run, load_cases
from tests.test_research_evidence_quality import canonical


class ResearchDraftQualityTests(unittest.TestCase):
    def setUp(self):
        self.cases = {case["id"]: case for case in load_cases()}

    def draft_run(self, case):
        run, sources, loader, _, _ = canonical(case)
        supported = has_support(run["evidence_review"])
        if supported:
            raw = {"version": 1, "items": {row["item_id"]: {
                "kind": "fact", "text": row["supports"][0]["quote"],
                "supports": [{key: row["supports"][0][key] for key in ("passage_id", "quote")}],
                "calculation": None} for row in run["evidence_review"]["items"] if row["state"] == "covered"}}
            run["draft"] = validate_response(raw, run["assessment"], run["evidence_review"])
        else:
            run["draft"] = gap_draft(run["assessment"], run["evidence_review"])
        run.update(workflow="research_draft", state="draft" if supported else "partial")
        run["budget"]["consumed"]["model_calls"] += int(supported)
        return run, sources, loader

    def test_all_frozen_cases_preserve_values_and_gaps_with_expected_call_counts(self):
        for case in self.cases.values():
            with self.subTest(case=case["id"]):
                run, sources, loader = self.draft_run(case)
                check_run(case, run, sources, loader, workflow="research_draft")

    def test_typed_wrong_value_and_extra_unsupported_figure_fail_semantic_oracle(self):
        case = self.cases["historical_tail"]
        for text in ("Acme revenue for FY2025 was USD 99 million.",
                     "Acme revenue for FY2025 was USD 42 million and profit was USD 99 million."):
            run, sources, loader = self.draft_run(case)
            # Prose truth is the later final-review gate; production validation
            # deliberately does not claim to prove arbitrary natural language.
            raw = {"version": 1, "items": {row["item_id"]: {
                "kind": row["kind"], "text": text,
                "supports": [{key: support[key] for key in ("passage_id", "quote")} for support in row["supports"]],
                "calculation": None} for row in run["draft"]["items"]}}
            run["draft"] = validate_response(raw, run["assessment"], run["evidence_review"])
            with self.subTest(text=text), self.assertRaisesRegex(AssertionError, "expected fixture value|unsupported numeric"):
                check_run(case, run, sources, loader, workflow="research_draft")

    def test_missing_draft_changed_binding_hidden_calls_and_holds_fail(self):
        case = self.cases["partial_revenue_only"]
        for change in (lambda run: run.update(draft=None),
                       lambda run: run["draft"].update(coverage_hash="f" * 64),
                       lambda run: run["budget"]["consumed"].update(model_calls=4),
                       lambda run: run["budget"]["reserved"].update(tokens=1)):
            run, sources, loader = self.draft_run(case)
            change(run)
            with self.subTest(change=change), self.assertRaises(AssertionError):
                check_run(case, run, sources, loader, workflow="research_draft")

    def test_numbers_from_uncited_sources_other_rows_or_unrelated_request_text_fail(self):
        for origin in ("uncited_source", "other_row", "unrelated_request"):
            case = deepcopy(self.cases["partial_revenue_only" if origin == "other_row" else "historical_tail"])
            if origin == "unrelated_request":
                case["request"] += " Internal reference 99 is not a financial value."
            else:
                case["sources"].append({
                    "title": "Synthetic Acme other disclosure",
                    "url": "https://reports.example.org/acme/other",
                    "retrieved_at": "2026-01-11T12:00:00+00:00",
                    "quote": "Acme operating profit for FY2025 was USD 99 million.",
                })
                if origin == "other_row":
                    case["expected"]["items"][1].update(
                        states=["covered"], minimum_sources=1,
                        support_fragments=[["operating profit", "FY2025", "USD", "99"]])
            run, sources, loader = self.draft_run(case)
            # This remains canonically cited, but 99 is outside the revenue
            # row's citations and qualifiers. Other packet/request numbers
            # cannot supply numeric provenance for this row's added prose.
            raw = {"version": 1, "items": {row["item_id"]: {
                "kind": row["kind"], "text": row["text"],
                "supports": [{key: support[key] for key in ("passage_id", "quote")} for support in row["supports"]],
                "calculation": None} for row in run["draft"]["items"]}}
            raw["items"]["i1"]["text"] += " Operating profit was USD 99 million."
            run["draft"] = validate_response(raw, run["assessment"], run["evidence_review"])
            with self.subTest(origin=origin), self.assertRaisesRegex(AssertionError, "unsupported numeric literal"):
                check_run(case, run, sources, loader, workflow="research_draft")

    def test_same_row_citations_qualifiers_and_server_calculation_remain_allowed(self):
        case = self.cases["historical_tail"]
        for calculated in (False, True):
            run, sources, loader = self.draft_run(case)
            support = run["draft"]["items"][0]["supports"][0]
            # FY2025 is supplied only by this requirement's copied qualifier;
            # the narrower citation still supplies the value and currency.
            raw = {"version": 1, "items": {"i1": {
                "kind": "calculation" if calculated else "fact",
                "text": "Acme revenue for FY2025 was USD 42 million." +
                        (" Twice that reported amount is USD 84.00 million." if calculated else ""),
                "supports": [{"passage_id": support["passage_id"], "quote": "USD 42 million."}],
                "calculation": {"operation": "sum", "operands": [
                    {"value": "42", "support_index": 0}, {"value": "42", "support_index": 0}],
                    "precision": 2} if calculated else None,
            }}}
            run["draft"] = validate_response(raw, run["assessment"], run["evidence_review"])
            with self.subTest(calculated=calculated):
                if calculated:
                    self.assertEqual(run["draft"]["items"][0]["calculation"]["result"], "84.00")
                check_run(case, run, sources, loader, workflow="research_draft")

    def test_malformed_draft_shapes_fail_with_controlled_non_echoing_assertion(self):
        case = self.cases["historical_tail"]
        marker = "UNTRUSTED_DRAFT_CONTENT"
        changes = (
            lambda run: run.update(draft=[{"state": "unreviewed", "text": marker}]),
            lambda run: run.update(draft=marker),
            lambda run: run.update(draft=7),
            lambda run: run["draft"].update(items={"i1": marker}),
            lambda run: run["draft"].update(items=[marker]),
            lambda run: run["draft"]["items"][0].update(supports=[marker]),
            lambda run: run["draft"]["items"][0].update(calculation=marker),
        )
        for index, change in enumerate(changes):
            run, sources, loader = self.draft_run(case)
            change(run)
            with self.subTest(shape=index):
                with self.assertRaisesRegex(AssertionError, "^Missing or noncanonical draft$") as caught:
                    check_run(case, run, sources, loader, workflow="research_draft")
                self.assertNotIn(marker, str(caught.exception))

    def test_capacity_gap_is_not_counted_as_a_successful_supported_draft(self):
        case = self.cases["historical_tail"]
        run, sources, loader = self.draft_run(case)
        run["draft"] = gap_draft(run["assessment"], run["evidence_review"], reason="writer_capacity")
        run["state"] = "partial"
        run["budget"]["consumed"]["model_calls"] = 2
        with self.assertRaisesRegex(AssertionError, "supported fixture did not produce"):
            check_run(case, run, sources, loader, workflow="research_draft")


if __name__ == "__main__":
    unittest.main()

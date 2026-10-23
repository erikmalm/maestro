"""The opt-in draft oracle rejects typed but unsupported fixture prose offline."""
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

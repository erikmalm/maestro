"""The opt-in draft oracle rejects typed but unsupported fixture prose offline."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
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

    def replace_fact(self, run, text, *, calculation=None):
        row = run["draft"]["items"][0]
        raw = {"version": 1, "items": {row["item_id"]: {
            "kind": "calculation" if calculation else "fact", "text": text,
            "supports": [{key: support[key] for key in ("passage_id", "quote")} for support in row["supports"]],
            "calculation": calculation}}}
        run["draft"] = validate_response(raw, run["assessment"], run["evidence_review"])

    def test_changed_quantity_scale_cannot_borrow_a_unit_elsewhere_in_the_row(self):
        case = self.cases["historical_tail"]
        for text in (
            "Acme revenue for FY2025 was USD 42 billion.",
            "Acme revenue for FY2025 was USD 42 thousand.",
            "Acme revenue for FY2025 was USD 42 billion. The source uses the word million.",
            "Acme revenue for FY2025 was USD 42 million, also stated as USD 42 billion.",
            "Acme revenue for FY2025 was USD 42 million, also stated as 42 billion.",
            "Acme revenue for FY2025 was EUR 42 million.",
            "Acme revenue for FY2025 was USD 42 million, also stated as EUR 42 million.",
        ):
            run, sources, loader = self.draft_run(case)
            self.replace_fact(run, text)
            with self.subTest(text=text), self.assertRaisesRegex(AssertionError, "quantity"):
                check_run(case, run, sources, loader, workflow="research_draft")

    def test_unicode_and_accounting_numbers_cannot_disappear_from_provenance(self):
        case = self.cases["historical_tail"]
        for text in (
            "Acme revenue for FY2025 was USD \u221242 million.",
            "Acme revenue for FY2025 was USD \u207b42 million.",
            "Acme revenue for FY2025 was USD (42) million.",
            "Acme revenue for FY2025 was (USD 42 million).",
            "Acme revenue for FY2025 was (USD 42 Million).",
            "Acme revenue for FY2025 was -USD 42 million.",
            "Acme revenue for FY2025 was -$42 million, in USD.",
            "Acme revenue for FY2025 was \u2212USD 42 million.",
            "Acme revenue for FY2025 was USD 42 million. Another amount was 42-.",
            "Acme revenue for FY2025 was USD 42 million-.",
            "Acme revenue for FY2025 was USD 42 million. Another amount was -usd 42 million.",
            "Acme revenue for FY2025 was USD 42 million. Another amount was \u2212eur 42 million.",
            "Acme revenue for FY2025 was USD 42 million. Another amount was 42usd-.",
            "Acme revenue for FY2025 was USD 42 million. Another amount was 42 USD\u2212.",
            "Acme revenue for FY2025 was USD 42 million\u2212.",
            "Acme revenue for FY2025 was USD 42 million. Another amount was 42$\u2212.",
            "Acme revenue for FY2025 was USD 42 million. Another amount was \u2212\u20b942 million.",
            "Acme revenue for FY2025 was USD 42 million. Another amount was -\u20b942 million.",
            "Acme revenue for FY2025 was USD 42 million. Operating profit was USD \uff19\uff19 million.",
            "Acme revenue for FY2025 was USD 42 million. Operating profit was USD 9\u200b9 million.",
            "Acme revenue for FY2025 was USD 42 million. Operating profit was USD 42,2025 million.",
            "Acme revenue for FY2025 was USD 42 million. Another amount was 42, 2025.",
            "Acme revenue for FY2025 was USD 42 million. Another amount was 42,\t2025.",
        ):
            run, sources, loader = self.draft_run(case)
            self.replace_fact(run, text)
            with self.subTest(text=ascii(text)), self.assertRaisesRegex(AssertionError, "numeric"):
                check_run(case, run, sources, loader, workflow="research_draft")

    def test_plain_punctuation_and_parenthesized_qualifiers_preserve_expected_quantity(self):
        case = self.cases["historical_tail"]
        for text in (
            "Acme revenue (FY2025) was USD 42 million; this is historical evidence.",
            "Acme revenue for FY2025 - as reported - was USD 42 million.",
            "Acme revenue for FY2025 was 42 million USD.",
            "Acme revenue was 42 million.",
        ):
            run, sources, loader = self.draft_run(case)
            self.replace_fact(run, text)
            with self.subTest(text=text):
                check_run(case, run, sources, loader, workflow="research_draft")

    def test_conflicting_fixture_currency_is_rejected_independently_of_label_case(self):
        case = self.cases["historical_tail"]
        for label in ("EUR", "eur", "eUr"):
            for amount in (f"{label} 42 million", f"42 million {label}", f"42million{label}"):
                run, sources, loader = self.draft_run(case)
                self.replace_fact(run, "Acme revenue for FY2025 was " + amount + ".")
                with self.subTest(amount=amount), self.assertRaisesRegex(AssertionError, "quantity"):
                    check_run(case, run, sources, loader, workflow="research_draft")

    def test_correct_fixture_currency_case_and_copied_qualifier_remain_valid(self):
        case = self.cases["historical_tail"]
        amounts = [f"{label} 42 million" for label in ("USD", "usd", "uSd")]
        amounts += [f"42 million {label}" for label in ("USD", "usd", "uSd")]
        amounts += ["42 million", "42millionusd"]
        for amount in amounts:
            run, sources, loader = self.draft_run(case)
            self.replace_fact(run, "Acme revenue was " + amount + ".")
            with self.subTest(amount=amount):
                check_run(case, run, sources, loader, workflow="research_draft")

    def test_unknown_uppercase_currency_keeps_legacy_conflict_guard(self):
        case = self.cases["historical_tail"]
        self.assertNotIn("GBP", case["expected"]["draft_currency_codes"])
        self.assertNotIn("JPY", case["expected"]["draft_currency_codes"])
        for label in ("GBP", "JPY"):
            for amount in (f"{label} 42 million", f"42 million {label}", f"42million{label}"):
                run, sources, loader = self.draft_run(case)
                self.replace_fact(run, "Acme revenue for FY2025 was " + amount + ".")
                with self.subTest(amount=amount), self.assertRaisesRegex(AssertionError, "quantity"):
                    check_run(case, run, sources, loader, workflow="research_draft")

    def test_leading_decimal_cannot_borrow_the_supported_integer_suffix(self):
        case = self.cases["historical_tail"]
        for amount in (".42", "-.42", "+.42", ",42", "-,42", "1..42"):
            run, sources, loader = self.draft_run(case)
            self.replace_fact(run, "Acme revenue for FY2025 was USD 42 million. "
                              "Another amount was USD " + amount + " million.")
            with self.subTest(amount=amount), self.assertRaisesRegex(AssertionError, "numeric"):
                check_run(case, run, sources, loader, workflow="research_draft")

    def test_complete_plain_decimal_requires_its_own_exact_fixture_support(self):
        for value in ("0.42", "+0.42"):
            case = deepcopy(self.cases["historical_tail"])
            case["sources"][0]["quote"] = "Acme revenue for FY2025 was USD " + value + " million."
            case["expected"]["support_fragments"] = [["FY2025", "USD", value]]
            case["expected"]["draft_quantities"][0]["value"] = value
            run, sources, loader = self.draft_run(case)
            with self.subTest(value=value):
                check_run(case, run, sources, loader, workflow="research_draft")
            # A complete decimal must not fall back to its integer suffix even
            # when the row still cites the original supported value of 42.
            original = self.cases["historical_tail"]
            run, sources, loader = self.draft_run(original)
            self.replace_fact(run, "Acme revenue for FY2025 was USD 42 million. "
                              "Another amount was USD " + value + " million.")
            with self.subTest(unsupported=value), self.assertRaisesRegex(AssertionError, "numeric"):
                check_run(original, run, sources, loader, workflow="research_draft")

    def test_quantity_fixture_requires_a_valid_bounded_currency_vocabulary(self):
        bad = (None, "USD", [], ["EUR"], ["USD", "USD"], ["usd", "EUR"],
               ["USD", "EURO"], ["USD", 7],
               ["USD"] + ["AA" + chr(ord("A") + index) for index in range(26)] + ["AB" + chr(ord("A") + index) for index in range(6)])
        with tempfile.TemporaryDirectory(prefix="maestro-draft-currency-vocabulary-") as temporary:
            path = Path(temporary) / "corpus.json"
            for vocabulary in bad:
                case = deepcopy(self.cases["historical_tail"])
                case["expected"]["draft_currency_codes"] = vocabulary
                path.write_text(json.dumps({"version": 2, "cases": [case]}), encoding="utf-8")
                with self.subTest(vocabulary=vocabulary), self.assertRaisesRegex(AssertionError, "currency"):
                    load_cases(path)
            case["expected"].pop("draft_currency_codes")
            path.write_text(json.dumps({"version": 2, "cases": [case]}), encoding="utf-8")
            with self.assertRaisesRegex(AssertionError, "currency"):
                load_cases(path)

    def test_server_calculation_retains_scale_and_cannot_authorize_a_different_one(self):
        case = self.cases["historical_tail"]
        calculation = {"operation": "sum", "operands": [
            {"value": "42", "support_index": 0}, {"value": "42", "support_index": 0}], "precision": 2}
        for scale in ("million", "billion"):
            run, sources, loader = self.draft_run(case)
            text = "Acme revenue for FY2025 was USD 42 million; twice that amount is USD 84.00 " + scale + "."
            self.replace_fact(run, text, calculation=calculation)
            with self.subTest(scale=scale):
                if scale == "million":
                    check_run(case, run, sources, loader, workflow="research_draft")
                else:
                    with self.assertRaisesRegex(AssertionError, "quantity"):
                        check_run(case, run, sources, loader, workflow="research_draft")

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

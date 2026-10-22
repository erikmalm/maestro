"""Synthetic regressions for the opt-in model-quality corpus checker."""
from copy import deepcopy
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from backend.research_requirements import validate_assessment
from tests.research_requirements_quality import check_assessment, load_cases


def canonical(case):
    """Build independent synthetic model output from the case annotations."""
    names = list(dict.fromkeys(item["subject"] for item in case["items"]))
    subjects = [{"id": f"s{index}", "kind": "company", "name": name, "identifiers": []}
                for index, name in enumerate(names)]
    items = [{"id": f"i{index}", "subject_ids": [f"s{names.index(item['subject'])}"],
              "criterion": item["criterion"], "period": item["period"], "currency": item["currency"],
              "unit": item.get("unit"), "freshness": None} for index, item in enumerate(case["items"])]
    raw = {"version": 1, "decision": case["decision"], "subjects": subjects, "items": items,
           "constraints": [{"kind": constraint["kind"], "strength": constraint["strength"],
                            "criterion": constraint["quote"],
                            "item_ids": [f"i{index}" for index in constraint["applies_to"]]}
                           for constraint in case["constraints"]],
           "clarifications": [{"kind": question["kind"], "quote": question["quote"], "item_ids": []}
                              for question in case["clarifications"]]}
    return validate_assessment(raw, case["request"])


class ResearchRequirementsQualityTests(unittest.TestCase):
    def setUp(self):
        self.cases = {case["id"]: case for case in load_cases()}

    def test_help_does_not_touch_the_ambient_private_directory(self):
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(prefix="maestro-quality-help-") as temporary:
            scratch = Path(temporary).resolve()
            self.assertEqual(scratch.parent, Path(tempfile.gettempdir()).resolve())
            ambient = scratch / "must-not-touch"
            for optimization in ([], ["-O"]):
                with self.subTest(optimization=optimization):
                    result = subprocess.run(
                        [sys.executable, *optimization, "-B", str(root / "scripts" / "check_research_requirements.py"), "--help"],
                        cwd=root, env={**os.environ, "MAESTRO_DATA_DIR": str(ambient)},
                        capture_output=True, text=True, encoding="utf-8", timeout=30,
                    )
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertIn("--model", result.stdout)
                    self.assertFalse(ambient.exists())

    def test_observed_missing_search_restriction_and_wrong_strength_are_rejected(self):
        case = self.cases["multiple_metrics_global_restrictions"]
        for mutation in (lambda value: value["constraints"].pop(),
                         lambda value: value["constraints"][1].update(strength="prefer")):
            with self.subTest(mutation=mutation):
                value = canonical(case)
                mutation(value)
                with self.assertRaisesRegex(AssertionError, "constraints|constraint"):
                    check_assessment(case, value)
        case = self.cases["single_solvena_exclusive_filings_no_browse"]
        # Each output remains structurally valid and verbatim. Correct category
        # and the complete negative clause are separate semantic requirements.
        for changes in ({"kind": "source"}, {"criterion": "web"},
                        {"kind": "source", "criterion": "web"}):
            with self.subTest(changes=changes):
                value = canonical(case)
                value["constraints"][1].update(changes)
                with self.assertRaisesRegex(AssertionError, "kind/strength/quote/scope"):
                    check_assessment(case, value)
        case = self.cases["source_exclusion_is_not_search_ban"]
        # The source noun phrase and forbid strength jointly preserve exclusion.
        # Missing coverage, incorrect category/polarity, or an incomplete source
        # target must still fail even though the imperative verb is optional.
        for changes in (None, {"kind": "search"}, {"strength": "prefer"},
                        {"strength": "require"}, {"criterion": "posts"}):
            with self.subTest(exclusion_changes=changes):
                value = canonical(case)
                if changes is None:
                    value["constraints"].pop()
                else:
                    value["constraints"][1].update(changes)
                with self.assertRaisesRegex(AssertionError, "constraints|constraint"):
                    check_assessment(case, value)

    def test_cross_applied_qualifiers_and_competing_values_in_broader_spans_are_rejected(self):
        case = self.cases["distinct_subjects_and_qualifiers"]
        for field in ("period", "currency", "unit"):
            with self.subTest(field=field):
                value = canonical(case)
                first, second = value["items"]
                first[field], second[field] = second[field], first[field]
                with self.assertRaisesRegex(AssertionError, "qualifiers"):
                    check_assessment(case, value)
        value = canonical(case)
        value["items"][0]["currency"] = "USD with Boreal operating profit for FY2025 in billions of SEK"
        with self.assertRaisesRegex(AssertionError, "qualifiers"):
            check_assessment(case, value)

    def test_item_specific_sources_cannot_become_global_or_swap_companies(self):
        case = self.cases["item_scoped_sources_global_search"]
        for scope in ([], ["i1"]):
            with self.subTest(scope=scope):
                value = canonical(case)
                value["constraints"][0]["item_ids"] = scope
                with self.assertRaisesRegex(AssertionError, "scope"):
                    check_assessment(case, value)

    def test_one_item_cannot_cover_two_demands_even_when_both_metric_phrases_are_present(self):
        case = self.cases["multiple_metrics_global_restrictions"]
        value = canonical(case)
        value["items"][0]["criterion"] = "revenue and operating profit"
        value["items"].pop()
        for constraint in value["constraints"]:
            constraint["item_ids"] = []
        with self.assertRaisesRegex(AssertionError, "2 deliverables, got 1"):
            check_assessment(case, value)
        value = canonical(case)
        value["items"][0]["criterion"] = "revenue and operating profit"
        value["items"][1]["criterion"] = "DKK"
        with self.assertRaisesRegex(AssertionError, "distinct item assignment"):
            check_assessment(case, value)

    def test_valid_cases_allow_reordering_broader_quotes_and_equivalent_global_scopes(self):
        self.assertEqual(len(self.cases), 14)
        for case in self.cases.values():
            with self.subTest(case=case["id"]):
                value = canonical(case)
                value["items"].reverse()
                value["constraints"].reverse()
                check_assessment(case, value)
        case = self.cases["multiple_metrics_global_restrictions"]
        value = canonical(case)
        for item in value["items"]:
            item.update(criterion="revenue and operating profit", period="for FY2025", currency="in DKK")
        value["constraints"][0].update(criterion="using annual reports only", item_ids=[])
        value["constraints"][1].update(criterion="do not search.", item_ids=[])
        check_assessment(case, value)

    def test_clarification_allows_partial_items_but_requires_questions_and_canonical_fields(self):
        for case_id in ("ambiguous_security_and_currency", "unresolved_single_reporting_period"):
            case = self.cases[case_id]
            empty = canonical(case)
            empty.update(subjects=[], items=[])
            check_assessment(case, empty)
            partial = canonical(case)
            partial["items"][0].update(subject_ids=[], period=None, currency=None)
            check_assessment(case, partial)
            missing = deepcopy(empty)
            missing["clarifications"] = missing["clarifications"][:-1]
            with self.assertRaises(AssertionError):
                check_assessment(case, missing)
        case = self.cases["unresolved_single_reporting_period"]
        selected = canonical(case)
        selected["items"][0]["period"] = "FY2024"
        with self.assertRaisesRegex(AssertionError, "qualifiers"):
            check_assessment(case, selected)
        case = self.cases["unrestricted_explicit_metric"]
        changed = canonical(case)
        changed["items"][0]["mandatory"] = False
        with self.assertRaisesRegex(AssertionError, "canonical"):
            check_assessment(case, changed)
        changed = canonical(case)
        changed["constraints"].append({"kind": "search", "strength": "forbid", "item_ids": [],
                                       "criterion": "Research"})
        with self.assertRaisesRegex(AssertionError, "0 separate constraints"):
            check_assessment(case, changed)

    def test_atlas_subject_alternative_keeps_restrictions_and_rejects_other_questions(self):
        case = self.cases["single_atlas_without_internet_searches"]
        alternative = {**case, "decision": "clarify",
                       "clarifications": [{"kind": "subject", "quote": "Atlas"}]}
        partial = canonical(alternative)
        partial["items"][0]["subject_ids"] = []
        check_assessment(case, partial)
        empty = deepcopy(partial)
        empty.update(subjects=[], items=[])
        for constraint in empty["constraints"]:
            constraint["item_ids"] = []
        check_assessment(case, empty)
        for index in range(2):
            with self.subTest(missing_constraint=index):
                missing = deepcopy(empty)
                missing["constraints"].pop(index)
                with self.assertRaisesRegex(AssertionError, "2 separate constraints"):
                    check_assessment(case, missing)
        for question in ({"kind": "currency", "quote": "SEK"},
                         {"kind": "subject", "quote": "issuer"}):
            with self.subTest(unnecessary_question=question):
                changed = canonical({**alternative,
                                     "clarifications": [*alternative["clarifications"], question]})
                with self.assertRaisesRegex(AssertionError, "only subject clarification"):
                    check_assessment(case, changed)
        changed = deepcopy(partial)
        changed["items"][0]["currency"] = "FY2023"
        with self.assertRaisesRegex(AssertionError, "qualifiers"):
            check_assessment(case, changed)
        unannotated = {key: value for key, value in case.items() if key != "allow_subject_clarification"}
        with self.assertRaisesRegex(AssertionError, "expected decision"):
            check_assessment(unannotated, empty)


if __name__ == "__main__":
    unittest.main()

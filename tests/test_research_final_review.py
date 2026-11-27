"""Pure final-review barriers and exact input bindings; no model or services."""
from copy import deepcopy
import json
import unittest

from backend.research_draft import gap_draft, validate_response as validate_draft
from backend.research_final_review import (
    GAP_REASONS, MAX_INPUT_BYTES, MAX_RESPONSE_BYTES, MAX_SCHEMA_BYTES,
    draft_hash, gap_review, response_schema, review_input, validate_response, validate_saved_final_review,
)
from tests.test_research_draft import response as draft_response, scope


def response(assessment, outcome="accepted", **changes):
    return {"version": 1, "outcome": outcome, "reason": "The supplied facts satisfy the original request.",
            "items": {item["id"]: {"outcome": "accepted", "reason": "The cited passage supports the requested fact."}
                      for item in assessment["items"]}, **changes}


class ResearchFinalReviewTests(unittest.TestCase):
    def setUp(self):
        self.assessment, self.coverage = scope(("covered",))
        self.draft = validate_draft(draft_response(self.coverage), self.assessment, self.coverage)

    def validate(self, raw=None):
        return validate_response(response(self.assessment) if raw is None else raw,
                                 self.assessment, self.coverage, self.draft)

    def reject(self, raw):
        with self.assertRaisesRegex(ValueError, "final research review"):
            self.validate(raw)

    def test_supported_review_preserves_semantic_findings_and_exact_input_hashes(self):
        original = deepcopy((self.assessment, self.coverage, self.draft))
        saved = self.validate()
        selected = review_input(self.assessment, self.coverage, self.draft)
        self.assertEqual((saved["basis"], saved["outcome"], saved["reason_code"]), ("semantic", "accepted", None))
        self.assertEqual(selected["original_request"], self.assessment["completion_criterion"])
        self.assertEqual(selected["assessment"], self.assessment)
        self.assertEqual(selected["coverage"], self.coverage)
        self.assertEqual(selected["draft"], self.draft)
        for key in ("assessment_hash", "packet_hash", "coverage_hash", "draft_hash"):
            self.assertEqual(saved[key], selected[key])
            self.assertEqual(len(saved[key]), 64)
        self.assertEqual(saved["draft_hash"], draft_hash(self.draft))
        self.assertEqual(validate_saved_final_review(saved, *original), saved)
        selected["draft"]["items"][0]["text"] = "Mutated caller copy"
        selected["coverage"]["items"][0]["supports"].clear()
        self.assertEqual((self.assessment, self.coverage, self.draft), original)
        self.assertEqual(self.draft["state"], "unreviewed")

    def test_mandatory_missing_stale_conflicting_unavailable_items_block_acceptance(self):
        for state in ("missing", "stale", "conflicting", "unavailable"):
            with self.subTest(state=state):
                assessment, coverage = scope(("covered", state))
                draft = validate_draft(draft_response(coverage), assessment, coverage)
                raw = response(assessment)
                with self.assertRaisesRegex(ValueError, "final research review"):
                    validate_response(raw, assessment, coverage, draft)
                raw["items"]["i1"]["outcome"] = "evidence_gap"
                with self.assertRaisesRegex(ValueError, "final research review"):
                    validate_response(raw, assessment, coverage, draft)
                raw["outcome"] = "partial"
                saved = validate_response(raw, assessment, coverage, draft)
                self.assertEqual([row["outcome"] for row in saved["items"]], ["accepted", "evidence_gap"])
                self.assertEqual(validate_saved_final_review(saved, assessment, coverage, draft), saved)

    def test_calculation_and_insufficient_prose_gaps_cannot_be_accepted(self):
        for reason in ("calculation_required", "insufficient_evidence"):
            raw_draft = draft_response(self.coverage)
            raw_draft["items"]["i0"] = {"kind": "gap", "text": "", "support_indexes": [], "gap_reason": reason}
            draft = validate_draft(raw_draft, self.assessment, self.coverage)
            raw = response(self.assessment)
            with self.assertRaisesRegex(ValueError, "final research review"):
                validate_response(raw, self.assessment, self.coverage, draft)
            raw["outcome"] = raw["items"]["i0"]["outcome"] = "evidence_gap"
            self.assertEqual(validate_response(raw, self.assessment, self.coverage, draft)["outcome"], "evidence_gap")

    def test_global_omission_or_restriction_can_block_otherwise_accepted_items(self):
        for outcome in ("writing_defect", "evidence_gap", "partial", "unverifiable"):
            raw = response(self.assessment, outcome, reason="A required restriction in the original request was not met.")
            saved = self.validate(raw)
            self.assertEqual((saved["outcome"], saved["items"][0]["outcome"]), (outcome, "accepted"))

    def test_semantic_figure_period_unit_and_interpretation_defects_are_recorded(self):
        for reason in ("Draft reports 42 but the source reports 41.", "Draft uses FY2024 instead of FY2025.",
                       "Draft changes millions to billions.", "Interpretation is asserted as a reported source fact."):
            raw = response(self.assessment, "writing_defect", reason=reason)
            raw["items"]["i0"] = {"outcome": "writing_defect", "reason": reason}
            saved = self.validate(raw)
            self.assertEqual(saved["items"][0]["reason"], reason)
            self.assertEqual(saved["outcome"], "writing_defect")
            raw["outcome"] = "accepted"
            self.reject(raw)

    def test_invalid_schema_extra_or_missing_items_and_unbounded_reasons_are_rejected(self):
        for mutation in (
            lambda raw: raw.update(version=True),
            lambda raw: raw.update(version=2),
            lambda raw: raw.update(outcome="verified"),
            lambda raw: raw.update(outcome=[]),
            lambda raw: raw.update(reason=" "),
            lambda raw: raw.update(reason="x" * 1201),
            lambda raw: raw.update(reason="x\x00"),
            lambda raw: raw.update(reason="\ud800"),
            lambda raw: raw.update(items=[]),
            lambda raw: raw.update(extra=True),
            lambda raw: raw["items"].clear(),
            lambda raw: raw["items"].update(unknown={"outcome": "accepted", "reason": "Unexpected item."}),
            lambda raw: raw["items"]["i0"].update(outcome="partial"),
            lambda raw: raw["items"]["i0"].update(outcome=False),
            lambda raw: raw["items"]["i0"].update(reason=""),
            lambda raw: raw["items"]["i0"].update(reason="x" * 801),
            lambda raw: raw["items"]["i0"].update(reason="x\x00"),
            lambda raw: raw["items"]["i0"].update(support_indexes=[0]),
        ):
            raw = response(self.assessment)
            mutation(raw)
            self.reject(raw)
        self.reject({"padding": "x" * MAX_RESPONSE_BYTES})

    def test_history_cannot_retarget_acceptance_to_changed_prose_evidence_or_request(self):
        saved = self.validate()
        changed_draft = deepcopy(self.draft)
        changed_draft["items"][0]["text"] = "Different otherwise structurally valid prose."
        with self.assertRaisesRegex(ValueError, "final research review"):
            validate_saved_final_review(saved, self.assessment, self.coverage, changed_draft)
        changed_coverage = deepcopy(self.coverage)
        changed_coverage["items"][0]["reason"] += " A different coverage decision."
        changed_draft = validate_draft(draft_response(changed_coverage), self.assessment, changed_coverage)
        with self.assertRaisesRegex(ValueError, "final research review"):
            validate_saved_final_review(saved, self.assessment, changed_coverage, changed_draft)
        changed_assessment = deepcopy(self.assessment)
        changed_assessment["completion_criterion"] += " Do not search."
        changed_assessment["constraints"] = [{"kind": "search", "strength": "forbid", "item_ids": [],
                                                "criterion": "Do not search"}]
        with self.assertRaisesRegex(ValueError, "final research review"):
            validate_saved_final_review(saved, changed_assessment, self.coverage, self.draft)

    def test_canonical_history_rejects_forged_modes_hashes_and_reordered_items(self):
        assessment, coverage = scope(("covered", "covered"))
        draft = validate_draft(draft_response(coverage), assessment, coverage)
        saved = validate_response(response(assessment), assessment, coverage, draft)
        for mutation in (
            lambda value: value.update(version=True),
            lambda value: value.update(basis="not_run"),
            lambda value: value.update(reason_code="review_capacity"),
            lambda value: value.update(assessment_hash="0" * 64),
            lambda value: value.update(packet_hash="0" * 64),
            lambda value: value.update(coverage_hash="0" * 64),
            lambda value: value.update(draft_hash="0" * 64),
            lambda value: value["items"].reverse(),
            lambda value: value["items"].append(deepcopy(value["items"][0])),
            lambda value: value["items"][0].update(item_id="other"),
            lambda value: value["items"][0].update(extra=True),
        ):
            changed = deepcopy(saved)
            mutation(changed)
            with self.assertRaisesRegex(ValueError, "final research review"):
                validate_saved_final_review(changed, assessment, coverage, draft)
        reordered = dict(reversed(list(saved.items())))
        self.assertEqual(validate_saved_final_review(reordered, assessment, coverage, draft), saved)
        self.assertEqual(draft_hash(dict(reversed(list(draft.items())))), draft_hash(draft))

    def test_no_call_outcomes_are_fixed_unverifiable_and_cannot_fabricate_acceptance(self):
        for reason, message in GAP_REASONS.items():
            draft = gap_draft(self.assessment, self.coverage, "writer_capacity") if reason == "no_supported_draft" else self.draft
            saved = gap_review(self.assessment, self.coverage, draft, reason)
            self.assertEqual((saved["basis"], saved["outcome"], saved["reason_code"]), ("not_run", "unverifiable", reason))
            self.assertEqual(saved["reason"], message)
            self.assertEqual(validate_saved_final_review(saved, self.assessment, self.coverage, draft), saved)
            for mutation in (lambda value: value.update(outcome="accepted"),
                             lambda value: value.update(reason="An invented completed review."),
                             lambda value: value["items"][0].update(outcome="accepted")):
                changed = deepcopy(saved)
                mutation(changed)
                with self.assertRaisesRegex(ValueError, "final research review"):
                    validate_saved_final_review(changed, self.assessment, self.coverage, draft)
        with self.assertRaisesRegex(ValueError, "final research review"):
            gap_review(self.assessment, self.coverage, self.draft, "complete")
        with self.assertRaisesRegex(ValueError, "final research review"):
            gap_review(self.assessment, self.coverage, self.draft, "no_supported_draft")

    def test_unavailable_draft_allows_only_no_call_review(self):
        assessment, coverage = scope(("missing",))
        draft = gap_draft(assessment, coverage)
        saved = gap_review(assessment, coverage, draft, "no_supported_draft")
        self.assertEqual(saved["outcome"], "unverifiable")
        self.assertEqual(validate_saved_final_review(saved, assessment, coverage, draft), saved)
        for action in (lambda: response_schema(assessment, coverage, draft),
                       lambda: validate_response(response(assessment), assessment, coverage, draft)):
            with self.assertRaisesRegex(ValueError, "final research review"):
                action()

    def test_invalid_source_bindings_fail_before_schema_input_or_review(self):
        changed = deepcopy(self.draft)
        changed["items"][0]["supports"][0]["quote"] = "Invented source quote."
        for action in (lambda: response_schema(self.assessment, self.coverage, changed),
                       lambda: review_input(self.assessment, self.coverage, changed),
                       lambda: validate_response(response(self.assessment), self.assessment, self.coverage, changed),
                       lambda: gap_review(self.assessment, self.coverage, changed)):
            with self.assertRaisesRegex(ValueError, "final research review"):
                action()

    def test_maximum_item_schema_and_input_are_bounded(self):
        assessment, coverage = scope(("covered",) * 16, long_ids=True)
        draft = validate_draft(draft_response(coverage), assessment, coverage)
        schema = response_schema(assessment, coverage, draft)
        self.assertEqual(schema["properties"]["items"]["required"], [item["id"] for item in assessment["items"]])
        self.assertLessEqual(len(json.dumps(schema).encode()), MAX_SCHEMA_BYTES)
        self.assertLessEqual(len(json.dumps(review_input(assessment, coverage, draft)).encode()), MAX_INPUT_BYTES)

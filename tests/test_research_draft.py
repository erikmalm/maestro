"""Pure draft contracts using synthetic saved evidence; no services or inference."""
from copy import deepcopy
import hashlib
import json
import unittest

from backend.research_draft import (
    INSTRUCTIONS, MAX_RESPONSE_BYTES, MAX_SAVED_BYTES, MAX_SCHEMA_BYTES, MESSAGE, WIRE_VERSION,
    coverage_hash, gap_draft, has_support, response_schema, validate_response,
    validate_saved_draft, writer_input,
)
from backend.research_evidence import validate_review
from backend.research_passages import evidence_packet_hash
from backend.research_requirements import validate_assessment


CHECKED_AT = "2026-10-23T14:00:00+02:00"


def scope(states=("covered", "missing"), *, long_ids=False):
    request = "Research Testsubject " + " and ".join(f"metric{index}" for index in range(len(states))) + " FY2025 millions USD."
    assessment = validate_assessment({
        "version": 1, "decision": "ready",
        "subjects": [{"id": "s1", "kind": "company", "name": "Testsubject", "identifiers": []}],
        "items": [{"id": "i" + (str(index).zfill(31) if long_ids else str(index)), "subject_ids": ["s1"],
                   "criterion": f"metric{index}", "period": "FY2025", "unit": "millions",
                   "currency": "USD", "freshness": None} for index in range(len(states))],
        "constraints": [], "clarifications": [],
    }, request)
    packet = {"version": 1, "offset_unit": "unicode_codepoint",
              "lookup": {"state": "ready", "search_limited": False, "skipped_count": 0},
              "selection": {"limited": False, "omitted_count": 0}, "sources": [], "passages": []}
    body = "Unselected context \U0001f680 " * 60
    for index in range(len(states) + 1):
        text = f"\U0001f680 Testsubject metric{index} FY2025 USD \u22121\u202f234,50 millions."
        start = len(body)
        body += text + " Other unselected text. "
        packet["passages"].append({"id": f"p{index}", "source_id": "c1", "start": start,
                                   "end": start + len(text), "text": text,
                                   "passage_hash": hashlib.sha256(text.encode()).hexdigest()})
    packet["sources"] = [{"id": "c1", "capture_id": "a" * 32,
                          "content_hash": hashlib.sha256(body.encode()).hexdigest(), "manifest_hash": "b" * 64,
                          "title": "Saved report", "url": "https://reports.example.org/synthetic",
                          "retrieved_at": "2020-01-02T00:00:00+00:00", "published_at": None, "modified_at": None,
                          "stale": True, "completeness": {"maestro_truncated": False, "full_page": False}}]
    rows = []
    for index, (item, state) in enumerate(zip(assessment["items"], states)):
        passages = packet["passages"][index:index + (2 if state == "conflicting" else 1)]
        supports = [{"passage_id": passage["id"], "quote": passage["text"][2:], "temporal_scope": "historical",
                     "period": "FY2025", "observation_date": None} for passage in passages] if state in ("covered", "stale", "conflicting") else []
        rows.append({"item_id": item["id"], "state": state, "reason": f"Decision for metric{index}.", "supports": supports})
    return assessment, validate_review({"version": 1, "items": rows}, assessment, packet, CHECKED_AT)


def response(review):
    return {"version": WIRE_VERSION, "items": {row["item_id"]: {
        "kind": "fact", "text": row["supports"][0]["quote"], "support_indexes": [0], "gap_reason": None,
    } for row in review["items"] if row["state"] == "covered"}}


class ResearchDraftTests(unittest.TestCase):
    def setUp(self):
        self.assessment, self.review = scope()
        self.raw = response(self.review)

    def validate(self, raw=None):
        return validate_response(self.raw if raw is None else raw, self.assessment, self.review)

    def reject(self, raw):
        with self.assertRaisesRegex(ValueError, "research draft"):
            self.validate(raw)

    def test_prose_and_gap_preserve_exact_qualifiers_full_support_and_hashes(self):
        original = deepcopy((self.assessment, self.review, self.raw))
        saved = self.validate()
        self.assertEqual((saved["version"], saved["state"], saved["reason"]), (2, "unreviewed", "draft_requires_review"))
        self.assertEqual([row["item_id"] for row in saved["items"]], ["i0", "i1"])
        self.assertEqual([row["kind"] for row in saved["items"]], ["fact", "gap"])
        for item, row in zip(self.assessment["items"], saved["items"]):
            for key in ("subject_ids", "criterion", "period", "unit", "currency", "freshness"):
                self.assertEqual(row[key], item[key])
        self.assertEqual(saved["items"][0]["supports"], self.review["items"][0]["supports"])
        self.assertIn("\u22121\u202f234,50 millions", saved["items"][0]["text"])
        packet = {key: self.review[key] for key in ("version", "offset_unit", "lookup", "selection", "sources", "passages")}
        self.assertEqual(saved["packet_hash"], evidence_packet_hash(packet))
        self.assertEqual(saved["coverage_hash"], coverage_hash(self.review))
        self.assertEqual(validate_saved_draft(saved, self.assessment, self.review), saved)
        saved["items"][0]["supports"][0]["quote"] = "changed"
        self.assertEqual((self.assessment, self.review, self.raw), original)

    def test_interpretation_remains_explicit_and_is_not_treated_as_verified(self):
        self.raw["items"]["i0"].update(kind="interpretation", text="This suggests a period-specific result.")
        saved = self.validate()
        self.assertEqual(saved["items"][0]["kind"], "interpretation")
        self.assertEqual(saved["state"], "unreviewed")
        self.assertIn("independent", INSTRUCTIONS)

    def test_covered_gaps_have_only_server_authored_text(self):
        for reason in ("calculation_required", "insufficient_evidence"):
            self.raw["items"]["i0"] = {"kind": "gap", "text": "", "support_indexes": [], "gap_reason": reason}
            saved = self.validate()
            row = saved["items"][0]
            self.assertEqual((row["coverage_state"], row["gap_reason"], row["supports"]), ("covered", reason, []))
            self.assertTrue(row["text"])
            self.assertEqual(validate_saved_draft(saved, self.assessment, self.review), saved)
            for mutation in ({"text": "Computed result: 99"}, {"support_indexes": [0]}, {"gap_reason": "coverage_gap"}):
                raw = deepcopy(self.raw)
                raw["items"]["i0"].update(mutation)
                self.reject(raw)
        self.assertIn("calculation_required", INSTRUCTIONS)
        self.assertIn("Derived arithmetic", MESSAGE)

    def test_noncovered_states_remain_fixed_visible_gaps(self):
        assessment, review = scope(("covered", "missing", "stale", "conflicting", "unavailable"))
        saved = validate_response(response(review), assessment, review)
        self.assertEqual([row["coverage_state"] for row in saved["items"]], ["covered", "missing", "stale", "conflicting", "unavailable"])
        for row in saved["items"][1:]:
            self.assertEqual((row["kind"], row["gap_reason"], row["supports"]), ("gap", "coverage_gap", []))
        self.assertEqual(validate_saved_draft(saved, assessment, review), saved)

    def test_closed_covered_id_map_rejects_omissions_extras_and_legacy_calculations(self):
        for mutation in (
            lambda raw: raw.update(version=1),
            lambda raw: raw.update(items=[]),
            lambda raw: raw["items"].clear(),
            lambda raw: raw["items"].update(i1=deepcopy(raw["items"]["i0"])),
            lambda raw: raw["items"]["i0"].update(item_id="i0"),
            lambda raw: raw["items"]["i0"].update(kind="calculation"),
            lambda raw: raw["items"]["i0"].update(calculation={"operation": "sum"}),
            lambda raw: raw["items"]["i0"].update(supports=[{"passage_id": "p0", "quote": "USD"}]),
        ):
            raw = deepcopy(self.raw)
            mutation(raw)
            self.reject(raw)

    def test_indexes_cannot_name_other_items_or_create_shortened_support(self):
        assessment, review = scope(("covered", "covered"))
        raw = response(review)
        raw["items"]["i0"]["support_indexes"] = [1]
        with self.assertRaisesRegex(ValueError, "research draft"):
            validate_response(raw, assessment, review)
        for indexes in ([], [True], [-1], [1], [0, 0], [0.0], ["0"], "p1", [[0]]):
            raw = deepcopy(self.raw)
            raw["items"]["i0"]["support_indexes"] = indexes
            self.reject(raw)
        for mutation in ({"text": ""}, {"text": " "}, {"text": "x\x00"}, {"text": "x" * 1201}, {"gap_reason": "calculation_required"}):
            raw = deepcopy(self.raw)
            raw["items"]["i0"].update(mutation)
            self.reject(raw)

    def test_indexes_select_complete_accepted_supports_in_the_declared_order(self):
        assessment, review = scope(("covered",))
        packet = {key: review[key] for key in ("version", "offset_unit", "lookup", "selection", "sources", "passages")}
        supports = [{"passage_id": passage["id"], "quote": passage["text"][2:], "temporal_scope": "historical",
                     "period": "FY2025", "observation_date": None} for passage in packet["passages"]]
        review = validate_review({"version": 1, "items": [{"item_id": "i0", "state": "covered",
                                 "reason": "Two accepted supports.", "supports": supports}]},
                                 assessment, packet, CHECKED_AT)
        raw = response(review)
        raw["items"]["i0"]["support_indexes"] = [1, 0]
        saved = validate_response(raw, assessment, review)
        self.assertEqual(saved["items"][0]["supports"], list(reversed(review["items"][0]["supports"])))
        self.assertEqual(validate_saved_draft(saved, assessment, review), saved)

    def test_history_revalidates_qualifiers_support_identity_pins_and_fixed_gaps(self):
        saved = self.validate()
        for mutation in (
            lambda value: value.update(version=1),
            lambda value: value.update(state="complete"),
            lambda value: value.update(coverage_hash="0" * 64),
            lambda value: value["items"].reverse(),
            lambda value: value["items"][0].update(unit="billions"),
            lambda value: value["items"][0]["supports"][0].update(quote="USD"),
            lambda value: value["items"][0]["supports"][0].update(start=0),
            lambda value: value["items"][0]["supports"][0].update(suitability="verified"),
            lambda value: value["items"][1].update(text="A made-up gap"),
            lambda value: value["items"].append(deepcopy(value["items"][0])),
        ):
            changed = deepcopy(saved)
            mutation(changed)
            with self.assertRaisesRegex(ValueError, "research draft"):
                validate_saved_draft(changed, self.assessment, self.review)
        changed_review = deepcopy(self.review)
        changed_review["sources"][0]["capture_id"] = "c" * 32
        with self.assertRaisesRegex(ValueError, "research draft"):
            validate_saved_draft(saved, self.assessment, changed_review)

    def test_canonical_hash_is_key_order_independent_and_covers_all_decisions(self):
        reordered = dict(reversed(list(self.review.items())))
        self.assertEqual(coverage_hash(reordered), coverage_hash(self.review))
        changed = deepcopy(self.review)
        changed["items"][0]["reason"] += " changed"
        self.assertNotEqual(coverage_hash(changed), coverage_hash(self.review))
        changed["items"][0]["supports"][0]["start"] += 1
        with self.assertRaisesRegex(ValueError, "research draft"):
            writer_input(self.assessment, changed)

    def test_unavailable_artifacts_do_not_need_writer_support_or_prose(self):
        assessment, review = scope(("missing", "unavailable"))
        self.assertFalse(has_support(review))
        saved = gap_draft(assessment, review)
        self.assertEqual((saved["state"], saved["reason"]), ("unavailable", "no_supported_facts"))
        self.assertEqual(validate_saved_draft(saved, assessment, review), saved)
        for reason in ("writer_capacity", "source_unavailable"):
            saved = gap_draft(self.assessment, self.review, reason)
            self.assertEqual(validate_saved_draft(saved, self.assessment, self.review), saved)
        with self.assertRaisesRegex(ValueError, "research draft"):
            gap_draft(self.assessment, self.review)
        with self.assertRaisesRegex(ValueError, "research draft"):
            response_schema(assessment, review)

    def test_bounded_schema_input_and_saved_size_at_maximum_item_count(self):
        assessment, review = scope(("covered",) * 16, long_ids=True)
        schema = response_schema(assessment, review)
        self.assertEqual(set(schema["properties"]["items"]["properties"]), {row["item_id"] for row in review["items"]})
        self.assertEqual(schema["properties"]["version"]["const"], 2)
        self.assertLess(len(json.dumps(schema).encode()), MAX_SCHEMA_BYTES)
        supplied = writer_input(assessment, review)
        self.assertEqual(supplied["coverage"], review)
        self.assertEqual(supplied["assessment"], assessment)
        supplied["coverage"]["sources"][0]["title"] = "changed"
        self.assertEqual(review["sources"][0]["title"], "Saved report")
        saved = validate_response(response(review), assessment, review)
        self.assertLess(len(json.dumps(saved).encode()), MAX_SAVED_BYTES)
        self.assertEqual(validate_saved_draft(saved, assessment, review), saved)
        oversized = deepcopy(self.raw)
        oversized["items"]["i0"]["text"] = "x" * MAX_RESPONSE_BYTES
        self.reject(oversized)


if __name__ == "__main__":
    unittest.main()

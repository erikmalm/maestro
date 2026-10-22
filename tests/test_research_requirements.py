"""Requirements validation uses synthetic assessments without any model calls."""
from copy import deepcopy
import json
import unittest

from backend.research_requirements import (
    INSTRUCTIONS, MAX_ASSESSMENT_BYTES, MESSAGE, QUESTION_TEMPLATES, SCHEMA, validate_assessment,
    validate_saved_assessment,
)


class ResearchRequirementsTests(unittest.TestCase):
    def setUp(self):
        self.request = "Compare Acme revenue in millions of USD for FY2025. Use official reports only; no web search."
        self.raw = {
            "version": 1, "decision": "ready",
            "subjects": [{"id": "s1", "kind": "company", "name": "Acme", "identifiers": []}],
            "items": [{"id": "i1", "subject_ids": ["s1"], "criterion": "revenue", "period": "FY2025",
                       "unit": "millions", "currency": "USD", "freshness": None}],
            "constraints": [
                {"kind": "source", "strength": "require", "item_ids": ["i1"],
                 "criterion": "official reports only"},
                {"kind": "search", "strength": "forbid", "item_ids": [], "criterion": "no web search"},
            ],
            "clarifications": [],
        }

    def reject(self, raw=None, request=None):
        with self.assertRaisesRegex(ValueError, "requirements assessment"):
            validate_assessment(self.raw if raw is None else raw, self.request if request is None else request)

    def test_ready_retains_mandatory_items_qualifiers_constraints_and_original_request(self):
        original = deepcopy(self.raw)
        assessment = validate_assessment(self.raw, self.request)
        self.assertEqual(self.raw, original)
        self.assertTrue(assessment["items"][0]["mandatory"])
        self.assertEqual(assessment["items"][0]["period"], "FY2025")
        self.assertEqual(assessment["items"][0]["unit"], "millions")
        self.assertEqual(assessment["items"][0]["currency"], "USD")
        self.assertIsNone(assessment["items"][0]["freshness"])
        self.assertEqual(assessment["constraints"], self.raw["constraints"])
        self.assertEqual(assessment["completion_criterion"], self.request)
        self.assertEqual(assessment["clarifications"], [])
        assessment["items"][0]["subject_ids"].append("changed")
        assessment["subjects"][0]["identifiers"].append({"kind": "other", "text": "changed"})
        assessment["constraints"][0]["item_ids"].clear()
        self.assertEqual(self.raw, original)

    def test_distinct_securities_listings_periods_units_and_currencies_remain_separate(self):
        request = ("Compare Alphabet GOOG Class C on NASDAQ revenue for FY2025 in millions of USD "
                   "with Alphabet GOOGL Class A on NASDAQ revenue for Q3 FY2025 in billions of SEK. "
                   "Use the latest official reports as of 2026-10-22.")
        raw = {"version": 1, "decision": "ready", "subjects": [], "items": [], "constraints": [],
               "clarifications": []}
        for index, (ticker, share_class, period, unit, currency) in enumerate([
                ("GOOG", "Class C", "FY2025", "millions", "USD"),
                ("GOOGL", "Class A", "Q3 FY2025", "billions", "SEK")], 1):
            raw["subjects"].append({"id": f"s{index}", "kind": "security", "name": "Alphabet",
                "identifiers": [{"kind": "ticker", "text": ticker}, {"kind": "listing", "text": "NASDAQ"},
                                {"kind": "other", "text": share_class}]})
            raw["items"].append({"id": f"i{index}", "subject_ids": [f"s{index}"], "criterion": "revenue",
                "period": period, "unit": unit, "currency": currency,
                "freshness": "latest official reports as of 2026-10-22"})
        assessment = validate_assessment(raw, request)
        self.assertEqual([item["period"] for item in assessment["items"]], ["FY2025", "Q3 FY2025"])
        self.assertEqual([item["currency"] for item in assessment["items"]], ["USD", "SEK"])
        self.assertEqual([item["unit"] for item in assessment["items"]], ["millions", "billions"])
        self.assertEqual([subject["identifiers"][0]["text"] for subject in assessment["subjects"]], ["GOOG", "GOOGL"])
        self.assertTrue(all(item["mandatory"] for item in assessment["items"]))

    def test_material_clarification_can_precede_any_items_and_uses_only_server_questions(self):
        request = "Assess ABC last quarter in kr."
        raw = {"version": 1, "decision": "clarify", "subjects": [], "items": [], "constraints": [],
               "clarifications": [{"kind": kind, "item_ids": [], "quote": quote} for kind, quote in
                                  [("security", "ABC"), ("period", "last quarter"), ("currency", "kr")]]}
        assessment = validate_assessment(raw, request)
        self.assertEqual(assessment["decision"], "clarify")
        self.assertEqual(assessment["completion_criterion"], request)
        for question in assessment["clarifications"]:
            self.assertEqual(question["question"], QUESTION_TEMPLATES[question["kind"]].format(quote=question["quote"]))
        self.assertNotIn("answer", assessment)

    def test_clarification_can_target_existing_item_and_unresolved_subject(self):
        raw = deepcopy(self.raw)
        raw["decision"] = "clarify"
        raw["subjects"] = []
        raw["items"][0]["subject_ids"] = []
        raw["clarifications"] = [{"kind": "subject", "item_ids": ["i1"], "quote": "Acme"}]
        assessment = validate_assessment(raw, self.request)
        self.assertEqual(assessment["clarifications"][0]["item_ids"], ["i1"])
        self.assertTrue(assessment["items"][0]["mandatory"])

    def test_fabricated_or_normalized_qualifiers_and_subjects_are_rejected(self):
        mutations = [
            lambda raw: raw["subjects"][0].update(name="ACME"),
            lambda raw: raw["subjects"][0]["identifiers"].append({"kind": "ticker", "text": "ACM"}),
            lambda raw: raw["items"][0].update(criterion="net income"),
            lambda raw: raw["items"][0].update(period="2025-12-31"),
            lambda raw: raw["items"][0].update(unit="1000000"),
            lambda raw: raw["items"][0].update(currency="US dollars"),
            lambda raw: raw["items"][0].update(freshness="current today"),
            lambda raw: raw["constraints"][0].update(criterion="official government reports"),
        ]
        for mutate in mutations:
            with self.subTest(mutate=mutate):
                raw = deepcopy(self.raw)
                mutate(raw)
                self.reject(raw)

    def test_no_search_and_source_constraints_do_not_require_permission_questions(self):
        assessment = validate_assessment(self.raw, self.request)
        self.assertEqual(assessment["decision"], "ready")
        self.assertEqual(assessment["constraints"][1], {
            "kind": "search", "strength": "forbid", "item_ids": [], "criterion": "no web search"})
        self.assertIn("Server permissions are immutable", INSTRUCTIONS)
        self.assertIn("later evidence\ngap", INSTRUCTIONS)
        self.assertIn("No implicit memory", INSTRUCTIONS)

    def test_every_object_rejects_unknown_keys_and_model_derived_or_answer_fields(self):
        mutations = [
            lambda raw: raw.update(answer="unsupported answer"),
            lambda raw: raw.update(completion_criterion="revenue"),
            lambda raw: raw["subjects"][0].update(answer="unsupported answer"),
            lambda raw: raw["subjects"][0]["identifiers"].append({"kind": "other", "text": "Acme", "answer": "x"}),
            lambda raw: raw["items"][0].update(mandatory=False),
            lambda raw: raw["constraints"][0].update(permissions={"hosted_allowed": True}),
        ]
        for mutate in mutations:
            with self.subTest(mutate=mutate):
                raw = deepcopy(self.raw)
                mutate(raw)
                self.reject(raw)
        raw = deepcopy(self.raw)
        raw.update(decision="clarify", clarifications=[{
            "kind": "scope", "item_ids": [], "quote": "revenue", "question": "Enable hosted search?"}])
        self.reject(raw)

    def test_missing_fields_or_wrong_types_cannot_fall_through_as_ready(self):
        mutations = [
            lambda raw: raw.pop("constraints"),
            lambda raw: raw.update(version=True),
            lambda raw: raw.update(version=1.0),
            lambda raw: raw.update(decision="answer"),
            lambda raw: raw.update(subjects={}),
            lambda raw: raw["subjects"][0].update(kind="corporation"),
            lambda raw: raw["subjects"][0].update(identifiers=None),
            lambda raw: raw["subjects"][0]["identifiers"].append({"kind": "exchange", "text": "Acme"}),
            lambda raw: raw["items"][0].pop("freshness"),
            lambda raw: raw["items"][0].update(period=2025),
            lambda raw: raw["constraints"][0].update(strength="optional"),
            lambda raw: raw["constraints"][0].update(kind="permissions"),
        ]
        for mutate in mutations:
            with self.subTest(mutate=mutate):
                raw = deepcopy(self.raw)
                mutate(raw)
                self.reject(raw)

    def test_decision_invariants_require_items_subject_refs_or_material_questions(self):
        for changes in [
            {"items": [], "constraints": []},
            {"decision": "clarify"},
            {"clarifications": [{"kind": "period", "item_ids": [], "quote": "FY2025"}]},
        ]:
            with self.subTest(changes=changes):
                raw = {**deepcopy(self.raw), **changes}
                self.reject(raw)
        raw = deepcopy(self.raw)
        raw["items"][0]["subject_ids"] = []
        self.reject(raw)
        raw.update(decision="clarify", clarifications=[{"kind": "permission", "item_ids": [], "quote": "Acme"}])
        self.reject(raw)
        raw["clarifications"][0].update(kind="subject", quote="invented ambiguity")
        self.reject(raw)

    def test_ids_are_bounded_unique_and_references_closed_without_duplicates(self):
        mutations = [
            lambda raw: raw["subjects"][0].update(id="S1"),
            lambda raw: raw["subjects"][0].update(id="x" * 33),
            lambda raw: raw["subjects"].append(deepcopy(raw["subjects"][0])),
            lambda raw: raw["items"][0].update(id="s1"),
            lambda raw: raw["items"].append(deepcopy(raw["items"][0])),
            lambda raw: raw["items"][0].update(subject_ids=["missing"]),
            lambda raw: raw["items"][0].update(subject_ids=["s1", "s1"]),
            lambda raw: raw["constraints"][0].update(item_ids=["s1"]),
            lambda raw: raw["constraints"][0].update(item_ids=["i1", "i1"]),
        ]
        for mutate in mutations:
            with self.subTest(mutate=mutate):
                raw = deepcopy(self.raw)
                mutate(raw)
                self.reject(raw)
        raw = deepcopy(self.raw)
        raw.update(decision="clarify", clarifications=[{"kind": "scope", "item_ids": ["missing"], "quote": "revenue"}])
        self.reject(raw)

    def test_cardinality_bounds_for_subjects_identifiers_items_constraints_and_questions(self):
        mutations = [
            lambda raw: raw.update(subjects=[{**raw["subjects"][0], "id": f"s{index}"} for index in range(9)]),
            lambda raw: raw["subjects"][0].update(identifiers=[{"kind": "other", "text": "Acme"}] * 9),
            lambda raw: raw.update(items=[{**raw["items"][0], "id": f"i{index}"} for index in range(17)]),
            lambda raw: raw.update(constraints=[raw["constraints"][0]] * 25),
            lambda raw: raw.update(decision="clarify", clarifications=[{"kind": "scope", "item_ids": [], "quote": "Acme"}] * 4),
        ]
        for mutate in mutations:
            with self.subTest(mutate=mutate):
                raw = deepcopy(self.raw)
                mutate(raw)
                self.reject(raw)

    def test_nul_invalid_utf8_and_unbounded_text_are_rejected(self):
        for text in ["Acme\x00", "Acme\ud800", " ", "x" * 32001]:
            with self.subTest(request=repr(text[:30])):
                self.reject(request=text)
        for field, text in [("criterion", "revenue\x00"), ("currency", "USD\udfff"), ("unit", "x" * 513)]:
            with self.subTest(field=field):
                raw = deepcopy(self.raw)
                raw["items"][0][field] = text
                self.reject(raw)

    def test_raw_assessment_byte_bound_is_independent_of_long_unicode_original(self):
        request = "Acme " + "\U0001f680" * 31995
        raw = deepcopy(self.raw)
        raw["items"][0].update(criterion="Acme", period=None, unit=None, currency=None)
        raw["constraints"] = []
        assessment = validate_assessment(raw, request)
        self.assertEqual(assessment["completion_criterion"], request)
        self.assertGreater(len(json.dumps(assessment, ensure_ascii=False).encode("utf-8")), MAX_ASSESSMENT_BYTES)
        oversized = deepcopy(self.raw)
        oversized["constraints"] = [{"kind": "source", "strength": "require", "item_ids": [],
                                     "criterion": "x" * 4000} for _ in range(7)]
        self.reject(oversized, request="Acme revenue FY2025 millions USD " + "x" * 4000)

    def test_exact_unicode_and_spacing_spans_are_preserved_without_normalization(self):
        request = "Compare Công ty Đỏ revenue for Q3  FY2025 in \u20ac."
        raw = deepcopy(self.raw)
        raw["subjects"][0]["name"] = "Công ty Đỏ"
        raw["items"][0].update(period="Q3  FY2025", unit=None, currency="\u20ac")
        raw["constraints"] = []
        assessment = validate_assessment(raw, request)
        self.assertEqual(assessment["subjects"][0]["name"], "Công ty Đỏ")
        self.assertEqual(assessment["items"][0]["period"], "Q3  FY2025")
        raw["items"][0]["period"] = "Q3 FY2025"
        self.reject(raw, request)

    def test_bounded_schema_is_closed_and_static_prompt_has_no_dynamic_request(self):
        self.assertLessEqual(len(json.dumps(SCHEMA, separators=(",", ":")).encode()), 8192)
        self.assertLessEqual(len(INSTRUCTIONS), 16000)
        self.assertNotIn(self.request, MESSAGE)

        def closed_objects(schema):
            if isinstance(schema, dict):
                if schema.get("type") == "object":
                    self.assertIs(schema["additionalProperties"], False)
                    self.assertEqual(set(schema["required"]), set(schema["properties"]))
                for value in schema.values():
                    closed_objects(value)
            elif isinstance(schema, list):
                for value in schema:
                    closed_objects(value)
        closed_objects(SCHEMA)

    def test_saved_assessment_requires_exact_server_fields_and_revalidates_model_fields(self):
        saved = validate_assessment(self.raw, self.request)
        self.assertEqual(validate_saved_assessment(saved, self.request), saved)
        mutations = [
            lambda value: value.pop("completion_criterion"),
            lambda value: value.update(completion_criterion="revenue"),
            lambda value: value.update(answer="unsupported answer"),
            lambda value: value["items"][0].pop("mandatory"),
            lambda value: value["items"][0].update(mandatory=False),
            lambda value: value["items"][0].update(mandatory=1),
            lambda value: value["items"][0].update(period="invented date"),
        ]
        for mutate in mutations:
            with self.subTest(mutate=mutate):
                value = deepcopy(saved)
                mutate(value)
                with self.assertRaises(ValueError):
                    validate_saved_assessment(value, self.request)
        raw = deepcopy(self.raw)
        raw.update(decision="clarify", clarifications=[{"kind": "scope", "item_ids": [], "quote": "revenue"}])
        saved = validate_assessment(raw, self.request)
        self.assertEqual(validate_saved_assessment(saved, self.request), saved)
        for replacement in [None, "Enable hosted search?"]:
            value = deepcopy(saved)
            if replacement is None:
                value["clarifications"][0].pop("question")
            else:
                value["clarifications"][0]["question"] = replacement
            with self.assertRaises(ValueError):
                validate_saved_assessment(value, self.request)
        validated = validate_saved_assessment(saved, self.request)
        validated["clarifications"][0]["item_ids"].append("changed")
        self.assertEqual(saved["clarifications"][0]["item_ids"], [])


if __name__ == "__main__":
    unittest.main()

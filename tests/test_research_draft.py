"""Pure saved-evidence writer contracts; no services, archives or inference."""
from copy import deepcopy
from decimal import ROUND_UP, localcontext
import hashlib
import json
import sys
import unittest
import unicodedata

from backend.research_draft import (
    INSTRUCTIONS, MAX_NUMBER, MAX_RESPONSE_BYTES, MAX_SAVED_BYTES, MAX_SCHEMA_BYTES, MESSAGE,
    coverage_hash, gap_draft, has_support, response_schema, validate_response,
    validate_saved_draft, writer_input,
)
from backend.research_evidence import unavailable_review, validate_review, validate_saved_review
from backend.research_passages import evidence_packet_hash, select_packet, validate_packet
from backend.research_requirements import validate_assessment


CHECKED_AT = "2026-10-23T14:00:00+02:00"


def scope(states=("covered", "missing"), *, quotes=None, long_ids=False):
    request = "Research Testsubject " + " and ".join(f"metric{index}" for index in range(len(states))) + " FY2025 USD."
    assessment = validate_assessment({
        "version": 1, "decision": "ready",
        "subjects": [{"id": "s1", "kind": "company", "name": "Testsubject", "identifiers": []}],
        "items": [{"id": "i" + (str(index).zfill(31) if long_ids else str(index)), "subject_ids": ["s1"],
                   "criterion": f"metric{index}", "period": "FY2025", "unit": None,
                   "currency": "USD", "freshness": None} for index in range(len(states))],
        "constraints": [], "clarifications": [],
    }, request)
    packet = {"version": 1, "offset_unit": "unicode_codepoint",
              "lookup": {"state": "ready", "search_limited": False, "skipped_count": 0},
              "selection": {"limited": False, "omitted_count": 0}, "sources": [], "passages": []}
    body = "Unselected context \U0001f680 \x00 " * 60
    for index in range(len(states) + 1):
        quote = quotes[index] if quotes and index < len(quotes) else f"Testsubject metric{index} FY2025 USD old 10 new 12."
        text = "\U0001f680 " + quote
        start = len(body)
        body += text + " Other unselected text. "
        packet["passages"].append({"id": "p" + (str(index).zfill(31) if long_ids else str(index)),
                                    "source_id": "c1", "start": start, "end": start + len(text),
                                    "text": text, "passage_hash": hashlib.sha256(text.encode()).hexdigest()})
    packet["sources"] = [{"id": "c1", "capture_id": "a" * 32,
                          "content_hash": hashlib.sha256(body.encode()).hexdigest(), "manifest_hash": "b" * 64,
                          "title": "Public\x00 saved report", "url": "https://reports.example.org/synthetic",
                          "retrieved_at": "2020-01-02T00:00:00+00:00", "published_at": None, "modified_at": None,
                          "stale": True, "completeness": {"maestro_truncated": False, "full_page": False}}]
    rows = []
    for index, (item, state) in enumerate(zip(assessment["items"], states)):
        passages = packet["passages"][index:index + (2 if state == "conflicting" else 1)]
        supports = [{"passage_id": passage["id"], "quote": passage["text"][2:], "temporal_scope": "historical",
                     "period": "FY2025", "observation_date": None} for passage in passages] if state in ("covered", "stale", "conflicting") else []
        rows.append({"item_id": item["id"], "state": state, "reason": f"Evidence decision for metric{index}.", "supports": supports})
    return assessment, validate_review({"version": 1, "items": rows}, assessment, packet, CHECKED_AT)


def response(review):
    return {"version": 1, "items": {row["item_id"]: {
        "kind": "fact", "text": "The saved evidence provides a supported draft statement.",
        "supports": [{key: row["supports"][0][key] for key in ("passage_id", "quote")}], "calculation": None,
    } for row in review["items"] if row["state"] == "covered"}}


class ResearchDraftTests(unittest.TestCase):
    def setUp(self):
        self.assessment, self.review = scope()
        self.raw = response(self.review)

    def validate(self, raw=None, *, assessment=None, review=None):
        return validate_response(self.raw if raw is None else raw,
                                 self.assessment if assessment is None else assessment,
                                 self.review if review is None else review)

    def reject(self, raw):
        with self.assertRaisesRegex(ValueError, "research draft"):
            validate_response(raw, self.assessment, self.review)

    def calculation(self, quote, first, second, *, operation="sum", precision=2):
        # A visible word after final punctuation distinguishes it from a
        # packet cropped in the middle of a decimal or grouped number.
        assessment, review = scope(("covered",), quotes=["FY2025 USD " + quote + " End."])
        raw = response(review)
        raw["items"]["i0"].update(kind="calculation", calculation={
            "operation": operation, "operands": [{"value": first, "support_index": 0},
                                                   {"value": second, "support_index": 0}], "precision": precision})
        return assessment, review, raw

    def test_supported_draft_and_server_gap_preserve_order_qualifiers_and_exact_provenance(self):
        original = deepcopy((self.assessment, self.review, self.raw))
        saved = self.validate()
        self.assertEqual((saved["state"], saved["reason"]), ("unreviewed", "draft_requires_review"))
        self.assertEqual([row["item_id"] for row in saved["items"]], ["i0", "i1"])
        self.assertEqual([row["kind"] for row in saved["items"]], ["fact", "gap"])
        self.assertEqual(saved["items"][1]["supports"], [])
        self.assertIsNone(saved["items"][1]["calculation"])
        for item, row in zip(self.assessment["items"], saved["items"]):
            for key in ("subject_ids", "criterion", "period", "unit", "currency", "freshness"):
                self.assertEqual(row[key], item[key])
        support = saved["items"][0]["supports"][0]
        accepted = self.review["items"][0]["supports"][0]
        self.assertEqual(support, {key: accepted[key] for key in ("passage_id", "quote", "source_id", "start", "end")})
        self.assertEqual(saved["coverage_hash"], coverage_hash(self.review))
        packet = {key: self.review[key] for key in ("version", "offset_unit", "lookup", "selection", "sources", "passages")}
        self.assertEqual(saved["packet_hash"], evidence_packet_hash(packet))
        self.assertEqual(validate_saved_draft(saved, self.assessment, self.review), saved)
        saved["items"][0]["supports"][0]["quote"] = "changed"
        self.assertEqual((self.assessment, self.review, self.raw), original)

    def test_unique_subquote_derives_original_unicode_codepoint_offsets(self):
        raw = deepcopy(self.raw)
        raw["items"]["i0"]["supports"][0]["quote"] = "USD old 10 new 12."
        saved = self.validate(raw)
        accepted = self.review["items"][0]["supports"][0]
        actual = saved["items"][0]["supports"][0]
        self.assertEqual(actual["start"], accepted["start"] + accepted["quote"].index(actual["quote"]))
        self.assertEqual(actual["end"], actual["start"] + len(actual["quote"]))
        self.assertEqual(validate_saved_draft(saved, self.assessment, self.review), saved)

    def test_repeated_subquotes_and_duplicate_support_pairs_are_rejected(self):
        assessment, review = scope(("covered",), quotes=["FY2025 USD 42 then 42 again."])
        raw = response(review)
        raw["items"]["i0"]["supports"][0]["quote"] = "42"
        with self.assertRaises(ValueError):
            validate_response(raw, assessment, review)
        raw = deepcopy(self.raw)
        raw["items"]["i0"]["supports"].append(deepcopy(raw["items"]["i0"]["supports"][0]))
        self.reject(raw)

    def test_other_item_and_unaccepted_packet_passages_cannot_support_a_row(self):
        assessment, review = scope(("covered", "covered"))
        raw = response(review)
        raw["items"]["i0"]["supports"] = deepcopy(raw["items"]["i1"]["supports"])
        with self.assertRaises(ValueError):
            validate_response(raw, assessment, review)
        for identity in ("p1", "p2", "unknown", "c1"):
            raw = deepcopy(self.raw)
            raw["items"]["i0"]["supports"][0]["passage_id"] = identity
            self.reject(raw)
        raw = deepcopy(self.raw)
        raw["items"]["i0"]["supports"][0]["quote"] = self.review["passages"][0]["text"]
        self.reject(raw)  # Includes text outside the accepted quote's range.

    def test_all_noncovered_states_remain_fixed_server_gaps(self):
        assessment, review = scope(("covered", "missing", "stale", "conflicting", "unavailable"))
        saved = validate_response(response(review), assessment, review)
        self.assertEqual([row["coverage_state"] for row in saved["items"]], ["covered", "missing", "stale", "conflicting", "unavailable"])
        for row in saved["items"][1:]:
            self.assertEqual(row["kind"], "gap")
            self.assertEqual(row["supports"], [])
            self.assertIsNone(row["calculation"])
            self.assertNotIn("Evidence decision", row["text"])
        raw = response(review)
        raw["items"]["i2"] = deepcopy(raw["items"]["i0"])
        with self.assertRaises(ValueError):
            validate_response(raw, assessment, review)

    def test_no_supported_facts_has_no_model_contract_and_gets_a_server_artifact(self):
        assessment, review = scope(("missing", "stale", "unavailable"))
        self.assertFalse(has_support(review))
        for operation in (response_schema, writer_input):
            with self.assertRaises(ValueError):
                operation(assessment, review)
        with self.assertRaises(ValueError):
            validate_response({"version": 1, "items": {}}, assessment, review)
        saved = gap_draft(assessment, review)
        self.assertEqual((saved["state"], saved["reason"]), ("unavailable", "no_supported_facts"))
        self.assertTrue(all(row["kind"] == "gap" for row in saved["items"]))
        self.assertEqual(validate_saved_draft(saved, assessment, review), saved)
        empty_review = unavailable_review(assessment, {"state": "disabled", "search_limited": False, "skipped_count": 0},
                                          {"limited": False, "omitted_count": 0}, "Saving is disabled.", CHECKED_AT)
        empty = gap_draft(assessment, empty_review)
        self.assertEqual(validate_saved_draft(empty, assessment, empty_review), empty)

    def test_capacity_and_source_loss_can_withhold_covered_rows_without_inventing_missing_evidence(self):
        self.assertTrue(has_support(self.review))
        with self.assertRaises(ValueError):
            gap_draft(self.assessment, self.review)
        for reason in ("writer_capacity", "source_unavailable"):
            saved = gap_draft(self.assessment, self.review, reason)
            self.assertEqual(saved["state"], "unavailable")
            self.assertEqual(saved["items"][0]["coverage_state"], "covered")
            self.assertTrue(all(row["kind"] == "gap" and not row["supports"] for row in saved["items"]))
            self.assertNotIn("No supported fact", saved["items"][0]["text"])
            self.assertEqual(validate_saved_draft(saved, self.assessment, self.review), saved)
        with self.assertRaises(ValueError):
            gap_draft(self.assessment, self.review, "model_invented_gap")

    def test_writer_input_carries_all_decisions_and_exact_packet_once_without_repeated_quotes(self):
        original = deepcopy((self.assessment, self.review))
        supplied = writer_input(self.assessment, self.review)
        self.assertEqual(supplied["assessment"], self.assessment)
        self.assertEqual(supplied["coverage"]["checked_at"], self.review["checked_at"])
        self.assertEqual(supplied["packet"]["sources"], self.review["sources"])
        self.assertEqual(supplied["packet"]["passages"], self.review["passages"])
        for row, accepted in zip(supplied["coverage"]["items"], self.review["items"]):
            for key in ("item_id", "model_state", "state", "reason", "suitability_reason"):
                self.assertEqual(row[key], accepted[key])
            for reference, support in zip(row["supports"], accepted["supports"]):
                self.assertNotIn("quote", reference)
                passage = next(value for value in supplied["packet"]["passages"] if value["id"] == reference["passage_id"])
                self.assertEqual(passage["text"][reference["quote_start"]:reference["quote_end"]], support["quote"])
        supplied["packet"]["sources"][0]["title"] = "changed"
        supplied["assessment"]["items"][0]["criterion"] = "changed"
        self.assertEqual((self.assessment, self.review), original)

    def test_schema_is_closed_shared_bounded_and_scoped_to_actual_covered_ids(self):
        assessment, review = scope(("covered",) * 16, long_ids=True)
        packet = {key: deepcopy(review[key]) for key in ("version", "offset_unit", "lookup", "selection", "sources", "passages")}
        packet["passages"] = []
        body = "Unselected source prefix. " * 60
        for index in range(40):
            text = f"FY2025 USD saved value {index}."
            start = len(body)
            body += text + "\n"
            packet["passages"].append({"id": "p" + str(index).zfill(31), "source_id": "c1", "start": start,
                                        "end": start + len(text), "text": text,
                                        "passage_hash": hashlib.sha256(text.encode()).hexdigest()})
        packet["sources"][0]["content_hash"] = hashlib.sha256(body.encode()).hexdigest()
        rows = []
        for index, item in enumerate(assessment["items"]):
            supports = []
            for number in range(3):
                passage = packet["passages"][(index * 3 + number) % 40]
                supports.append({"passage_id": passage["id"], "quote": passage["text"], "temporal_scope": "historical",
                                 "period": "FY2025", "observation_date": None})
            rows.append({"item_id": item["id"], "state": "covered", "reason": "Supported in selected evidence.", "supports": supports})
        review = validate_review({"version": 1, "items": rows}, assessment, packet, CHECKED_AT)
        schema = response_schema(assessment, review)
        self.assertLess(len(json.dumps(schema).encode()), MAX_SCHEMA_BYTES)
        items = schema["properties"]["items"]
        self.assertEqual(items["required"], [item["id"] for item in assessment["items"]])
        self.assertEqual(list(schema["$defs"]), ["row"])
        self.assertTrue(all(value == {"$ref": "#/$defs/row"} for value in items["properties"].values()))
        self.assertEqual(len(schema["$defs"]["row"]["properties"]["supports"]["items"]["properties"]["passage_id"]["enum"]), 40)
        partial = response_schema(self.assessment, self.review)
        self.assertEqual(partial["properties"]["items"]["required"], ["i0"])
        self.assertEqual(partial["$defs"]["row"]["properties"]["supports"]["items"]["properties"]["passage_id"]["enum"], ["p0"])

        def closed(value):
            if isinstance(value, dict):
                if value.get("type") == "object":
                    self.assertIs(value["additionalProperties"], False)
                    self.assertEqual(set(value["required"]), set(value["properties"]))
                for child in value.values():
                    closed(child)
            elif isinstance(value, list):
                for child in value:
                    closed(child)
        closed(schema)
        partial["$defs"]["row"]["properties"]["text"]["maxLength"] = 1
        self.assertEqual(response_schema(self.assessment, self.review)["$defs"]["row"]["properties"]["text"]["maxLength"], 1200)

    def test_raw_root_rows_types_sizes_and_ids_are_strict(self):
        for raw in (None, [], {}, {**self.raw, "version": True}, {**self.raw, "version": 2},
                    {**self.raw, "version": 1.0}, {**self.raw, "answer": "extra"},
                    {"version": 1, "items": []}, {"version": 1, "items": {}}, json.dumps(self.raw)):
            self.reject(raw)
        for mutate in (
            lambda row: row.update(item_id="i0"), lambda row: row.update(answer="extra"),
            lambda row: row.pop("calculation"), lambda row: row.update(kind="gap"),
            lambda row: row.update(text="x" * 1201), lambda row: row.update(text="bad\ud800"),
            lambda row: row.update(text="bad\x00"), lambda row: row.update(supports=[]),
            lambda row: row.update(supports=[row["supports"][0]] * 5),
            lambda row: row["supports"][0].update(source_id="c1"),
            lambda row: row["supports"][0].update(quote="Invented quote"),
            lambda row: row.update(calculation={"result": "42"}),
        ):
            raw = deepcopy(self.raw)
            mutate(raw["items"]["i0"])
            self.reject(raw)
        for identity in ("foreign", "i1", "s1"):
            raw = deepcopy(self.raw)
            raw["items"][identity] = deepcopy(raw["items"]["i0"])
            self.reject(raw)
        oversized = {"version": 1, "items": {"x": "x" * MAX_RESPONSE_BYTES}}
        self.reject(oversized)

    def test_all_four_operations_are_computed_and_saved_revalidated(self):
        cases = (("0.1 and 0.2.", "0.1", "0.2", "sum", 2, "0.30"),
                 ("10 and 12.", "10", "12", "difference", 0, "-2"),
                 ("1 and 3.", "1", "3", "ratio", 6, "0.333333"),
                 ("10 and 12.", "10", "12", "percent_change", 2, "20.00"),
                 ("-10 and -12.", "-10", "-12", "percent_change", 2, "20.00"),
                 ("+10 and +12.", "+10", "+12", "sum", 2, "22.00"))
        for quote, first, second, operation, precision, expected in cases:
            with self.subTest(operation=operation, first=first):
                assessment, review, raw = self.calculation(quote, first, second, operation=operation, precision=precision)
                saved = validate_response(raw, assessment, review)
                calculation = saved["items"][0]["calculation"]
                self.assertEqual(calculation["result"], expected)
                self.assertEqual(calculation["rounding"], "ROUND_HALF_EVEN")
                self.assertEqual(calculation["operands"], raw["items"]["i0"]["calculation"]["operands"])
                self.assertEqual(validate_saved_draft(saved, assessment, review), saved)

    def test_rounding_is_half_even_fixed_precision_and_independent_of_ambient_decimal_context(self):
        for first, expected in (("2.5", "2"), ("3.5", "4"), ("-2.5", "-2"), ("-0.1", "0")):
            with self.subTest(first=first):
                assessment, review, raw = self.calculation(first + " and 0.", first, "0", precision=0)
                with localcontext() as ambient:
                    ambient.prec = 2
                    ambient.rounding = ROUND_UP
                    ambient.Emin, ambient.Emax = -2, 2
                    saved = validate_response(raw, assessment, review)
                self.assertEqual(saved["items"][0]["calculation"]["result"], expected)
        large = "9" * MAX_NUMBER
        assessment, review, raw = self.calculation(large + " and 1.", large, "1", precision=6)
        saved = validate_response(raw, assessment, review)
        self.assertEqual(saved["items"][0]["calculation"]["result"], "1" + "0" * MAX_NUMBER + ".000000")

    def test_numeric_operands_require_exact_whole_plain_ascii_literals(self):
        bad = (("42", "4"), ("-42", "42"), ("\u221242", "42"), ("42.5", "42"),
               ("42,000", "42"), ("42'000", "42"), ("42\u2019000", "42"),
               ("42\u00a0000", "42"), ("42\u202f000", "42"), ("42\u066c000", "42"),
               ("1e+42", "+42"), ("1e-42", "-42"), (".42", "42"),
               ("\u06642", "2"), ("42widgets", "42"), ("metric42", "42"),
               ("0.10", "0.1"), ("NaN", "NaN"), ("Infinity", "Infinity"),
               ("1e2", "1e2"), ("\uff14\uff12", "\uff14\uff12"), ("01", "01"))
        for quoted, value in bad:
            with self.subTest(quoted=quoted, value=value):
                assessment, review, raw = self.calculation("Amounts " + quoted + " and 0.", value, "0")
                with self.assertRaises(ValueError):
                    validate_response(raw, assessment, review)

    def test_subquotes_cannot_crop_numeric_sign_group_exponent_or_decimal_context(self):
        for original, cropped, operand in (("-42", "42", "42"), ("\u221242", "42", "42"),
                                           ("1,234", "234", "234"), ("1e+42", "+42", "+42"),
                                           ("42.5", "42", "42"), ("metric42", "42", "42")):
            with self.subTest(original=original):
                assessment, review, raw = self.calculation("Amount " + original + " and 0.", operand, "0")
                raw["items"]["i0"]["supports"] = [
                    {"passage_id": "p0", "quote": cropped}, {"passage_id": "p0", "quote": "and 0."}]
                raw["items"]["i0"]["calculation"]["operands"][1]["support_index"] = 1
                with self.assertRaises(ValueError):
                    validate_response(raw, assessment, review)

    def test_whitespace_in_exponents_cannot_authorize_mantissa_or_exponent_fragments(self):
        cases = (("42 e-3", "42"), ("42 E - 3", "42"), ("42\te\t+\t3", "42"),
                 ("42\ne -\n3", "42"), ("0.42 e-3", "0.42"), ("42 e-3", "-3"),
                 ("42 E +3", "+3"), ("42 e 3", "3"), ("42 e\u22123", "42"),
                 ("42e-3", "42"), ("42 e3 7", "7"), ("42 e-3 7", "7"),
                 ("42 e3\t7", "7"), ("42 E - 3\n7", "7"), ("42e3 7", "7"))
        for amount, value in cases:
            self.assert_formatted_rejected(amount, value)
        # A separately labelled signed operand and ordinary prose beginning
        # with E remain independent of scientific notation.
        for quote in ("10 and -12.", "10 Euros and -12."):
            assessment, review, raw = self.calculation(quote, "10", "-12")
            saved = validate_response(raw, assessment, review)
            self.assertEqual(saved["items"][0]["calculation"]["result"], "-2.00")

    def test_trailing_mantissa_separators_keep_complete_exponents_in_one_rejected_candidate(self):
        # These unsupported spellings must not yield either a mantissa or an
        # exponent operand. Expectations come from the plain-literal contract,
        # independently of the production candidate grammar.
        mantissas = (("42.", "42"), ("42 .", "42"), ("42..", "42"),
                     ("42. .", "42"), ("42.,", "42"), ("0.42.", "0.42"))
        spacing = (("", "", ""), (" ", " ", ""), ("\t", "\n", "\t"))
        for mantissa, value in mantissas:
            for marker in ("e", "E"):
                for sign in ("", "+", "-"):
                    for before_marker, before_sign, after_sign in spacing:
                        amount = mantissa + before_marker + marker + before_sign + sign + after_sign + "3"
                        exponent = sign + "3" if not after_sign else "3"
                        for fragment in (value, exponent):
                            self.assert_formatted_rejected(amount, fragment, match="research draft")

    def test_trailing_point_exponents_keep_control_and_compatibility_context(self):
        cases = (("42.\x01e +3", "42"), ("42.\x01e +3", "+3"),
                 ("42.\u200bE -3", "42"), ("42.\u200bE -3", "-3"),
                 ("42\uff0e\uff45 +3", "42"), ("42\uff0e\uff45 +3", "+3"),
                 ("42\u2024 E \u22123", "42"), ("42\u2024 E \u22123", "3"),
                 ("42.\u0301 e +3", "42"), ("42.\u066c e +3", "+3"))
        for amount, value in cases:
            self.assert_formatted_rejected(amount, value, match="research draft")
        # Even NUL may exist in the archived packet outside the clean accepted
        # quote. It must not detach a signed exponent suffix from its mantissa.
        for control in ("\x00", "\x01", "\x1b", "\x7f", "\x9f", "\u200b"):
            for crop in ("review", "writer"):
                with self.subTest(control=ascii(control), crop=crop):
                    assessment, review, raw = self.control_calculation("42." + control + "e -3", "-3", crop=crop)
                    with self.assertRaisesRegex(ValueError, "research draft"):
                        validate_response(raw, assessment, review)

    def test_saved_draft_cannot_replay_a_trailing_point_exponent_fragment(self):
        for amount, value in (("42.e3", "42"), ("42.E+3", "42"),
                              ("42. e -3", "-3"), ("42.,e +3", "+3")):
            with self.subTest(amount=amount, value=value):
                assessment, review, raw = self.formatted_calculation(amount, value, "writer")
                raw["items"]["i0"]["calculation"]["precision"] = 2
                self.assert_saved_calculation_rejected(
                    raw, assessment, review, {"42": "42.00", "-3": "-3.00", "+3": "3.00"}[value])

    def test_exponent_candidate_refusal_preserves_plain_values_and_sentence_periods(self):
        cases = (("42.", "42", "42.00"), ("42.5.", "42.5", "42.50"),
                 ("0.42. Examples follow.", "0.42", "0.42"),
                 ("-42. Earnings recorded.", "-42", "-42.00"),
                 ("+42. e.g. reported revenue.", "+42", "42.00"),
                 ("42. End of sentence.", "42", "42.00"))
        for amount, value, expected in cases:
            for crop in ("whole", "writer", "review"):
                with self.subTest(amount=amount, value=value, crop=crop):
                    self.assert_formatted_value(amount, value, expected, crop, precision=2)

    def test_even_accepted_coverage_crops_cannot_remove_packet_numeric_context(self):
        assessment, review = scope(("covered",), quotes=["Amount -42 and 0. FY2025 USD."])
        packet = {key: review[key] for key in ("version", "offset_unit", "lookup", "selection", "sources", "passages")}
        cropped = validate_review({"version": 1, "items": [{"item_id": "i0", "state": "covered", "reason": "Supported.",
                                  "supports": [{"passage_id": "p0", "quote": "42 and 0. FY2025 USD.",
                                                "temporal_scope": "historical", "period": "FY2025", "observation_date": None}]}]},
                                 assessment, packet, CHECKED_AT)
        raw = response(cropped)
        raw["items"]["i0"].update(kind="calculation", calculation={"operation": "sum", "precision": 0,
            "operands": [{"value": "42", "support_index": 0}, {"value": "0", "support_index": 0}]})
        with self.assertRaises(ValueError):
            validate_response(raw, assessment, cropped)

    def control_calculation(self, amount, value, *, crop):
        # Archived source text permits controls; accepted model quotes do not
        # permit NUL. Keep the complete original packet while accepting only
        # the clean suffix containing the requested historical qualifiers.
        assessment, review = scope(("covered",), quotes=["Amount -42 and 0. FY2025 USD. End."])
        packet = {key: deepcopy(review[key]) for key in
                  ("version", "offset_unit", "lookup", "selection", "sources", "passages")}
        passage = packet["passages"][0]
        passage["text"] = "\U0001f680 Amount " + amount + " and 0. FY2025 USD. End."
        passage["end"] = passage["start"] + len(passage["text"])
        passage["passage_hash"] = hashlib.sha256(passage["text"].encode()).hexdigest()
        packet["passages"] = [passage]
        packet["sources"][0]["content_hash"] = hashlib.sha256(
            ("x" * passage["start"] + passage["text"]).encode()).hexdigest()
        quote = value + " and 0. FY2025 USD. End."
        review = validate_review({"version": 1, "items": [{
            "item_id": "i0", "state": "covered", "reason": "Historical quote.",
            "supports": [{"passage_id": "p0", "quote": quote, "temporal_scope": "historical",
                          "period": "FY2025", "observation_date": None}]}]}, assessment, packet, CHECKED_AT)
        raw = response(review)
        raw["items"]["i0"].update(kind="calculation", calculation={
            "operation": "sum", "precision": 2,
            "operands": [{"value": value, "support_index": 0}, {"value": "0", "support_index": 0}]})
        if crop == "writer":
            raw["items"]["i0"]["supports"] = [{"passage_id": "p0", "quote": value + " and 0."}]
        return assessment, review, raw

    def test_nonwhitespace_controls_cannot_hide_signs_or_grouped_digits(self):
        controls = [chr(codepoint) for codepoint in range(160)
                    if unicodedata.category(chr(codepoint)) == "Cc" and not chr(codepoint).isspace()]
        for control in controls:
            for amount, value in (("-" + control + "42", "42"),
                                  ("-USD" + control + " 42", "42"),
                                  ("1" + control + "234", "234"),
                                  ("4" + control + "2", "2")):
                for crop in ("review", "writer"):
                    with self.subTest(control=ascii(control), amount=ascii(amount), crop=crop):
                        assessment, review, raw = self.control_calculation(amount, value, crop=crop)
                        with self.assertRaises(ValueError):
                            validate_response(raw, assessment, review)

    def test_control_rejection_view_preserves_whitespace_and_original_literal_offsets(self):
        whitespace = [chr(codepoint) for codepoint in range(160)
                      if unicodedata.category(chr(codepoint)) == "Cc" and chr(codepoint).isspace()]
        for control in whitespace:
            with self.subTest(whitespace=ascii(control)):
                assessment, review, raw = self.calculation("10 and " + control + "-12.", "10", "-12")
                saved = validate_response(raw, assessment, review)
                self.assertEqual(saved["items"][0]["calculation"]["result"], "-2.00")
                self.assertEqual(validate_saved_draft(saved, assessment, review), saved)
        for control in ("\x00", "\x01", "\x1b", "\x7f", "\x9f"):
            assessment, review, raw = self.control_calculation("Note" + control + ". Amount 42", "42", crop="writer")
            saved = validate_response(raw, assessment, review)
            self.assertEqual(saved["items"][0]["calculation"]["result"], "42.00")
            passage = review["passages"][0]
            support = saved["items"][0]["supports"][0]
            self.assertEqual(passage["text"][support["start"] - passage["start"]:
                                            support["end"] - passage["start"]], support["quote"])
            self.assertEqual(validate_saved_draft(saved, assessment, review), saved)

    def formatted_calculation(self, amount, value, crop, *, zero_quote="and 0."):
        quote = "Amount " + amount + " " + zero_quote + " FY2025 USD. End."
        assessment, review = scope(("covered",), quotes=[quote])
        if crop == "review":
            # The reviewer can accept a unique suffix while the full packet
            # still exposes the amount's original sign/grouping envelope.
            packet = {key: review[key] for key in ("version", "offset_unit", "lookup", "selection", "sources", "passages")}
            review = validate_review({"version": 1, "items": [{
                "item_id": "i0", "state": "covered", "reason": "Supported.",
                "supports": [{"passage_id": "p0", "quote": quote[quote.index(value):],
                              "temporal_scope": "historical", "period": "FY2025", "observation_date": None}],
            }]}, assessment, packet, CHECKED_AT)
        raw = response(review)
        raw["items"]["i0"].update(kind="calculation", calculation={
            "operation": "sum", "precision": 0,
            "operands": [{"value": value, "support_index": 0}, {"value": "0", "support_index": 0}],
        })
        if crop == "writer":
            raw["items"]["i0"]["supports"] = [
                {"passage_id": "p0", "quote": value},
                {"passage_id": "p0", "quote": zero_quote + " FY2025 USD. End."},
            ]
            raw["items"]["i0"]["calculation"]["operands"][1]["support_index"] = 1
        return assessment, review, raw

    def assert_formatted_rejected(self, amount, value="42", *, crops=("whole", "writer", "review"),
                                  zero_quote="and 0.", match=None):
        for crop in crops:
            with self.subTest(amount=ascii(amount), value=value, crop=crop):
                assessment, review, raw = self.formatted_calculation(amount, value, crop, zero_quote=zero_quote)
                failure = self.assertRaisesRegex(ValueError, match) if match else self.assertRaises(ValueError)
                with failure:
                    validate_response(raw, assessment, review)

    def assert_formatted_value(self, amount, value, expected, crop, *, precision=None, zero_quote="and 0."):
        assessment, review, raw = self.formatted_calculation(amount, value, crop, zero_quote=zero_quote)
        if precision is not None:
            raw["items"]["i0"]["calculation"]["precision"] = precision
        saved = validate_response(raw, assessment, review)
        self.assertEqual(saved["items"][0]["calculation"]["result"], expected)
        self.assertEqual(validate_saved_draft(saved, assessment, review), saved)
        passages = {passage["id"]: passage for passage in review["passages"]}
        for support in saved["items"][0]["supports"]:
            passage = passages[support["passage_id"]]
            self.assertEqual(passage["text"][support["start"] - passage["start"]:
                                                support["end"] - passage["start"]], support["quote"])
        return saved, review

    def assert_saved_calculation_rejected(self, raw, assessment, review, result):
        # Start from a canonical fact; saved rejection must not depend on the
        # invalid calculation passing initial validation first.
        fact = deepcopy(raw)
        fact["items"]["i0"].update(kind="fact", calculation=None)
        saved = validate_response(fact, assessment, review)
        saved["items"][0].update(kind="calculation", calculation={
            **deepcopy(raw["items"]["i0"]["calculation"]), "result": result, "rounding": "ROUND_HALF_EVEN"})
        with self.assertRaisesRegex(ValueError, "research draft"):
            validate_saved_draft(saved, assessment, review)

    def test_currency_between_amount_and_sign_cannot_authorize_positive_digits(self):
        amounts = ("-$42", "- $42", "\u2212\u00a342", "-USD 42", "\u2212 USD 42", "\u2212USD42",
                   "-US$ 42", "- USD $42", "-kr 42", "-usd 42", "-eur 42", "-\u5143 42",
                   "-XUSD 42", "-U.S.$42", "-Rp. 42",
                   "42$-", "42 USD-", "42 USD -", "42 US$ \u2212", "42 usd-", "42 eur -", "42 \u5143 -",
                   "42 XUSD-", "42 U.S.$-", "42 Rp. -")
        for amount in amounts:
            self.assert_formatted_rejected(amount)

    def test_connected_sign_pairs_cannot_supply_a_single_signed_operand(self):
        for first in "+-\u2212\u00b1\u2213":
            for second in "+-":
                for spacing in ("", " ", "\t", "\n", "\u202f", "\x01\u200b "):
                    for envelope in ("", "USD ", "million USD ", "(USD) (million) "):
                        amount = "USD " + first + spacing + envelope + second + "42 million"
                        value = second + "42"
                        for crop in ("whole", "writer", "review"):
                            with self.subTest(amount=ascii(amount), crop=crop):
                                assessment, review, raw = self.formatted_calculation(
                                    amount, value, crop, zero_quote="Separate operand 0.")
                                with self.assertRaisesRegex(ValueError, "research draft"):
                                    validate_response(raw, assessment, review)
                                self.assert_saved_calculation_rejected(raw, assessment, review, str(int(value)))
        # Compatibility signs, dashes, and discarded controls stay attached to
        # the same rejected cluster without repairing the exact ASCII suffix.
        for amount, value in (("USD \uff0b -42 million", "-42"),
                              ("USD \u2014 +42 million", "+42"),
                              ("USD \u207b\x01 +42 million", "+42")):
            self.assert_formatted_rejected(amount, value, zero_quote="Separate operand 0.")

    def test_sign_cluster_refusal_preserves_separate_values_and_prose(self):
        for amount, value in (("Recorded \u2014 amount USD -42 million", "-42"),
                              ("USD +42 million. Details \u2014 recorded", "+42"),
                              ("USD -42 million. End of sentence", "-42"),
                              ("42 widgets", "42")):
            for crop in ("whole", "writer", "review"):
                self.assert_formatted_value(amount, value, str(int(value)), crop,
                                            zero_quote="Separate operand 0.")
        for quote in ("10 and -12.", "10 Euros and -12.", "10 recorded; USD -12."):
            assessment, review, raw = self.calculation(quote, "10", "-12", precision=0)
            saved = validate_response(raw, assessment, review)
            self.assertEqual(saved["items"][0]["calculation"]["result"], "-2")
            self.assertEqual(validate_saved_draft(saved, assessment, review), saved)

    def test_wrapped_currency_scale_envelopes_cannot_detach_explicit_signs(self):
        wrappers = (("(", ")"), ("[", "]"), ("{", "}"), ("\u27e6", "\u27e7"),
                    ("\u3010", "\u3011"), ("\uff08", "\uff09"))
        for opening, closing in wrappers:
            currency = opening + "USD" + closing
            nested = opening + currency + closing
            amounts = ("-" + currency + "42", "-" + nested + "42", "42 " + currency + "-",
                       "42 million " + currency + "-", "42 " + currency + " (million)-",
                       "42 (million) " + currency + "-", "-" + currency + " (million) 42")
            for amount in amounts:
                self.assert_formatted_rejected(amount, zero_quote="Separate operand 0.", match="research draft")
        # Currency symbols, opaque labels, the literal in-connector and
        # invisible controls retain the same explicit-sign boundary.
        for amount in ("-(US$)42", "42 million (XUSD)-", "- (in USD) 42",
                       "42 million (in USD)-", "- in USD million 42", "- million in USD 42",
                       "\u2212\u200b(USD)42", "42 (million) (USD)\x01\u2212"):
            self.assert_formatted_rejected(amount, crops=("writer",), zero_quote="Separate operand 0.")

    def test_saved_draft_cannot_replay_a_wrapped_currency_sign_fragment(self):
        for amount in ("-(USD)42", "42 million (USD)-", "42 [USD] (million)-", "-\u27e6USD\u27e742"):
            for crop in ("whole", "writer", "review"):
                with self.subTest(amount=ascii(amount), crop=crop):
                    assessment, review, raw = self.formatted_calculation(
                        amount, "42", crop, zero_quote="Separate operand 0.")
                    self.assert_saved_calculation_rejected(raw, assessment, review, "42")

    def test_currency_annotations_preserve_complete_literals_and_original_offsets(self):
        controls = (("(USD)42", "42"), ("((USD))42", "42"), ("(USD)-42", "-42"),
                    ("[USD]+42", "+42"), ("\u27e6USD\u27e742", "42"),
                    ("42 million (USD)", "42"), ("42 (million) USD", "42"),
                    ("USD (million) 42", "42"), ("42 million (in USD)", "42"),
                    ("42 (recorded amount).", "42"), ("42 described by the source (a note).", "42"))
        for amount, value in controls:
            for crop in ("whole", "writer", "review"):
                with self.subTest(amount=ascii(amount), crop=crop):
                    self.assert_formatted_value(amount, value, str(int(value)), crop, zero_quote="Separate operand 0.")
        # Bracket projection never unwraps the amount itself.
        for amount in ("(USD 42)", "[USD 42]", "\u27e6USD 42\u27e7", "((USD)42)"):
            self.assert_formatted_rejected(amount, crops=("writer",), zero_quote="Separate operand 0.")

    def test_all_original_currency_symbols_retain_signs_before_compatibility_expansion(self):
        symbols = [chr(codepoint) for codepoint in range(sys.maxunicode + 1)
                   if unicodedata.category(chr(codepoint)) == "Sc"]
        self.assertIn("\ufdfc", symbols)
        for symbol in symbols:
            for amount in ("-" + symbol + " 42", "42 " + symbol + "-"):
                with self.subTest(symbol=ascii(symbol), amount=ascii(amount)):
                    self.assert_formatted_rejected(amount, crops=("whole",))

    def test_currency_compatibility_mapping_preserves_original_support_offsets_and_plain_values(self):
        for symbol in ("\ufdfc", "\ufe69", "\uff04", "\u20b9"):
            for amount, value, expected in ((symbol + "42", "42", "42"),
                                            (symbol + "-42", "-42", "-42")):
                with self.subTest(symbol=ascii(symbol), value=value):
                    saved, review = self.assert_formatted_value(amount, value, expected, "writer")
                    support = saved["items"][0]["supports"][0]
                    passage = review["passages"][0]
                    self.assertEqual(support["quote"], value)
                    self.assertEqual(support["start"] - passage["start"], passage["text"].index(value))

    def selected_calculation(self, body):
        assessment, previous = scope(("covered",))
        candidate = {key: deepcopy(value) for key, value in previous["sources"][0].items() if key != "id"}
        candidate.update(content=body, content_hash=hashlib.sha256(body.encode()).hexdigest())
        packet = select_packet([candidate], assessment, {"state": "ready", "search_limited": False, "skipped_count": 0})
        self.assertEqual(validate_packet(packet, lambda *args: candidate), packet)
        self.assertEqual([(passage["start"], passage["end"]) for passage in packet["passages"]], [(600, 1800)])
        passage = packet["passages"][0]
        review = validate_review({"version": 1, "items": [{
            "item_id": "i0", "state": "covered", "reason": "The selected saved passage contains this historical amount.",
            "supports": [{"passage_id": passage["id"], "quote": passage["text"],
                          "temporal_scope": "historical", "period": "FY2025", "observation_date": None}],
        }]}, assessment, packet, CHECKED_AT)
        raw = response(review)
        raw["items"]["i0"].update(kind="calculation", calculation={
            "operation": "sum", "precision": 0,
            "operands": [{"value": "42", "support_index": 0}, {"value": "0", "support_index": 0}],
        })
        return assessment, review, raw, packet

    def test_real_selected_packet_cannot_hide_a_sign_before_a_currency_envelope(self):
        for label in ("USD", "XUSD", "U.S.$", "(USD)", "((USD))", "[USD]", "\u27e6USD\u27e7",
                      "(million) USD", "(USD) (million)", "(in USD)"):
            body = "x" * 599 + "-" + label + " 42 and 0. "
            body += " " * (1500 - len(body)) + "Testsubject metric0 FY2025."
            body += " " * (1795 - len(body)) + " End."
            with self.subTest(label=label):
                assessment, review, raw, packet = self.selected_calculation(body)
                self.assertEqual(body[599], "-")
                self.assertTrue(packet["passages"][0]["text"].startswith(label + " 42"))
                with self.assertRaises(ValueError):
                    validate_response(raw, assessment, review)

    def test_real_selected_packet_cannot_hide_a_sign_after_a_currency_envelope(self):
        for label in ("USD", "XUSD", "U.S.$", "million", "million USD", "USD million", "billion EUR",
                      "millions USD", "millionUSD", "USDmillion", "(USD)", "((USD))", "[USD]",
                      "\u27e6USD\u27e7", "million (USD)", "(million) USD", "(USD) (million)", "million (in USD)"):
            body = " " * 700 + "Testsubject metric0 FY2025. Amount 0 recorded. "
            body += " " * (1500 - len(body)) + "USD context. "
            tail = "42 " + label
            body += " " * (1800 - len(tail) - len(body)) + tail + "-"
            body += " " * (2795 - len(body)) + " End."
            with self.subTest(label=label):
                assessment, review, raw, packet = self.selected_calculation(body)
                self.assertEqual(body[1800], "-")
                self.assertTrue(packet["passages"][0]["text"].endswith(tail))
                with self.assertRaises(ValueError):
                    validate_response(raw, assessment, review)

    def test_real_selected_packet_refuses_partial_currency_annotation_edges(self):
        for opening, closing in (("(", ")"), ("[", "]"), ("\u27e6", "\u27e7")):
            lead = "-" + opening + "XUSD"
            body = "x" * (600 - len(lead)) + lead + closing + "42 and 0. "
            body += " " * (1500 - len(body)) + "Testsubject metric0 FY2025."
            body += " " * (1795 - len(body)) + " End."
            with self.subTest(side="left", closing=ascii(closing)):
                assessment, review, raw, packet = self.selected_calculation(body)
                self.assertTrue(packet["passages"][0]["text"].startswith(closing + "42"))
                with self.assertRaises(ValueError):
                    validate_response(raw, assessment, review)
            for label in ("USD", "millio"):
                body = " " * 700 + "Testsubject metric0 FY2025. Amount 0 recorded. "
                body += " " * (1500 - len(body)) + "USD context. "
                tail = "42 " + opening + label
                body += " " * (1800 - len(tail) - len(body)) + tail + closing + "-"
                body += " " * (2795 - len(body)) + " End."
                with self.subTest(side="right", opening=ascii(opening), label=label):
                    assessment, review, raw, packet = self.selected_calculation(body)
                    self.assertTrue(packet["passages"][0]["text"].endswith(tail))
                    with self.assertRaises(ValueError):
                        validate_response(raw, assessment, review)

    def test_real_selected_packet_refuses_partial_scale_words_and_saved_replay(self):
        for value, left_fragment, right_fragment in (
            ("42", "ion", "millio"), ("-42", "ion", "millio"), ("+42", "ion", "millio"),
            ("42", "\uff49\uff4f\uff4e", "\uff4d\uff49\uff4c\uff4c\uff49\uff4f"),
            ("42", "i\u200bon", "milli\x01o"),
        ):
            for side in ("left", "right"):
                if side == "left":
                    body = "x" * 595 + "-mill" + left_fragment + " EUR " + value + " and 0. "
                    body = (body.ljust(1500) + "Testsubject metric0 FY2025.").ljust(1795) + " End."
                else:
                    tail = value + " EUR " + right_fragment
                    body = " " * 700 + "Testsubject metric0 FY2025. Amount 0 recorded. "
                    body = body.ljust(1500) + "USD context. "
                    body = (body.ljust(1800 - len(tail)) + tail + "n-").ljust(2795) + " End."
                with self.subTest(side=side, value=value, fragment=(left_fragment, right_fragment)):
                    assessment, review, raw, packet = self.selected_calculation(body)
                    raw["items"]["i0"]["calculation"]["operands"][0]["value"] = value
                    self.assertEqual(packet["passages"][0]["text"], body[600:1800])
                    with self.assertRaisesRegex(ValueError, "research draft"):
                        validate_response(raw, assessment, review)
                    self.assert_saved_calculation_rejected(raw, assessment, review, str(int(value)))

    def test_every_scale_currency_envelope_cut_refuses_raw_and_saved_operands(self):
        scales = ("thousand", "thousands", "million", "millions",
                  "billion", "billions", "trillion", "trillions")
        for scale in scales:
            labels = {scale}
            for currency in ("EUR", "U.S.$"):
                labels.update((scale + " " + currency, currency + " " + scale,
                               scale + " in " + currency, "in " + currency + " " + scale,
                               scale + currency, currency + scale))
            for label in sorted(labels):
                for side in ("left", "right"):
                    # Every cut preserving 42 is derived from a source with an
                    # unsupported outer sign. No production token grammar is
                    # used to choose expected refusals or fragment lengths.
                    envelope = "-" + label + " " if side == "left" else "42 " + label
                    cuts = range(1, len(envelope) + 1) if side == "left" else range(2, len(envelope) + 1)
                    for cut in cuts:
                        if side == "left":
                            body = "x" * (600 - cut) + envelope + "42 and 0. "
                            body = (body.ljust(1500) + "Testsubject metric0 FY2025.").ljust(1795) + " End."
                        else:
                            body = " " * 700 + "Testsubject metric0 FY2025. Amount 0 recorded. "
                            body = body.ljust(1500) + "USD context. "
                            body = (body.ljust(1800 - cut) + envelope + "-").ljust(2795) + " End."
                        with self.subTest(label=label, side=side, cut=cut):
                            assessment, review, raw, _ = self.selected_calculation(body)
                            with self.assertRaisesRegex(ValueError, "research draft"):
                                validate_response(raw, assessment, review)
                            self.assert_saved_calculation_rejected(raw, assessment, review, "42")

    def test_real_selected_packet_preserves_visible_currency_context_at_nonzero_offsets(self):
        body = "x" * 600 + "Visible boundary. USD 42 and 0. "
        body += " " * (1500 - len(body)) + "Testsubject metric0 FY2025."
        body += " " * (1795 - len(body)) + " End."
        assessment, review, raw, _ = self.selected_calculation(body)
        saved = validate_response(raw, assessment, review)
        self.assertEqual(saved["items"][0]["calculation"]["result"], "42")
        self.assertEqual(validate_saved_draft(saved, assessment, review), saved)

    def test_mixed_or_repeated_group_separators_cannot_authorize_numeric_fragments(self):
        amounts = ("1\t234", "1  234", "1, 234", "1,\t234", "1\u2009\u2009234",
                   "1\u00a0\u202f234", "1,\u202f234", "1' 234", "1\u066c\u2009234")
        for amount in amounts:
            for value in ("1", "234"):
                self.assert_formatted_rejected(amount, value)

    def test_underscore_groups_cannot_authorize_numeric_fragments(self):
        for amount in ("42_3", "42 _ 3", "42 _ _ 3", "42\t_\t3", "42 e1 _ 3",
                       "42 _\u0301_ 3", "42 e1 _\u0301_ 3"):
            for value in ("42", "3"):
                self.assert_formatted_rejected(amount, value)

    def test_scaled_postfix_signs_cannot_become_positive_operands(self):
        for label in ("thousand", "million", "billion", "trillion", "million USD", "USD million", "Billion EUR",
                      "thousands USD", "millions USD", "billions EUR", "trillions USD", "millionUSD", "USDmillion"):
            for sign in ("-", "\u2212"):
                self.assert_formatted_rejected("42 " + label + sign + ".", zero_quote="Separate operand 0.")
        for label in ("thousand", "million", "billion", "trillion", "million USD", "USD million",
                      "millions USD", "millionUSD"):
            assessment, review, raw = self.formatted_calculation(
                "42 " + label + ".", "42", "whole", zero_quote="Separate operand 0.")
            saved = validate_response(raw, assessment, review)
            self.assertEqual(saved["items"][0]["calculation"]["result"], "42")

    def test_leading_arabic_numeric_separators_cannot_authorize_ascii_digit_suffixes(self):
        for separator in ("\u066b", "\u066c"):
            for sign in ("", "+", "-", "\u2212"):
                self.assert_formatted_rejected("USD" + sign + separator + "42")

    def test_whitespace_in_unsupported_leading_numeric_clusters_cannot_expose_digit_suffixes(self):
        amounts = ["USD" + prefix + separator + spacing + "42"
                   for separator in ("\u066b", "\u066c")
                   for prefix, spacing in (("", " "), ("", "\t"), ("-", " "),
                                           ("- ", ""), ("+", "\t"), ("\u2212 ", " "))]
        amounts += ["USD" + prefix + "42" for prefix in ("-. ", "-, ", "+. ", "+, ", "- .\t", "\u2212, ")]
        for amount in amounts:
            self.assert_formatted_rejected(amount)

    def test_leading_separator_refusal_keeps_plain_decimals_and_quoted_operands_valid(self):
        controls = (("USD 42", "42", "42"), ("USD +42", "+42", "42"),
                    ("USD -42", "-42", "-42"), ("USD 0.42", "0.42", "0.42"),
                    ("'42'", "42", "42"), ("\u201842\u2019", "42", "42"),
                    ("'0.42'", "0.42", "0.42"), ("Sentence. 42", "42", "42"),
                    ("Amount, 42", "42", "42"))
        for amount, value, expected in controls:
            for crop in ("whole", "writer", "review"):
                with self.subTest(amount=ascii(amount), crop=crop):
                    self.assert_formatted_value(amount, value, expected, crop, precision=2 if "." in value else 0)

    def test_currency_context_keeps_complete_plain_and_signed_ascii_literals_valid(self):
        controls = (("$42", "42", "42"), ("USD 42", "42", "42"), ("+42", "+42", "42"),
                    ("-42", "-42", "-42"), ("USD -42", "-42", "-42"), ("$-42", "-42", "-42"),
                    ("US$ -42", "-42", "-42"), ("\u00a342", "42", "42"),
                    ("XUSD 42", "42", "42"), ("U.S.$42", "42", "42"), ("Rp. 42", "42", "42"))
        for amount, value, expected in controls:
            for crop in ("whole", "writer", "review"):
                with self.subTest(amount=amount, crop=crop):
                    self.assert_formatted_value(amount, value, expected, crop)
        for second, expected in (("-12", "-2"), ("+12", "22")):
            # "and" is a short letter label, but this sign starts a separate
            # complete operand rather than being a postfix monetary sign.
            assessment, review, raw = self.calculation("10 and " + second + ".", "10", second, precision=0)
            saved = validate_response(raw, assessment, review)
            self.assertEqual(saved["items"][0]["calculation"]["result"], expected)
        for amount in ("- 42", "USD - 42", "$- 42"):
            assessment, review, raw = self.formatted_calculation(amount, "42", "whole")
            with self.subTest(amount=amount), self.assertRaises(ValueError):
                validate_response(raw, assessment, review)

    def test_unicode_numeric_formats_cannot_supply_ascii_fragments_or_normalized_values(self):
        cases = (("1\u2009234", "234"), ("1\u2007234", "1"), ("1\u200a234", "234"),
                 ("1\u1680234", "234"), ("1\uff0e234", "234"), ("1\u2024234", "234"),
                 ("1e\u207a42", "42"), ("\u207b42", "42"), ("\u207a42", "42"),
                 ("\u279642", "42"), ("\u279542", "42"), ("\u221242", "42"),
                 ("1\u2011234", "234"), ("1\u2018234", "234"),
                 ("42\u200b000", "42"), ("42\u200d000", "42"), ("\uff14\uff12", "42"),
                 ("42\u20445", "42"), ("42\u22155", "42"))
        for original, operand in cases:
            with self.subTest(original=original):
                assessment, review, raw = self.calculation("Amount " + original + " and 0.", operand, "0")
                with self.assertRaises(ValueError):
                    validate_response(raw, assessment, review)
                if operand in review["items"][0]["supports"][0]["quote"]:
                    raw["items"]["i0"]["supports"] = [
                        {"passage_id": "p0", "quote": operand}, {"passage_id": "p0", "quote": "and 0."}]
                    raw["items"]["i0"]["calculation"]["operands"][1]["support_index"] = 1
                    with self.assertRaises(ValueError):
                        validate_response(raw, assessment, review)

    def test_unsupported_accounting_and_postfix_signs_cannot_become_positive_operands(self):
        for amount in ("(42)", "( 42 )", "($42)", "(42\u20ac)", "(USD 42)", "(42 USD)",
                       "42-", "42\u2212",
                       "42\ufe63", "42\uff0d", "42\u2011", "42\u2796", "42 +", "42\u00b1", "- 42"):
            with self.subTest(amount=amount):
                assessment, review, raw = self.calculation("Amount " + amount + " and 0.", "42", "0")
                with self.assertRaises(ValueError):
                    validate_response(raw, assessment, review)

    def test_rejection_view_preserves_valid_ascii_values_offsets_and_unicode_prose(self):
        quote = "Quoted \u201c+10\u201d and \u201c-12\u201d. End."
        assessment, review, raw = self.calculation(quote, "+10", "-12", precision=2)
        saved = validate_response(raw, assessment, review)
        calculation = saved["items"][0]["calculation"]
        self.assertEqual(calculation["result"], "-2.00")
        self.assertEqual(calculation["operands"], raw["items"]["i0"]["calculation"]["operands"])
        self.assertEqual(saved["items"][0]["supports"][0]["quote"], review["items"][0]["supports"][0]["quote"])
        self.assertEqual(saved["items"][0]["supports"][0]["start"], review["items"][0]["supports"][0]["start"])
        self.assertEqual(validate_saved_draft(saved, assessment, review), saved)

    def test_numeric_runs_need_visible_packet_boundaries(self):
        assessment, review = scope(("covered",), quotes=["42 and 0. FY2025 USD."])
        passage = review["passages"][0]
        passage["text"] = passage["text"][2:]
        passage["start"] += 2
        passage["passage_hash"] = hashlib.sha256(passage["text"].encode()).hexdigest()
        raw = response(review)
        raw["items"]["i0"].update(kind="calculation", calculation={"operation": "sum", "precision": 0,
            "operands": [{"value": "42", "support_index": 0}, {"value": "0", "support_index": 0}]})
        with self.assertRaises(ValueError):
            validate_response(raw, assessment, review)
        assessment, review = scope(("covered",), quotes=["FY2025 USD 42 and 0"])
        raw = response(review)
        raw["items"]["i0"].update(kind="calculation", calculation={"operation": "sum", "precision": 0,
            "operands": [{"value": "42", "support_index": 0}, {"value": "0", "support_index": 0}]})
        with self.assertRaises(ValueError):
            validate_response(raw, assessment, review)
        # The trailing dot might continue into .5 outside the selected packet.
        for ending in ("42.", "42,", "42\uff0e", "42\u2024", "42 ", "42. ", "42\u2009"):
            assessment, review = scope(("covered",), quotes=["FY2025 USD 0 and " + ending])
            raw = response(review)
            raw["items"]["i0"].update(kind="calculation", calculation={"operation": "sum", "precision": 0,
                "operands": [{"value": "42", "support_index": 0}, {"value": "0", "support_index": 0}]})
            with self.subTest(ending=ending), self.assertRaises(ValueError):
                validate_response(raw, assessment, review)
        for prefix in (" ", ". ", "\u2009", "_ ", "_\u0301_ "):
            assessment, review = scope(("covered",), quotes=["42 and 0. FY2025 USD. End."])
            passage = review["passages"][0]
            passage["text"] = prefix + passage["text"][2:]
            passage["start"] -= len(prefix) - 2
            passage["passage_hash"] = hashlib.sha256(passage["text"].encode()).hexdigest()
            review["sources"][0]["content_hash"] = hashlib.sha256(
                ("x" * passage["start"] + passage["text"]).encode()).hexdigest()
            self.assertEqual(validate_saved_review(review, assessment), review)
            raw = response(review)
            raw["items"]["i0"].update(kind="calculation", calculation={"operation": "sum", "precision": 0,
                "operands": [{"value": "42", "support_index": 0}, {"value": "0", "support_index": 0}]})
            with self.subTest(prefix=prefix), self.assertRaises(ValueError):
                validate_response(raw, assessment, review)

    def test_calculation_shape_indices_counts_precision_and_zero_division_are_strict(self):
        assessment, review, raw = self.calculation("10 and 0.", "10", "0")
        for mutate in (
            lambda value: value.update(operation="product"), lambda value: value.update(result="10"),
            lambda value: value.update(precision=True), lambda value: value.update(precision=7),
            lambda value: value.update(precision=-1), lambda value: value.update(precision=2.0),
            lambda value: value.update(operands=value["operands"][:1]),
            lambda value: value.update(operands=value["operands"] * 2),
            lambda value: value["operands"][0].update(support_index=True),
            lambda value: value["operands"][0].update(support_index=-1),
            lambda value: value["operands"][0].update(support_index=1),
            lambda value: value["operands"][0].update(value=10),
        ):
            changed = deepcopy(raw)
            mutate(changed["items"]["i0"]["calculation"])
            with self.assertRaises(ValueError):
                validate_response(changed, assessment, review)
        for operation, first, second in (("ratio", "10", "0"), ("percent_change", "0", "10")):
            assessment, review, raw = self.calculation("10 and -0 and 0.", first, second, operation=operation)
            with self.assertRaises(ValueError):
                validate_response(raw, assessment, review)
        raw = deepcopy(self.raw)
        raw["items"]["i0"]["kind"] = "calculation"
        self.reject(raw)

    def test_saved_tampering_of_binding_derived_fields_gaps_order_and_calculation_is_rejected(self):
        saved = self.validate()
        mutations = (
            lambda value: value.update(version=True), lambda value: value.update(state="accepted"),
            lambda value: value.update(reason="invented"), lambda value: value.update(packet_hash="f" * 64),
            lambda value: value.update(coverage_hash="f" * 64), lambda value: value["items"].reverse(),
            lambda value: value["items"].append(deepcopy(value["items"][0])),
            lambda value: value["items"][0].update(period="FY2030"),
            lambda value: value["items"][0].update(coverage_state="missing"),
            lambda value: value["items"][0]["supports"][0].update(source_id="c2"),
            lambda value: value["items"][0]["supports"][0].update(start=1),
            lambda value: value["items"][0]["supports"][0].update(start=float(value["items"][0]["supports"][0]["start"])),
            lambda value: value["items"][1].update(text="Invented gap figure 99."),
        )
        for mutate in mutations:
            changed = deepcopy(saved)
            mutate(changed)
            with self.assertRaises(ValueError):
                validate_saved_draft(changed, self.assessment, self.review)
        assessment, review, raw = self.calculation("10 and 12.", "10", "12")
        saved = validate_response(raw, assessment, review)
        for field, value in (("result", "99.00"), ("rounding", "ROUND_UP")):
            changed = deepcopy(saved)
            changed["items"][0]["calculation"][field] = value
            with self.assertRaises(ValueError):
                validate_saved_draft(changed, assessment, review)
        with self.assertRaises(ValueError):
            validate_saved_draft({**self.validate(), "extra": "x" * MAX_SAVED_BYTES}, self.assessment, self.review)

    def test_saved_draft_is_bound_to_all_coverage_and_packet_metadata(self):
        saved = self.validate()
        for mutate in (
            lambda value: value["items"][1].update(reason="Another valid model gap reason."),
            lambda value: value["sources"][0].update(title="Another source title"),
            lambda value: value["selection"].update(limited=True, omitted_count=1),
        ):
            review = deepcopy(self.review)
            mutate(review)
            if review["selection"]["limited"]:
                for row in review["items"]:
                    row["suitability_reason"] += " Lookup or passage selection was incomplete."
            with self.assertRaises(ValueError):
                validate_saved_draft(saved, self.assessment, review)
            self.assertNotEqual(coverage_hash(review), saved["coverage_hash"])
        reversed_keys = {key: self.review[key] for key in reversed(self.review)}
        self.assertEqual(coverage_hash(reversed_keys), coverage_hash(self.review))

    def test_invalid_assessments_reviews_and_malformed_json_are_never_repaired(self):
        for assessment in (None, {}, {**self.assessment, "decision": "clarify"}):
            with self.assertRaises(ValueError):
                response_schema(assessment, self.review)
        changed = deepcopy(self.review)
        changed["items"][0]["supports"][0]["start"] += 1
        for operation in (response_schema, writer_input):
            with self.assertRaises(ValueError):
                operation(self.assessment, changed)
        malformed = '{"version":1,"items":{"i0":}}'
        with self.assertRaises(ValueError):
            json.loads(malformed)
        self.reject(malformed)
        for value in (None, {}, {**self.review, "version": True}):
            with self.assertRaises(ValueError):
                coverage_hash(value)

    def test_prompts_are_bounded_and_have_no_fixture_specific_facts(self):
        self.assertLess(len(INSTRUCTIONS.encode()), 4000)
        self.assertIn("unreviewed", INSTRUCTIONS)
        for value in ("Testsubject", "FY2025", "USD old 10 new 12", self.assessment["completion_criterion"]):
            self.assertNotIn(value, INSTRUCTIONS + MESSAGE)


if __name__ == "__main__":
    unittest.main()

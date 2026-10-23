"""Pure wire-format scope, strict rejection and canonical projection checks."""
from copy import deepcopy
import hashlib
import json
import unittest

from backend.provider import _research_json_object
from backend.research_evidence import (
    INSTRUCTIONS, MAX_RESPONSE_SCHEMA_BYTES, MESSAGE, SCHEMA, WIRE_VERSION,
    response_schema, unavailable_review, validate_response, validate_review,
    validate_saved_review, validate_stage_review,
)
from backend.research_requirements import validate_assessment


CHECKED_AT = "2026-10-23T14:00:00+02:00"


def requirements(count=2, *, long_ids=False):
    request = "Research Testsubject " + " and ".join(f"metric{index}" for index in range(count)) + " FY2025 USD."
    return validate_assessment({
        "version": 1, "decision": "ready",
        "subjects": [{"id": "s1", "kind": "company", "name": "Testsubject", "identifiers": []}],
        "items": [{"id": "i" + (str(index).zfill(31) if long_ids else str(index)),
                   "subject_ids": ["s1"], "criterion": f"metric{index}", "period": "FY2025",
                   "unit": None, "currency": "USD", "freshness": None} for index in range(count)],
        "constraints": [], "clarifications": [],
    }, request)


def evidence_packet(count=1, *, long_ids=False):
    passages = []
    body = ""
    for index in range(count):
        text = f"\U0001f680 Testsubject metric{index} FY2025 USD 42."
        start = len(body)
        body += text + " Saved separator. "
        passages.append({"id": "p" + (str(index).zfill(31) if long_ids else str(index)),
                         "source_id": "c1", "start": start, "end": start + len(text),
                         "text": text, "passage_hash": hashlib.sha256(text.encode()).hexdigest()})
    return {
        "version": 1, "offset_unit": "unicode_codepoint",
        "lookup": {"state": "ready", "search_limited": False, "skipped_count": 0},
        "selection": {"limited": False, "omitted_count": 0},
        "sources": [{"id": "c1", "capture_id": "a" * 32,
                     "content_hash": hashlib.sha256(body.encode()).hexdigest(), "manifest_hash": "b" * 64,
                     "title": "Saved report", "url": "https://reports.example.org/synthetic",
                     "retrieved_at": "2020-01-02T00:00:00+00:00", "published_at": None,
                     "modified_at": None, "stale": True,
                     "completeness": {"maestro_truncated": False, "full_page": False}}],
        "passages": passages,
    }


def response(assessment, packet):
    first = assessment["items"][0]["id"]
    passage = packet["passages"][0]
    rows = {item["id"]: {"state": "missing", "reason": "No support in the inspected packet.", "supports": []}
            for item in assessment["items"]}
    rows[first] = {"state": "covered", "reason": "The passage addresses this requirement.", "supports": [{
        "passage_id": passage["id"], "quote": passage["text"][2:], "temporal_scope": "historical",
        "period": "FY2025", "observation_date": None,
    }]}
    return {"version": WIRE_VERSION, "items": rows}


class ResearchEvidenceResponseTests(unittest.TestCase):
    def setUp(self):
        self.assessment = requirements()
        self.packet = evidence_packet()
        self.raw = response(self.assessment, self.packet)

    def validate(self, raw=None, *, assessment=None, packet=None):
        return validate_response(self.raw if raw is None else raw,
                                 self.assessment if assessment is None else assessment,
                                 self.packet if packet is None else packet, CHECKED_AT)

    def reject(self, raw):
        with self.assertRaisesRegex(ValueError, "evidence review"):
            validate_response(raw, self.assessment, self.packet, CHECKED_AT)

    def test_schema_binds_actual_item_and_passage_ids_with_shared_closed_rows(self):
        schema = response_schema(self.assessment, self.packet)
        self.assertEqual(schema["properties"]["version"], {"type": "integer", "const": 2})
        items = schema["properties"]["items"]
        self.assertEqual(items["required"], [item["id"] for item in self.assessment["items"]])
        self.assertEqual(items["properties"], {item["id"]: {"$ref": "#/$defs/row"}
                                                for item in self.assessment["items"]})
        row = schema["$defs"]["row"]
        self.assertEqual(set(row["properties"]), {"state", "reason", "supports"})
        self.assertEqual(row["properties"]["supports"]["items"]["properties"]["passage_id"]["enum"], ["p0"])

        def closed(value):
            if isinstance(value, dict):
                if value.get("type") == "object":
                    self.assertIs(value["additionalProperties"], False)
                    self.assertEqual(set(value["properties"]), set(value["required"]))
                for child in value.values():
                    closed(child)
            elif isinstance(value, list):
                for child in value:
                    closed(child)
        closed(schema)

    def test_schema_fits_sixteen_maximum_length_item_ids_and_forty_passage_ids(self):
        assessment = requirements(16, long_ids=True)
        packet = evidence_packet(40, long_ids=True)
        schema = response_schema(assessment, packet)
        self.assertLessEqual(len(json.dumps(schema).encode()), MAX_RESPONSE_SCHEMA_BYTES)
        self.assertEqual(len(schema["properties"]["items"]["required"]), 16)
        passage_ids = schema["$defs"]["row"]["properties"]["supports"]["items"]["properties"]["passage_id"]["enum"]
        self.assertEqual(passage_ids, [passage["id"] for passage in packet["passages"]])
        self.assertEqual(len(schema["$defs"]), 1)

    def test_schema_factory_does_not_alias_inputs_or_legacy_schema(self):
        originals = deepcopy((self.assessment, self.packet, SCHEMA))
        schema = response_schema(self.assessment, self.packet)
        schema["$defs"]["row"]["properties"]["supports"]["items"]["properties"]["quote"]["maxLength"] = 1
        schema["properties"]["items"]["required"].clear()
        self.assertEqual((self.assessment, self.packet, SCHEMA), originals)
        self.assertEqual(response_schema(self.assessment, self.packet)["properties"]["items"]["required"], ["i0", "i1"])
        self.assertEqual(SCHEMA["properties"]["items"]["type"], "array")
        self.assertEqual(SCHEMA["properties"]["version"]["const"], 1)

    def test_projection_is_in_assessment_order_and_preserves_canonical_v1(self):
        originals = deepcopy((self.raw, self.assessment, self.packet))
        raw = deepcopy(self.raw)
        raw["items"] = dict(reversed(list(raw["items"].items())))
        saved = self.validate(raw)
        legacy = {"version": 1, "items": [{"item_id": item["id"], **self.raw["items"][item["id"]]}
                                           for item in self.assessment["items"]]}
        self.assertEqual(saved, validate_review(legacy, self.assessment, self.packet, CHECKED_AT))
        self.assertEqual(saved["version"], 1)
        self.assertEqual([row["item_id"] for row in saved["items"]], ["i0", "i1"])
        self.assertEqual([row["state"] for row in saved["items"]], ["covered", "missing"])
        self.assertEqual(validate_saved_review(saved, self.assessment), saved)
        support = saved["items"][0]["supports"][0]
        self.assertEqual((support["start"], support["end"]), (2, len(self.packet["passages"][0]["text"])))
        self.assertEqual(saved["sources"], self.packet["sources"])
        self.assertEqual((self.raw, self.assessment, self.packet), originals)
        saved["items"][0]["supports"][0]["start"] += 1
        with self.assertRaises(ValueError):
            validate_saved_review(saved, self.assessment)

    def test_root_is_exact_version_two_object_map(self):
        for version in (1, 3, True, 2.0, "2", None):
            with self.subTest(version=version):
                self.reject({**deepcopy(self.raw), "version": version})
        for raw in (None, [], {}, {"version": 2}, {**self.raw, "answer": "extra"},
                    {"version": 2, "items": list(self.raw["items"].values())}):
            with self.subTest(raw=raw):
                self.reject(raw)

    def test_omitted_extra_and_foreign_item_ids_are_rejected(self):
        for mutate in (
            lambda rows: rows.pop("i1"),
            lambda rows: rows.clear(),
            lambda rows: rows.update(foreign=deepcopy(rows["i1"])),
            lambda rows: rows.update(foreign=rows.pop("i1")),
            lambda rows: rows.update(s1=rows.pop("i1")),
        ):
            raw = deepcopy(self.raw)
            mutate(raw["items"])
            self.reject(raw)

    def test_row_is_closed_without_an_inner_item_id(self):
        for mutate in (
            lambda row: row.update(item_id="i0"),
            lambda row: row.update(answer="extra"),
            lambda row: row.pop("reason"),
            lambda row: row.update(state="complete"),
            lambda row: row.update(reason="x" * 801),
            lambda row: row.update(supports={}),
        ):
            raw = deepcopy(self.raw)
            mutate(raw["items"]["i0"])
            self.reject(raw)
        raw = deepcopy(self.raw)
        raw["items"]["i0"] = []
        self.reject(raw)

    def test_dangling_passage_and_unsupported_support_fields_are_rejected(self):
        for mutate in (
            lambda support: support.update(passage_id="outside"),
            lambda support: support.update(passage_id="c1"),
            lambda support: support.update(quote="An invented quote."),
            lambda support: support.update(start=2),
            lambda support: support.update(content_hash="c" * 64),
        ):
            raw = deepcopy(self.raw)
            mutate(raw["items"]["i0"]["supports"][0])
            self.reject(raw)

    def test_existing_support_state_and_duplicate_pair_rules_remain_strict(self):
        for mutate in (
            lambda row: row.update(supports=[]),
            lambda row: row.update(state="missing"),
            lambda row: row.update(state="conflicting"),
            lambda row: row["supports"].append(deepcopy(row["supports"][0])),
        ):
            raw = deepcopy(self.raw)
            mutate(raw["items"]["i0"])
            self.reject(raw)

    def test_malformed_json_is_rejected_without_repair(self):
        malformed = '{"version":2,"items":{"i0":{"state":"missing","reason":"gap","supports":[]},"i1":}}'
        with self.assertRaises(ValueError):
            json.loads(malformed, object_pairs_hook=_research_json_object)
        self.reject(malformed)
        self.reject(json.dumps(self.raw))

    def test_provider_json_boundary_rejects_duplicate_decoded_keys_at_each_depth(self):
        for text in (
            '{"version":2,"version":2,"items":{}}',
            '{"version":2,"items":{"i0":{},"\\u00690":{}}}',
            '{"version":2,"items":{"i0":{"state":"missing","state":"covered"}}}',
            '{"version":2,"items":{"i0":{"supports":[{"passage_id":"p0","passage_id":"p1"}]}}}',
        ):
            with self.subTest(text=text), self.assertRaisesRegex(ValueError, "duplicate JSON object keys"):
                json.loads(text, object_pairs_hook=_research_json_object)

    def test_new_model_path_requires_valid_ready_assessment_and_nonempty_packet(self):
        invalid_assessments = [None, {}, deepcopy(self.assessment), deepcopy(self.assessment)]
        invalid_assessments[2]["items"] = []
        invalid_assessments[3]["items"][0]["mandatory"] = False
        for assessment in invalid_assessments:
            with self.subTest(assessment=assessment), self.assertRaises(ValueError):
                response_schema(assessment, self.packet)
        empty = deepcopy(self.packet)
        empty.update(sources=[], passages=[])
        for operation in (
            lambda: response_schema(self.assessment, empty),
            lambda: self.validate(packet=empty),
        ):
            with self.assertRaises(ValueError):
                operation()
        damaged = deepcopy(self.packet)
        damaged["passages"][0]["passage_hash"] = "0" * 64
        with self.assertRaises(ValueError):
            response_schema(self.assessment, damaged)
        server = unavailable_review(self.assessment, empty["lookup"], empty["selection"], "No selected evidence.", CHECKED_AT)
        self.assertEqual(validate_saved_review(server, self.assessment), server)

    def test_stage_schema_selects_exact_wire_version_without_fallback(self):
        schema = response_schema(self.assessment, self.packet)
        legacy = {"version": 1, "items": [{"item_id": item["id"], **self.raw["items"][item["id"]]}
                                           for item in self.assessment["items"]]}
        expected = self.validate()
        self.assertEqual(validate_stage_review(self.raw, self.assessment, self.packet, CHECKED_AT, schema), expected)
        self.assertEqual(validate_stage_review(legacy, self.assessment, self.packet, CHECKED_AT, deepcopy(SCHEMA)), expected)
        for raw, selected in ((legacy, schema), (self.raw, SCHEMA), (self.raw, {})):
            with self.subTest(raw=raw, selected=selected), self.assertRaises(ValueError):
                validate_stage_review(raw, self.assessment, self.packet, CHECKED_AT, selected)
        reordered = {key: schema[key] for key in reversed(schema)}
        self.assertEqual(validate_stage_review(self.raw, self.assessment, self.packet, CHECKED_AT, reordered), expected)

    def test_stage_rejects_unknown_or_different_scope_schemas(self):
        schema = response_schema(self.assessment, self.packet)
        mutations = (
            lambda value: value["properties"]["version"].update(const=3),
            lambda value: value["properties"]["items"]["properties"].update(foreign={"$ref": "#/$defs/row"}),
            lambda value: value["$defs"]["row"]["properties"]["supports"]["items"]["properties"]["passage_id"].update(enum=["foreign"]),
            lambda value: value.update(description="An unbound contract."),
        )
        for mutate in mutations:
            changed = deepcopy(schema)
            mutate(changed)
            with self.assertRaises(ValueError):
                validate_stage_review(self.raw, self.assessment, self.packet, CHECKED_AT, changed)
        legacy = deepcopy(SCHEMA)
        legacy["properties"]["version"]["const"] = True
        with self.assertRaises(ValueError):
            validate_stage_review(self.raw, self.assessment, self.packet, CHECKED_AT, legacy)

    def test_prompt_names_the_wire_contract_without_case_specific_content(self):
        self.assertIn("version 2", INSTRUCTIONS)
        self.assertIn("version 2", MESSAGE)
        self.assertIn("items is a JSON object", INSTRUCTIONS)
        self.assertNotIn("items is an array", MESSAGE)
        for value in (self.assessment["completion_criterion"], "Testsubject", "FY2025", "USD 42"):
            self.assertNotIn(value, INSTRUCTIONS + MESSAGE)


if __name__ == "__main__":
    unittest.main()

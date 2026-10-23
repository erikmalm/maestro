"""Pure evidence provenance and minimum suitability checks; no services or I/O."""
from copy import deepcopy
import hashlib
import json
import unittest

from backend.research_evidence import (
    INSTRUCTIONS, MAX_PACKET_BYTES, MESSAGE, SCHEMA, unavailable_review,
    validate_review, validate_saved_review,
)
from backend.research_requirements import validate_assessment


CHECKED_AT = "2026-10-23T14:00:00+02:00"


def requirements(request="Research Acme revenue for FY2025 in USD.", *, criterion="revenue",
                 period="FY2025", currency="USD", freshness=None, constraints=None):
    return validate_assessment({
        "version": 1, "decision": "ready",
        "subjects": [{"id": "s1", "kind": "company", "name": "Acme", "identifiers": []}],
        "items": [{"id": "i1", "subject_ids": ["s1"], "criterion": criterion,
                   "period": period, "unit": None, "currency": currency, "freshness": freshness}],
        "constraints": constraints or [], "clarifications": [],
    }, request)


def evidence_packet(text="\U0001f680 Acme revenue for FY2025 was USD 42 million.", *, start=1300):
    body = "x" * start + text + " Remaining saved source text."
    return {
        "version": 1, "offset_unit": "unicode_codepoint",
        "lookup": {"state": "ready", "search_limited": False, "skipped_count": 0},
        "selection": {"limited": False, "omitted_count": 0},
        "sources": [{"id": "c1", "capture_id": "a" * 32,
                     "content_hash": hashlib.sha256(body.encode()).hexdigest(), "manifest_hash": "b" * 64,
                     "title": "Acme annual report", "url": "https://reports.example.org/acme",
                     "retrieved_at": "2020-01-02T00:00:00+00:00", "published_at": None,
                     "modified_at": None, "stale": True,
                     "completeness": {"maestro_truncated": False, "full_page": False}}],
        "passages": [{"id": "p1", "source_id": "c1", "start": start, "end": start + len(text),
                      "text": text, "passage_hash": hashlib.sha256(text.encode()).hexdigest()}],
    }


def review(quote="Acme revenue for FY2025 was USD 42 million.", *, state="covered",
           scope="historical", period="FY2025", observation_date=None):
    return {"version": 1, "items": [{
        "item_id": "i1", "state": state, "reason": "The cited passage addresses the requested revenue.",
        "supports": [{"passage_id": "p1", "quote": quote, "temporal_scope": scope,
                      "period": period, "observation_date": observation_date}],
    }]}


class ResearchEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.assessment = requirements()
        self.packet = evidence_packet()
        self.raw = review()

    def validate(self, raw=None, packet=None, assessment=None, checked_at=CHECKED_AT):
        return validate_review(self.raw if raw is None else raw,
                               self.assessment if assessment is None else assessment,
                               self.packet if packet is None else packet, checked_at)

    def reject(self, raw=None, packet=None, assessment=None, checked_at=CHECKED_AT):
        with self.assertRaisesRegex(ValueError, "evidence review"):
            self.validate(raw, packet, assessment, checked_at)

    def test_historical_period_reuses_old_capture_and_derives_middle_unicode_locations(self):
        original = deepcopy((self.raw, self.assessment, self.packet))
        saved = self.validate()
        self.assertEqual(saved["items"][0]["state"], "covered")
        self.assertEqual(saved["checked_at"], CHECKED_AT)
        support = saved["items"][0]["supports"][0]
        self.assertEqual((support["start"], support["end"]), (1302, 1302 + len(self.raw["items"][0]["supports"][0]["quote"])))
        self.assertEqual(support["freshness_basis"], "explicit_requested_period")
        self.assertEqual(support["suitability"], "suitable")
        self.assertIsNone(support["best_before"])
        self.assertTrue(saved["sources"][0]["stale"])
        self.assertEqual((self.raw, self.assessment, self.packet), original)
        saved["sources"][0]["title"] = "changed"
        saved["items"][0]["supports"][0]["quote"] = "changed"
        self.assertEqual((self.raw, self.assessment, self.packet), original)

    def test_old_observation_cannot_cover_current_requirement_even_if_retrieved_today(self):
        assessment = requirements("Research Acme stock price in USD.", criterion="stock price", period=None)
        packet = evidence_packet("Acme stock price on 2020-01-02 was USD 12.")
        packet["sources"][0].update(retrieved_at=CHECKED_AT, stale=False)
        raw = review(packet["passages"][0]["text"], scope="current", period=None,
                     observation_date="2020-01-02")
        saved = self.validate(raw, packet, assessment)
        item = saved["items"][0]
        self.assertEqual((item["model_state"], item["state"]), ("covered", "stale"))
        self.assertEqual(item["supports"][0]["freshness_basis"], "past_observation_for_current_requirement")
        self.assertIn("withheld", item["suitability_reason"])
        self.assertEqual(validate_saved_review(saved, assessment), saved)

    def test_unknown_or_today_observation_is_not_independent_origin_verification(self):
        assessment = requirements("Research Acme stock price in USD.", criterion="stock price", period=None)
        for observation in (None, "2026-10-23", "2026-10-24"):
            with self.subTest(observation=observation):
                text = "Acme stock price was USD 12" + (" on " + observation if observation else "") + "."
                packet = evidence_packet(text)
                raw = review(text, scope="current", period=None, observation_date=observation)
                saved = self.validate(raw, packet, assessment)
                self.assertEqual(saved["items"][0]["state"], "unavailable")
                self.assertEqual(saved["items"][0]["supports"][0]["freshness_basis"], "current_origin_verification_unknown")

    def test_explicit_refresh_and_freshness_require_origin_check_while_retaining_context(self):
        raw = review(scope="refresh")
        saved = self.validate(raw)
        self.assertEqual(saved["items"][0]["state"], "unavailable")
        self.assertEqual(len(saved["items"][0]["supports"]), 1)
        assessment = requirements("Research Acme revenue for FY2025 in USD; latest source required.",
                                  freshness="latest source required")
        saved = self.validate(assessment=assessment)
        self.assertEqual(saved["items"][0]["state"], "unavailable")
        assessment = requirements("Research Acme revenue for FY2025 in USD; latest source required.", constraints=[{
            "kind": "freshness", "strength": "require", "item_ids": [], "criterion": "latest source required"}])
        self.assertEqual(self.validate(assessment=assessment)["items"][0]["state"], "unavailable")

    def test_historical_period_must_match_item_and_be_explicit_inside_support(self):
        packet = evidence_packet("Acme revenue FY2024 was USD 40; FY2025 was USD 42.")
        raw = review(packet["passages"][0]["text"], period="FY2024")
        saved = self.validate(raw, packet)
        self.assertEqual(saved["items"][0]["state"], "unavailable")
        self.assertEqual(saved["items"][0]["supports"][0]["freshness_basis"], "requested_period_not_established")
        raw["items"][0]["supports"][0]["period"] = "FY2030"
        self.reject(raw, packet)
        raw["items"][0]["supports"][0].update(period=None)
        self.assertEqual(self.validate(raw, packet)["items"][0]["state"], "unavailable")

    def test_unspecified_only_accepts_non_temporal_requirement(self):
        assessment = requirements("Research Acme headquarters.", criterion="headquarters", period=None, currency=None)
        packet = evidence_packet("Acme headquarters are in Example City.")
        raw = review(packet["passages"][0]["text"], scope="unspecified", period=None)
        self.assertEqual(self.validate(raw, packet, assessment)["items"][0]["state"], "covered")
        dated = evidence_packet("Acme headquarters recorded on 2020-01-02.")
        raw = review(dated["passages"][0]["text"], scope="unspecified", period=None,
                     observation_date="2020-01-02")
        self.assertEqual(self.validate(raw, dated, assessment)["items"][0]["state"], "unavailable")
        raw = review(scope="unspecified")
        self.assertEqual(self.validate(raw)["items"][0]["state"], "unavailable")

    def test_all_covered_supports_must_be_suitable(self):
        packet = evidence_packet("Acme revenue FY2025 USD 42; observation 2020-01-02.")
        raw = review("Acme revenue FY2025 USD 42")
        raw["items"][0]["supports"].append({"passage_id": "p1", "quote": "observation 2020-01-02",
            "temporal_scope": "current", "period": None, "observation_date": "2020-01-02"})
        self.assertEqual(self.validate(raw, packet)["items"][0]["state"], "stale")

    def test_every_mandatory_item_appears_once_with_closed_references(self):
        for mutation in (
            lambda raw: raw.update(items=[]),
            lambda raw: raw["items"].append(deepcopy(raw["items"][0])),
            lambda raw: raw["items"][0].update(item_id="unknown"),
            lambda raw: raw["items"][0]["supports"][0].update(passage_id="unknown"),
        ):
            raw = deepcopy(self.raw)
            mutation(raw)
            self.reject(raw)
        assessment = deepcopy(self.assessment)
        assessment["items"].append({**deepcopy(assessment["items"][0]), "id": "i2"})
        self.reject(assessment=assessment)

    def test_support_quotes_are_exact_unique_bounded_and_not_normalized(self):
        for quote in ("invented revenue", "Acme Revenue", "Acme revenue\x00", "x" * 1201):
            raw = review(quote)
            self.reject(raw)
        packet = evidence_packet("Acme revenue FY2025 USD 42; Acme revenue FY2025 USD 42.")
        self.reject(review("Acme revenue FY2025 USD 42"), packet)
        raw = deepcopy(self.raw)
        raw["items"][0]["supports"].append(deepcopy(raw["items"][0]["supports"][0]))
        self.reject(raw)

    def test_observation_dates_are_real_iso_days_quoted_in_support(self):
        for text, observed in (("Observation 2026-02-30", "2026-02-30"),
                               ("Observation 23/10/2026", "23/10/2026"),
                               ("Observation unknown", "2026-10-23")):
            with self.subTest(observed=observed):
                packet = evidence_packet(text)
                self.reject(review(text, scope="current", period=None, observation_date=observed), packet)

    def test_model_cannot_supply_offsets_pins_or_canonical_fields(self):
        mutations = (
            lambda raw: raw.update(answer="unsupported answer"),
            lambda raw: raw.update(sources=[]),
            lambda raw: raw["items"][0].update(model_state="covered"),
            lambda raw: raw["items"][0]["supports"][0].update(start=1300),
            lambda raw: raw["items"][0]["supports"][0].update(content_hash="c" * 64),
            lambda raw: raw["items"][0]["supports"][0].update(freshness_basis="verified"),
        )
        for mutation in mutations:
            raw = deepcopy(self.raw)
            mutation(raw)
            self.reject(raw)

    def test_packet_hash_locations_identity_and_nonoverlap_are_checked(self):
        mutations = (
            lambda packet: packet["passages"][0].update(passage_hash="0" * 64),
            lambda packet: packet["passages"][0].update(end=1301),
            lambda packet: packet["passages"][0].update(start=True),
            lambda packet: packet["passages"][0].update(source_id=[]),
            lambda packet: packet["passages"][0].update(source_id="unknown"),
            lambda packet: packet["passages"].append(deepcopy(packet["passages"][0])),
            lambda packet: packet["passages"].append({**deepcopy(packet["passages"][0]), "id": "p2"}),
            lambda packet: packet["sources"].append(deepcopy(packet["sources"][0])),
            lambda packet: packet["sources"][0].update(capture_id="wrong"),
            lambda packet: packet["sources"][0].update(completeness={"maestro_truncated": False, "full_page": True}),
            lambda packet: packet.update(offset_unit="byte"),
        )
        for mutation in mutations:
            packet = deepcopy(self.packet)
            mutation(packet)
            self.reject(packet=packet)

    def test_missing_is_local_to_inspected_packet_and_unavailable_lookup_cannot_prove_absence(self):
        raw = {"version": 1, "items": [{"item_id": "i1", "state": "missing",
               "reason": "No support in selected passages.", "supports": []}]}
        packet = deepcopy(self.packet)
        packet["lookup"].update(state="limited", search_limited=True, skipped_count=1)
        packet["selection"].update(limited=True, omitted_count=2)
        item = self.validate(raw, packet)["items"][0]
        self.assertEqual(item["state"], "missing")
        self.assertIn("not an exhaustive", item["suitability_reason"])
        self.assertIn("incomplete", item["suitability_reason"])
        for state in ("disabled", "unconfigured", "unavailable"):
            packet["lookup"]["state"] = state
            self.assertEqual(self.validate(raw, packet)["items"][0]["state"], "unavailable")
        raw["items"][0]["supports"] = deepcopy(self.raw["items"][0]["supports"])
        self.reject(raw, packet)

    def test_conflicts_need_two_distinct_passages_and_preserve_affected_item(self):
        packet = deepcopy(self.packet)
        text = "Acme revenue for FY2025 was USD 43 million."
        packet["passages"].append({"id": "p2", "source_id": "c1", "start": 2000,
                                   "end": 2000 + len(text), "text": text,
                                   "passage_hash": hashlib.sha256(text.encode()).hexdigest()})
        raw = review(state="conflicting")
        self.reject(raw, packet)
        raw["items"][0]["supports"].append({**deepcopy(raw["items"][0]["supports"][0]),
                                           "passage_id": "p2", "quote": text})
        item = self.validate(raw, packet)["items"][0]
        self.assertEqual((item["item_id"], item["state"]), ("i1", "conflicting"))
        self.assertEqual({support["passage_id"] for support in item["supports"]}, {"p1", "p2"})

    def test_saved_review_rederives_flags_offsets_policy_and_checked_at(self):
        saved = self.validate()
        self.assertEqual(validate_saved_review(saved, self.assessment), saved)
        mutations = (
            lambda value: value.pop("checked_at"),
            lambda value: value["items"][0].update(state="missing"),
            lambda value: value["items"][0].pop("model_state"),
            lambda value: value["items"][0].update(suitability_reason="Verified facts"),
            lambda value: value["items"][0]["supports"][0].update(start=0),
            lambda value: value["items"][0]["supports"][0].update(source_id="unknown"),
            lambda value: value["items"][0]["supports"][0].update(suitability="unavailable"),
            lambda value: value["items"][0]["supports"][0].update(best_before=CHECKED_AT),
            lambda value: value["items"][0]["supports"][0].update(freshness_basis="origin_verified"),
        )
        for mutation in mutations:
            value = deepcopy(saved)
            mutation(value)
            with self.assertRaises(ValueError):
                validate_saved_review(value, self.assessment)
        packet = evidence_packet("Acme revenue FY2025", start=0)
        saved = self.validate(review("Acme revenue FY2025"), packet)
        saved["items"][0]["supports"][0]["start"] = False
        with self.assertRaises(ValueError):
            validate_saved_review(saved, self.assessment)

    def test_unavailable_server_record_needs_no_selected_sources_or_model_output(self):
        saved = unavailable_review(self.assessment,
            {"state": "disabled", "search_limited": False, "skipped_count": 0},
            {"limited": False, "omitted_count": 0},
            "Source saving is disabled; no saved evidence was inspected.", CHECKED_AT)
        self.assertEqual(saved["sources"], [])
        self.assertEqual(saved["passages"], [])
        self.assertEqual(saved["items"][0]["state"], "unavailable")
        self.assertEqual(validate_saved_review(saved, self.assessment), saved)

    def test_types_bounds_and_nonready_assessments_fail_closed(self):
        mutations = (
            lambda raw: raw.update(version=True),
            lambda raw: raw["items"][0].update(reason="x" * 801),
            lambda raw: raw["items"][0].update(reason="bad\ud800"),
            lambda raw: raw["items"][0].update(state="answer"),
            lambda raw: raw["items"][0].update(supports=[raw["items"][0]["supports"][0]] * 5),
            lambda raw: raw["items"][0].update(supports=[]),
        )
        for mutation in mutations:
            raw = deepcopy(self.raw)
            mutation(raw)
            self.reject(raw)
        for checked_at in ("2026-10-23", "not a time", None):
            self.reject(checked_at=checked_at)
        assessment = deepcopy(self.assessment)
        assessment.update(decision="clarify", clarifications=[{"kind": "scope", "item_ids": [],
                          "quote": "revenue", "question": 'What scope do you mean by "revenue"?'}])
        self.reject(assessment=assessment)
        packet = deepcopy(self.packet)
        packet["selection"]["omitted_count"] = True
        self.reject(packet=packet)
        packet = deepcopy(self.packet)
        packet["sources"][0]["title"] = "x" * MAX_PACKET_BYTES
        self.reject(packet=packet)

    def test_schema_and_static_prompt_are_bounded_closed_and_contain_no_fixture_request(self):
        self.assertLess(len(json.dumps(SCHEMA).encode()), 8192)
        self.assertLess(len(INSTRUCTIONS.encode()), 16000)
        self.assertNotIn(self.assessment["completion_criterion"], MESSAGE)

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
        closed(SCHEMA)


if __name__ == "__main__":
    unittest.main()

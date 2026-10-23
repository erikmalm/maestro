"""Pure saved-passage selection and exact revalidation; no archive or model calls."""
from copy import deepcopy
import hashlib
import unittest

from backend.research_passages import MAX_PACKET_BYTES, packet_size, select_packet, validate_packet


LOOKUP = {"state": "ready", "search_limited": False, "skipped_count": 0}


def assessment():
    return {"subjects": [{"name": "Acme", "identifiers": []}],
            "items": [{"criterion": "revenue", "period": "FY2025", "unit": None,
                       "currency": "USD", "freshness": None}]}


def candidate(content, number=1, **changes):
    return {"capture_id": f"{number:032x}", "content_hash": hashlib.sha256(content.encode("utf-8")).hexdigest(),
            "manifest_hash": f"{number:064x}", "title": "Synthetic public report",
            "url": f"https://reports.example.org/{number}", "retrieved_at": "2026-10-22T12:00:00+00:00",
            "published_at": None, "modified_at": None, "stale": False,
            "completeness": {"maestro_truncated": False, "full_page": False}, "content": content,
            "body_verified": True, "match_kind": "keyword", **changes}


class ResearchPassageTests(unittest.TestCase):
    def loader(self, sources, *, stale=False):
        records = {source["capture_id"]: source for source in sources}

        def load(capture_id, content_hash, manifest_hash):
            source = deepcopy(records[capture_id])
            if (source["content_hash"], source["manifest_hash"]) != (content_hash, manifest_hash):
                raise KeyError("The saved version is unavailable.")
            if not stale:
                source.pop("stale")  # Canonical get_capture has no stale field.
            return source

        return load

    def test_relevant_tail_after_first_1200_characters_is_selected_and_reopened(self):
        text = "unrelated context " * 390 + "\nAcme revenue FY2025 was USD 42 billion."
        source = candidate(text)
        packet = select_packet([source], assessment(), LOOKUP, max_passages=1)
        passage = packet["passages"][0]
        self.assertGreater(passage["start"], 1200)
        self.assertIn("Acme revenue FY2025", passage["text"])
        self.assertEqual(passage["text"], text[passage["start"]:passage["end"]])
        self.assertEqual(validate_packet(packet, self.loader([source])), packet)

    def test_unicode_offsets_and_hashes_use_unchanged_codepoints_and_utf8(self):
        text = "\U0001f680 e\u0301 " * 650 + "\nAcme revenue FY2025 USD \u00a342."
        source = candidate(text)
        packet = select_packet([source], assessment(), LOOKUP, max_passages=1)
        passage = packet["passages"][0]
        self.assertEqual(packet["offset_unit"], "unicode_codepoint")
        self.assertGreater(len(text[:passage["start"]].encode("utf-8")), passage["start"])
        self.assertEqual(passage["text"], text[passage["start"]:passage["end"]])
        self.assertEqual(passage["passage_hash"], hashlib.sha256(passage["text"].encode("utf-8")).hexdigest())
        self.assertEqual(validate_packet(packet, self.loader([source])), packet)

    def test_packet_byte_limit_includes_metadata_unicode_and_json_escapes(self):
        sources = [candidate(("\U0001f680\n" * 500) + " Acme revenue FY2025 USD", number)
                   for number in range(1, 9)]
        for maximum in (MAX_PACKET_BYTES, 2500, 1200):
            with self.subTest(maximum=maximum):
                packet = select_packet(sources, assessment(), LOOKUP, max_bytes=maximum)
                self.assertLessEqual(packet_size(packet), maximum)
                self.assertTrue(packet["selection"]["limited"])
                self.assertLessEqual(len(packet["sources"]), 8)
                self.assertLessEqual(len(packet["passages"]), 6)
                self.assertTrue(all(len(passage["text"]) <= 1200 for passage in packet["passages"]))
                self.assertEqual(validate_packet(packet, self.loader(sources)), packet)

    def test_omissions_lookup_limits_and_unused_sources_remain_explicit(self):
        sources = [candidate("Acme revenue FY2025 USD " + "x" * 1300, number) for number in range(1, 9)]
        sources.append(candidate("Entirely unrelated saved text.", 9))
        lookup = {"state": "limited", "search_limited": True, "skipped_count": 3, "message": "Work bounded."}
        packet = select_packet(sources, assessment(), lookup, max_passages=2)
        self.assertEqual(packet["lookup"], {key: lookup[key] for key in LOOKUP})
        self.assertTrue(packet["selection"]["limited"])
        self.assertGreater(packet["selection"]["omitted_count"], 0)
        self.assertEqual({source["id"] for source in packet["sources"]},
                         {passage["source_id"] for passage in packet["passages"]})
        self.assertNotIn(sources[-1]["capture_id"], {source["capture_id"] for source in packet["sources"]})
        self.assertEqual(validate_packet(packet, self.loader(sources)), packet)

    def test_short_source_metadata_and_historical_completeness_are_preserved(self):
        source = candidate("Acme revenue FY2025 USD", title="", stale=True,
                           completeness={"maestro_truncated": True, "full_page": False})
        original = deepcopy(source)
        packet = select_packet([source, deepcopy(source)], assessment(), LOOKUP)
        self.assertEqual(len(packet["sources"]), 1)
        self.assertEqual(packet["sources"][0]["completeness"], source["completeness"])
        self.assertTrue(packet["sources"][0]["stale"])
        self.assertIsNone(packet["sources"][0]["published_at"])
        self.assertEqual(source, original)
        refreshed = validate_packet(packet, self.loader([source], stale=True))
        refreshed["sources"][0]["completeness"]["full_page"] = True
        self.assertEqual(source, original)
        self.assertFalse(packet["sources"][0]["completeness"]["full_page"])

    def test_selected_windows_are_trimmed_to_nonoverlapping_source_spans(self):
        source = candidate(("Acme revenue FY2025 USD " + "context " * 70 + "\n") * 10)
        packet = select_packet([source], assessment(), LOOKUP)
        self.assertGreater(len(packet["passages"]), 1)
        for index, passage in enumerate(packet["passages"]):
            for other in packet["passages"][index + 1:]:
                self.assertTrue(passage["end"] <= other["start"] or other["end"] <= passage["start"])
        self.assertEqual(validate_packet(packet, self.loader([source])), packet)

    def test_missing_deleted_or_now_ineligible_capture_rejects_the_packet(self):
        source = candidate("Acme revenue FY2025 USD")
        packet = select_packet([source], assessment(), LOOKUP)
        for exception in (KeyError("deleted"), ValueError("now ineligible"), OSError("missing object")):
            with self.subTest(exception=type(exception)):
                def unavailable(*pins):
                    raise exception
                with self.assertRaises(ValueError):
                    validate_packet(packet, unavailable)

    def test_changed_metadata_or_content_from_loader_cannot_change_recorded_evidence(self):
        source = candidate("Acme revenue FY2025 USD")
        packet = select_packet([source], assessment(), LOOKUP)
        changes = ({"title": "Changed title"}, {"url": "https://different.example.org/report"},
                   {"retrieved_at": "2026-10-23T12:00:00+00:00"}, {"published_at": "2026-10-21T12:00:00+00:00"},
                   {"manifest_hash": "f" * 64}, {"content_hash": "f" * 64}, {"content": "Changed saved body"},
                   {"completeness": {"maestro_truncated": True, "full_page": False}}, {"stale": True})
        for change in changes:
            with self.subTest(change=change), self.assertRaises(ValueError):
                validate_packet(packet, lambda *pins: {**source, **change})

    def test_packet_metadata_offsets_text_hashes_and_pins_cannot_be_tampered(self):
        source = candidate("Acme revenue FY2025 USD")
        packet = select_packet([source], assessment(), LOOKUP)
        mutations = (
            lambda value: value["sources"][0].update(title="Changed title"),
            lambda value: value["sources"][0].update(content_hash="f" * 64),
            lambda value: value["sources"][0].update(manifest_hash="f" * 64),
            lambda value: value["passages"][0].update(start=True),
            lambda value: value["passages"][0].update(start=1),
            lambda value: value["passages"][0].update(end=99999),
            lambda value: value["passages"][0].update(text="Invented fact"),
            lambda value: value["passages"][0].update(passage_hash="f" * 64),
            lambda value: value["passages"][0].update(source_id="c2"),
            lambda value: value.update(offset_unit="utf16"),
            lambda value: value.update(version=True),
            lambda value: value.update(answer="Invented answer"),
        )
        for mutate in mutations:
            changed = deepcopy(packet)
            mutate(changed)
            with self.subTest(mutate=mutate), self.assertRaises(ValueError):
                validate_packet(changed, self.loader([source]))

    def test_duplicate_unreferenced_and_excess_records_are_rejected(self):
        source = candidate("Acme revenue FY2025 USD")
        packet = select_packet([source], assessment(), LOOKUP)
        mutations = (
            lambda value: value["sources"].append({**value["sources"][0], "id": "c2"}),
            lambda value: value["sources"].append({**value["sources"][0], "id": "c2", "capture_id": "2" * 32}),
            lambda value: value["passages"].append({**value["passages"][0], "id": "p2"}),
            lambda value: value.update(passages=[{**value["passages"][0], "id": f"p{number}"} for number in range(1, 8)]),
            lambda value: value["selection"].update(limited=False, omitted_count=1),
        )
        for mutate in mutations:
            changed = deepcopy(packet)
            mutate(changed)
            with self.subTest(mutate=mutate), self.assertRaises(ValueError):
                validate_packet(changed, self.loader([source]))
        overlap = deepcopy(packet)
        second = {**overlap["passages"][0], "id": "p2", "start": 1,
                  "text": source["content"][1:]}
        second["passage_hash"] = hashlib.sha256(second["text"].encode("utf-8")).hexdigest()
        overlap["passages"].append(second)
        with self.assertRaises(ValueError):
            validate_packet(overlap, self.loader([source]))

    def test_invalid_bounds_and_inconsistent_candidate_hashes_are_rejected(self):
        source = candidate("Acme revenue FY2025 USD")
        for options in ({"max_passages": 0}, {"max_passages": 7}, {"max_passages": True},
                        {"max_bytes": 1}, {"max_bytes": MAX_PACKET_BYTES + 1}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                select_packet([source], assessment(), LOOKUP, **options)
        for changed in ({**source, "content": "Altered bytes"}, {**source, "content_hash": "f" * 64},
                        {**source, "capture_id": "bad"}):
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                select_packet([changed], assessment(), LOOKUP)

    def test_empty_and_unmatched_candidates_do_not_invent_passages(self):
        for sources in ([], [candidate("Entirely unrelated saved text.")]):
            packet = select_packet(sources, assessment(), {**LOOKUP, "skipped_count": 2})
            self.assertEqual(packet["sources"], [])
            self.assertEqual(packet["passages"], [])
            self.assertEqual(packet["lookup"]["skipped_count"], 2)
            self.assertEqual(validate_packet(packet, lambda *pins: self.fail("Unexpected source read")), packet)


if __name__ == "__main__":
    unittest.main()

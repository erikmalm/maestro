"""Larger saved text keeps exact tail evidence and bounded local nomination."""
from copy import deepcopy
import hashlib
import sqlite3
import unittest
from unittest.mock import patch

from backend import research_passages as passages
from tests.test_research_passages import LOOKUP, assessment, candidate


class LargePassageBoundsTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.object(passages, "SOURCE_BYTES", 256 * 1024))

    def loader(self, sources):
        records = {source["capture_id"]: source for source in sources}

        def load(capture_id, content_hash, manifest_hash):
            source = records[capture_id]
            self.assertEqual((content_hash, manifest_hash), (source["content_hash"], source["manifest_hash"]))
            return deepcopy(source)

        return load

    def guard_sql(self, *, before_highlight=None, after_insert=None):
        connect = sqlite3.connect

        class GuardedConnection(sqlite3.Connection):
            current_body = None

            def execute(connection, statement, parameters=(), **kwargs):
                if statement.startswith("SELECT highlight(") and before_highlight is not None:
                    before_highlight(connection.current_body)
                result = super().execute(statement, parameters, **kwargs)
                if statement.startswith("INSERT INTO passage_text"):
                    connection.current_body = parameters[0]
                    if after_insert is not None:
                        after_insert(parameters[0])
                return result

        def guarded(database, **kwargs):
            return connect(database, factory=GuardedConnection, **kwargs)

        return patch.object(passages.sqlite3, "connect", side_effect=guarded)

    def test_sparse_tail_of_maximum_body_preserves_unicode_offsets_and_pins(self):
        tail = "\n\U0001f680 Acme revenue FY2025 was USD 42 million."
        prefix = "ordinary context " * 14000
        padding = 256 * 1024 - len((prefix + tail).encode("utf-8"))
        text = prefix + "x" * padding + tail
        source = candidate(text, completeness={"maestro_truncated": True, "full_page": False})
        original = deepcopy(source)
        packet = passages.select_packet([source], assessment(), LOOKUP, max_passages=1)
        self.assertEqual(len(text.encode("utf-8")), 256 * 1024)
        passage = packet["passages"][0]
        self.assertGreater(passage["start"], 8192)
        self.assertIn("Acme revenue FY2025", passage["text"])
        self.assertEqual(passage["text"], text[passage["start"]:passage["end"]])
        self.assertEqual(passage["passage_hash"], hashlib.sha256(passage["text"].encode("utf-8")).hexdigest())
        self.assertEqual(packet["sources"][0]["content_hash"], source["content_hash"])
        self.assertEqual(packet["sources"][0]["manifest_hash"], source["manifest_hash"])
        self.assertEqual(packet["sources"][0]["completeness"], source["completeness"])
        self.assertLessEqual(passages.packet_size(packet), 7000)
        self.assertEqual(passages.validate_packet(packet, self.loader([source])), packet)
        self.assertEqual(source, original)

    def test_dense_body_is_omitted_whole_before_native_highlight(self):
        dense = candidate(("Acme revenue FY2025 USD " * 11000)[:256 * 1024], 2)
        sources = [candidate("Acme revenue FY2025 USD 41.", 1), dense,
                   candidate("Acme revenue FY2025 USD 42.", 3)]
        original = deepcopy(sources)

        def bounded_highlight(body):
            # This guard makes the regression fail before an old unbounded
            # native highlight can hang the test process.
            self.assertNotEqual(body, dense["content"])

        with self.guard_sql(before_highlight=bounded_highlight):
            packet = passages.select_packet(sources, assessment(), LOOKUP)
        self.assertEqual({source["capture_id"] for source in packet["sources"]},
                         {sources[0]["capture_id"], sources[2]["capture_id"]})
        self.assertEqual(packet["selection"], {"limited": True, "omitted_count": 1})
        self.assertEqual(packet["lookup"], LOOKUP)
        self.assertEqual(passages.validate_packet(packet, self.loader(sources)), packet)
        self.assertEqual(sources, original)

    def test_tight_packet_fitting_reserves_digits_for_omitted_dense_candidates(self):
        sparse = candidate("Acme revenue FY2025 was USD 42 million.")
        dense = [candidate("Acme " * 513, number) for number in range(2, 12)]
        sources = [sparse, *dense]
        original = deepcopy(sources)
        single = passages.select_packet([sparse], assessment(), LOOKUP, max_passages=1)
        # One nominated window has a one-digit counter; the ten whole-source
        # omissions need two digits even after the usable passage is selected.
        allowance = passages.packet_size({**single, "selection": {"limited": True, "omitted_count": 1}})
        packet = passages.select_packet(sources, assessment(), LOOKUP, max_passages=1, max_bytes=allowance)
        self.assertEqual(len(packet["passages"]), 1)
        self.assertEqual(packet["selection"], {"limited": True, "omitted_count": 10})
        self.assertLessEqual(passages.packet_size(packet), allowance)
        self.assertEqual(packet["sources"][0]["content_hash"], sparse["content_hash"])
        self.assertEqual(packet["sources"][0]["manifest_hash"], sparse["manifest_hash"])
        self.assertEqual(passages.validate_packet(packet, self.loader(sources)), packet)
        self.assertEqual(sources, original)

    def test_tight_packet_fitting_reserves_the_longer_final_boolean(self):
        source = candidate("Acme revenue FY2025 was USD 42 million.")
        full = passages.select_packet([source], assessment(), LOOKUP, max_passages=1)
        self.assertFalse(full["selection"]["limited"])
        # JSON false is one byte longer than true. A full source that fits a
        # true placeholder must still yield a bounded, usable fitted packet.
        allowance = passages.packet_size(full) - 1
        packet = passages.select_packet([source], assessment(), LOOKUP, max_passages=1, max_bytes=allowance)
        self.assertEqual(len(packet["passages"]), 1)
        self.assertEqual(packet["selection"], {"limited": True, "omitted_count": 0})
        self.assertLessEqual(passages.packet_size(packet), allowance)
        self.assertLess(len(packet["passages"][0]["text"]), len(source["content"]))
        self.assertEqual(passages.validate_packet(packet, self.loader([source])), packet)

    def test_aggregate_body_allowance_retains_only_complete_candidate_nominations(self):
        first = candidate("ordinary context " * 1000 + "Acme revenue FY2025 USD 41.", 1)
        sources = [first, candidate("Acme revenue FY2025 USD 42.", 2),
                   candidate("Acme revenue FY2025 USD 43.", 3)]
        allowed = len(first["content"].encode("utf-8"))

        def first_body_only(body):
            self.assertNotIn(body, (sources[1]["content"], sources[2]["content"]))

        with patch.object(passages, "PASSAGE_INPUT_BYTES", allowed), self.guard_sql(after_insert=first_body_only):
            packet = passages.select_packet(sources, assessment(), LOOKUP, max_passages=1)
        self.assertEqual([source["capture_id"] for source in packet["sources"]], [first["capture_id"]])
        self.assertTrue(packet["selection"]["limited"])
        self.assertGreaterEqual(packet["selection"]["omitted_count"], 2)
        self.assertGreater(packet["passages"][0]["start"], 8192)
        self.assertEqual(passages.validate_packet(packet, self.loader(sources)), packet)

    def test_sql_work_limit_interrupts_large_tokenization_before_highlight(self):
        source = candidate("ordinary context " * 12000 + "Acme revenue FY2025 USD 42.")
        with (patch.object(passages, "PASSAGE_SQL_STEPS", 1000),
              self.guard_sql(before_highlight=lambda body: self.fail("Highlight after SQL work limit"))):
            packet = passages.select_packet([source], assessment(), LOOKUP)
        self.assertEqual(packet["sources"], [])
        self.assertEqual(packet["passages"], [])
        self.assertEqual(packet["selection"], {"limited": True, "omitted_count": 1})

    def test_elapsed_deadline_discards_current_whole_candidate(self):
        source = candidate("ordinary context " * 12000 + "Acme revenue FY2025 USD 42.")
        clock = [0.0]

        def expire_after_body(body):
            if body == source["content"]:
                clock[0] = 3.0

        with (patch.object(passages.time, "monotonic", side_effect=lambda: clock[0]),
              self.guard_sql(after_insert=expire_after_body,
                             before_highlight=lambda body: self.fail("Highlight after elapsed deadline"))):
            packet = passages.select_packet([source], assessment(), LOOKUP)
        self.assertEqual(packet["sources"], [])
        self.assertEqual(packet["passages"], [])
        self.assertEqual(packet["selection"], {"limited": True, "omitted_count": 1})

    def test_validation_rejects_source_reads_exceeding_shared_byte_allowance(self):
        sources = [candidate("ordinary context " * 3000 + f"Acme revenue FY2025 USD {number}.", number)
                   for number in (1, 2)]
        packet = passages.select_packet(sources, assessment(), LOOKUP, max_passages=2)
        self.assertEqual(len(packet["sources"]), 2)
        with (patch.object(passages, "PASSAGE_INPUT_BYTES", len(sources[0]["content"].encode("utf-8"))),
              self.assertRaisesRegex(ValueError, "invalid or unavailable")):
            passages.validate_packet(packet, self.loader(sources))

    def test_validation_withholds_body_returned_after_elapsed_deadline(self):
        source = candidate("Acme revenue FY2025 USD 42.")
        packet = passages.select_packet([source], assessment(), LOOKUP)
        clock = [0.0]

        def late_source(*pins):
            clock[0] = 3.0
            return source

        with (patch.object(passages.time, "monotonic", side_effect=lambda: clock[0]),
              self.assertRaisesRegex(ValueError, "invalid or unavailable")):
            passages.validate_packet(packet, late_source)


if __name__ == "__main__":
    unittest.main()

"""Large saved excerpts retain bounded screening through journal and research reads."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from backend import app as backend
from backend.acquisition import AcquisitionJournal
from backend.context_policy import DEFAULT, SOURCE_BYTES
from backend.context_store import ContextStore
from backend.research import ResearchStore
from backend.research_passages import select_packet
from backend.research_stages import safe_stage_content
from tests.test_research_passages import LOOKUP, assessment


class ResearchStageSourceBoundsTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory(prefix="maestro-source-screening-")))
        self.context = ContextStore(self.root / "private" / "workspace.sqlite3", self.root / "archive")
        self.context.configure({**DEFAULT, "enabled": True})
        self.journal = AcquisitionJournal(ResearchStore(self.context.database))
        self.key = "synthetic-current-source-screening-key"
        self.enterContext(patch("backend.credentials.read", side_effect=lambda endpoint: (self.key, "memory")))
        self.enterContext(patch.object(backend, "context_store", return_value=self.context))
        self.enterContext(patch.object(backend, "source_secrets", side_effect=lambda: (self.key,)))
        self.at = "2026-11-27T10:00:00+00:00"

    def capture(self, unit, suffix=""):
        tail = "\nAcme FY2025 revenue was USD 42 million. " + suffix
        prefix_bytes = SOURCE_BYTES - len(tail.encode("utf-8"))
        body = unit * (prefix_bytes // len(unit)) + "x" * (prefix_bytes % len(unit)) + tail
        self.assertEqual(len(body.encode("utf-8")), SOURCE_BYTES)
        source = self.context.capture("Synthetic Acme revenue", self.at, [{
            "title": "Synthetic source with repeated punctuation",
            "url": "https://reports.example.org/acme", "content": body,
        }], 1)["sources"][0]
        self.assertEqual(source["archive_status"], "saved")
        return source

    def pins(self, source):
        reference = {field: source[field] for field in ("capture_id", "content_hash", "manifest_hash", "retrieved_at")}
        record = {"spec": {"max_results": 1}, "receipt": {"at": self.at, "sources": [
            {field: source[field] for field in ("url", "title", "content_hash")}]}}
        return record, reference

    def packet(self, source):
        packet = select_packet([{**source, "stale": False}], assessment(), LOOKUP, max_passages=1)
        self.assertEqual(len(packet["passages"]), 1)
        return packet

    def test_maximum_sources_with_separated_escapes_retain_exact_journal_pins(self):
        for unit in ("x\n", 'x"', "x\\"):
            with self.subTest(unit=repr(unit)):
                source = self.capture(unit)
                record, reference = self.pins(source)
                self.assertEqual(self.journal._pins(record, [reference], self.context, self.at), [reference])

    def test_research_revalidation_accepts_maximum_newline_quote_and_backslash_sources(self):
        for unit in ("x\n", 'x"', "x\\"):
            with self.subTest(unit=repr(unit)):
                packet = self.packet(self.capture(unit))
                self.assertEqual(backend._verified_evidence_packet(packet, lambda value: value), packet)

    def test_later_credential_after_many_escapes_is_detected_as_a_credential(self):
        later_key = "synthetic-later-source-screening-key"
        escaped = "".join("\\u" + format(ord(char), "04x") for char in later_key)
        source = self.capture("x\n", escaped)
        record, reference = self.pins(source)
        packet = self.packet(source)
        self.key = later_key
        with self.assertRaisesRegex(ValueError, "Remove credentials"):
            self.journal._pins(record, [reference], self.context, self.at)
        # Revalidation withholds the packet rather than publishing its prose.
        with self.assertRaises(ValueError):
            backend._verified_evidence_packet(packet, lambda value: self.fail("Unsafe packet published"))

    def test_retained_credential_is_still_screened_after_rotation(self):
        initial_key = self.key
        escaped = "".join("\\u" + format(ord(char), "04x") for char in initial_key)
        source = self.capture('x"', escaped)
        self.key = "synthetic-rotated-source-screening-key"
        with self.assertRaisesRegex(ValueError, "Remove credentials"):
            safe_stage_content([source, json.dumps(source, ensure_ascii=False)],
                               {"protocol": "ollama"}, secrets=(initial_key,))

    def test_aggregate_character_and_depth_allowances_remain_finite(self):
        with self.assertRaisesRegex(ValueError, "screening bounds"):
            safe_stage_content(["x" * (2 * 1024 * 1024), "y" * (2 * 1024 * 1024), "z"], {})
        value = "safe"
        for _ in range(33):
            value = [value]
        with self.assertRaisesRegex(ValueError, "screening bounds"):
            safe_stage_content(value, {})


if __name__ == "__main__":
    unittest.main()

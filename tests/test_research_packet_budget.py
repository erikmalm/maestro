"""Fit optional saved passages within the shared run allowance, not just context."""
from contextlib import closing
import json
import sqlite3
import unittest
from unittest.mock import patch

from backend import app as backend
from backend.research_passages import select_packet
from backend.research_stages import FINISHING_TOKENS
from tests import test_research_evidence_api as evidence_fixtures
from tests.test_research_requirements_api import REQUEST


class ResearchPacketBudgetTests(unittest.TestCase):
    def setUp(self):
        self.fixture = evidence_fixtures.ResearchEvidenceApiTests(
            "test_historical_tail_support_uses_two_owned_calls_and_releases_all_holds")
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.setUp()
        with backend.provider().transaction() as state:
            # Context comfortably fits the original packet; only the shared
            # consumed allowance and protected finishing holds constrain it.
            state["config"].update(ollama_context_tokens=131072, max_output_tokens=1024)
        for number in range(3):
            self.fixture.capture((f"Unrelated saved background {number}. " * 120) + evidence_fixtures.HISTORICAL)
        self.snapshot = self.fixture.archive_fixture.source_snapshot()
        self.packet_caps = []
        self.protected = []
        inference = self.fixture.inference

        def response(config, path, payload, deadline):
            reply = inference(config, path, payload, deadline)
            if path == "/api/chat":
                if "decision" in payload["format"]["properties"]:
                    reply.update(prompt_eval_count=7000, eval_count=1000)
                else:
                    with closing(sqlite3.connect(backend.DATABASE)) as db:
                        held = db.execute("SELECT held FROM research_finishing").fetchall()
                    self.protected.append(json.loads(held[0][0]))
            return reply

        def selected(*args, **kwargs):
            self.packet_caps.append(kwargs["max_bytes"])
            return select_packet(*args, **kwargs)

        self.enterContext(patch("backend.provider.background_network", side_effect=response))
        self.enterContext(patch("backend.research_passages.select_packet", side_effect=selected))

    def set_tokens(self, total):
        with backend.workspace_transaction() as state:
            state["limits"]["max_tokens"] = total

    def assert_preserved_and_released(self, run, calls):
        self.assertEqual(run["assessment"]["completion_criterion"], REQUEST)
        self.assertEqual([(item["criterion"], item["period"], item["currency"])
                          for item in run["assessment"]["items"]], [("revenue", "FY2025", "USD")])
        consumed = run["budget"]["consumed"]
        self.assertEqual((consumed["model_calls"], consumed["tokens"], consumed["hosted_searches"]),
                         (calls, 8000 + (calls - 1) * 48, 0))
        self.assertTrue(all(amount == 0 for amount in run["budget"]["reserved"].values()))
        self.fixture.fixture.assert_released()
        with closing(sqlite3.connect(backend.DATABASE)) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM research_finishing WHERE run_id=?", (run["id"],)).fetchone()[0], 0)
        self.assertEqual(self.fixture.archive_fixture.source_snapshot(), self.snapshot)

    def test_consumed_tokens_and_finishing_holds_force_a_smaller_evidence_packet(self):
        self.set_tokens(40000)
        run = self.fixture.run_now()
        self.assertEqual(run["state"], "partial")
        self.assertEqual(run["evidence_review"]["items"][0]["state"], "covered")
        self.assertGreater(len(self.packet_caps), 1)
        self.assertEqual(self.packet_caps[0], 7000)
        self.assertLess(self.packet_caps[-1], 7000)
        self.assertTrue(run["evidence_review"]["selection"]["limited"])
        self.assertEqual(len(self.fixture.fixture.chats()), 2)
        self.assertEqual(self.fixture.inputs[0]["assessment"]["completion_criterion"], REQUEST)
        self.assertEqual(len(self.protected), 1)
        self.assertEqual(set(self.protected[0]), {"writer", "reviewer"})
        self.assertEqual(sum(slot["tokens"] for slot in self.protected[0].values()), 2 * FINISHING_TOKENS)
        self.assert_preserved_and_released(run, 2)

    def test_no_optional_packet_fits_returns_a_truthful_gap_without_second_inference(self):
        self.set_tokens(34000)
        run = self.fixture.run_now()
        self.assertEqual(run["state"], "partial")
        review = run["evidence_review"]
        self.assertEqual(review["sources"], [])
        self.assertEqual(review["passages"], [])
        self.assertEqual(review["items"][0]["state"], "missing")
        self.assertTrue(review["selection"]["limited"])
        self.assertEqual(review["selection"]["omitted_count"], 3)
        self.assertIn("not an exhaustive", review["items"][0]["reason"])
        self.assertEqual(len(self.fixture.fixture.chats()), 1)
        self.assertEqual(self.protected, [])
        self.assertEqual(self.packet_caps[:4], [7000, 4500, 2500, 1200])
        self.assert_preserved_and_released(run, 1)


if __name__ == "__main__":
    unittest.main()

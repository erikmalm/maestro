"""Review regressions for unused finishing capacity and terminal ownership."""
from contextlib import closing
import json
import sqlite3
import unittest
from unittest.mock import patch

from backend.provider import ProviderFailure
from backend.research import ResearchStore
from tests import test_research_stage_integration as fixtures


class ResearchStageReviewTests(unittest.TestCase):
    def fixture(self):
        fixture = fixtures.ResearchStageIntegrationTests(
            "test_provider_and_run_reservation_roll_back_as_one_transaction")
        self.addCleanup(fixture.doCleanups)
        fixture.setUp()
        return fixture

    def test_undispatched_writer_and_reviewer_keep_their_finishing_capacity(self):
        for kind in ("writer", "reviewer"):
            with self.subTest(kind=kind):
                fixture = self.fixture()
                with patch("backend.provider.background_network", side_effect=ProviderFailure("Synthetic model unavailable.", False)):
                    with self.assertRaises(ValueError):
                        fixture.generate(fixture.stage(kind))
                budget = fixture.store.get(fixture.run["id"])["budget"]
                self.assertEqual(budget["consumed"]["model_calls"], 0)
                self.assertEqual(budget["reserved"]["model_calls"], 2)
                self.assertEqual(budget["reserved"]["tokens"], 2 * 8192)
                self.assertEqual(budget["reserved"]["seconds"], 10)
                with patch("backend.provider.background_network", side_effect=fixture.response):
                    fixture.generate(fixture.stage())
                    fixture.generate(fixture.stage())
                    with self.assertRaises(ValueError):
                        fixture.generate(fixture.stage())
                    fixture.generate(fixture.stage("writer"))
                    fixture.generate(fixture.stage("reviewer"))
                final = fixture.store.get(fixture.run["id"])["budget"]
                self.assertEqual(final["consumed"]["model_calls"], 4)
                self.assertEqual(final["reserved"]["model_calls"], 0)

    def test_terminal_or_deleted_owner_does_not_recreate_unused_holds(self):
        for outcome in ("cancel", "expire", "recover", "delete"):
            with self.subTest(outcome=outcome):
                fixture = self.fixture()
                stage = fixture.stage("writer")

                def reject_after_terminal(*args):
                    if outcome == "cancel":
                        fixture.store.cancel(stage.run_id)
                    elif outcome == "expire":
                        with patch.object(ResearchStore, "clock", return_value=fixture.now + 121):
                            fixture.store.expire()
                    elif outcome == "recover":
                        fixture.store.recover()
                    else:
                        fixture.store.delete(stage.run_id)
                    raise ProviderFailure("Synthetic model unavailable.", False)

                with patch("backend.provider.background_network", side_effect=reject_after_terminal):
                    with self.assertRaises(ValueError):
                        fixture.generate(stage)
                with closing(sqlite3.connect(fixture.database)) as db:
                    self.assertIsNone(db.execute("SELECT held FROM research_finishing WHERE run_id=?",
                                                 (stage.run_id,)).fetchone())
                    state, reserved = db.execute("SELECT state,reserved FROM research_stages WHERE id=?",
                                                 (stage.stage_id,)).fetchone()
                    self.assertEqual(state, "failed")
                    self.assertEqual(json.loads(reserved)["model_calls"], 0)
                self.assertEqual(fixture.provider.read_state()["ledger"][-1]["model_calls"], 0)

    def test_dispatched_invalid_output_does_not_refund_a_finishing_call(self):
        fixture = self.fixture()

        def malformed(*args):
            result = fixture.response(*args)
            if args[1] == "/api/chat":
                result["message"]["content"] = "not JSON"
            return result

        with patch("backend.provider.background_network", side_effect=malformed):
            with self.assertRaises(ValueError):
                fixture.generate(fixture.stage("writer"))
        budget = fixture.store.get(fixture.run["id"])["budget"]
        self.assertEqual(budget["consumed"]["model_calls"], 1)
        self.assertEqual(budget["consumed"]["tokens"], 48)
        self.assertEqual(budget["reserved"]["model_calls"], 1)


if __name__ == "__main__":
    unittest.main()

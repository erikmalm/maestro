"""Draft finishing capacity shares the parent's owned adapter and reservations."""
from contextlib import closing
from copy import deepcopy
import json
import sqlite3
import unittest
from unittest.mock import patch
import uuid

from backend.research import ResearchStore
from backend.provider import LocalUncertain
from backend.research_stages import DRAFT_FINISHING_SECONDS, DRAFT_FINISHING_TOKENS
from tests import test_research_stage_integration as fixtures


class ResearchWriterBudgetTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch("backend.credentials.read", return_value=(None, "missing")))
        self.fixture = fixtures.ResearchStageIntegrationTests(
            "test_provider_and_run_reservation_roll_back_as_one_transaction")
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.setUp()
        self.enterContext(patch.object(ResearchStore, "clock", side_effect=lambda: self.fixture.now))
        self.template = deepcopy(self.fixture.run)
        with self.fixture.provider.transaction() as state:
            state["config"]["ollama_context_tokens"] = 65536
        self.new_run()

    def new_run(self, *, workflow="research_draft", tokens=100000, seconds=120):
        self.fixture.store.delete(self.fixture.run["id"])
        budget = deepcopy(self.template["budget"])
        budget["total"].update(tokens=tokens, seconds=seconds)
        budget["remaining"] = budget["total"].copy()
        self.fixture.run = self.fixture.store.create(
            uuid.uuid4().hex, "Synthetic draft from saved evidence.", "", None,
            self.template["permissions"], budget, workflow=workflow)[0]
        _, self.fixture.claim = self.fixture.store.claim(self.fixture.run["id"], lookup=False)

    def prepare(self, kind="requirements", *, seconds=5, **kwargs):
        return self.fixture.store.prepare_stage(
            self.fixture.run["id"], self.fixture.claim, kind, model=self.fixture.config["model"],
            schema=self.fixture.schema, assignment="Return a synthetic structured result.",
            seconds=seconds, **kwargs)

    def held(self):
        with closing(sqlite3.connect(self.fixture.database)) as db:
            row = db.execute("SELECT held FROM research_finishing WHERE run_id=?", (self.fixture.run["id"],)).fetchone()
        return None if row is None else json.loads(row[0])

    def stage_record(self, stage):
        with closing(sqlite3.connect(self.fixture.database)) as db:
            state, spec, reserved = db.execute(
                "SELECT state,spec,reserved FROM research_stages WHERE id=?", (stage.stage_id,)).fetchone()
        return state, json.loads(spec), json.loads(reserved)

    def budget(self):
        return self.fixture.store.get(self.fixture.run["id"])["budget"]

    def generate(self, stage, *, large=False, pre_dispatch=None):
        return self.fixture.provider.generate_research_stage(
            stage, "Return JSON using the assigned schema.",
            [{"role": "user", "content": "Public saved evidence. " * (2200 if large else 1)}],
            pre_dispatch=pre_dispatch)

    def complete(self, stage, *, elapsed=0, large=False):
        def response(*args):
            if args[1] == "/api/chat":
                self.fixture.now += elapsed
            return self.fixture.response(*args)
        with patch("backend.provider.background_network", side_effect=response):
            return self.generate(stage, large=large)

    def authorize(self, stage, *, reserved=False):
        with self.fixture.provider.transaction(with_db=True) as (state, db):
            return self.fixture.store.authorize_stage(stage, self.fixture.workspace, state, db, reserved=reserved)

    def test_draft_writer_expands_only_its_slot_and_preserves_reviewer(self):
        self.complete(self.prepare(seconds=45), elapsed=7)
        original = self.held()
        self.assertEqual({slot["tokens"] for slot in original.values()}, {DRAFT_FINISHING_TOKENS})
        self.assertEqual({slot["seconds"] for slot in original.values()}, {DRAFT_FINISHING_SECONDS})
        self.complete(self.prepare("evidence_review", seconds=45), elapsed=7)
        writer = self.prepare("writer", seconds=45)
        before = self.budget()
        prepared = self.authorize(writer)
        self.assertEqual(prepared["finishing_token_limit"], original["writer"]["tokens"] + before["remaining"]["tokens"])
        self.assertEqual(prepared["finishing_seconds_limit"], 86)
        assigned = []

        def response(*args):
            if args[1] == "/api/chat":
                assigned.append(self.authorize(writer, reserved=True))
                self.assertEqual(self.held(), {"reviewer": original["reviewer"]})
                self.fixture.now += 7
            return self.fixture.response(*args)

        with patch("backend.provider.background_network", side_effect=response):
            self.generate(writer, large=True)
        self.assertGreater(assigned[0]["finishing_token_limit"], original["writer"]["tokens"])
        self.assertEqual(assigned[0]["finishing_seconds_limit"], 45)
        self.assertEqual(self.held(), {"reviewer": original["reviewer"]})
        self.assertEqual(self.stage_record(writer)[1]["finishing_hold"], original["writer"])
        after = self.budget()
        self.assertEqual((after["consumed"]["model_calls"], after["consumed"]["tokens"]), (3, 144))
        self.assertEqual(after["reserved"], original["reviewer"])
        self.assertEqual(after["remaining"]["tokens"], 100000 - 144 - original["reviewer"]["tokens"])
        self.assertEqual(after["remaining"]["seconds"], 79)
        self.assertEqual(after["consumed"]["hosted_searches"], 0)
        reviewer = self.prepare("reviewer", seconds=45)
        self.complete(reviewer, elapsed=7, large=True)
        final = self.budget()
        self.assertEqual(self.held(), {})
        self.assertEqual((final["consumed"]["model_calls"], final["consumed"]["tokens"]), (4, 192))
        self.assertTrue(all(amount == 0 for amount in final["reserved"].values()))
        self.assertEqual(final["remaining"]["model_calls"], 0)

    def test_first_writer_accounts_for_virtual_reviewer_hold(self):
        writer = self.prepare("writer", seconds=45)
        prepared = self.authorize(writer)
        self.assertEqual(prepared["finishing_token_limit"], 100000 - DRAFT_FINISHING_TOKENS)
        self.assertEqual(prepared["finishing_seconds_limit"], 100)
        self.complete(writer, elapsed=7, large=True)
        self.assertEqual(set(self.held()), {"reviewer"})
        self.assertEqual(self.budget()["reserved"]["seconds"], DRAFT_FINISHING_SECONDS)

    def test_finishing_limits_do_not_hide_an_actual_usage_deficit(self):
        stage = self.prepare()

        def response(*args):
            result = self.fixture.response(*args)
            if args[1] == "/api/chat":
                result["prompt_eval_count"] = 90000
            return result

        with patch("backend.provider.background_network", side_effect=response):
            self.generate(stage)
        self.assertEqual(self.budget()["remaining"]["tokens"], 0)
        writer = self.prepare("writer", seconds=45)
        self.assertEqual(self.authorize(writer)["finishing_token_limit"], 0)
        with patch("backend.provider.background_network") as network, self.assertRaises(ValueError):
            self.generate(writer)
        network.assert_not_called()

    def test_undispatched_expanded_writer_restores_exact_original_holds(self):
        self.complete(self.prepare(), elapsed=1)
        original = self.held()
        before = self.budget()
        writer = self.prepare("writer", seconds=45)
        paths = []

        def response(*args):
            paths.append(args[1])
            return self.fixture.response(*args)

        with (patch("backend.provider.background_network", side_effect=response),
              self.assertRaisesRegex(ValueError, "Synthetic preflight")):
            self.generate(writer, large=True, pre_dispatch=lambda: (_ for _ in ()).throw(ValueError("Synthetic preflight")))
        self.assertEqual(paths, ["/api/show"])
        self.assertEqual(self.held(), original)
        self.assertEqual(self.budget(), before)
        entry = self.fixture.provider.read_state()["ledger"][-1]
        self.assertEqual((entry["status"], entry["model_calls"]), ("failed", 0))
        self.assertEqual(self.stage_record(writer)[0], "failed")

    def test_insufficient_initial_capacity_rolls_back_without_network(self):
        for tokens, seconds in ((2 * DRAFT_FINISHING_TOKENS, 120), (100000, 40)):
            with self.subTest(tokens=tokens, seconds=seconds):
                self.new_run(tokens=tokens, seconds=seconds)
                stage = self.prepare()
                before = self.budget()
                with patch("backend.provider.background_network") as network, self.assertRaises(ValueError):
                    self.generate(stage)
                network.assert_not_called()
                self.assertIsNone(self.held())
                self.assertEqual(self.budget(), before)

    def test_cancelled_preflight_does_not_restore_consumed_finishing_hold(self):
        self.complete(self.prepare())
        writer = self.prepare("writer", seconds=45)
        paths = []

        def response(*args):
            paths.append(args[1])
            return self.fixture.response(*args)

        with patch("backend.provider.background_network", side_effect=response), self.assertRaises(ValueError):
            self.generate(writer, large=True, pre_dispatch=lambda: self.fixture.store.cancel(writer.run_id))
        self.assertEqual(paths, ["/api/show"])
        self.assertIsNone(self.held())
        budget = self.budget()
        self.assertEqual(budget["consumed"]["model_calls"], 1)
        self.assertTrue(all(amount == 0 for amount in budget["reserved"].values()))

    def test_unknown_writer_keeps_assigned_capacity_until_explicit_recovery(self):
        self.complete(self.prepare())
        writer = self.prepare("writer", seconds=45)

        def response(*args):
            if args[1] == "/api/chat":
                raise LocalUncertain("Synthetic writer completion unknown.", False)
            return self.fixture.response(*args)

        with patch("backend.provider.background_network", side_effect=response), self.assertRaises(ValueError):
            self.generate(writer, large=True)
        state, _, assigned = self.stage_record(writer)
        self.assertEqual(state, "uncertain")
        self.assertGreater(assigned["tokens"], DRAFT_FINISHING_TOKENS)
        self.assertEqual(set(self.held()), {"reviewer"})
        self.fixture.store.cancel(writer.run_id)
        self.assertIsNone(self.held())
        self.assertEqual(self.budget()["reserved"], assigned)
        entry = self.fixture.provider.read_state()["ledger"][-1]
        with patch("backend.provider.background_network", return_value={"models": []}) as network:
            self.fixture.provider.recover_local(entry["id"], True, self.fixture.config["base_url"])
        self.assertEqual([call.args[1] for call in network.call_args_list], ["/api/ps"])
        budget = self.budget()
        self.assertEqual((budget["consumed"]["model_calls"], budget["consumed"]["tokens"]),
                         (2, 48 + assigned["tokens"]))
        self.assertTrue(all(amount == 0 for amount in budget["reserved"].values()))
        self.assertEqual(self.stage_record(writer)[0], "failed")

    def test_writer_cannot_spend_protected_reviewer_tokens(self):
        self.new_run(tokens=80000)
        self.complete(self.prepare())
        original, before = self.held(), self.budget()
        writer = self.prepare("writer", seconds=45)
        with patch("backend.provider.background_network") as network, self.assertRaises(ValueError):
            self.generate(writer, large=True)
        network.assert_not_called()
        self.assertEqual(self.held(), original)
        self.assertEqual(self.budget(), before)

    def test_writer_cannot_spend_protected_reviewer_time(self):
        self.complete(self.prepare(seconds=45), elapsed=40)
        self.complete(self.prepare("evidence_review", seconds=30), elapsed=25)
        original, before = self.held(), self.budget()
        writer = self.prepare("writer", seconds=45)
        self.assertEqual(self.authorize(writer)["finishing_seconds_limit"], 35)
        with patch("backend.provider.background_network") as network, self.assertRaises(ValueError):
            self.generate(writer)
        network.assert_not_called()
        self.assertEqual(self.held(), original)
        self.assertEqual(self.budget(), before)

    def test_new_holds_capture_smaller_context_limit(self):
        with self.fixture.provider.transaction() as state:
            state["config"]["ollama_context_tokens"] = 16384
        self.complete(self.prepare())
        self.assertEqual({slot["tokens"] for slot in self.held().values()}, {16384})
        self.assertEqual({slot["seconds"] for slot in self.held().values()}, {20})

    def test_legacy_workflow_retains_fixed_finishing_caps(self):
        self.new_run(workflow="lookup")
        self.complete(self.prepare())
        original, before = self.held(), self.budget()
        self.assertEqual({slot["tokens"] for slot in original.values()}, {8192})
        self.assertEqual({slot["seconds"] for slot in original.values()}, {5})
        for seconds, large in ((6, False), (5, True)):
            with self.subTest(seconds=seconds, large=large):
                writer = self.prepare("writer", seconds=seconds)
                prepared = self.authorize(writer)
                self.assertEqual((prepared["finishing_token_limit"], prepared["finishing_seconds_limit"]), (8192, 5))
                with patch("backend.provider.background_network") as network, self.assertRaises(ValueError):
                    self.generate(writer, large=large)
                network.assert_not_called()
                self.assertEqual(self.held(), original)
                self.assertEqual(self.budget(), before)

    def test_coverage_hash_is_exact_and_bound_into_owned_specification(self):
        for value in ("a" * 63, "A" * 64, True, 17, "a" * 64 + "\x00"):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "coverage hash"):
                self.prepare("writer", coverage_hash=value)
        writer = self.prepare("writer", coverage_hash="a" * 64, packet_hash="b" * 64)
        spec = self.stage_record(writer)[1]
        self.assertEqual((spec["coverage_hash"], spec["packet_hash"]), ("a" * 64, "b" * 64))


if __name__ == "__main__":
    unittest.main()

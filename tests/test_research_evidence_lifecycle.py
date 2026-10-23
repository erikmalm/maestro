"""Evidence-stage cleanup and last preflight fences with disposable local work."""
from contextlib import closing
import json
import sqlite3
import unittest
from unittest.mock import patch

from backend import provider as provider_module
from backend.provider import LocalUncertain
from backend.research import ResearchStore
from backend.research_requirements import validate_assessment
from tests import test_research_stage_integration as fixtures


class ResearchEvidenceLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch("backend.credentials.read", return_value=(None, "missing")))
        self.fixture = fixtures.ResearchStageIntegrationTests(
            "test_reported_overrun_is_recorded_and_blocks_further_dispatch")
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.setUp()
        self.addCleanup(self.clear_pending)

    def clear_pending(self):
        with provider_module._research_accounting_lock:
            for key, _ in provider_module._pending_research_accounting(self.fixture.database):
                provider_module._research_accounting.pop(key, None)

    def stage_row(self, stage):
        with closing(sqlite3.connect(self.fixture.database)) as db:
            return db.execute("SELECT state,output,reserved FROM research_stages WHERE id=?",
                              (stage.stage_id,)).fetchone()

    def generate(self, stage, *, pre_dispatch=None):
        return self.fixture.provider.generate_research_stage(
            stage, "Return JSON using the assigned schema.",
            [{"role": "user", "content": "Frozen synthetic saved evidence."}], pre_dispatch=pre_dispatch)

    def test_evidence_failure_preserves_unknown_stage_until_conservative_recovery(self):
        stage = self.fixture.stage("evidence_review")

        def response(*args):
            if args[1] == "/api/chat":
                raise LocalUncertain("Synthetic completion unknown.", False)
            return self.fixture.response(*args)

        with patch("backend.provider.background_network", side_effect=response), self.assertRaises(ValueError):
            self.generate(stage)
        self.assertEqual(self.stage_row(stage)[0], "uncertain")
        self.fixture.store.fail(stage.run_id, self.fixture.claim, "evidence_failed")
        self.assertEqual(self.stage_row(stage)[:2], ("uncertain", None))
        before = self.fixture.store.get(stage.run_id)["budget"]
        held_tokens = before["reserved"]["tokens"]
        self.assertGreater(held_tokens, 0)
        entry = self.fixture.provider.read_state()["ledger"][-1]
        with patch("backend.provider.background_network", return_value={"models": []}) as network:
            self.fixture.provider.recover_local(entry["id"], True, self.fixture.config["base_url"])
        self.assertEqual([call.args[1] for call in network.call_args_list], ["/api/ps"])
        after = self.fixture.store.get(stage.run_id)["budget"]
        self.assertEqual(after["consumed"]["model_calls"], 1)
        self.assertEqual(after["consumed"]["tokens"], held_tokens)
        self.assertTrue(all(amount == 0 for amount in after["reserved"].values()))
        self.assertEqual(self.stage_row(stage)[:2], ("failed", None))

    def test_evidence_failure_keeps_returned_actuals_reconcilable_exactly_once(self):
        stage = self.fixture.stage("evidence_review")
        settle, outcomes = ResearchStore.settle_stage, []

        def temporary_failure(store, *args, **kwargs):
            outcomes.append(kwargs["outcome"])
            if len(outcomes) <= 2:
                raise sqlite3.OperationalError("Synthetic settlement failure.")
            return settle(store, *args, **kwargs)

        with (patch.object(ResearchStore, "settle_stage", new=temporary_failure),
              patch("backend.provider.background_network", side_effect=self.fixture.response),
              self.assertRaises(ValueError)):
            self.generate(stage)
        self.assertEqual(outcomes, ["completed", "failed"])
        self.fixture.store.fail(stage.run_id, self.fixture.claim, "evidence_failed")
        state, output, held = self.stage_row(stage)
        self.assertEqual((state, output), ("reserved", None))
        self.assertGreater(json.loads(held)["tokens"], 0)
        with patch("backend.provider.background_network", side_effect=AssertionError("Inference replay")):
            self.assertEqual(self.fixture.provider.reconcile_research(), 1)
            self.assertEqual(self.fixture.provider.reconcile_research(), 0)
        after = self.fixture.store.get(stage.run_id)["budget"]
        self.assertEqual(after["consumed"]["model_calls"], 1)
        self.assertEqual(after["consumed"]["tokens"], 48)
        self.assertTrue(all(amount == 0 for amount in after["reserved"].values()))
        entry = self.fixture.provider.read_state()["ledger"][-1]
        self.assertEqual((entry["status"], entry["input_tokens"], entry["output_tokens"]), ("settled", 37, 11))
        self.assertEqual(self.stage_row(stage)[:2], ("failed", None))

    def test_rejected_completed_evidence_prose_is_cleared_without_refunding_actual_usage(self):
        stage = self.fixture.stage("evidence_review")
        with patch("backend.provider.background_network", side_effect=self.fixture.response):
            self.generate(stage)
        self.assertEqual(self.stage_row(stage)[0], "completed")
        self.assertIsNotNone(self.stage_row(stage)[1])
        self.fixture.store.fail(stage.run_id, self.fixture.claim, "evidence_failed")
        self.assertEqual(self.stage_row(stage)[:2], ("failed", None))
        budget = self.fixture.store.get(stage.run_id)["budget"]
        self.assertEqual((budget["consumed"]["model_calls"], budget["consumed"]["tokens"]), (1, 48))

    def assert_preflight_rejects(self, action):
        stage = self.fixture.stage("evidence_review")
        paths = []

        def response(*args):
            paths.append(args[1])
            return self.fixture.response(*args)

        with patch("backend.provider.background_network", side_effect=response), self.assertRaises(ValueError):
            self.generate(stage, pre_dispatch=lambda: action(stage))
        self.fixture.store.fail(stage.run_id, self.fixture.claim, "evidence_failed")
        self.assertEqual(paths, ["/api/show"])
        entry = self.fixture.provider.read_state()["ledger"][-1]
        self.assertEqual((entry["status"], entry["model_calls"]), ("failed", 0))
        self.assertEqual(self.stage_row(stage)[1], None)

    def test_cancel_during_archive_pre_dispatch_prevents_inference(self):
        self.assert_preflight_rejects(lambda stage: self.fixture.store.cancel(stage.run_id))
        self.assertEqual(self.fixture.store.get(self.fixture.run["id"])["state"], "cancelled")

    def test_delete_during_archive_pre_dispatch_prevents_inference_and_recreation(self):
        self.assert_preflight_rejects(lambda stage: self.fixture.store.delete(stage.run_id))
        with self.assertRaises(KeyError):
            self.fixture.store.get(self.fixture.run["id"])

    def test_settings_change_during_archive_pre_dispatch_prevents_inference(self):
        alternate = "synthetic-next-local:latest"
        with self.fixture.provider.transaction() as state:
            state["models"].append(alternate)
        self.assert_preflight_rejects(lambda stage: self.fixture.provider.configure(
            {**self.fixture.config, "model": alternate}, "", False))
        self.assertEqual(self.fixture.provider.read_state()["config"]["model"], alternate)

    def lookup_subject(self, name, result):
        # Importing the application for this helper must not initialize personal
        # storage when this test file is run independently.
        with patch.dict("os.environ", {"MAESTRO_DATA_DIR": str(self.fixture.root / "bootstrap")}):
            from backend import app as backend
        from backend.context_store import CandidateWork, validate_candidate_lookup
        request = "Research " + name + " revenue FY2025 USD."
        assessment = validate_assessment({
            "version": 1, "decision": "ready",
            "subjects": [{"id": "s1", "kind": "topic", "name": name, "identifiers": []}],
            "items": [{"id": "i1", "subject_ids": ["s1"], "criterion": "revenue", "period": "FY2025",
                       "unit": None, "currency": "USD", "freshness": None}],
            "constraints": [], "clarifications": [],
        }, request)
        queries = []

        def lookup(entry, *, work):
            validate_candidate_lookup(**entry.model_dump())
            self.assertIsInstance(work, CandidateWork)
            self.assertEqual(entry.limit, 8)
            queries.append(entry.query)
            return result

        with patch.object(backend, "_search_context_candidates", side_effect=lookup):
            packet = backend._research_candidates(assessment)
        return packet, queries

    def test_long_valid_subject_uses_at_most_16_native_terms_and_reports_trim(self):
        name = " ".join("topic" + str(number) for number in range(1, 18))
        candidate = {"capture_id": "1" * 32, "content_hash": "2" * 64, "manifest_hash": "3" * 64}
        result = {"state": "ready", "search_limited": False, "skipped_count": 0, "candidates": [candidate]}
        (candidates, lookup), queries = self.lookup_subject(name, result)
        self.assertEqual(candidates, [candidate])
        self.assertEqual(len(queries), 1)
        self.assertEqual(len(queries[0].split()), 16)
        self.assertNotIn("topic17", queries[0])
        self.assertEqual(lookup, {"state": "limited", "search_limited": True, "skipped_count": 0})

    def test_punctuation_subject_falls_back_to_bounded_inventory_with_honest_disabled_state(self):
        for state in ("disabled", "unconfigured"):
            with self.subTest(state=state):
                result = {"state": state, "search_limited": False, "skipped_count": 0, "candidates": []}
                (candidates, lookup), queries = self.lookup_subject("!!!", result)
                self.assertEqual(queries, [""])
                self.assertEqual(candidates, [])
                self.assertEqual(lookup, {"state": state, "search_limited": True, "skipped_count": 0})


if __name__ == "__main__":
    unittest.main()

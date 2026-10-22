"""Returned model work retries accounting only after temporary storage failure."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing, contextmanager
from dataclasses import asdict
import json
import sqlite3
import time
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend import app as backend
from backend import provider as provider_module
from backend.provider import LocalUncertain, Provider
from backend.research import ResearchStore
from tests import test_research_stage_safety as fixtures


class ResearchSettlementReviewTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.ResearchStageSafetyTests("test_forged_and_replayed_identity_cannot_dispatch_or_reset_allowance")
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.setUp()
        self.addCleanup(self.clear_pending)

    def clear_pending(self):
        with provider_module._research_accounting_lock:
            for key, _ in provider_module._pending_research_accounting(self.fixture.database):
                provider_module._research_accounting.pop(key, None)

    def pending_known(self, *, overrun=False):
        stage = self.fixture.stage()
        if overrun:
            self.fixture.response["prompt_eval_count"] = 600001
        original, calls = ResearchStore.settle_stage, []

        def temporary_failure(store, *arguments, **keywords):
            calls.append(keywords["outcome"])
            if len(calls) <= 2:
                raise sqlite3.OperationalError("Synthetic temporary settlement write failure.")
            return original(store, *arguments, **keywords)

        with patch.object(ResearchStore, "settle_stage", new=temporary_failure), self.assertRaises(ValueError):
            self.fixture.generate(stage)
        self.assertEqual(calls, ["completed", "failed"])
        self.assertEqual(self.fixture.ledger()[-1]["status"], "reserved")
        return stage

    def test_double_settlement_failure_reconciles_exact_overrun_once_without_another_call(self):
        stage = self.pending_known(overrun=True)
        before = len(self.fixture.requests)
        self.fixture.store.cancel(stage.run_id)
        with self.assertRaisesRegex(ValueError, "accounting is pending"):
            self.fixture.provider.generate("New ordinary message", self.fixture.chat_id)
        self.assertEqual(len(self.fixture.requests), before)
        pending = provider_module._pending_research_accounting(self.fixture.database)
        self.assertEqual(len(pending), 1)
        serialized = json.dumps(asdict(pending[0][1]))
        self.assertNotIn("Synthetic stage safety objective", serialized)
        self.assertNotIn("Use only the supplied", serialized)
        self.assertEqual(self.fixture.provider.reconcile_research(), 1)
        self.assertEqual(self.fixture.provider.reconcile_research(), 0)
        entry = self.fixture.ledger()[-1]
        self.assertEqual((entry["status"], entry["input_tokens"], entry["output_tokens"], entry["model_calls"]),
                         ("settled", 600001, 7, 1))
        budget = self.fixture.store.get(stage.run_id)["budget"]
        self.assertEqual(budget["consumed"]["tokens"], 600008)
        self.assertEqual(budget["consumed"]["model_calls"], 1)
        self.assertEqual(budget["remaining"]["tokens"], 0)
        self.assertEqual(budget["reserved"]["tokens"], 0)
        self.assertEqual(len(self.fixture.requests), before)

    def test_pending_actuals_survive_deletion_without_private_output_or_request_recreation(self):
        stage = self.pending_known()
        self.fixture.store.delete(stage.run_id)
        before = len(self.fixture.requests)
        self.assertEqual(self.fixture.provider.reconcile_research(), 1)
        with self.assertRaises(KeyError):
            self.fixture.store.get(stage.run_id)
        with closing(sqlite3.connect(self.fixture.database)) as db:
            spec, output, consumed, reserved = db.execute(
                "SELECT spec,output,consumed,reserved FROM research_stages WHERE id=?", (stage.stage_id,)).fetchone()
        self.assertEqual(spec, "{}")
        self.assertIsNone(output)
        self.assertEqual(json.loads(consumed)["tokens"], 48)
        self.assertEqual(json.loads(reserved)["tokens"], 0)
        self.assertEqual(self.fixture.ledger()[-1]["status"], "settled")
        self.assertEqual(len(self.fixture.requests), before)

    def test_pending_receipt_is_retained_until_reconciliation_transaction_commits(self):
        stage = self.pending_known()
        transaction = self.fixture.provider.transaction

        @contextmanager
        def fail_commit(with_db=False):
            with transaction(with_db=with_db) as selected:
                yield selected
                raise sqlite3.OperationalError("Synthetic commit failure.")

        with patch.object(self.fixture.provider, "transaction", new=fail_commit), self.assertRaises(sqlite3.OperationalError):
            self.fixture.provider.reconcile_research()
        self.assertEqual(len(provider_module._pending_research_accounting(self.fixture.database)), 1)
        self.assertEqual(self.fixture.ledger()[-1]["status"], "reserved")
        self.assertEqual(self.fixture.store.get(stage.run_id)["budget"]["consumed"]["tokens"], 0)
        self.fixture.provider.reconcile_research()
        self.assertEqual(self.fixture.store.get(stage.run_id)["budget"]["consumed"]["tokens"], 48)

    def test_concurrent_provider_instances_cannot_double_apply_pending_actuals(self):
        stage = self.pending_known()
        another = Provider(str(self.fixture.database), self.fixture.provider.timezone)
        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(lambda provider: provider.reconcile_research(), (self.fixture.provider, another)))
        budget = self.fixture.store.get(stage.run_id)["budget"]
        self.assertEqual(budget["consumed"]["model_calls"], 1)
        self.assertEqual(budget["consumed"]["tokens"], 48)
        self.assertEqual(provider_module._pending_research_accounting(self.fixture.database), [])

    def test_failed_unknown_cleanup_publishes_recovery_and_keeps_dispatch_reservation(self):
        stage = self.fixture.stage()

        def unknown_transport():
            raise LocalUncertain("Synthetic unconfirmed model completion.", False)

        self.fixture.chat_hook = unknown_transport
        with patch.object(ResearchStore, "settle_stage", side_effect=sqlite3.OperationalError("Synthetic cleanup failure.")):
            with self.assertRaises(ValueError):
                self.fixture.generate(stage)
        before = len(self.fixture.requests)
        held = self.fixture.store.get(stage.run_id)["budget"]["reserved"]["tokens"]
        self.assertEqual(self.fixture.provider.usage()["local_requests"], [])
        self.fixture.provider.reconcile_research()
        entry = self.fixture.ledger()[-1]
        self.assertEqual(entry["status"], "reserved")
        self.assertTrue(entry["local_unknown"])
        self.assertEqual(len(self.fixture.provider.usage()["local_requests"]), 1)
        self.assertEqual(self.fixture.store.get(stage.run_id)["budget"]["reserved"]["tokens"], held)
        self.assertEqual(len(self.fixture.requests), before)
        self.fixture.store.cancel(stage.run_id)
        dispatch_bound = self.fixture.store.get(stage.run_id)["budget"]["reserved"]["tokens"]
        self.fixture.provider.recover_local(entry["id"], True, self.fixture.config["base_url"])
        self.assertEqual(self.fixture.store.get(stage.run_id)["budget"]["consumed"]["tokens"], dispatch_bound)
        self.assertEqual([request[1] for request in self.fixture.requests[before:]], ["/api/ps"])

    def test_losing_process_cache_falls_back_to_existing_conservative_restart_recovery(self):
        stage = self.pending_known()
        self.clear_pending()  # A real process restart cannot retain Python memory.
        before = len(self.fixture.requests)
        self.fixture.provider.recover()
        self.fixture.store.recover()
        entry = self.fixture.ledger()[-1]
        self.assertTrue(entry["local_unknown"])
        dispatch_bound = self.fixture.store.get(stage.run_id)["budget"]["reserved"]["tokens"]
        self.fixture.provider.recover_local(entry["id"], True, self.fixture.config["base_url"])
        run = self.fixture.store.get(stage.run_id)
        self.assertEqual(run["state"], "interrupted")
        self.assertEqual(run["budget"]["consumed"]["tokens"], dispatch_bound)
        self.assertEqual(run["budget"]["reserved"]["tokens"], 0)
        self.assertEqual([request[1] for request in self.fixture.requests[before:]], ["/api/ps"])

    def test_empty_pending_maintenance_does_not_create_a_database(self):
        missing = self.fixture.root / "unused" / "workspace.sqlite3"
        self.assertEqual(Provider(missing, self.fixture.provider.timezone).reconcile_research(), 0)
        self.assertFalse(missing.parent.exists())

    def test_existing_lifespan_controller_reconciles_returned_work_with_reflection_disabled(self):
        stage = self.pending_known()
        before = len(self.fixture.requests)
        with (patch.object(backend, "DATABASE", self.fixture.database),
              patch.dict("os.environ", {"MAESTRO_CONTEXT_ARCHIVE_DIR": ""}),
              patch.object(backend, "maintain_research_runs", wraps=backend.maintain_research_runs) as maintenance):
            with TestClient(backend.app):
                deadline = time.monotonic() + 2
                while self.fixture.ledger()[-1]["status"] != "settled" and time.monotonic() < deadline:
                    time.sleep(0.01)
            self.assertGreaterEqual(maintenance.call_count, 1)
        self.assertEqual(self.fixture.ledger()[-1]["status"], "settled")
        run = self.fixture.store.get(stage.run_id)
        self.assertEqual(run["state"], "interrupted")
        self.assertEqual(run["budget"]["consumed"]["tokens"], 48)
        self.assertEqual(len(self.fixture.requests), before)


if __name__ == "__main__":
    unittest.main()

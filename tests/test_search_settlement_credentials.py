"""Credential outages cannot undo durable accounting for a booked search."""
from contextlib import closing
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import httpx

from backend import credentials
from backend.web_search import DEFAULT, SearchFailure, WebSearch
from tests import test_acquisition_journal as fixtures


class SearchCredentialSettlementTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory(prefix="maestro-search-settlement-")))
        self.clock = datetime(2026, 11, 27, 12, tzinfo=timezone.utc)
        self.sequence = 0
        self.key = "synthetic-search-settlement-key"
        self.enterContext(patch("backend.credentials.read", return_value=(self.key, "memory")))
        self.enterContext(patch.object(WebSearch, "now", side_effect=lambda: self.clock))
        self.enterContext(patch("backend.web_search.search_response", side_effect=self.response))
        self.tested_at = (self.clock - timedelta(minutes=1)).isoformat()

    async def response(self, key, query, max_results):
        return httpx.Response(200), json.dumps({"results": [{
            "title": "Synthetic settlement result", "url": "https://example.org/settlement",
            "content": "Synthetic settlement evidence."}]}).encode()

    def booking(self, outcome, *, test=False):
        # Each matrix case starts in its own database so a failed settlement
        # cannot hide the remaining outcomes behind the active-booking guard.
        self.sequence += 1
        self.service = WebSearch(self.root / (str(self.sequence) + ".sqlite3"), timezone.utc)
        with self.service.transaction() as state:
            state["config"] = {**DEFAULT, "enabled": True}
            state["tested_at"] = self.tested_at
        self.clock += timedelta(seconds=6)
        booking = self.service.reserve("Synthetic settlement " + outcome, test=test)
        if outcome != "cancelled":
            self.service.dispatch(booking, finalize=False)
        return booking

    def test_ordinary_outcomes_commit_without_rereading_credentials(self):
        for outcome in ("completed", "failed", "uncertain", "cancelled"):
            for error_type in (ValueError, OSError):
                with self.subTest(outcome=outcome, error=error_type.__name__):
                    booking = self.booking(outcome)
                    with patch("backend.credentials.read", side_effect=error_type("Synthetic key outage")) as read:
                        self.assertTrue(self.service.finalize(booking, outcome))
                        self.assertFalse(self.service.finalize(booking, outcome))
                        read.assert_not_called()
                    state = self.service.read_state()
                    self.assertEqual(state["ledger"][-1]["status"], outcome)
                    self.assertEqual(state["tested_at"], self.tested_at)
                    self.assertEqual(self.service.summary()["searches_today"], 1)

    def test_connection_test_outcomes_commit_and_clear_verification_when_key_read_fails(self):
        for outcome in ("completed", "failed", "uncertain", "cancelled"):
            for error_type in (ValueError, OSError):
                with self.subTest(outcome=outcome, error=error_type.__name__):
                    booking = self.booking(outcome, test=True)
                    with patch("backend.credentials.read", side_effect=error_type("Synthetic key outage")):
                        self.assertTrue(self.service.finalize(booking, outcome))
                    state = self.service.read_state()
                    self.assertEqual(state["ledger"][-1]["status"], outcome)
                    self.assertIsNone(state["tested_at"])
                    self.assertEqual(self.service.summary()["searches_today"], 1)

    def test_ordinary_settlement_commits_with_its_caller_transaction_during_key_outage(self):
        booking = self.booking("completed")
        with closing(sqlite3.connect(self.service.database)) as db, db:
            db.execute("CREATE TABLE settlement_probe(state TEXT)")
            db.execute("INSERT INTO settlement_probe VALUES('pending')")
        with patch("backend.credentials.read", side_effect=ValueError("Synthetic mounted key unavailable")) as read:
            with closing(sqlite3.connect(self.service.database)) as db, db:
                db.execute("BEGIN IMMEDIATE")
                db.execute("UPDATE settlement_probe SET state='settled'")
                self.assertTrue(self.service.finalize(booking, "completed", db=db))
            read.assert_not_called()
        with closing(sqlite3.connect(self.service.database)) as db:
            self.assertEqual(db.execute("SELECT state FROM settlement_probe").fetchone()[0], "settled")
        self.assertEqual(self.service.read_state()["ledger"][0]["status"], "completed")


class AcquisitionCredentialSettlementTests(unittest.TestCase):
    def setUp(self):
        # Composition avoids rediscovering the imported fixture's tests.
        self.fixture = fixtures.AcquisitionJournalTests(
            "test_dispatch_occurs_once_and_outside_database_write_lock")
        self.addCleanup(self.fixture.doCleanups)
        # Keep the implementation before the fixture replaces credentials.read.
        self.read_credential = credentials.read
        self.fixture.setUp()
        self.key_file = self.fixture.root / "mounted-search-secret"
        self.key_file.write_text(self.fixture.key, encoding="utf-8")
        self.enterContext(patch.dict(os.environ, {"MAESTRO_SEARCH_KEY_FILE": str(self.key_file)}))

    def assert_charged_once(self, attempt, booking, outcome, journal_state):
        f = self.fixture
        record = f.journal.get(attempt.attempt_id)
        self.assertEqual(record["state"], journal_state)
        self.assertIsNone(record["receipt"])
        self.assertEqual(record["accounted"], 2)
        self.assertEqual(len(f.ledger()), 1)
        self.assertEqual(f.ledger()[0]["status"], outcome)
        budget = f.store.get(attempt.run_id)["budget"]
        self.assertEqual(budget["consumed"]["hosted_searches"], 1)
        self.assertEqual(budget["reserved"]["hosted_searches"], 0)
        self.assertEqual(budget["reserved"]["seconds"], 0)
        self.assertFalse(f.search.finalize(booking, outcome))
        with self.assertRaises(ValueError):
            f.journal.dispatch(attempt, booking)
        self.assertEqual(len(f.requests), 1)
        self.assertEqual(f.store.get(attempt.run_id)["budget"]["consumed"]["hosted_searches"], 1)

    def assert_repaired_key_allows_new_search(self):
        f = self.fixture
        self.key_file.write_text(f.key, encoding="utf-8")
        f.clock += 6
        receipt = f.search.search("Synthetic search after mounted secret repair")
        self.assertEqual(receipt["sources"], [f.source])
        self.assertEqual(len(f.requests), 2)
        self.assertEqual(len(f.ledger()), 2)
        self.assertEqual(f.ledger()[-1]["status"], "completed")
        self.assertEqual(f.search.summary()["searches_today"], 2)

    def _response_then_credential_failure(self, *, invalid):
        f = self.fixture
        attempt = f.prepare()
        booking = f.journal.reserve(attempt)
        original = WebSearch._response

        def unavailable_after_screening(service, *args, **kwargs):
            receipt = original(service, *args, **kwargs)
            if invalid:
                self.key_file.write_text("invalid\x00synthetic-secret", encoding="utf-8")
            else:
                self.key_file.unlink()
            return receipt

        with patch("backend.credentials.read", new=self.read_credential):
            with patch.object(WebSearch, "_response", new=unavailable_after_screening):
                with self.assertRaises(ValueError):
                    f.journal.dispatch(attempt, booking)
            # A usable provider response is charged even though unavailable
            # credentials prevent the journal's final content safety screen.
            self.assert_charged_once(attempt, booking, "completed", "failed")
            self.assert_repaired_key_allows_new_search()

    def test_removed_mounted_secret_after_response_cannot_roll_back_accounting(self):
        self._response_then_credential_failure(invalid=False)

    def test_invalid_mounted_secret_after_response_cannot_roll_back_accounting(self):
        self._response_then_credential_failure(invalid=True)

    def test_credential_os_error_after_response_cannot_roll_back_accounting(self):
        f = self.fixture
        attempt = f.prepare()
        booking = f.journal.reserve(attempt)
        original = WebSearch._response
        unavailable = False

        def read_credential(endpoint):
            if unavailable:
                raise OSError("Synthetic credential store unavailable")
            return self.read_credential(endpoint)

        def unavailable_after_screening(service, *args, **kwargs):
            nonlocal unavailable
            receipt = original(service, *args, **kwargs)
            unavailable = True
            return receipt

        with patch("backend.credentials.read", side_effect=read_credential):
            with patch.object(WebSearch, "_response", new=unavailable_after_screening):
                with self.assertRaises(ValueError):
                    f.journal.dispatch(attempt, booking)
            self.assert_charged_once(attempt, booking, "completed", "failed")
            unavailable = False
            self.assert_repaired_key_allows_new_search()

    def test_timeout_after_mounted_secret_removal_commits_unknown_outcome(self):
        f = self.fixture
        attempt = f.prepare()
        booking = f.journal.reserve(attempt)

        def timeout(*args):
            self.key_file.unlink()
            raise httpx.ReadTimeout("Synthetic transport timeout")

        f.handler = timeout
        with patch("backend.credentials.read", new=self.read_credential):
            with self.assertRaises(SearchFailure) as failure:
                f.journal.dispatch(attempt, booking)
            self.assertEqual(failure.exception.outcome, "uncertain")
            self.assertTrue(failure.exception.dispatched)
            self.assert_charged_once(attempt, booking, "uncertain", "uncertain")
            f.handler = f.response
            self.assert_repaired_key_allows_new_search()


if __name__ == "__main__":
    unittest.main()

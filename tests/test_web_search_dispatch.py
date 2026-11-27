"""One opaque booking and one transport dispatch; all sources and HTTP are synthetic."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing, contextmanager
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import asyncio
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import patch

import httpx

from backend.web_search import DEFAULT, ENDPOINT, SearchFailure, WebSearch


class SearchDispatchTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory(prefix="maestro-search-dispatch-")))
        self.database = self.root / "workspace.sqlite3"
        self.service = WebSearch(self.database, timezone.utc)
        self.clock = datetime(2026, 11, 27, 12, tzinfo=timezone.utc)
        self.key = "synthetic-search-dispatch-key"
        self.enterContext(patch("backend.web_search.credentials.read", side_effect=lambda endpoint: (self.key, "memory")))
        self.enterContext(patch.object(WebSearch, "now", side_effect=lambda: self.clock))
        with self.service.transaction() as state:
            state["config"] = {**DEFAULT, "enabled": True}
            state["tested_at"] = self.clock.isoformat()
        self.calls = []

    def response(self, request):
        self.calls.append(request)
        return httpx.Response(200, json={"results": [{"title": "Synthetic disclosure",
                              "url": "https://example.org/report", "content": "Synthetic saved fact."}]})

    @contextmanager
    def http(self, handler=None):
        client = httpx.AsyncClient
        transport = httpx.MockTransport(handler or self.response)
        with patch("backend.web_search.httpx.AsyncClient", side_effect=lambda **kwargs: client(transport=transport, **kwargs)):
            yield

    def status(self):
        return self.service.read_state()["ledger"][-1]["status"]

    def test_external_transaction_commits_or_rolls_back_journal_and_quota_together(self):
        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute("CREATE TABLE acquisition_probe(id TEXT PRIMARY KEY)")
        with self.assertRaisesRegex(RuntimeError, "synthetic rollback"):
            with closing(sqlite3.connect(self.database)) as db, db:
                db.execute("BEGIN IMMEDIATE")
                self.service.reserve("Synthetic future search", request_id="a" * 32, db=db)
                db.execute("INSERT INTO acquisition_probe VALUES(?)", ("a" * 32,))
                raise RuntimeError("synthetic rollback")
        self.assertEqual(self.service.read_state()["ledger"], [])
        with closing(sqlite3.connect(self.database)) as db:
            self.assertEqual(db.execute("SELECT * FROM acquisition_probe").fetchall(), [])
            with self.assertRaisesRegex(ValueError, "active"):
                self.service.reserve("Synthetic future search", db=db)
        with closing(sqlite3.connect(self.root / "different.sqlite3")) as db, db:
            db.execute("BEGIN IMMEDIATE")
            with self.assertRaisesRegex(ValueError, "same workspace"):
                self.service.reserve("Synthetic future search", db=db)

    def test_one_dispatch_under_concurrency_and_transport_holds_no_database_lock(self):
        booking = self.service.reserve("Synthetic one dispatch", request_id="a" * 32)
        entered, release = threading.Event(), threading.Event()

        def response(request):
            # A separate writer can acquire the database while transport is active.
            with closing(sqlite3.connect(self.database, timeout=0.1)) as db, db:
                db.execute("BEGIN IMMEDIATE")
                db.execute("CREATE TABLE transport_lock_probe(id INTEGER)")
            entered.set()
            self.assertTrue(release.wait(3))
            return self.response(request)

        with self.http(response), ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(self.service.dispatch, booking)
            try:
                self.assertTrue(entered.wait(3))
                second = pool.submit(self.service.dispatch, booking)
                with self.assertRaises(SearchFailure):
                    second.result(timeout=3)
            finally:
                release.set()
            self.assertEqual(first.result(timeout=3)["query"], booking.query)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.status(), "completed")
        self.assertEqual(self.service.summary()["searches_today"], 1)
        self.assertFalse(self.service.finalize(booking, "completed"))
        with self.assertRaises(ValueError):
            self.service.reserve("Synthetic one dispatch", request_id=booking.request_id)

    def test_dispatch_callback_and_ledger_claim_share_one_commit(self):
        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute("CREATE TABLE acquisition_probe(state TEXT)")
            db.execute("INSERT INTO acquisition_probe VALUES('pending')")
        booking = self.service.reserve("Synthetic journal callback")

        def before(db):
            self.assertTrue(db.in_transaction)
            db.execute("UPDATE acquisition_probe SET state='dispatched'")

        def response(request):
            with closing(sqlite3.connect(self.database)) as db:
                self.assertEqual(db.execute("SELECT state FROM acquisition_probe").fetchone()[0], "dispatched")
            self.assertEqual(self.status(), "dispatched")
            return self.response(request)

        with self.http(response):
            receipt = self.service.dispatch(booking, before_dispatch=before, finalize=False)
        self.assertEqual(self.status(), "dispatched")
        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute("BEGIN IMMEDIATE")
            self.service.finalize(booking, "completed", db=db)
            db.execute("UPDATE acquisition_probe SET state='response_received'")
        self.assertEqual(self.status(), "completed")
        self.assertEqual(receipt["sources"][0]["content"], "Synthetic saved fact.")
        self.assertNotIn(booking.query.encode(), self.database.read_bytes())
        self.assertNotIn(self.key.encode(), self.database.read_bytes())

    def test_delayed_journal_booking_preserves_spacing_before_legacy_dispatch(self):
        booked_at = self.clock.isoformat()
        booking = self.service.reserve("Synthetic delayed journal acquisition")
        self.clock += timedelta(seconds=20)
        dispatched_at = self.clock.isoformat()
        with self.http():
            self.service.dispatch(booking, before_dispatch=lambda db: None, finalize=False)
        self.assertTrue(self.service.finalize(booking, "completed"))
        # Quota stays on the booking date while dispatch spacing uses its own time.
        entry = self.service.read_state()["ledger"][0]
        self.assertEqual(entry["at"], booked_at)
        self.assertEqual(self.service.summary()["searches_today"], 1)
        with self.http(), self.assertRaisesRegex(ValueError, "five seconds"):
            self.service.search("Synthetic immediate legacy follow-up")
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(entry["dispatched_at"], dispatched_at)
        self.clock += timedelta(seconds=5)
        with self.http():
            self.service.search("Synthetic properly spaced legacy follow-up")
        self.assertEqual(len(self.calls), 2)

    def test_delayed_failed_and_unknown_dispatches_keep_the_shared_spacing(self):
        for kind in ("http", "timeout"):
            with self.subTest(kind=kind):
                self.clock += timedelta(seconds=60)
                booking = self.service.reserve("Synthetic delayed outcome " + kind)
                self.clock += timedelta(seconds=20)

                def response(request):
                    self.calls.append(request)
                    if kind == "timeout":
                        raise httpx.ReadTimeout("Synthetic timeout", request=request)
                    return httpx.Response(503)

                with self.http(response), self.assertRaises(SearchFailure):
                    self.service.dispatch(booking)
                calls_before = len(self.calls)
                with self.http(), self.assertRaisesRegex(ValueError, "five seconds"):
                    self.service.search("Synthetic immediate follow-up after " + kind)
                self.assertEqual(len(self.calls), calls_before)
                self.clock += timedelta(seconds=5)
                with self.http():
                    self.service.search("Synthetic spaced follow-up after " + kind)
                self.assertEqual(len(self.calls), calls_before + 1)

    def test_callback_failure_rolls_back_dispatch_and_does_not_send(self):
        booking = self.service.reserve("Synthetic cancelled owner")

        def before(db):
            db.execute("CREATE TABLE must_rollback(id INTEGER)")
            raise ValueError("The synthetic owner was cancelled.")

        with self.http(), self.assertRaises(SearchFailure) as error:
            self.service.dispatch(booking, before_dispatch=before)
        self.assertFalse(error.exception.dispatched)
        self.assertEqual(error.exception.outcome, "cancelled")
        self.assertEqual(self.status(), "cancelled")
        self.assertEqual(self.calls, [])
        with closing(sqlite3.connect(self.database)) as db:
            self.assertIsNone(db.execute("SELECT name FROM sqlite_master WHERE name='must_rollback'").fetchone())

    def test_forged_booking_changed_credentials_and_disabled_settings_never_send(self):
        for mutation in ("query", "nonce", "max_results", "credential", "disabled"):
            with self.subTest(mutation=mutation):
                with self.service.transaction() as state:
                    state["ledger"] = []
                    state["config"]["enabled"] = True
                self.key = "synthetic-search-dispatch-key"
                booking = self.service.reserve("Synthetic public query")
                changed = booking
                if mutation == "query":
                    changed = replace(booking, query="Changed synthetic query")
                elif mutation == "nonce":
                    changed = replace(booking, nonce="b" * 32)
                elif mutation == "max_results":
                    changed = replace(booking, max_results=1)
                elif mutation == "credential":
                    self.key = "synthetic-replacement-key"
                else:
                    with self.service.transaction() as state:
                        state["config"]["enabled"] = False
                with self.http(), self.assertRaises(ValueError):
                    self.service.dispatch(changed)
        self.assertEqual(self.calls, [])

    def test_recovery_known_pre_dispatch_and_unknown_post_dispatch_do_not_replay(self):
        pending = self.service.reserve("Synthetic pending search")
        self.service.recover()
        self.assertEqual(self.status(), "cancelled")
        with self.http(), self.assertRaises(SearchFailure):
            self.service.dispatch(pending)
        self.clock += timedelta(seconds=6)
        sent = self.service.reserve("Synthetic response before ingestion")
        with self.http():
            self.service.dispatch(sent, finalize=False)
        self.service.recover()
        self.assertEqual(self.status(), "uncertain")
        with self.http(), self.assertRaises(SearchFailure):
            self.service.dispatch(sent)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.service.summary()["searches_today"], 2)

    def test_transport_timeout_and_cancellation_are_uncertain_but_http_failure_is_known(self):
        for kind in ("timeout", "cancel", "http", "empty"):
            with self.subTest(kind=kind):
                self.clock += timedelta(seconds=6)
                booking = self.service.reserve("Synthetic outcome " + kind)

                def response(request):
                    self.calls.append(request)
                    if kind == "timeout":
                        raise httpx.ReadTimeout("Synthetic timeout", request=request)
                    if kind == "cancel":
                        raise asyncio.CancelledError()
                    return httpx.Response(503) if kind == "http" else httpx.Response(200, json={"results": []})

                error_type = asyncio.CancelledError if kind == "cancel" else SearchFailure
                with self.http(response), self.assertRaises(error_type):
                    self.service.dispatch(booking)
                self.assertEqual(self.status(), "uncertain" if kind in ("timeout", "cancel") else "failed")
        self.assertEqual(len(self.calls), 4)
        self.assertEqual(self.service.summary()["searches_today"], 4)

    def test_encoded_transport_key_is_rejected_after_credential_rotation(self):
        for codec in ("percent", "html", "json"):
            for field in ("title", "url", "content"):
                with self.subTest(codec=codec, field=field):
                    self.clock += timedelta(seconds=6)
                    self.key = "synthetic-search-dispatch-key"
                    transport_key = self.key
                    if codec == "percent":
                        encoded_key = "".join("%25" + format(ord(char), "02X") for char in transport_key)
                    elif codec == "html":
                        encoded_key = "".join("&#" + str(ord(char)) + ";" for char in transport_key)
                    else:
                        encoded_key = "".join(chr(92) + "u" + format(ord(char), "04x") for char in transport_key)
                    booking = self.service.reserve("Synthetic rotation safety " + codec + " " + field)
                    unsafe = {"title": "Synthetic unsafe source", "url": "https://example.org/unsafe",
                              "content": "Synthetic unsafe content"}
                    unsafe[field] += encoded_key
                    safe = {"title": "Synthetic safe source", "url": "https://example.org/safe",
                            "content": "Synthetic safe content"}

                    def response(request):
                        self.assertEqual(request.headers["Authorization"], "Bearer " + transport_key)
                        self.key = "synthetic-rotated-search-key"
                        return httpx.Response(200, json={"results": [unsafe, safe]})

                    with self.http(response):
                        receipt = self.service.dispatch(booking)
                    self.assertEqual(receipt["sources"], [safe])
                    self.assertNotIn(encoded_key, json.dumps(receipt))
                    self.assertNotIn(transport_key.encode(), self.database.read_bytes())
                    self.assertEqual(self.status(), "completed")

    def test_shared_lower_quota_and_midnight_booking_fence(self):
        with self.service.transaction() as state:
            state["config"]["daily_limit"] = 1
        with self.http():
            self.service.search("Synthetic legacy search")
        self.clock += timedelta(seconds=6)
        with self.assertRaisesRegex(ValueError, "Daily"):
            self.service.reserve("Synthetic research search")
        self.clock += timedelta(days=1)
        booking = self.service.reserve("Synthetic next-day booking")
        self.clock += timedelta(days=1)
        with self.http(), self.assertRaises(SearchFailure) as error:
            self.service.dispatch(booking)
        self.assertFalse(error.exception.dispatched)
        self.assertEqual(self.status(), "cancelled")
        self.assertEqual(len(self.calls), 1)


if __name__ == "__main__":
    unittest.main()

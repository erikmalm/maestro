"""Durable acquisition ownership under synthetic transport and isolated storage.

Public Research remains saved-only. The future hosted run fixture below deliberately
changes the private row after ordinary creation; no production admission is widened.
"""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timezone
import asyncio
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import patch
import uuid

import httpx

from backend.acquisition import AcquisitionJournal
from backend.context_store import ContextStore, DEFAULT as CONTEXT_DEFAULT
from backend.provider import DEFAULT as PROVIDER_DEFAULT
from backend.research import ResearchStore
from backend.research_evidence import unavailable_review, validate_review
from backend.research_requirements import validate_assessment
from backend.web_search import WebSearch


class AcquisitionJournalTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory(prefix="maestro-acquisition-")))
        self.database = self.root / "private" / "workspace.sqlite3"
        self.store = ResearchStore(self.database, timezone.utc)
        self.clock = datetime(2026, 11, 27, 10, tzinfo=timezone.utc).timestamp()
        self.enterContext(patch.object(ResearchStore, "clock", side_effect=lambda: self.clock))
        self.enterContext(patch.object(WebSearch, "now", side_effect=lambda: self.now()))
        self.key = "synthetic-acquisition-secret"
        self.enterContext(patch("backend.credentials.read", return_value=(self.key, "memory")))
        self.workspace = {"chats": [{"id": "chat", "messages": []}],
                          "limits": {"run_usd": 1, "daily_usd": 5, "monthly_usd": 50, "max_tokens": 100000}}
        self.provider = {"config": {**PROVIDER_DEFAULT, "protocol": "ollama", "model": "synthetic-local:latest"},
                         "models": ["synthetic-local:latest"], "ledger": []}
        total = {"model_calls": 4, "tokens": 100000, "seconds": 120,
                 "estimated_model_usd": 1, "hosted_searches": 0}
        self.budget = {"total": total, "consumed": dict.fromkeys(total, 0),
                       "reserved": dict.fromkeys(total, 0), "remaining": total.copy(),
                       "deadline": datetime.fromtimestamp(self.clock + 120, timezone.utc).isoformat(),
                       "parent_limits": deepcopy(self.workspace["limits"]),
                       "daily_search": {"limit": 20, "used": 0, "remaining": 20}}
        self.permissions = {"source_mode": "saved_only", "hosted_allowed": False,
                            "memory_recall": False, "memory_derived": False, "chat_id": "chat"}
        with self.store._transaction() as db:
            db.execute("CREATE TABLE workspace(id INTEGER PRIMARY KEY,value TEXT NOT NULL)")
            db.execute("CREATE TABLE provider_state(id INTEGER PRIMARY KEY,value TEXT NOT NULL)")
            self.save_settings(db)
        self.search = WebSearch(self.database, timezone.utc)
        with self.search.transaction() as state:
            state.update(config={"enabled": True, "daily_limit": 20, "max_results": 3},
                         tested_at=self.now().isoformat(), paused_until=None, ledger=[])
        self.journal = AcquisitionJournal(self.store)
        self.requests = []
        self.source = {"title": "Synthetic Acme disclosure", "url": "https://reports.example.org/acme",
                       "content": "Acme FY2025 revenue was USD 42 million."}
        self.handler = self.response
        self.enterContext(patch("backend.web_search.search_response", side_effect=self.call_response))

    def now(self):
        return datetime.fromtimestamp(self.clock, timezone.utc)

    def save_settings(self, db):
        db.execute("INSERT OR REPLACE INTO workspace VALUES(1,?)", (json.dumps(self.workspace),))
        db.execute("INSERT OR REPLACE INTO provider_state VALUES(1,?)", (json.dumps(self.provider),))

    async def call_response(self, key, query, max_results):
        self.requests.append((key, query, max_results))
        return self.handler(key, query, max_results)

    def response(self, key, query, max_results):
        self.assertEqual(key, self.key)
        return httpx.Response(200), json.dumps({"results": [self.source]}).encode()

    def edit_run(self, run_id, mutate):
        with self.store._transaction() as db:
            row = db.execute("SELECT payload FROM research_runs WHERE id=?", (run_id,)).fetchone()
            payload = json.loads(row[0])
            mutate(payload)
            db.execute("UPDATE research_runs SET payload=? WHERE id=?", (json.dumps(payload), run_id))

    def create_run(self, *, hosted=True, subject="Acme", period="FY2025", currency="USD",
                   request_suffix="", constraints=None):
        request = f"Research {subject} revenue for {period} in {currency}." + request_suffix
        run, _ = self.store.create(uuid.uuid4().hex, request, "", "chat", self.permissions,
                                   deepcopy(self.budget), workflow="research_answer")
        token = self.store.claim(run["id"], lookup=False)[1]
        assessment = validate_assessment({
            "version": 1, "decision": "ready",
            "subjects": [{"id": "s1", "kind": "company", "name": subject, "identifiers": []}],
            "items": [{"id": "i1", "subject_ids": ["s1"], "criterion": "revenue",
                       "period": period, "unit": None, "currency": currency, "freshness": None}],
            "constraints": constraints or [], "clarifications": [],
        }, request)
        evidence = unavailable_review(assessment, {"state": "unavailable", "search_limited": False,
                                                   "skipped_count": 0},
                                      {"limited": False, "omitted_count": 0},
                                      "No saved evidence was available.", self.now().isoformat())

        def seed(payload):
            payload.update(assessment=assessment, evidence_review=evidence)
            if hosted:
                payload["permissions"].update(source_mode="research", hosted_allowed=True)
                payload["budget"]["total"]["hosted_searches"] = 1
                payload["budget"]["remaining"]["hosted_searches"] = 1
        self.edit_run(run["id"], seed)
        return run["id"], token

    def prepare(self, run_id=None, token=None, **changes):
        if run_id is None:
            run_id, token = self.create_run()
        values = {"item_ids": ["i1"], "query": "Acme FY2025 revenue USD", "max_results": 3}
        return self.journal.prepare(run_id, token, **{**values, **changes})

    def ledger(self):
        return self.search.read_state()["ledger"]

    def test_saved_only_public_run_cannot_book_or_dispatch(self):
        run_id, token = self.create_run(hosted=False)
        try:
            attempt = self.prepare(run_id, token)
        except ValueError:
            pass
        else:
            with self.assertRaises(ValueError):
                self.journal.reserve(attempt)
        self.assertEqual(self.ledger(), [])
        self.assertEqual(self.requests, [])

    def test_dispatch_occurs_once_and_outside_database_write_lock(self):
        attempt = self.prepare()
        booking = self.journal.reserve(attempt)

        def unlocked_response(key, query, max_results):
            with closing(sqlite3.connect(self.database, timeout=0.1)) as db, db:
                db.execute("BEGIN IMMEDIATE")
                db.execute("UPDATE workspace SET value=value WHERE id=1")
            return self.response(key, query, max_results)
        self.handler = unlocked_response
        receipt = self.journal.dispatch(attempt, booking)
        self.assertEqual(receipt["at"], self.now().isoformat())
        self.assertEqual(len(self.requests), 1)
        with self.assertRaises(ValueError):
            self.journal.dispatch(attempt, booking)
        self.assertEqual(len(self.requests), 1)
        self.assertEqual(len(self.ledger()), 1)
        self.assertEqual(self.ledger()[0]["status"], "completed")
        self.assertNotIn(self.key, self.database.read_bytes().decode("utf-8", errors="ignore"))

    def test_concurrent_reservations_share_one_existing_daily_booking(self):
        attempt = self.prepare()
        barrier = threading.Barrier(2)

        def reserve():
            barrier.wait(timeout=5)
            try:
                return self.journal.reserve(attempt)
            except ValueError:
                return None
        with ThreadPoolExecutor(max_workers=2) as pool:
            bookings = list(pool.map(lambda _: reserve(), range(2)))
        accepted = [booking for booking in bookings if booking is not None]
        self.assertTrue(accepted)
        self.assertEqual(len({booking.request_id for booking in accepted}), 1)
        self.assertEqual(len(self.ledger()), 1)
        self.assertEqual(self.search.summary()["searches_today"], 1)
        budget = self.store.get(attempt.run_id)["budget"]
        self.assertEqual(budget["reserved"]["hosted_searches"], 1)
        self.assertEqual(budget["remaining"]["hosted_searches"], 0)
        self.assertEqual(self.requests, [])

    def test_shared_ledger_and_run_reservation_roll_back_together(self):
        attempt = self.prepare()
        original = WebSearch.reserve

        def interrupted(service, *args, **kwargs):
            original(service, *args, **kwargs)
            raise RuntimeError("Synthetic interruption before journal commit")
        with patch.object(WebSearch, "reserve", autospec=True, side_effect=interrupted):
            with self.assertRaises(RuntimeError):
                self.journal.reserve(attempt)
        self.assertEqual(self.ledger(), [])
        self.assertEqual(self.store.get(attempt.run_id)["budget"]["reserved"]["hosted_searches"], 0)
        self.assertEqual(self.journal.get(attempt.attempt_id)["state"], "prepared")
        booking = self.journal.reserve(attempt)
        self.assertEqual(self.ledger()[0]["id"], booking.request_id)

    def test_daily_limit_is_shared_with_legacy_and_finishing_time_is_protected(self):
        attempt = self.prepare()
        with self.search.transaction() as state:
            state["config"]["daily_limit"] = 1
        self.journal.reserve(attempt)
        self.clock += 6
        with self.assertRaises(ValueError):
            self.search.reserve("A separate legacy query")
        self.assertEqual(len(self.ledger()), 1)
        self.store.cancel(attempt.run_id)
        with self.search.transaction() as state:
            state["config"]["daily_limit"] = 20
        run_id, token = self.create_run()
        self.edit_run(run_id, lambda p: p["budget"]["total"].update(seconds=60))
        attempt = self.prepare(run_id, token)
        with self.assertRaises(ValueError):
            self.journal.reserve(attempt)
        self.assertEqual(len(self.ledger()), 1)

    def test_credential_content_is_rejected_and_rotation_fences_existing_booking(self):
        run_id, token = self.create_run()
        with self.assertRaises(ValueError):
            self.prepare(run_id, token, query="Search " + self.key)
        attempt = self.prepare(run_id, token)
        booking = self.journal.reserve(attempt)
        with patch("backend.credentials.read", return_value=("synthetic-rotated-secret", "memory")):
            with self.assertRaises(ValueError):
                self.journal.dispatch(attempt, booking)
        self.assertEqual(self.requests, [])

    def test_rotation_during_transport_cannot_publish_encoded_old_credential(self):
        current = [self.key]
        self.enterContext(patch("backend.credentials.read", side_effect=lambda endpoint: (current[0], "memory")))
        attempt = self.prepare()
        booking = self.journal.reserve(attempt)
        encoded_key = "".join("%25" + format(ord(char), "02X") for char in self.key)

        def rotate_and_return_secret(key, query, max_results):
            current[0] = "synthetic-rotated-search-secret"
            leaked = {**self.source, "url": "https://reports.example.org/" + encoded_key}
            return httpx.Response(200), json.dumps({"results": [leaked]}).encode()
        self.handler = rotate_and_return_secret
        with self.assertRaises(ValueError):
            self.journal.dispatch(attempt, booking)
        record = self.journal.get(attempt.attempt_id)
        self.assertEqual(record["state"], "failed")
        self.assertNotIn(encoded_key, json.dumps(record))
        self.assertNotIn(self.key, json.dumps(record))

    def test_rotation_during_transport_cannot_publish_json_escaped_old_credential(self):
        current = [self.key]
        self.enterContext(patch("backend.credentials.read", side_effect=lambda endpoint: (current[0], "memory")))
        attempt = self.prepare()
        booking = self.journal.reserve(attempt)
        escaped_key = "".join("\\u" + format(ord(char), "04x") for char in self.key)

        def rotate_and_return_secret(key, query, max_results):
            current[0] = "synthetic-rotated-search-secret"
            leaked = {**self.source, "url": "https://reports.example.org/" + escaped_key,
                      "content": "Synthetic disclosure " + escaped_key}
            return httpx.Response(200), json.dumps({"results": [leaked]}).encode()
        self.handler = rotate_and_return_secret
        with self.assertRaises(ValueError):
            self.journal.dispatch(attempt, booking)
        record = self.journal.get(attempt.attempt_id)
        self.assertEqual(record["state"], "failed")
        self.assertIsNone(record["receipt"])
        self.assertNotIn(self.key, json.dumps(record))

    def test_provider_model_change_fences_existing_hosted_booking(self):
        attempt = self.prepare()
        booking = self.journal.reserve(attempt)
        self.provider["config"]["model"] = "synthetic-other-local:latest"
        self.provider["models"].append("synthetic-other-local:latest")
        with self.store._transaction() as db:
            self.save_settings(db)
        with self.assertRaises(ValueError):
            self.journal.dispatch(attempt, booking)
        self.assertEqual(self.requests, [])
        self.assertEqual(self.ledger()[0]["status"], "cancelled")
        self.assertEqual(self.journal.get(attempt.attempt_id)["state"], "failed")

    def test_explicit_search_forbid_survives_future_hosted_fixture_permissions(self):
        constraint = {"kind": "search", "strength": "forbid", "item_ids": [],
                      "criterion": "Do not search online."}
        run_id, token = self.create_run(request_suffix=" Do not search online.", constraints=[constraint])
        self.assertEqual(self.store.get(run_id)["assessment"]["constraints"], [constraint])
        with self.assertRaises(ValueError):
            attempt = self.prepare(run_id, token)
            self.journal.reserve(attempt)
        self.assertEqual(self.requests, [])
        self.assertEqual(self.ledger(), [])

    def test_concurrent_dispatch_claims_have_one_transport_and_one_quota_entry(self):
        attempt = self.prepare()
        booking = self.journal.reserve(attempt)
        entered, release = threading.Event(), threading.Event()

        def blocked_response(key, query, max_results):
            entered.set()
            self.assertTrue(release.wait(5))
            return self.response(key, query, max_results)
        self.handler = blocked_response
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(self.journal.dispatch, attempt, booking)
            self.assertTrue(entered.wait(5))
            second = pool.submit(self.journal.dispatch, attempt, booking)
            try:
                with self.assertRaises(ValueError):
                    second.result(timeout=5)
                self.assertEqual(self.ledger()[0]["status"], "dispatched")
                self.assertEqual(self.journal.get(attempt.attempt_id)["state"], "dispatched")
                self.assertEqual(self.store.get(attempt.run_id)["budget"]["reserved"]["hosted_searches"], 1)
            finally:
                release.set()
            first.result(timeout=5)
        self.assertEqual(len(self.requests), 1)
        self.assertEqual(len(self.ledger()), 1)

    def test_foreign_claim_cannot_reserve_or_dispatch(self):
        attempt = self.prepare()
        forged = replace(attempt, claim_token=uuid.uuid4().hex)
        with self.assertRaises(ValueError):
            self.journal.reserve(forged)
        self.assertEqual(self.ledger(), [])
        booking = self.journal.reserve(attempt)
        with self.assertRaises(ValueError):
            self.journal.dispatch(forged, booking)
        self.assertEqual(self.requests, [])
        self.assertEqual(self.journal.get(attempt.attempt_id)["state"], "pending")
        self.assertEqual(self.ledger()[0]["status"], "reserved")
        self.journal.dispatch(attempt, booking)
        self.assertEqual(len(self.requests), 1)

    def test_cancellation_between_booking_and_dispatch_prevents_transport(self):
        attempt = self.prepare()
        booking = self.journal.reserve(attempt)
        self.store.cancel(attempt.run_id)
        with self.assertRaises(ValueError):
            self.journal.dispatch(attempt, booking)
        self.assertEqual(self.requests, [])
        self.assertEqual(len(self.ledger()), 1)
        self.assertNotEqual(self.ledger()[0]["status"], "reserved")

    def test_newly_memory_derived_chat_and_changed_limits_refuse_dispatch(self):
        for change in ("memory", "limits"):
            with self.subTest(change=change):
                if change == "limits":
                    self.store.cancel(attempt.run_id)
                    self.clock += 6
                attempt = self.prepare()
                booking = self.journal.reserve(attempt)
                with self.store._transaction() as db:
                    if change == "memory":
                        self.workspace["chats"][0]["messages"] = [{"memory_ids": ["private-memory"]}]
                    else:
                        self.workspace["limits"]["max_tokens"] -= 1
                    self.save_settings(db)
                with self.assertRaises(ValueError):
                    self.journal.dispatch(attempt, booking)
                self.assertEqual(self.requests, [])
                self.workspace["chats"][0]["messages"] = []
                with self.store._transaction() as db:
                    self.save_settings(db)

    def test_structured_subject_period_currency_and_material_parameters_are_distinct(self):
        seen = set()
        for changes in ({}, {"subject": "Beta"}, {"period": "FY2024"}, {"currency": "SEK"}):
            run_id, token = self.create_run(**changes)
            attempt = self.prepare(run_id, token)
            identity = self.journal.get(attempt.attempt_id)["identity_hash"]
            self.assertNotIn(identity, seen)
            seen.add(identity)
            self.store.cancel(run_id)
        run_id, token = self.create_run()
        first = self.prepare(run_id, token)
        smaller = self.prepare(run_id, token, max_results=2)
        self.assertNotEqual(self.journal.get(first.attempt_id)["identity_hash"],
                            self.journal.get(smaller.attempt_id)["identity_hash"])
        duplicate = self.prepare(run_id, token)
        self.assertEqual(duplicate.attempt_id, first.attempt_id)
        self.assertEqual(self.ledger(), [])

    def test_mutated_gap_or_assessment_cannot_reuse_prepared_authority(self):
        attempt = self.prepare()
        self.edit_run(attempt.run_id, lambda p: p["assessment"]["items"][0].update(currency="SEK"))
        with self.assertRaises(ValueError):
            self.journal.reserve(attempt)
        self.assertEqual(self.requests, [])
        self.assertEqual(self.ledger(), [])

    def test_missing_forged_and_covered_saved_reviews_cannot_authorize_acquisition(self):
        run_id, token = self.create_run()
        original = self.store.get(run_id)["evidence_review"]
        forged = deepcopy(original)
        forged["items"][0]["state"] = "missing"
        for saved in (None, forged):
            with self.subTest(saved=saved):
                self.edit_run(run_id, lambda payload: payload.update(evidence_review=saved))
                with self.assertRaises(ValueError):
                    self.prepare(run_id, token)
        self.edit_run(run_id, lambda payload: payload.update(evidence_review=original))
        with self.assertRaises(ValueError):
            self.prepare(run_id, token, item_ids=["unknown"])
        assessment = self.store.get(run_id)["assessment"]
        text = self.source["content"]
        digest = hashlib.sha256(text.encode()).hexdigest()
        packet = {"version": 1, "offset_unit": "unicode_codepoint",
                  "lookup": {"state": "ready", "search_limited": False, "skipped_count": 0},
                  "selection": {"limited": False, "omitted_count": 0},
                  "sources": [{"id": "c1", "capture_id": "a" * 32, "content_hash": digest,
                               "manifest_hash": "b" * 64, "title": self.source["title"],
                               "url": self.source["url"], "retrieved_at": self.now().isoformat(),
                               "published_at": None, "modified_at": None, "stale": False,
                               "completeness": {"maestro_truncated": False, "full_page": False}}],
                  "passages": [{"id": "p1", "source_id": "c1", "start": 0, "end": len(text),
                                "text": text, "passage_hash": digest}]}
        covered = validate_review({"version": 1, "items": [{"item_id": "i1", "state": "covered",
                                  "reason": "The saved passage supports the requested historical metric.",
                                  "supports": [{"passage_id": "p1", "quote": text,
                                                "temporal_scope": "historical", "period": "FY2025",
                                                "observation_date": None}]}]},
                                  assessment, packet, self.now().isoformat())
        self.edit_run(run_id, lambda payload: payload.update(evidence_review=covered))
        with self.assertRaises(ValueError):
            self.prepare(run_id, token)
        self.assertEqual(self.requests, [])
        self.assertEqual(self.ledger(), [])

    def test_attempt_preparation_is_bounded_and_duplicate_identity_uses_same_record(self):
        run_id, token = self.create_run()
        first = self.prepare(run_id, token, query="Synthetic query 0")
        for index in range(1, 4):
            self.prepare(run_id, token, query=f"Synthetic query {index}")
        with self.assertRaises(ValueError):
            self.prepare(run_id, token, query="Synthetic query 4")
        self.assertEqual(self.prepare(run_id, token, query="Synthetic query 0").attempt_id, first.attempt_id)
        self.assertEqual(self.ledger(), [])

    def seed_quota_entries(self, count, daily_limit=200):
        with self.search.transaction() as state:
            state["config"]["daily_limit"] = daily_limit
            state["ledger"] = [{"id": uuid.uuid4().hex,
                                "at": datetime.fromtimestamp(self.clock - 60 - index * 6, timezone.utc).isoformat(),
                                "status": "completed"} for index in range(count)]

    def test_research_cap_is_twenty_even_when_legacy_configuration_is_higher(self):
        self.seed_quota_entries(20)
        attempt = self.prepare()
        with self.assertRaises(ValueError):
            self.journal.reserve(attempt)
        self.assertEqual(len(self.ledger()), 20)
        self.assertEqual(self.requests, [])

    def test_captured_lower_cap_and_newly_lowered_settings_are_both_authoritative(self):
        self.seed_quota_entries(1)
        run_id, token = self.create_run()
        self.edit_run(run_id, lambda payload: payload["budget"]["daily_search"].update(limit=1, used=1, remaining=0))
        attempt = self.prepare(run_id, token)
        with self.assertRaises(ValueError):
            self.journal.reserve(attempt)
        self.assertEqual(len(self.ledger()), 1)
        self.store.cancel(run_id)
        self.seed_quota_entries(0)
        attempt = self.prepare()
        booking = self.journal.reserve(attempt)
        with self.search.transaction() as state:
            state["config"]["daily_limit"] = 0
        with self.assertRaises(ValueError):
            self.journal.dispatch(attempt, booking)
        self.assertEqual(self.ledger()[0]["status"], "cancelled")
        self.assertEqual(self.requests, [])

    def prepare_stage(self, run_id, token):
        return self.store.prepare_stage(run_id, token, "requirements", model="synthetic-local:latest",
                                        schema={"type": "object", "properties": {}},
                                        assignment="Return the assigned synthetic result.",
                                        permitted_actions=["return_json", "report_gap"], seconds=5)

    def test_journal_booking_cannot_overlap_prepared_reserved_or_uncertain_model_stage(self):
        run_id, token = self.create_run()
        attempt = self.prepare(run_id, token)
        stage = self.prepare_stage(run_id, token)
        for state in ("prepared", "reserved", "uncertain"):
            with self.subTest(state=state):
                # Synthetic persisted states isolate the mutual-exclusion barrier;
                # public local stages still cannot authorize hosted permissions.
                with self.store._transaction() as db:
                    db.execute("UPDATE research_stages SET state=? WHERE id=?", (state, stage.stage_id))
                with self.assertRaisesRegex(ValueError, "stage"):
                    self.journal.reserve(attempt)
                self.assertEqual(self.journal.get(attempt.attempt_id)["state"], "prepared")
                self.assertEqual(self.ledger(), [])
        self.assertEqual(self.requests, [])

    def test_model_stage_preparation_and_authorization_cannot_overlap_hosted_attempt(self):
        run_id, token = self.create_run()
        attempt = self.prepare(run_id, token)
        stage = self.prepare_stage(run_id, token)
        with self.store._transaction() as db:
            saved_stage = db.execute("SELECT * FROM research_stages WHERE id=?", (stage.stage_id,)).fetchone()
            db.execute("DELETE FROM research_stages WHERE id=?", (stage.stage_id,))
        self.journal.reserve(attempt)
        with self.store._transaction() as db:
            # Restore an earlier preparation as a synthetic stale/racing caller.
            db.execute("INSERT INTO research_stages VALUES(" + ",".join("?" for _ in saved_stage) + ")", saved_stage)
        for state in ("pending", "dispatched", "uncertain"):
            with self.subTest(state=state):
                with self.store._transaction() as db:
                    db.execute("UPDATE acquisition_attempts SET state=? WHERE id=?", (state, attempt.attempt_id))
                with self.assertRaisesRegex(ValueError, "hosted research acquisition"):
                    self.prepare_stage(run_id, token)
                with self.store._transaction() as db:
                    with self.assertRaisesRegex(ValueError, "hosted research acquisition"):
                        self.store.authorize_stage(stage, self.workspace, self.provider, db)
        self.assertEqual(len(self.ledger()), 1)
        self.assertEqual(self.requests, [])

    def test_empty_and_failed_responses_do_not_block_a_fresh_later_run(self):
        for status, results in ((200, []), (503, [])):
            with self.subTest(status=status):
                attempt = self.prepare()
                booking = self.journal.reserve(attempt)
                self.handler = lambda *args: (httpx.Response(status), json.dumps({"results": results}).encode())
                with self.assertRaises(ValueError):
                    self.journal.dispatch(attempt, booking)
                self.assertEqual(self.journal.get(attempt.attempt_id)["state"], "failed")
                self.store.cancel(attempt.run_id)
                self.clock += 6
        self.handler = self.response
        attempt = self.prepare()
        self.journal.dispatch(attempt, self.journal.reserve(attempt))
        self.assertEqual(len(self.requests), 3)
        self.assertEqual(len(self.ledger()), 3)

    def test_unknown_transport_outcome_stays_charged_and_cannot_replay(self):
        attempt = self.prepare()
        booking = self.journal.reserve(attempt)

        def unknown(*args):
            raise TimeoutError("Synthetic transport outcome is unknown")
        self.handler = unknown
        with self.assertRaises(ValueError):
            self.journal.dispatch(attempt, booking)
        self.assertEqual(self.journal.get(attempt.attempt_id)["state"], "uncertain")
        self.assertEqual(self.ledger()[0]["status"], "uncertain")
        self.store.recover()
        with self.assertRaises(ValueError):
            self.journal.dispatch(attempt, booking)
        self.assertEqual(len(self.requests), 1)
        self.assertEqual(self.search.summary()["searches_today"], 1)

    def test_restart_after_booking_preserves_charge_and_never_sends(self):
        attempt = self.prepare()
        booking = self.journal.reserve(attempt)
        self.store.recover()
        self.search.recover()
        with self.assertRaises(ValueError):
            self.journal.dispatch(attempt, booking)
        self.assertEqual(self.requests, [])
        self.assertEqual(len(self.ledger()), 1)
        self.assertNotEqual(self.ledger()[0]["status"], "reserved")

    def test_restart_after_dispatch_keeps_uncertain_even_if_old_response_returns(self):
        attempt = self.prepare()
        booking = self.journal.reserve(attempt)
        entered, release = threading.Event(), threading.Event()

        def blocked_response(key, query, max_results):
            entered.set()
            self.assertTrue(release.wait(5))
            return self.response(key, query, max_results)
        self.handler = blocked_response
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(self.journal.dispatch, attempt, booking)
            self.assertTrue(entered.wait(5))
            # Match application startup order: shared ledger, then owned run recovery.
            self.search.recover()
            self.store.recover()
            release.set()
            try:
                future.result(timeout=5)
            except ValueError:
                pass
        record = self.journal.get(attempt.attempt_id)
        self.assertEqual(record["state"], "uncertain")
        self.assertIsNone(record["receipt"])
        self.assertEqual(self.ledger()[0]["status"], "uncertain")
        with self.assertRaises(ValueError):
            self.journal.dispatch(attempt, booking)
        self.assertEqual(len(self.requests), 1)

    def test_restart_after_response_before_ingestion_never_replays_or_publishes(self):
        attempt = self.prepare()
        booking = self.journal.reserve(attempt)
        receipt = self.journal.dispatch(attempt, booking)
        self.search.recover()
        self.store.recover()
        self.assertEqual(self.journal.get(attempt.attempt_id)["state"], "response_received")
        context = ContextStore(self.database, self.root / "archive")
        context.configure({**CONTEXT_DEFAULT, "enabled": True})
        saved = context.capture(receipt["query"], receipt["at"], receipt["sources"], 3)["sources"][0]
        reference = {name: saved[name] for name in ("capture_id", "content_hash", "manifest_hash", "retrieved_at")}
        with self.assertRaises(ValueError):
            self.journal.publish(attempt, [reference], context)
        with self.assertRaises(ValueError):
            self.journal.dispatch(attempt, booking)
        self.assertEqual(len(self.requests), 1)
        self.assertIsNone(self.journal.get(attempt.attempt_id)["refs"])

    def test_deletion_during_transport_settles_only_opaque_accounting(self):
        attempt = self.prepare(query="Private synthetic acquisition query")
        booking = self.journal.reserve(attempt)
        entered, release = threading.Event(), threading.Event()

        def blocked_response(key, query, max_results):
            entered.set()
            self.assertTrue(release.wait(5))
            return self.response(key, query, max_results)
        self.handler = blocked_response
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(self.journal.dispatch, attempt, booking)
            self.assertTrue(entered.wait(5))
            self.store.delete(attempt.run_id)
            release.set()
            try:
                future.result(timeout=5)
            except ValueError:
                pass
        record = self.journal.get(attempt.attempt_id)
        self.assertIsNone(record["spec"])
        self.assertIsNone(record["receipt"])
        self.assertEqual(len(self.requests), 1)
        self.assertEqual(len(self.ledger()), 1)
        with self.assertRaises(ValueError):
            self.journal.dispatch(attempt, booking)

    def test_response_is_not_reusable_until_exact_source_pins_are_published(self):
        attempt = self.prepare()
        receipt = self.journal.dispatch(attempt, self.journal.reserve(attempt))
        self.assertEqual(self.journal.get(attempt.attempt_id)["state"], "response_received")
        context = ContextStore(self.database, self.root / "archive")
        context.configure({**CONTEXT_DEFAULT, "enabled": True})
        with self.assertRaises(ValueError):
            self.journal.reusable(attempt, context, retrieved_after="2026-01-01T00:00:00+00:00")
        captured = context.capture(receipt["query"], receipt["at"], receipt["sources"], 3)["sources"][0]
        reference = {name: captured[name] for name in ("capture_id", "content_hash", "manifest_hash", "retrieved_at")}
        self.assertTrue(self.journal.publish(attempt, [reference], context))
        self.assertEqual(self.journal.get(attempt.attempt_id)["state"], "completed")
        original = deepcopy(reference)
        self.clock += 10
        self.assertEqual(self.journal.reusable(attempt, context, retrieved_after="2026-01-01T00:00:00+00:00"), [original])
        with self.assertRaises(ValueError):
            self.journal.reusable(attempt, context, retrieved_after=self.now().isoformat())
        context.configure({**CONTEXT_DEFAULT, "enabled": False})
        with self.assertRaises((ValueError, KeyError)):
            self.journal.reusable(attempt, context, retrieved_after="2026-01-01T00:00:00+00:00")

    def test_durable_pin_validation_supports_larger_stored_excerpt_and_checks_its_tail(self):
        # Storage support is independent of the legacy transport's 1,200-char
        # clipping. Seed the compact received-source identity directly here.
        context = ContextStore(self.database, self.root / "archive")
        context.configure({**CONTEXT_DEFAULT, "enabled": True})
        source = {**self.source, "content": "Historical context.\n" * 8000 + "Acme revenue was USD 42 million."}
        saved = context.capture("Acme revenue", self.now().isoformat(), [source], 1)["sources"][0]
        ref = {name: saved[name] for name in ("capture_id", "content_hash", "manifest_hash", "retrieved_at")}
        record = {"spec": {"max_results": 1}, "receipt": {"at": saved["retrieved_at"], "sources": [
            {"url": saved["url"], "title": saved["title"], "content_hash": saved["content_hash"]}]}}
        self.assertEqual(self.journal._pins(record, [ref], context, saved["retrieved_at"]), [ref])
        encoded = "".join("\\u" + format(ord(char), "04x") for char in "synthetic-later-tail-key")
        source["content"] += encoded
        later = context.capture("Acme revenue", self.now().isoformat(), [source], 1)["sources"][0]
        later_ref = {name: later[name] for name in ref}
        record["receipt"]["sources"][0]["content_hash"] = later["content_hash"]
        with patch("backend.credentials.read", return_value=("synthetic-later-tail-key", "memory")):
            with self.assertRaises(ValueError):
                self.journal._pins(record, [later_ref], context, later["retrieved_at"])

    def test_publication_rejects_wrong_timestamp_and_foreign_capture(self):
        attempt = self.prepare()
        receipt = self.journal.dispatch(attempt, self.journal.reserve(attempt))
        context = ContextStore(self.database, self.root / "archive")
        context.configure({**CONTEXT_DEFAULT, "enabled": True})
        foreign = {**self.source, "url": "https://reports.example.org/other"}
        for source, at in ((self.source, "2020-01-01T00:00:00+00:00"), (foreign, receipt["at"])):
            saved = context.capture(receipt["query"], at, [source], 3)["sources"][0]
            reference = {name: saved[name] for name in ("capture_id", "content_hash", "manifest_hash", "retrieved_at")}
            with self.assertRaises(ValueError):
                self.journal.publish(attempt, [reference], context)
        self.assertEqual(self.journal.get(attempt.attempt_id)["state"], "response_received")

    def test_foreign_workspace_catalogue_cannot_supply_publication_or_reuse_pins(self):
        attempt = self.prepare()
        receipt = self.journal.dispatch(attempt, self.journal.reserve(attempt))
        foreign = ContextStore(self.root / "other-private" / "workspace.sqlite3", self.root / "other-archive")
        foreign.configure({**CONTEXT_DEFAULT, "enabled": True})
        saved = foreign.capture(receipt["query"], receipt["at"], receipt["sources"], 3)["sources"][0]
        reference = {name: saved[name] for name in ("capture_id", "content_hash", "manifest_hash", "retrieved_at")}
        with self.assertRaises(ValueError):
            self.journal.publish(attempt, [reference], foreign)
        self.assertEqual(self.journal.get(attempt.attempt_id)["state"], "response_received")

    def test_cancelled_transport_keeps_unknown_dispatch_and_quota_guard(self):
        attempt = self.prepare()
        booking = self.journal.reserve(attempt)

        def cancelled(*args):
            raise asyncio.CancelledError()
        self.handler = cancelled
        with self.assertRaises(asyncio.CancelledError):
            self.journal.dispatch(attempt, booking)
        self.assertEqual(self.journal.get(attempt.attempt_id)["state"], "uncertain")
        self.assertEqual(self.ledger()[0]["status"], "uncertain")
        with self.assertRaises(ValueError):
            self.journal.dispatch(attempt, booking)
        self.assertEqual(len(self.requests), 1)

    def test_changed_search_settings_reject_dispatch_and_release_pending_booking(self):
        attempt = self.prepare()
        booking = self.journal.reserve(attempt)
        with self.search.transaction() as state:
            state["config"]["enabled"] = False
        with self.assertRaises(ValueError):
            self.journal.dispatch(attempt, booking)
        self.assertEqual(self.requests, [])
        self.assertEqual(self.ledger()[0]["status"], "cancelled")
        self.assertEqual(self.journal.get(attempt.attempt_id)["state"], "failed")
        budget = self.store.get(attempt.run_id)["budget"]
        self.assertEqual(budget["reserved"]["hosted_searches"], 0)
        self.assertEqual(budget["consumed"]["hosted_searches"], 1)

    def test_received_response_is_retained_when_finishing_time_has_been_consumed(self):
        run_id, token = self.create_run()
        self.edit_run(run_id, lambda p: p["budget"]["total"].update(seconds=80))
        attempt = self.prepare(run_id, token)

        def slow_response(key, query, max_results):
            self.clock += 60
            return self.response(key, query, max_results)
        self.handler = slow_response
        receipt = self.journal.dispatch(attempt, self.journal.reserve(attempt))
        self.assertEqual(self.journal.get(attempt.attempt_id)["state"], "response_received")
        self.assertEqual(receipt["at"], self.now().isoformat())
        budget = self.store.get(attempt.run_id)["budget"]
        self.assertEqual(budget["reserved"]["hosted_searches"], 0)
        self.assertEqual(budget["consumed"]["hosted_searches"], 1)

    def test_same_url_and_time_cannot_publish_different_response_bytes(self):
        attempt = self.prepare()
        receipt = self.journal.dispatch(attempt, self.journal.reserve(attempt))
        context = ContextStore(self.database, self.root / "archive")
        context.configure({**CONTEXT_DEFAULT, "enabled": True})
        changed = {**self.source, "content": "Synthetic changed unsupported source bytes."}
        saved = context.capture(receipt["query"], receipt["at"], [changed], 3)["sources"][0]
        reference = {name: saved[name] for name in ("capture_id", "content_hash", "manifest_hash", "retrieved_at")}
        with self.assertRaises(ValueError):
            self.journal.publish(attempt, [reference], context)
        self.assertEqual(self.journal.get(attempt.attempt_id)["state"], "response_received")

    def test_same_url_bytes_and_time_cannot_publish_different_response_title(self):
        attempt = self.prepare()
        receipt = self.journal.dispatch(attempt, self.journal.reserve(attempt))
        context = ContextStore(self.database, self.root / "archive")
        context.configure({**CONTEXT_DEFAULT, "enabled": True})
        changed = {**self.source, "title": "Synthetic altered source provenance"}
        saved = context.capture(receipt["query"], receipt["at"], [changed], 3)["sources"][0]
        reference = {name: saved[name] for name in ("capture_id", "content_hash", "manifest_hash", "retrieved_at")}
        with self.assertRaises(ValueError):
            self.journal.publish(attempt, [reference], context)
        self.assertEqual(self.journal.get(attempt.attempt_id)["state"], "response_received")

    def test_chat_deletion_during_transport_cannot_restore_private_acquisition_data(self):
        attempt = self.prepare(query="Synthetic private chat acquisition terms")
        booking = self.journal.reserve(attempt)
        entered, release = threading.Event(), threading.Event()

        def blocked_response(key, query, max_results):
            entered.set()
            self.assertTrue(release.wait(5))
            return self.response(key, query, max_results)
        self.handler = blocked_response
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(self.journal.dispatch, attempt, booking)
            self.assertTrue(entered.wait(5))
            with self.store._transaction() as db:
                self.workspace["chats"] = []
                self.save_settings(db)
                self.store.delete_chat("chat", db)
            release.set()
            try:
                future.result(timeout=5)
            except ValueError:
                pass
        record = self.journal.get(attempt.attempt_id)
        self.assertIsNone(record["spec"])
        self.assertIsNone(record["receipt"])
        self.assertIsNone(record["refs"])
        self.assertEqual(len(self.ledger()), 1)
        self.assertEqual(len(self.requests), 1)

    def publish_response(self):
        attempt = self.prepare()
        receipt = self.journal.dispatch(attempt, self.journal.reserve(attempt))
        context = ContextStore(self.database, self.root / "archive")
        context.configure({**CONTEXT_DEFAULT, "enabled": True})
        saved = context.capture(receipt["query"], receipt["at"], receipt["sources"], 3)["sources"][0]
        reference = {name: saved[name] for name in ("capture_id", "content_hash", "manifest_hash", "retrieved_at")}
        return attempt, context, reference

    def test_publication_rechecks_memory_and_current_parent_limits(self):
        for change in ("memory", "limits"):
            with self.subTest(change=change):
                if change == "limits":
                    self.store.cancel(attempt.run_id)
                    self.clock += 6
                attempt, context, reference = self.publish_response()
                with self.store._transaction() as db:
                    if change == "memory":
                        self.workspace["chats"][0]["messages"] = [{"memory_ids": ["new-private-memory"]}]
                    else:
                        self.workspace["limits"]["max_tokens"] -= 1
                    self.save_settings(db)
                with self.assertRaises(ValueError):
                    self.journal.publish(attempt, [reference], context)
                self.assertEqual(self.journal.get(attempt.attempt_id)["state"], "response_received")
                self.workspace["chats"][0]["messages"] = []
                with self.store._transaction() as db:
                    self.save_settings(db)

    def test_delete_during_pin_read_fences_reuse_before_return(self):
        attempt, context, reference = self.publish_response()
        self.journal.publish(attempt, [reference], context)
        original = context.get_capture

        def delete_after_read(*args, **kwargs):
            source = original(*args, **kwargs)
            self.store.delete(attempt.run_id)
            return source
        with patch.object(context, "get_capture", side_effect=delete_after_read):
            with self.assertRaises(ValueError):
                self.journal.reusable(attempt, context, retrieved_after="2026-01-01T00:00:00+00:00")
        self.assertIsNone(self.journal.get(attempt.attempt_id)["refs"])


    def test_cancelled_owner_cannot_receive_late_transport_result(self):
        attempt = self.prepare()
        booking = self.journal.reserve(attempt)

        def cancel_before_response(key, query, max_results):
            self.store.cancel(attempt.run_id)
            return self.response(key, query, max_results)

        self.handler = cancel_before_response
        with self.assertRaises(ValueError):
            self.journal.dispatch(attempt, booking)
        record = self.journal.get(attempt.attempt_id)
        self.assertEqual(record["state"], "uncertain")
        self.assertIsNone(record["receipt"])
        self.assertEqual(len(self.requests), 1)
        self.assertEqual(self.ledger()[0]["status"], "uncertain")
        budget = self.store.get(attempt.run_id)["budget"]
        self.assertEqual(budget["reserved"]["hosted_searches"], 0)
        self.assertEqual(budget["consumed"]["hosted_searches"], 1)


if __name__ == "__main__":
    unittest.main()

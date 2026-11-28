"""One hosted gap acquisition through real ownership, capture and research stages."""
from contextlib import closing, contextmanager
from copy import deepcopy
from dataclasses import replace
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor
import json
import sqlite3
import threading
import unittest
from unittest.mock import patch

import httpx

from backend import app as backend
from backend.acquisition import AcquisitionJournal
from backend.web_search import ENDPOINT, TARGETED_RESPONSE_BYTES, WebSearch
from tests import test_research_final_review_api as finals
from tests import test_research_evidence_api as evidence
from tests import test_acquisition_journal as journal_fixtures


class TargetedResearchAcquisitionTests(unittest.TestCase):
    def setUp(self):
        self.fixture = finals.ResearchFinalReviewApiTests(
            "test_supported_answer_uses_four_owned_calls_and_preserves_original_draft")
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.setUp()
        self.client, self.headers = self.fixture.client, self.fixture.headers
        self.evidence = self.fixture.fixture.fixture
        self.requirements = self.evidence.fixture
        self.archive_fixture = self.evidence.archive_fixture
        self.archive_fixture.keys[ENDPOINT] = "synthetic-targeted-search-secret"
        self.source = {"title": "Synthetic Acme annual disclosure", "url": "https://reports.example.org/acme",
                       "content": "Unrelated public padding. " * 100 + evidence.HISTORICAL}
        self.requests, self.search_hook = [], None
        self.enterContext(patch("backend.web_search.targeted_search_response", side_effect=self.response))
        config = self.client.get("/api/context").json()["config"]
        response = self.client.put("/api/context", json={**config, "enabled": True}, headers=self.headers)
        self.assertEqual(response.status_code, 200, response.text)
        self.search = WebSearch(backend.DATABASE, backend.TIMEZONE)
        with self.search.transaction() as state:
            state.update(config={"enabled": True, "daily_limit": 20, "max_results": 3},
                         tested_at=datetime.now(backend.TIMEZONE).isoformat())

    async def response(self, key, query, max_results, limits):
        self.requests.append({"query": query, "max_results": max_results, "limits": deepcopy(limits)})
        if self.search_hook:
            self.search_hook()
        return httpx.Response(200), json.dumps({"results": [self.source]}).encode()

    def payload(self, **changes):
        return self.requirements.payload(**{"workflow": "research_answer", "source_mode": "research", **changes})

    def submit(self, payload=None, **changes):
        response = self.client.post("/api/research/runs", json=payload or self.payload(**changes), headers=self.headers)
        self.assertEqual(response.status_code, 202, response.text)
        return self.requirements.get(response.json()["run"]["id"])

    @staticmethod
    def kinds(run):
        return [event["kind"] for event in run["events"]]

    def assert_released(self, run, calls, searches):
        self.assertEqual(run["budget"]["consumed"]["model_calls"], calls)
        self.assertEqual(run["budget"]["consumed"]["hosted_searches"], searches)
        self.assertTrue(all(value == 0 for value in run["budget"]["reserved"].values()))

    def test_empty_archive_acquires_full_excerpt_before_review_and_reuses_it_on_repeat(self):
        run = self.submit()
        self.assertEqual((run["state"], run["final_review"]["outcome"]), ("accepted", "accepted"))
        self.assertEqual(len(self.requests), 1)
        self.assertEqual(self.requests[0]["limits"]["max_response_bytes"], TARGETED_RESPONSE_BYTES)
        self.assertIn("acquisition_saved", self.kinds(run))
        self.assert_released(run, 4, 1)
        self.assertEqual(run["budget"]["daily_search"]["used"], 1)
        source = run["evidence_review"]["sources"][0]
        saved = backend.context_store().get_capture(source["capture_id"], source["content_hash"], source["manifest_hash"])
        self.assertEqual(saved["content"], self.source["content"])
        self.assertGreater(run["draft"]["items"][0]["supports"][0]["start"], 1200)
        self.assertFalse(saved["completeness"]["full_page"])
        repeated = self.submit()
        self.assertEqual(repeated["state"], "accepted")
        self.assertEqual(repeated["evidence_review"]["sources"][0]["capture_id"], source["capture_id"])
        self.assertEqual(len(self.requests), 1)
        self.assert_released(repeated, 4, 0)

    def test_successful_capture_survives_invalid_writer(self):
        self.fixture.fixture.writer_builder = lambda selected: {"invalid": True}
        run = self.submit()
        self.assertEqual(run["state"], "failed")
        self.assertIsNone(run["draft"])
        self.assertIn("acquisition_saved", self.kinds(run))
        captures = backend.context_store().list_captures()["sources"]
        self.assertEqual(len(captures), 1)
        source = captures[0]
        self.assertEqual(backend.context_store().get_capture(source["capture_id"], source["content_hash"],
                                                            source["manifest_hash"])["content"], self.source["content"])
        self.assert_released(run, 3, 1)

    def test_saved_only_and_disabled_saving_do_not_spend_a_search(self):
        run = self.submit(source_mode="saved_only")
        self.assertEqual(run["state"], "partial")
        self.assertNotIn("acquisition_started", self.kinds(run))
        self.assert_released(run, 1, 0)
        config = self.client.get("/api/context").json()["config"]
        self.client.put("/api/context", json={**config, "enabled": False}, headers=self.headers)
        run = self.submit()
        self.assertIn("acquisition_not_saved", self.kinds(run))
        self.assertEqual(run["state"], "partial")
        self.assertEqual(self.requests, [])
        self.assert_released(run, 1, 0)

    def test_failed_capture_keeps_gap_and_actual_search_usage_without_fake_pin(self):
        with patch.object(backend.ContextStore, "capture", return_value={"sources": []}):
            run = self.submit()
        self.assertEqual(run["state"], "partial")
        self.assertIn("acquisition_not_saved", self.kinds(run))
        self.assertEqual(run["evidence_review"]["sources"], [])
        self.assertEqual(len(self.requests), 1)
        self.assert_released(run, 1, 1)

    def test_partial_saved_review_protects_reassessment_and_finishing_calls_before_search(self):
        self.evidence.capture()
        self.evidence.raw["items"].append({**deepcopy(self.evidence.raw["items"][0]), "id": "i2", "criterion": "operating profit"})
        self.evidence.review_builder = lambda selected: {"version": 2, "items": {
            "i1": {"state": "covered", "reason": evidence.REASON, "supports": [self.evidence.support(selected)]},
            "i2": {"state": "missing", "reason": "No operating profit evidence.", "supports": []}}}
        run = self.submit(request="Compare Acme revenue and operating profit in USD for FY2025.")
        self.assertIn("acquisition_unavailable", self.kinds(run))
        self.assertEqual(self.requests, [])
        self.assertEqual(run["budget"]["consumed"]["hosted_searches"], 0)

    def test_known_oversized_objective_and_schema_stop_before_hosted_spend(self):
        for count in (4, 8, 16):
            with self.subTest(items=count), self.isolated_case() as case:
                with backend.workspace_transaction() as state:
                    state["limits"]["max_tokens"] = 20000
                case.evidence.raw["items"] = [{**deepcopy(case.evidence.raw["items"][0]), "id": f"i{index}"}
                                              for index in range(1, count + 1)]
                run = case.submit()
                self.assertEqual(run["state"], "partial")
                self.assertIn("acquisition_unavailable", self.kinds(run))
                self.assertEqual(case.requests, [])
                self.assert_released(run, 1, 0)

    def test_daily_cap_is_shared_with_legacy_search_and_connection_tests(self):
        with self.search.transaction() as state:
            state["config"]["daily_limit"] = 1
            state["ledger"] = [{"id": "1" * 32, "at": datetime.now(backend.TIMEZONE).isoformat(), "status": "completed"}]
        run = self.submit()
        self.assertEqual(run["state"], "partial")
        self.assertEqual(self.requests, [])
        self.assert_released(run, 1, 0)
        self.assertEqual(self.search.summary()["searches_today"], 1)

    def test_mode_is_restricted_and_submission_identity_includes_opt_in(self):
        invalid = self.client.post("/api/research/runs", json=self.payload(workflow="requirements"), headers=self.headers)
        self.assertEqual(invalid.status_code, 422)
        payload = self.payload(source_mode="saved_only")
        self.submit(payload)
        repeated = self.client.post("/api/research/runs", json={**payload, "source_mode": "research"}, headers=self.headers)
        self.assertEqual(repeated.status_code, 409)
        self.assertEqual(self.requests, [])

    def test_memory_derived_history_cannot_opt_into_hosted_research(self):
        chat = self.archive_fixture.chat()
        with backend.workspace_transaction() as state:
            state["chats"][0]["messages"].append({"id": "synthetic-message", "role": "assistant", "text": "Private recall",
                                                   "memory_ids": ["synthetic-memory"], "at": backend.now()})
        response = self.client.post("/api/research/runs", json=self.payload(chat_id=chat), headers=self.headers)
        self.assertEqual(response.status_code, 422)
        self.assertEqual(self.requests, [])

    def test_cancellation_after_dispatch_withholds_capture_and_prevents_replay(self):
        def cancel():
            with closing(sqlite3.connect(backend.DATABASE)) as db:
                run_id = db.execute("SELECT id FROM research_runs WHERE state='running'").fetchone()[0]
            response = self.client.post("/api/research/runs/" + run_id + "/cancel", headers=self.headers)
            self.assertEqual(response.status_code, 200)
        self.search_hook = cancel
        run = self.submit()
        self.assertEqual(run["state"], "cancelled")
        self.assertEqual(len(self.requests), 1)
        self.assertEqual(backend.context_store().list_captures()["sources"], [])
        self.assert_released(run, 1, 1)

    @contextmanager
    def isolated_case(self):
        case = type(self)(self._testMethodName)
        try:
            case.setUp()
            yield case
        finally:
            case.doCleanups()

    def terminal_request(self, kind, run_id, chat):
        if kind == "cancel":
            return self.client.post("/api/research/runs/" + run_id + "/cancel", headers=self.headers)
        path = "/api/chats/" + chat if kind == "chat_delete" else "/api/research/runs/" + run_id
        return self.client.delete(path, headers=self.headers)

    def source_counts(self):
        with closing(sqlite3.connect(backend.DATABASE)) as db:
            queries = db.execute("SELECT COUNT(*) FROM context_queries").fetchone()[0]
        return len(backend.context_store().list_captures()["sources"]), queries

    def test_terminal_transition_wins_before_capture_without_late_source_or_query_writes(self):
        for kind in ("cancel", "run_delete", "chat_delete"):
            with self.subTest(kind=kind), self.isolated_case() as case:
                received, proceed = threading.Event(), threading.Event()
                original = AcquisitionJournal.dispatch
                chat = case.archive_fixture.chat()

                def after_response(journal, *args):
                    receipt = original(journal, *args)
                    received.set()
                    if not proceed.wait(5):
                        raise RuntimeError("Synthetic capture gate timed out.")
                    return receipt

                with patch.object(AcquisitionJournal, "dispatch", new=after_response), ThreadPoolExecutor(max_workers=1) as pool:
                    future = pool.submit(case.client.post, "/api/research/runs", json=case.payload(chat_id=chat), headers=case.headers)
                    try:
                        self.assertTrue(received.wait(5))
                        with closing(sqlite3.connect(backend.DATABASE)) as db:
                            run_id = db.execute("SELECT id FROM research_runs WHERE state='running'").fetchone()[0]
                        response = case.terminal_request(kind, run_id, chat)
                        self.assertEqual(response.status_code, 200, response.text)
                        self.assertEqual(case.source_counts(), (0, 0))
                    finally:
                        proceed.set()
                    self.assertEqual(future.result(timeout=5).status_code, 202)
                self.assertEqual(case.source_counts(), (0, 0))
                self.assertEqual(len(case.requests), 1)

    def test_capture_wins_and_terminal_transition_waits_for_publication(self):
        for kind in ("cancel", "run_delete", "chat_delete"):
            with self.subTest(kind=kind), self.isolated_case() as case:
                capturing, proceed, attempted, finished = (threading.Event() for _ in range(4))
                original = backend.ContextStore.capture
                chat = case.archive_fixture.chat()

                def delayed_capture(archive, *args, **kwargs):
                    capturing.set()
                    if not proceed.wait(5):
                        raise RuntimeError("Synthetic publication gate timed out.")
                    return original(archive, *args, **kwargs)

                def terminal(run_id):
                    attempted.set()
                    response = case.terminal_request(kind, run_id, chat)
                    finished.set()
                    return response

                with patch.object(backend.ContextStore, "capture", new=delayed_capture), ThreadPoolExecutor(max_workers=2) as pool:
                    work = pool.submit(case.client.post, "/api/research/runs", json=case.payload(chat_id=chat), headers=case.headers)
                    try:
                        self.assertTrue(capturing.wait(5))
                        with closing(sqlite3.connect(backend.DATABASE)) as db:
                            run_id = db.execute("SELECT id FROM research_runs WHERE state='running'").fetchone()[0]
                        stop = pool.submit(terminal, run_id)
                        self.assertTrue(attempted.wait(5))
                        self.assertFalse(finished.wait(0.1))
                    finally:
                        proceed.set()
                    response = stop.result(timeout=5)
                    self.assertEqual(response.status_code, 200, response.text)
                    at_terminal = case.source_counts()
                    self.assertEqual(work.result(timeout=5).status_code, 202)
                self.assertEqual(at_terminal, (1, 1))
                self.assertEqual(case.source_counts(), at_terminal)


class TargetedAcquisitionTransportTests(unittest.TestCase):
    def setUp(self):
        self.fixture = journal_fixtures.AcquisitionJournalTests(
            "test_dispatch_occurs_once_and_outside_database_write_lock")
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.setUp()
        self.limits = {"max_response_bytes": TARGETED_RESPONSE_BYTES,
                       "max_source_bytes": 256 * 1024, "max_capture_bytes": 768 * 1024}
        self.enterContext(patch("backend.web_search.targeted_search_response", side_effect=self.response))

    async def response(self, key, query, max_results, limits):
        self.assertEqual(limits, self.limits)
        return await self.fixture.call_response(key, query, max_results)

    def prepare(self):
        return self.fixture.prepare(limits=self.limits)

    def test_full_maximum_escaped_source_survives_transport_and_journal_screening(self):
        for text in ("a\n" * (128 * 1024), 'a"' * (128 * 1024), "a\\" * (128 * 1024)):
            with self.subTest(kind=text[:2]):
                f = self.fixture
                f.source = {**f.source, "content": text}
                attempt = self.prepare()
                receipt = f.journal.dispatch(attempt, f.journal.reserve(attempt))
                self.assertEqual(receipt["sources"][0]["content"], text)
                self.assertEqual(f.journal.get(attempt.attempt_id)["state"], "response_received")
                f.store.cancel(attempt.run_id)
                f.clock += 6

    def test_source_batch_and_response_limits_refuse_whole_input_and_keep_usage(self):
        f = self.fixture
        cases = ("source", "batch", "response")
        for case in cases:
            with self.subTest(case=case):
                if case == "source":
                    f.source = {**f.source, "content": "x" * (self.limits["max_source_bytes"] + 1)}
                    f.handler = f.response
                elif case == "batch":
                    self.limits["max_capture_bytes"] = self.limits["max_source_bytes"]
                    f.source = {**f.source, "content": "x" * (self.limits["max_source_bytes"] // 2 + 1)}
                    f.handler = lambda *args: (httpx.Response(200), json.dumps({"results": [f.source, f.source]}).encode())
                else:
                    f.handler = lambda *args: (httpx.Response(200), b" " * (TARGETED_RESPONSE_BYTES + 1))
                attempt = self.prepare()
                with self.assertRaises(ValueError):
                    f.journal.dispatch(attempt, f.journal.reserve(attempt))
                record = f.journal.get(attempt.attempt_id)
                self.assertEqual(record["state"], "failed")
                self.assertIsNone(record["receipt"])
                budget = f.store.get(attempt.run_id)["budget"]
                self.assertEqual((budget["consumed"]["hosted_searches"], budget["reserved"]["hosted_searches"]), (1, 0))
                f.store.cancel(attempt.run_id)
                f.clock += 6

    def test_material_limits_are_bound_to_attempt_identity_and_booking(self):
        f = self.fixture
        attempt = self.prepare()
        booking = f.journal.reserve(attempt)
        record = f.journal.get(attempt.attempt_id)
        self.assertEqual(record["spec"]["limits"], self.limits)
        altered = replace(booking, limits={**self.limits, "max_source_bytes": 8192})
        with self.assertRaises(ValueError):
            f.journal.dispatch(attempt, altered)
        self.assertEqual(f.requests, [])
        self.assertEqual(f.journal.get(attempt.attempt_id)["state"], "pending")
        receipt = f.journal.dispatch(attempt, booking)
        self.assertEqual(receipt["sources"][0]["content"], f.source["content"])

    def test_delayed_booking_cannot_dispatch_after_reassessment_capacity_is_spent(self):
        f = self.fixture
        attempt = self.prepare()
        booking = f.journal.reserve(attempt)
        f.clock += 81
        with self.assertRaises(ValueError):
            f.journal.dispatch(attempt, booking)
        self.assertEqual(f.requests, [])
        self.assertEqual(f.journal.get(attempt.attempt_id)["state"], "failed")
        self.assertEqual(f.ledger()[0]["status"], "cancelled")
        budget = f.store.get(attempt.run_id)["budget"]
        self.assertEqual((budget["consumed"]["hosted_searches"], budget["reserved"]["hosted_searches"]), (1, 0))

    def test_key_rotation_after_transport_screen_cannot_publish_targeted_body(self):
        f = self.fixture
        current = [f.key]
        self.enterContext(patch("backend.credentials.read", side_effect=lambda endpoint: (current[0], "memory")))
        rotated = "synthetic-rotated-targeted-secret"
        f.source = {**f.source, "content": "Public padding. " * 12000 + rotated}
        original = WebSearch._response

        def rotate(service, *args):
            result = original(service, *args)
            current[0] = rotated
            return result

        attempt = self.prepare()
        with patch.object(WebSearch, "_response", new=rotate):
            with self.assertRaises(ValueError):
                f.journal.dispatch(attempt, f.journal.reserve(attempt))
        record = f.journal.get(attempt.attempt_id)
        self.assertEqual(record["state"], "failed")
        self.assertIsNone(record["receipt"])
        self.assertNotIn(rotated, f.database.read_bytes().decode("utf-8", errors="ignore"))


if __name__ == "__main__":
    unittest.main()

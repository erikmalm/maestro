"""Review regressions at acquisition response, elapsed-time and policy boundaries."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from copy import deepcopy
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import threading
import unittest
from unittest.mock import patch
import uuid

import httpx

from backend.context_store import ContextStore, DEFAULT as CONTEXT_DEFAULT
from backend.web_search import ENDPOINT, WebSearch
from tests import test_acquisition_journal as fixtures


class AcquisitionReviewRegressionTests(unittest.TestCase):
    def setUp(self):
        # Delegate setup without inheriting and rediscovering the fixture's tests.
        self.fixture = fixtures.AcquisitionJournalTests(
            "test_dispatch_occurs_once_and_outside_database_write_lock")
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.setUp()

    def test_remote_provider_configuration_cannot_prepare_or_book_acquisition(self):
        f = self.fixture
        remote_key = "synthetic-remote-provider-secret"
        f.provider["config"].update(protocol="responses", base_url="https://api.openai.com/v1")
        with f.store._transaction() as db:
            f.save_settings(db)
        f.source = {**f.source, "url": "https://reports.example.org/" + remote_key}
        self.enterContext(patch("backend.credentials.read", side_effect=lambda endpoint: (
            f.key if endpoint == ENDPOINT else remote_key, "memory")))
        run_id, token = f.create_run()
        with self.assertRaises(ValueError):
            f.prepare(run_id, token)
        self.assertEqual(f.requests, [])
        self.assertEqual(f.ledger(), [])
        self.assertEqual(f.store.get(run_id)["budget"]["reserved"]["hosted_searches"], 0)
        with closing(sqlite3.connect(f.database)) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM acquisition_attempts").fetchone()[0], 0)

    def _assert_late_credential_rotation_refused(self, *, field="url", encoding="raw"):
        f = self.fixture
        current = [f.key]
        self.enterContext(patch("backend.credentials.read", side_effect=lambda endpoint: (current[0], "memory")))
        attempt = f.prepare()
        booking = f.journal.reserve(attempt)
        rotated = "synthetic-new-secret-after-response"
        encoded = rotated
        if encoding == "percent":
            encoded = "".join("%" + format(ord(char), "02X") for char in rotated)
        elif encoding == "json":
            encoded = "".join("\\u" + format(ord(char), "04x") for char in rotated)
        prefix = "https://reports.example.org/" if field == "url" else "Synthetic response content: "
        f.source = {**f.source, field: prefix + encoded}
        if field == "content":
            self.assertNotIn(rotated, f.source["url"])
            self.assertNotIn(rotated, f.source["title"])
        original = WebSearch._response

        def rotate_after_screening(service, *args, **kwargs):
            receipt = original(service, *args, **kwargs)
            current[0] = rotated
            return receipt

        with patch.object(WebSearch, "_response", new=rotate_after_screening):
            with self.assertRaises(ValueError):
                f.journal.dispatch(attempt, booking)
        record = f.journal.get(attempt.attempt_id)
        self.assertEqual(record["state"], "failed")
        self.assertIsNone(record["receipt"])
        self.assertNotIn(rotated, json.dumps(record))
        self.assertNotIn(encoded, json.dumps(record))
        self.assertNotIn(rotated, f.database.read_bytes().decode("utf-8", errors="ignore"))
        self.assertEqual(len(f.requests), 1)
        self.assertEqual(len(f.ledger()), 1)
        self.assertEqual(f.ledger()[0]["status"], "completed")
        budget = f.store.get(attempt.run_id)["budget"]
        self.assertEqual(budget["consumed"]["hosted_searches"], 1)
        self.assertEqual(budget["reserved"]["hosted_searches"], 0)
        with self.assertRaises(ValueError):
            f.journal.dispatch(attempt, booking)
        self.assertEqual(len(f.requests), 1)

    def test_credential_rotated_after_response_screening_is_never_saved_or_returned(self):
        self._assert_late_credential_rotation_refused()

    def test_late_credential_rotation_rejects_a_secret_present_only_in_response_content(self):
        self._assert_late_credential_rotation_refused(field="content")

    def test_late_credential_rotation_rejects_percent_encoded_response_content(self):
        self._assert_late_credential_rotation_refused(field="content", encoding="percent")

    def test_late_credential_rotation_rejects_json_escaped_response_content(self):
        self._assert_late_credential_rotation_refused(field="content", encoding="json")

    def _book_with_finishing(self, *, held):
        f = self.fixture
        run_id, token = f.create_run(hosted=False)
        if held:
            stage = f.prepare_stage(run_id, token)
            request_id = uuid.uuid4().hex
            with f.store._transaction() as db:
                f.store.reserve_stage(stage, f.workspace, f.provider, request_id, 64, 64, db)
            with f.store._transaction() as db:
                self.assertTrue(f.store.settle_stage(
                    stage, request_id, db=db, input_tokens=32, output_tokens=16,
                    output={"synthetic": "requirements completed"},
                    workspace=f.workspace, provider_state=f.provider))
            holds = self._holds(run_id)
            self.assertEqual(set(holds), {"writer", "reviewer"})
            self.assertEqual(sum(value["seconds"] for value in holds.values()), 40)
        else:
            holds = None

        # Only the private future-hosted fixture changes admission after an
        # ordinary saved-only stage has established genuine finishing holds.
        def hosted(payload):
            payload["permissions"].update(source_mode="research", hosted_allowed=True)
            payload["budget"]["total"]["hosted_searches"] = 1
            payload["budget"]["remaining"]["hosted_searches"] = 1
        f.edit_run(run_id, hosted)
        attempt = f.prepare(run_id, token)
        booking = f.journal.reserve(attempt)
        self.assertEqual(f.store.get(run_id)["budget"]["reserved"]["seconds"], 70 if held else 30)
        return attempt, booking, holds

    def _holds(self, run_id):
        with closing(sqlite3.connect(self.fixture.database)) as db:
            row = db.execute("SELECT held FROM research_finishing WHERE run_id=?", (run_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def _assert_delayed_dispatch_refused(self, *, held):
        f = self.fixture
        attempt, booking, holds = self._book_with_finishing(held=held)
        f.clock += 60
        with self.assertRaises(ValueError):
            f.journal.dispatch(attempt, booking)
        self.assertEqual(f.requests, [])
        self.assertEqual(f.journal.get(attempt.attempt_id)["state"], "failed")
        self.assertEqual(len(f.ledger()), 1)
        self.assertEqual(f.ledger()[0]["status"], "cancelled")
        self.assertEqual(self._holds(attempt.run_id), holds)
        budget = f.store.get(attempt.run_id)["budget"]
        self.assertEqual(budget["reserved"]["seconds"], 40 if held else 0)
        self.assertEqual(budget["reserved"]["hosted_searches"], 0)
        self.assertEqual(budget["consumed"]["hosted_searches"], 1)
        with self.assertRaises(ValueError):
            f.journal.dispatch(attempt, booking)
        self.assertEqual(f.store.get(attempt.run_id)["budget"]["consumed"]["hosted_searches"], 1)
        self.assertEqual(f.requests, [])

    def test_elapsed_time_cannot_spend_an_existing_finishing_hold(self):
        self._assert_delayed_dispatch_refused(held=True)

    def test_elapsed_time_also_preserves_finishing_capacity_without_a_hold(self):
        self._assert_delayed_dispatch_refused(held=False)

    def test_delayed_dispatch_allows_an_exact_fit_with_real_finishing_holds(self):
        f = self.fixture
        attempt, booking, holds = self._book_with_finishing(held=True)
        f.clock += 50
        receipt = f.journal.dispatch(attempt, booking)
        self.assertEqual(receipt["sources"], [f.source])
        self.assertEqual(len(f.requests), 1)
        self.assertEqual(f.ledger()[0]["status"], "completed")
        self.assertEqual(f.journal.get(attempt.attempt_id)["state"], "response_received")
        self.assertEqual(self._holds(attempt.run_id), holds)
        budget = f.store.get(attempt.run_id)["budget"]
        self.assertEqual(budget["reserved"]["seconds"], 40)
        self.assertEqual(budget["consumed"]["hosted_searches"], 1)

    def _two_source_response(self):
        f = self.fixture
        other = {"title": "Synthetic Acme second disclosure", "url": "https://other.example.org/acme",
                 "content": "Acme reports the same synthetic FY2025 revenue of USD 42 million."}
        f.handler = lambda *args: (httpx.Response(200), json.dumps({"results": [f.source, other]}).encode())
        attempt = f.prepare()
        receipt = f.journal.dispatch(attempt, f.journal.reserve(attempt))
        context = ContextStore(f.database, f.root / "archive")
        context.configure({**CONTEXT_DEFAULT, "enabled": True})
        saved = context.capture(receipt["query"], receipt["at"], receipt["sources"], 3)["sources"]
        references = [{name: source[name] for name in
                       ("capture_id", "content_hash", "manifest_hash", "retrieved_at")} for source in saved]
        self.assertEqual(len(references), 2)
        return attempt, context, references, other

    @staticmethod
    def _configure_in_process(context, config):
        # A separate interpreter cannot share ContextStore instance locks,
        # Python globals or monkey patches with the pin-reading operation.
        script = ("import json, sys; from backend.context_store import ContextStore; "
                  "ContextStore(sys.argv[1], sys.argv[2]).configure(json.loads(sys.argv[3]))")
        subprocess.run([sys.executable, "-c", script, str(context.database), str(context.archive), json.dumps(config)],
                       cwd=Path(__file__).resolve().parents[1], check=True, capture_output=True, text=True, timeout=10)

    def _policy_race(self, *, operation, disable=False, aba=False, same_config=False,
                     legacy=False, child_process=False):
        f = self.fixture
        attempt, context, references, other = self._two_source_response()
        if operation == "reuse":
            f.journal.publish(attempt, references, context)
        before = f.journal.get(attempt.attempt_id)
        both = {**CONTEXT_DEFAULT, "enabled": True, "capture_policy": "approved_sources",
                "public_sources": [f.source["url"], other["url"]]}
        initial = both if disable or aba or same_config else {**both, "public_sources": [f.source["url"]]}
        changed = {**both, "enabled": False} if disable else {**both, "public_sources": [other["url"]]}
        if same_config:
            changed = deepcopy(initial)
        context.configure(initial)
        if legacy:
            with context._transaction() as (state, _):
                state.pop("policy_generation", None)
            self.assertIsNone(context.policy_generation())
        another = ContextStore(f.database, f.root / "archive")
        entered, release, configuration_started = threading.Event(), threading.Event(), threading.Event()
        original = context.get_capture
        pause_at = references[-1 if disable else 0]["capture_id"]
        paused = []

        def paused_read(*args, **kwargs):
            source = original(*args, **kwargs)
            if args[0] == pause_at and not paused:
                paused.append(True)
                entered.set()
                self.assertTrue(release.wait(5), "pin-read release was not signalled")
            return source

        def invoke():
            try:
                if operation == "publish":
                    return "accepted", f.journal.publish(attempt, references, context)
                return "accepted", f.journal.reusable(
                    attempt, context, retrieved_after="2026-01-01T00:00:00+00:00")
            except (ValueError, KeyError):
                return "refused", None

        def reconfigure():
            configuration_started.set()
            result = (self._configure_in_process(another, changed) if child_process
                      else another.configure(deepcopy(changed)))
            if aba:
                result = another.configure(deepcopy(initial))
            return result

        with patch.object(context, "get_capture", side_effect=paused_read), ThreadPoolExecutor(max_workers=2) as pool:
            pending = pool.submit(invoke)
            try:
                self.assertTrue(entered.wait(5), "operation did not reach the saved-source pin read")
                configuration = pool.submit(reconfigure)
                self.assertTrue(configuration_started.wait(5))
                configuration.result(timeout=5)
            finally:
                release.set()
            result, returned = pending.result(timeout=5)
            configuration.result(timeout=5)

        self.assertEqual(result, "refused", "a policy change must fence publication and return of pins")
        self.assertIsNone(returned)
        self.assertEqual(f.journal.get(attempt.attempt_id), before)
        self.assertEqual(another._state()["config"]["enabled"], changed["enabled"])

    def test_publication_never_combines_pins_eligible_under_different_policies(self):
        self._policy_race(operation="publish", disable=False)

    def test_reuse_never_combines_pins_eligible_under_different_policies(self):
        self._policy_race(operation="reuse", disable=False)

    def test_publication_fences_archive_disable_after_the_last_pin_read(self):
        self._policy_race(operation="publish", disable=True)

    def test_reuse_fences_archive_disable_after_the_last_pin_read(self):
        self._policy_race(operation="reuse", disable=True)

    def test_publication_fences_policy_change_even_when_original_configuration_is_restored(self):
        self._policy_race(operation="publish", aba=True)

    def test_reuse_fences_policy_change_even_when_original_configuration_is_restored(self):
        self._policy_race(operation="reuse", aba=True)

    def test_publication_fences_archive_disable_from_an_independent_process(self):
        self._policy_race(operation="publish", disable=True, child_process=True)

    def test_publication_fences_the_first_configuration_save_of_a_legacy_archive(self):
        self._policy_race(operation="publish", same_config=True, legacy=True)

    def test_policy_generation_supports_legacy_state_and_identical_configuration_saves(self):
        f = self.fixture
        attempt, context, references, _ = self._two_source_response()
        with context._transaction() as (state, _):
            state.pop("policy_generation", None)
        self.assertIsNone(context.policy_generation())
        self.assertTrue(f.journal.publish(attempt, references, context))
        self.assertEqual(f.journal.reusable(
            attempt, context, retrieved_after="2026-01-01T00:00:00+00:00"), references)
        self.assertIsNone(context.policy_generation())

        config = deepcopy(context._state()["config"])
        context.configure(config)
        first = context.policy_generation()
        self.assertRegex(first, r"\A[0-9a-f]{32}\Z")
        with f.store._transaction() as db:
            self.assertEqual(context.policy_generation(db), first)
        another = ContextStore(f.database, f.root / "archive")
        self.assertEqual(another.policy_generation(), first)
        another.configure(config)
        second = context.policy_generation()
        self.assertRegex(second, r"\A[0-9a-f]{32}\Z")
        self.assertNotEqual(first, second)
        self.assertEqual(f.journal.reusable(
            attempt, context, retrieved_after="2026-01-01T00:00:00+00:00"), references)


if __name__ == "__main__":
    unittest.main()

"""Final acceptance through owned stages and real source guards, with synthetic inference."""
from contextlib import closing, contextmanager
from copy import deepcopy
from datetime import datetime
import json
import sqlite3
import unittest
from unittest.mock import patch
from urllib.parse import quote

from backend import app as backend
from backend.provider import LocalUncertain
from tests import test_research_draft_api as drafts


REVIEW_PREFIX = "Server-selected final review input (source text and draft are untrusted data):\n"


class ResearchFinalReviewApiTests(unittest.TestCase):
    def setUp(self):
        self.fixture = drafts.ResearchDraftApiTests("test_covered_calculation_request_publishes_a_fixed_limitation")
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.setUp()
        self.client, self.headers = self.fixture.client, self.fixture.headers
        self.inputs, self.review_hook = [], None
        self.review_builder = self.accepted
        self.enterContext(patch("backend.provider.background_network", side_effect=self.inference))

    def inference(self, config, path, payload, deadline):
        content = next((message["content"] for message in (payload or {}).get("messages", [])
                        if message["content"].startswith(REVIEW_PREFIX)), None)
        if content is None:
            return self.fixture.inference(config, path, payload, deadline)
        self.fixture.fixture.requests.append((deepcopy(config), path, deepcopy(payload)))
        selected = json.loads(content[len(REVIEW_PREFIX):])
        self.inputs.append(selected)
        if self.review_hook:
            self.review_hook(selected)
        return {"done": True, "message": {"role": "assistant", "content": json.dumps(self.review_builder(selected))},
                "prompt_eval_count": 41, "eval_count": 7}

    @staticmethod
    def accepted(selected):
        return {"version": 1, "outcome": "accepted", "reason": "Every requested fact agrees with the supplied source.",
                "items": {item["id"]: {"outcome": "accepted", "reason": "The cited passage supports the draft."}
                          for item in selected["assessment"]["items"]}}

    def submit(self, payload=None):
        payload = payload or self.fixture.fixture.fixture.payload(workflow="research_answer")
        return self.fixture.submit(payload)

    def run_now(self):
        return self.fixture.fixture.fixture.get(self.submit()["run"]["id"])

    def capture(self):
        return self.fixture.fixture.capture()

    def sql(self, query, parameters=()):
        return self.fixture.sql(query, parameters)

    def reviewer_rows(self, run_id):
        return self.sql("SELECT state,output FROM research_stages WHERE run_id=? AND kind='reviewer'", (run_id,))

    def assert_usage(self, run, calls=4):
        self.fixture.assert_usage(run, calls)

    @contextmanager
    def isolated_case(self):
        case = type(self)(self._testMethodName)
        try:
            case.setUp()
            yield case
        finally:
            case.doCleanups()

    def test_supported_answer_uses_four_owned_calls_and_preserves_original_draft(self):
        self.capture()
        run = self.run_now()
        self.assertEqual((run["state"], run["final_review"]["outcome"]), ("accepted", "accepted"))
        self.assertEqual(run["final_review"]["basis"], "semantic")
        self.assertEqual(run["draft"]["state"], "unreviewed")
        self.assertEqual(self.inputs[0]["draft"], run["draft"])
        self.assertEqual(self.inputs[0]["coverage"], run["evidence_review"])
        self.assertEqual(self.inputs[0]["original_request"], run["request"])
        self.assertIn("reviewer_started", [event["kind"] for event in run["events"]])
        self.assertEqual(run["events"][-1]["kind"], "answer_accepted")
        self.assertEqual(self.fixture.fixture.fixture.get(run["id"]), run)
        history = self.client.get("/api/research/runs").json()["runs"][0]
        self.assertIsNone(history["final_review"])
        self.assertNotEqual(history["state"], "accepted")
        self.assertTrue(all("tools" not in call[2] for call in self.fixture.fixture.fixture.chats()))
        self.assert_usage(run)

    def test_semantic_writing_and_evidence_findings_prevent_full_acceptance(self):
        for outcome in ("writing_defect", "evidence_gap", "partial", "unverifiable"):
            with self.subTest(outcome=outcome), self.isolated_case() as case:
                case.capture()
                def finding(selected):
                    raw = case.accepted(selected)
                    raw.update(outcome=outcome, reason="The draft changes the requested reporting period.")
                    raw["items"]["i1"].update(outcome="writing_defect", reason="FY2024 is not the requested FY2025.")
                    return raw
                case.review_builder = finding
                run = case.run_now()
                self.assertEqual((run["state"], run["final_review"]["outcome"]), ("partial", outcome))
                self.assertEqual(run["final_review"]["items"][0]["outcome"], "writing_defect")
                self.assertEqual(run["draft"]["state"], "unreviewed")
                case.assert_usage(run)

    def test_calculation_gap_cannot_be_approved_even_by_a_positive_reviewer(self):
        self.capture()
        self.fixture.writer_builder = lambda selected: {"version": 2, "items": {"i1": {
            "kind": "gap", "text": "", "support_indexes": [], "gap_reason": "calculation_required"}}}
        run = self.run_now()
        self.assertEqual(run["state"], "failed")
        self.assertIsNone(run["final_review"])
        self.assertEqual(run["draft"]["items"][0]["gap_reason"], "calculation_required")
        self.assertEqual(self.reviewer_rows(run["id"]), [("failed", None)])
        self.assert_usage(run)

    def test_no_evidence_finishes_unverifiable_without_writer_or_reviewer_calls(self):
        run = self.run_now()
        self.assertEqual((run["state"], run["final_review"]["basis"], run["final_review"]["outcome"]),
                         ("partial", "not_run", "unverifiable"))
        self.assertEqual(run["final_review"]["reason_code"], "no_supported_draft")
        self.assertEqual(self.inputs, [])
        self.assert_usage(run, 1)

    def test_exhausted_review_time_retains_unreviewed_draft_and_releases_finishing_holds(self):
        self.capture()
        finish = backend.ResearchStore.finish_draft
        def finish_then_advance(store, run_id, token, draft, **options):
            completed = finish(store, run_id, token, draft, **options)
            deadline = datetime.fromisoformat(store.get(run_id)["budget"]["deadline"]).timestamp()
            self.enterContext(patch.object(backend.ResearchStore, "clock", return_value=deadline - 0.5))
            return completed
        with patch.object(backend.ResearchStore, "finish_draft", new=finish_then_advance):
            run = self.run_now()
        self.assertEqual(run["state"], "partial")
        self.assertEqual(run["final_review"]["reason_code"], "review_capacity")
        self.assertEqual(run["draft"]["state"], "unreviewed")
        self.assertEqual(self.inputs, [])
        self.assertEqual(self.reviewer_rows(run["id"]), [("failed", None)])
        self.assert_usage(run, 3)

    def completed_review_after(self, seconds):
        self.capture()
        def advance(selected):
            run_id = self.fixture.current_id()
            started = self.sql("SELECT updated_at FROM research_stages WHERE run_id=? AND kind='reviewer'",
                               (run_id,))[0][0]
            finished = datetime.fromisoformat(started).timestamp() + seconds
            self.enterContext(patch.object(backend.ResearchStore, "clock", return_value=finished))
        self.review_hook = advance
        return self.run_now()

    def test_final_review_can_finish_after_sixty_seconds_within_parent_allowance(self):
        run = self.completed_review_after(60)
        self.assertEqual(run["state"], "accepted")
        self.assertEqual(run["final_review"]["outcome"], "accepted")
        self.assertEqual(run["budget"]["total"]["seconds"], 120)
        self.assertEqual(run["budget"]["total"]["model_calls"], 4)
        saved = json.loads(self.sql("SELECT spec FROM research_stages WHERE run_id=? AND kind='reviewer'",
                                    (run["id"],))[0][0])
        self.assertGreater(saved["seconds"], 60)
        self.assertLessEqual(saved["seconds"], 90)
        self.assert_usage(run)

    def test_final_review_cannot_accept_a_reply_after_parent_deadline(self):
        run = self.completed_review_after(121)
        self.assertEqual(run["state"], "failed")
        self.assertIsNone(run["final_review"])
        self.assertEqual(run["draft"]["state"], "unreviewed")
        self.assertEqual(run["budget"]["total"]["seconds"], 120)
        self.assertEqual(run["budget"]["consumed"]["seconds"], 120)
        self.assertEqual(self.reviewer_rows(run["id"]), [("failed", None)])
        self.assert_usage(run)

    def test_late_reviewer_reply_cannot_cross_cancellation_deletion_settings_or_restart(self):
        for action, expected in (("cancel", "cancelled"), ("delete", None), ("settings", "failed"), ("recover", "interrupted")):
            with self.subTest(action=action), self.isolated_case() as case:
                case.capture()
                def change(selected):
                    store, run_id = backend.research_store(), case.fixture.current_id()
                    if action == "settings":
                        with backend.provider().transaction() as state:
                            state["config"]["max_output_tokens"] += 1
                    elif action == "recover":
                        store.recover()
                    else:
                        getattr(store, action)(run_id)
                case.review_hook = change
                run_id = case.submit()["run"]["id"]
                response = case.client.get("/api/research/runs/" + run_id)
                if expected is None:
                    self.assertEqual(response.status_code, 404)
                else:
                    run = response.json()
                    self.assertEqual(run["state"], expected)
                    self.assertIsNone(run["final_review"])
                    case.assert_usage(run)
                self.assertTrue(all(row[1] is None for row in case.reviewer_rows(run_id)))
                entry = backend.provider().read_state()["ledger"][-1]
                self.assertEqual((entry["status"], entry["input_tokens"], entry["output_tokens"]), ("settled", 41, 7))
                case.fixture.fixture.fixture.assert_released()

    def test_unknown_review_completion_retains_reservation_and_is_never_replayed(self):
        self.capture()
        def unknown(selected):
            raise LocalUncertain("Synthetic reviewer completion unknown.", False)
        self.review_hook = unknown
        payload = self.fixture.fixture.fixture.payload(workflow="research_answer")
        run_id = self.submit(payload)["run"]["id"]
        run = self.fixture.fixture.fixture.get(run_id)
        self.assertEqual(run["state"], "failed")
        self.assertIsNone(run["final_review"])
        self.assertGreater(run["budget"]["reserved"]["tokens"], 0)
        self.assertEqual(run["budget"]["consumed"]["model_calls"], 3)
        self.assertEqual(self.reviewer_rows(run_id), [("uncertain", None)])
        self.assertFalse(self.submit(payload)["created"])
        backend.execute_research_run(run_id)
        self.assertEqual(len(self.inputs), 1)

    def test_escaped_initial_credential_stays_blocked_after_rotation_during_review(self):
        self.capture()
        initial = "synthetic-final-review/+?&key"
        escaped = quote(initial, safe="")
        current = {"key": initial}
        self.assertNotEqual(escaped, initial)
        self.review_hook = lambda selected: current.update(key="synthetic-final-review-rotated-key")

        def escaped_output(selected):
            raw = self.accepted(selected)
            raw["reason"] = escaped
            return raw

        self.review_builder = escaped_output
        with patch("backend.credentials.read", side_effect=lambda endpoint: (current["key"], "synthetic")):
            run = self.run_now()
        self.assertEqual(run["state"], "failed")
        self.assertIsNone(run["final_review"])
        self.assertEqual(run["draft"]["state"], "unreviewed")
        self.assertEqual(self.reviewer_rows(run["id"]), [("failed", None)])
        self.assertNotIn(initial, json.dumps(run))
        self.assertNotIn(escaped, json.dumps(run))
        entry = backend.provider().read_state()["ledger"][-1]
        self.assertEqual((entry["status"], entry["input_tokens"], entry["output_tokens"]), ("settled", 41, 7))
        self.assert_usage(run)

    def test_withdrawn_source_blocks_publication_and_withdraws_earlier_acceptance_on_read(self):
        for timing in ("during_review", "after_acceptance"):
            with self.subTest(timing=timing), self.isolated_case() as case:
                source = case.capture()
                if timing == "during_review":
                    case.review_hook = lambda selected: case.fixture.revoke(source)
                run = case.run_now()
                before = backend.research_store().get(run["id"])
                if timing == "during_review":
                    self.assertEqual(run["state"], "failed")
                    self.assertIsNone(run["final_review"])
                    self.assertEqual(case.reviewer_rows(run["id"]), [("failed", None)])
                else:
                    self.assertEqual(run["state"], "accepted")
                    case.fixture.revoke(source)
                    run = case.fixture.fixture.fixture.get(run["id"])
                    self.assertEqual(run["state"], "partial")
                    self.assertEqual(run["final_review"]["reason_code"], "source_unavailable")
                self.assertNotIn(drafts.DRAFT_TEXT, json.dumps(run))
                self.assertEqual(backend.research_store().get(run["id"]), before)
                case.assert_usage(run)

    def test_finalizer_rejects_changed_findings_or_original_draft_binding(self):
        self.capture()
        finish = backend.ResearchStore.finish_final_review
        def publish(store, run_id, token, final, *, stage=None, secrets=()):
            changed = deepcopy(final)
            changed["reason"] = "A different supported-looking approval."
            with self.assertRaisesRegex(ValueError, "differs from the owned reviewer response"):
                finish(store, run_id, token, changed, stage=stage, secrets=secrets)
            original = self.sql("SELECT spec FROM research_stages WHERE id=?", (stage.stage_id,))[0][0]
            for field in ("draft_hash", "assessment_hash"):
                with self.subTest(binding=field):
                    spec = {**json.loads(original), field: "f" * 64}
                    try:
                        with closing(sqlite3.connect(backend.DATABASE)) as db, db:
                            db.execute("UPDATE research_stages SET spec=? WHERE id=?", (json.dumps(spec), stage.stage_id))
                        with self.assertRaisesRegex(ValueError, "differs from the owned stage"):
                            finish(store, run_id, token, final, stage=stage, secrets=secrets)
                    finally:
                        with closing(sqlite3.connect(backend.DATABASE)) as db, db:
                            db.execute("UPDATE research_stages SET spec=? WHERE id=?", (original, stage.stage_id))
            return finish(store, run_id, token, final, stage=stage, secrets=secrets)
        with patch.object(backend.ResearchStore, "finish_final_review", new=publish):
            run = self.run_now()
        self.assertEqual(run["state"], "accepted")
        self.assert_usage(run)


if __name__ == "__main__":
    unittest.main()

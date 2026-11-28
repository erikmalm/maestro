"""One owned correction cycle through real stages and disposable saved evidence."""
from contextlib import closing, contextmanager
from copy import deepcopy
from datetime import datetime
import json
import sqlite3
import unittest
from unittest.mock import patch

import httpx

from backend import app as backend
from backend.acquisition import AcquisitionJournal, gap_query
from backend.research_final_review import evidence_fingerprint
from backend.provider import LocalUncertain
from backend.web_search import ENDPOINT, WebSearch
from tests import test_research_final_review_api as finals
from tests import test_research_evidence_api as evidence


BAD = "Acme reported FY2025 revenue of USD 100."
GOOD = "Acme reported FY2025 revenue of USD 10."
PROFIT = "Acme FY2025 operating profit in USD was 3."


class ResearchContinuationTests(unittest.TestCase):
    def setUp(self):
        self.fixture = finals.ResearchFinalReviewApiTests(
            "test_supported_answer_uses_four_owned_calls_and_preserves_original_draft")
        self.fixture.legacy_allowance = False
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.setUp()
        self.client, self.headers = self.fixture.client, self.fixture.headers
        self.drafts = self.fixture.fixture
        self.evidence = self.drafts.fixture
        self.requirements = self.evidence.fixture
        self.archive_fixture = self.evidence.archive_fixture
        self.fixture.capture()
        self.drafts.writer_builder = self.writer
        self.fixture.review_builder = self.reviewer
        self.corrected = True
        self.searches = []

    def writer(self, selected):
        text = GOOD if "correction" in selected and self.corrected else BAD
        return {"version": 2, "items": {"i1": {"kind": "fact", "text": text, "support_indexes": [0], "gap_reason": None}}}

    def reviewer(self, selected):
        raw = self.fixture.accepted(selected)
        if selected["draft"]["items"][0]["text"] == BAD:
            raw.update(outcome="writing_defect", reason="The draft changes the source amount.")
            raw["items"]["i1"].update(outcome="writing_defect", reason="100 must be 10.")
        return raw

    def payload(self, **changes):
        return self.requirements.payload(**{"workflow": "research_answer", **changes})

    def submit(self, **changes):
        response = self.client.post("/api/research/runs", json=self.payload(**changes), headers=self.headers)
        self.assertEqual(response.status_code, 202, response.text)
        return self.requirements.get(response.json()["run"]["id"])

    @staticmethod
    def kinds(run):
        return [event["kind"] for event in run["events"]]

    def assert_usage(self, run, calls, searches=0):
        self.assertEqual(run["budget"]["consumed"]["model_calls"], calls)
        self.assertEqual(run["budget"]["consumed"]["hosted_searches"], searches)
        self.assertEqual(run["budget"]["total"]["model_calls"], 7)
        self.assertEqual(run["budget"]["total"]["seconds"], 180)
        self.assertTrue(all(amount == 0 for amount in run["budget"]["reserved"].values()))
        self.assertLessEqual(len(run["events"]), 32)

    @contextmanager
    def isolated(self):
        case = type(self)(self._testMethodName)
        try:
            case.setUp()
            yield case
        finally:
            case.doCleanups()

    def test_one_corrected_answer_is_accepted_with_original_budget_and_raw_artifacts(self):
        visible = []
        def inspect(selected):
            if "correction" in selected:
                run = self.client.get("/api/research/runs/" + self.drafts.current_id()).json()
                visible.append(run)
                self.assertEqual(run["draft"]["items"][0]["text"], BAD)
                self.assertEqual(run["final_review"]["outcome"], "writing_defect")
                self.assertEqual(selected["correction"]["review"], run["final_review"])
        self.drafts.writer_hook = inspect
        run = self.submit()
        self.assertEqual(run["state"], "accepted")
        self.assertEqual(run["draft"]["items"][0]["text"], GOOD)
        self.assertEqual((run["revision"]["count"], run["revision"]["action"]), (1, "rewrite"))
        self.assertEqual(len(visible), 1)
        rows = self.fixture.sql("SELECT kind,json_extract(spec,'$.revision'),output FROM research_stages WHERE run_id=?", (run["id"],))
        self.assertEqual([(kind, revision) for kind, revision, _ in rows if kind == "writer"], [("writer", 0), ("writer", 1)])
        self.assertTrue(all(output for _, _, output in rows))
        self.assert_usage(run, 6)

    def test_unchanged_rewrite_keeps_prior_rejection_and_does_not_review_it_again(self):
        self.corrected = False
        run = self.submit()
        self.assertEqual(run["state"], "partial")
        self.assertEqual(run["final_review"]["outcome"], "writing_defect")
        self.assertEqual(run["draft"]["items"][0]["text"], BAD)
        self.assertIn("revision_no_progress", self.kinds(run))
        self.assertEqual(len(self.fixture.inputs), 1)
        self.assert_usage(run, 5)

    def two_item_rewrite(self, change_target):
        self.evidence.capture(evidence.HISTORICAL + " " + PROFIT)
        self.evidence.raw["items"].append({**deepcopy(self.evidence.raw["items"][0]),
                                           "id": "i2", "criterion": "operating profit"})
        self.evidence.review_builder = lambda selected: {"version": 2, "items": {item["id"]: {
            "state": "covered", "reason": "The exact quote supplies this fact.",
            "supports": [self.evidence.support(selected, PROFIT if item["id"] == "i2" else evidence.HISTORICAL)]}
            for item in selected["assessment"]["items"]}}
        def writer(selected):
            correction = "correction" in selected
            return {"version": 2, "items": {
                "i1": {"kind": "fact", "text": GOOD if correction and change_target else BAD,
                       "support_indexes": [0], "gap_reason": None},
                "i2": {"kind": "fact", "text": "Acme reported FY2025 operating profit of USD 3." if correction else PROFIT,
                       "support_indexes": [0], "gap_reason": None}}}
        self.drafts.writer_builder = writer
        return self.submit(request="Compare Acme revenue and operating profit in USD for FY2025.")

    def test_paraphrasing_an_accepted_item_does_not_progress_an_unchanged_rejected_item(self):
        run = self.two_item_rewrite(change_target=False)
        self.assert_usage(run, 5)
        self.assertEqual(run["state"], "partial")
        self.assertEqual(run["revision"]["item_ids"], ["i1"])
        self.assertEqual(run["draft"]["items"][0]["text"], BAD)
        self.assertEqual(run["draft"]["items"][1]["text"], PROFIT)
        self.assertEqual(run["final_review"]["outcome"], "writing_defect")
        self.assertEqual(len(self.fixture.inputs), 1)
        self.assertIn("revision_no_progress", self.kinds(run))

    def test_changing_the_named_rejected_item_still_receives_final_review(self):
        run = self.two_item_rewrite(change_target=True)
        self.assert_usage(run, 6)
        self.assertEqual(run["state"], "accepted")
        self.assertEqual(run["draft"]["items"][0]["text"], GOOD)
        self.assertEqual(len(self.fixture.inputs), 2)

    def test_second_rejection_stops_after_one_cycle(self):
        self.fixture.review_builder = lambda selected: {"version": 1, "outcome": "writing_defect", "reason": "Still incorrect.",
            "items": {"i1": {"outcome": "writing_defect", "reason": "Still incorrect."}}}
        run = self.submit()
        self.assertEqual(run["state"], "partial")
        self.assertEqual(run["revision"]["count"], 1)
        self.assert_usage(run, 6)

    def test_global_only_and_calculation_findings_cannot_start_correction(self):
        for calculation in (False, True):
            with self.subTest(calculation=calculation), self.isolated() as case:
                if calculation:
                    case.drafts.writer_builder = lambda selected: {"version": 2, "items": {"i1": {
                        "kind": "gap", "text": "", "support_indexes": [], "gap_reason": "calculation_required"}}}
                    case.fixture.review_builder = lambda selected: {"version": 1, "outcome": "unverifiable", "reason": "Needs calculation.",
                        "items": {"i1": {"outcome": "unverifiable", "reason": "Needs calculation."}}}
                else:
                    case.fixture.review_builder = lambda selected: {**case.fixture.accepted(selected), "outcome": "writing_defect"}
                run = case.submit()
                self.assertEqual(run["state"], "partial")
                self.assertEqual(run["revision"]["count"], 0)
                self.assertIn("revision_stopped", self.kinds(run))
                case.assert_usage(run, 4)

    def test_capacity_exhaustion_preserves_rejection_without_dispatch(self):
        original = backend.ResearchStore.finish_final_review
        clock = {"now": None}
        def now():
            return clock["now"] if clock["now"] is not None else datetime.now().timestamp()
        def finish(store, run_id, token, review, **kwargs):
            clock["now"] = datetime.fromisoformat(store.get(run_id)["created_at"]).timestamp() + 145
            return original(store, run_id, token, review, **kwargs)
        with patch.object(backend.ResearchStore, "clock", side_effect=now), patch.object(backend.ResearchStore, "finish_final_review", new=finish):
            run = self.submit()
        self.assertEqual(run["state"], "partial")
        self.assertIn("revision_capacity", self.kinds(run))
        self.assertEqual(run["revision"]["count"], 0)
        self.assert_usage(run, 4)

    def test_new_revision_no_call_stop_ignores_older_completed_writer(self):
        original = backend.execute_draft_writer
        clock = {"now": None}
        def now():
            return clock["now"] if clock["now"] is not None else datetime.now().timestamp()
        def write(run, token, store):
            if run["revision"]["count"]:
                clock["now"] = datetime.fromisoformat(run["created_at"]).timestamp() + 179
            return original(run, token, store)
        with patch.object(backend.ResearchStore, "clock", side_effect=now), patch.object(backend, "execute_draft_writer", new=write):
            run = self.submit()
        self.assertEqual(run["state"], "partial")
        self.assertEqual(run["final_review"]["outcome"], "writing_defect")
        self.assertIn("revision_capacity", self.kinds(run))
        self.assert_usage(run, 4)

    def test_cancellation_after_claim_stops_before_new_inference(self):
        original = backend.ResearchStore.finish_final_review
        def finish(store, run_id, token, review, **kwargs):
            result = original(store, run_id, token, review, **kwargs)
            if store.get(run_id)["revision"]["count"]:
                store.cancel(run_id)
            return result
        with patch.object(backend.ResearchStore, "finish_final_review", new=finish):
            run = self.submit()
        self.assertEqual(run["state"], "cancelled")
        self.assert_usage(run, 4)

    def test_source_withdrawal_before_correction_withholds_old_prose_and_new_dispatch(self):
        original = backend.execute_draft_writer
        def write(run, token, store):
            if run["revision"]["count"]:
                source = run["evidence_review"]["sources"][0]
                path = self.evidence.manifest(source)
                manifest = json.loads(path.read_text(encoding="utf-8"))
                path.write_text(json.dumps({**manifest, "eligible": False}), encoding="utf-8")
            return original(run, token, store)
        with patch.object(backend, "execute_draft_writer", new=write):
            run = self.submit()
        self.assertEqual(run["state"], "failed")
        self.assertEqual(run["draft"]["state"], "unavailable")
        self.assert_usage(run, 4)
        self.assertTrue(self.fixture.sql("SELECT output FROM research_stages WHERE run_id=? AND kind='writer' "
                                        "AND json_extract(spec,'$.revision')=0", (run["id"],))[0][0])

    def test_unknown_correction_keeps_original_artifacts_and_never_replays(self):
        def unknown(selected):
            if "correction" in selected:
                raise LocalUncertain("Synthetic correction completion unknown.", False)
        self.drafts.writer_hook = unknown
        payload = self.payload()
        response = self.client.post("/api/research/runs", json=payload, headers=self.headers)
        self.assertEqual(response.status_code, 202, response.text)
        run = self.requirements.get(response.json()["run"]["id"])
        self.assertEqual(run["state"], "failed")
        self.assertGreater(run["budget"]["reserved"]["tokens"], 0)
        self.assertEqual(run["budget"]["consumed"]["model_calls"], 4)
        rows = self.fixture.sql("SELECT json_extract(spec,'$.revision'),state,output FROM research_stages "
                                "WHERE run_id=? AND kind='writer'", (run["id"],))
        self.assertEqual([(revision, state) for revision, state, _ in rows], [(0, "completed"), (1, "uncertain")])
        self.assertTrue(rows[0][2])
        repeated = self.client.post("/api/research/runs", json=payload, headers=self.headers)
        self.assertFalse(repeated.json()["created"])
        backend.execute_research_run(run["id"])
        self.assertEqual(len(self.drafts.writer_inputs), 2)

    def test_settings_change_during_correction_withholds_new_output(self):
        def change(selected):
            if "correction" in selected:
                with backend.provider().transaction() as state:
                    state["config"]["max_output_tokens"] += 1
        self.drafts.writer_hook = change
        run = self.submit()
        self.assertEqual(run["state"], "failed")
        self.assertEqual(run["draft"]["items"][0]["text"], BAD)
        self.assertEqual(run["final_review"]["outcome"], "writing_defect")
        self.assert_usage(run, 5)

    def test_deletion_after_claim_purges_private_work_before_new_dispatch(self):
        original = backend.ResearchStore.finish_final_review
        def finish(store, run_id, token, review, **kwargs):
            result = original(store, run_id, token, review, **kwargs)
            if store.get(run_id)["revision"]["count"]:
                store.delete(run_id)
            return result
        with patch.object(backend.ResearchStore, "finish_final_review", new=finish):
            response = self.client.post("/api/research/runs", json=self.payload(), headers=self.headers)
        run_id = response.json()["run"]["id"]
        self.assertEqual(self.client.get("/api/research/runs/" + run_id).status_code, 404)
        self.assertEqual(len(self.drafts.writer_inputs), 1)
        self.assertTrue(all(output is None for (output,) in self.fixture.sql("SELECT output FROM research_stages WHERE run_id=?", (run_id,))))
        self.assertEqual(len(backend.context_store().list_captures()["sources"]), 1)

    def test_old_captured_run_keeps_four_calls_and_120_seconds_without_correction(self):
        budget, create = backend.research_budget, backend.ResearchStore.create
        def legacy_create(store, *args, **kwargs):
            return create(store, *args, **{**kwargs, "continuation": False})
        with patch.object(backend, "research_budget", side_effect=lambda state, source_mode="saved_only", workflow="lookup":
                          budget(state, source_mode)), patch.object(backend.ResearchStore, "create", new=legacy_create):
            run = self.submit()
        self.assertEqual((run["budget"]["total"]["model_calls"], run["budget"]["total"]["seconds"]), (4, 120))
        self.assertEqual(run["revision"]["limit"], 0)
        self.assertEqual(run["budget"]["consumed"]["model_calls"], 4)
        self.assertNotIn("revision_started", self.kinds(run))

    @contextmanager
    def acquisition_case(self, helpful=True, defer=True):
        self.archive_fixture.keys[ENDPOINT] = "synthetic-continuation-search-secret"
        search = WebSearch(backend.DATABASE, backend.TIMEZONE)
        with search.transaction() as state:
            state.update(config={"enabled": True, "daily_limit": 20, "max_results": 3},
                         tested_at=datetime.now(backend.TIMEZONE).isoformat())
        self.evidence.raw["items"].append({**deepcopy(self.evidence.raw["items"][0]), "id": "i2", "criterion": "operating profit"})
        def coverage(selected):
            rows = {}
            for item in selected["assessment"]["items"]:
                quote = PROFIT if item["id"] == "i2" else evidence.HISTORICAL
                found = any(quote in passage["text"] for passage in selected["packet"]["passages"])
                rows[item["id"]] = {"state": "covered" if found else "missing", "reason": "Synthetic exact quote coverage.",
                                     "supports": [self.evidence.support(selected, quote)] if found else []}
            return {"version": 2, "items": rows}
        self.evidence.review_builder = coverage
        self.drafts.writer_builder = lambda selected: {"version": 2, "items": {row["item_id"]: {
            "kind": "fact", "text": PROFIT if row["item_id"] == "i2" else GOOD,
            "support_indexes": [0], "gap_reason": None} for row in selected["coverage"]["items"] if row["state"] == "covered"}}
        def review(selected):
            raw = self.fixture.accepted(selected)
            for row in selected["coverage"]["items"]:
                if row["state"] != "covered":
                    raw.update(outcome="evidence_gap", reason="A mandatory fact remains missing.")
                    raw["items"][row["item_id"]].update(outcome="evidence_gap", reason="Need operating profit evidence.")
            return raw
        self.fixture.review_builder = review
        async def response(key, query, max_results, limits):
            self.searches.append(query)
            return httpx.Response(200), json.dumps({"results": [{"title": "Synthetic Acme extra disclosure",
                "url": "https://reports.example.org/acme-extra", "content": evidence.HISTORICAL + " " +
                    (PROFIT if helpful else "This source has no operating profit figure.")}]}).encode()
        original = backend.execute_targeted_acquisition
        def after_review(run, token, store, **kwargs):
            # Exercise the permitted review-to-acquisition transition. The
            # initial pass deliberately uses only its saved packet.
            if run["revision"]["count"] or not defer:
                return original(run, token, store, **kwargs)
        with patch("backend.web_search.targeted_search_response", side_effect=response), patch.object(backend, "execute_targeted_acquisition", new=after_review):
            yield

    def test_one_useful_acquisition_replaces_dependent_artifacts_atomically(self):
        original = backend.ResearchStore.finish_evidence_review
        replaced = []
        def finish(store, run_id, token, review, **kwargs):
            result = original(store, run_id, token, review, **kwargs)
            current = store.get(run_id)
            if current["revision"]["count"]:
                replaced.append(current)
                self.assertIsNone(current["draft"])
                self.assertIsNone(current["final_review"])
            return result
        with self.acquisition_case(), patch.object(backend.ResearchStore, "finish_evidence_review", new=finish):
            run = self.submit(request="Compare Acme revenue and operating profit in USD for FY2025.", source_mode="research")
        self.assertEqual(run["state"], "accepted")
        self.assertEqual(run["revision"]["action"], "acquire")
        self.assertEqual(len(replaced), 1)
        self.assertEqual(len(self.searches), 1)
        self.assert_usage(run, 7, 1)

    def test_initial_acquisition_then_writing_correction_fits_seven_calls_and_event_cap(self):
        with self.acquisition_case(defer=False):
            def writer(selected):
                corrected = "correction" in selected
                return {"version": 2, "items": {row["item_id"]: {"kind": "fact",
                    "text": PROFIT if row["item_id"] == "i2" else GOOD if corrected else BAD,
                    "support_indexes": [0], "gap_reason": None} for row in selected["coverage"]["items"] if row["state"] == "covered"}}
            self.drafts.writer_builder = writer
            self.fixture.review_builder = self.reviewer
            run = self.submit(request="Compare Acme revenue and operating profit in USD for FY2025.", source_mode="research")
        self.assertEqual(run["state"], "accepted")
        self.assertEqual(run["revision"]["action"], "rewrite")
        self.assertEqual(len(self.searches), 1)
        self.assert_usage(run, 7, 1)

    def test_unhelpful_acquisition_stops_with_previous_rejection_and_retains_capture(self):
        with self.acquisition_case(helpful=False):
            run = self.submit(request="Compare Acme revenue and operating profit in USD for FY2025.", source_mode="research")
        self.assertEqual(run["state"], "partial")
        self.assertEqual(run["final_review"]["outcome"], "evidence_gap")
        self.assertIn("revision_no_progress", self.kinds(run))
        self.assertEqual(len(backend.context_store().list_captures()["sources"]), 2)
        self.assert_usage(run, 5, 1)

    def test_equivalent_prepared_query_is_not_retried_by_continuation(self):
        def prepare(selected):
            run_id = self.drafts.current_id()
            store = backend.research_store()
            with closing(sqlite3.connect(backend.DATABASE)) as db:
                token = db.execute("SELECT claim_token FROM research_runs WHERE id=?", (run_id,)).fetchone()[0]
            run = store.get(run_id)
            item_id, query = gap_query(run["assessment"], ["i2"])
            AcquisitionJournal(store).prepare(run_id, token, item_ids=[item_id], query=query)
        self.fixture.review_hook = prepare
        with self.acquisition_case():
            run = self.submit(request="Compare Acme revenue and operating profit in USD for FY2025.", source_mode="research")
        self.assertEqual(run["revision"]["count"], 0)
        self.assertIn("revision_no_progress", self.kinds(run))
        self.assertEqual(self.searches, [])
        self.assert_usage(run, 4)

    def test_evidence_fingerprint_ignores_only_new_capture_ids_and_retrieval_dates(self):
        self.corrected = False
        run = self.submit()
        original = run["evidence_review"]
        changed = deepcopy(original)
        changed["checked_at"] = "2026-11-28T10:00:00+01:00"
        for source in changed["sources"]:
            source.update(capture_id="f" * 32, retrieved_at="2026-11-28T10:00:00+01:00")
        self.assertEqual(evidence_fingerprint(original), evidence_fingerprint(changed))


if __name__ == "__main__":
    unittest.main()

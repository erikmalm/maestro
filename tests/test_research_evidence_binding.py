"""Exact owned packet binding and private read guards with disposable archives."""
from contextlib import closing
from copy import deepcopy
import hashlib
import json
import sqlite3
import unittest
from unittest.mock import patch

from backend import app as backend
from backend.research_evidence import validate_review
from backend.research_passages import evidence_packet_hash, select_packet
from tests import test_research_evidence_api as fixtures


LOOKUP = {"state": "ready", "search_limited": False, "skipped_count": 0}


def reverse_keys(value):
    if isinstance(value, dict):
        return {key: reverse_keys(child) for key, child in reversed(list(value.items()))}
    if isinstance(value, list):
        return [reverse_keys(child) for child in value]
    return value


class ResearchEvidenceBindingTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.ResearchEvidenceApiTests(
            "test_historical_tail_support_uses_two_owned_calls_and_releases_all_holds")
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.setUp()
        self.finish = backend.ResearchStore.finish_evidence_review
        self.client, self.headers = self.fixture.client, self.fixture.headers

    def candidate(self, content=fixtures.HISTORICAL):
        return {**self.fixture.capture(content), "stale": True}

    def run_with(self, source, before_finish):
        def publish(store, run_id, token, review, *, stage=None, secrets=()):
            before_finish(store, run_id, token, review, stage, secrets)
            return self.finish(store, run_id, token, review, stage=stage, secrets=secrets)

        with (patch.object(backend, "_research_candidates", return_value=([source], deepcopy(LOOKUP))),
              patch.object(backend.ResearchStore, "finish_evidence_review", new=publish)):
            run = self.fixture.run_now()
        self.assertEqual(run["state"], "partial")
        self.assertEqual(run["evidence_review"]["items"][0]["state"], "covered")
        self.fixture.assert_usage(run)
        return run

    def stage_output(self, stage):
        with closing(sqlite3.connect(backend.DATABASE)) as db:
            output = db.execute("SELECT output FROM research_stages WHERE id=? AND state='completed'",
                                (stage.stage_id,)).fetchone()
        self.assertIsNotNone(output)
        return json.loads(output[0])

    def test_another_real_saved_source_cannot_replace_owned_selected_source(self):
        original = self.candidate()
        other = self.candidate("Different publisher context. " + fixtures.HISTORICAL)
        self.assertNotEqual(original["capture_id"], other["capture_id"])
        checked = []

        def attempt(store, run_id, token, review, stage, secrets):
            assessment = store.get(run_id)["assessment"]
            packet = select_packet([other], assessment, LOOKUP)
            # Both captures are real, eligible and hash-checked. Only the first
            # packet was supplied to this model stage.
            backend._verified_evidence_packet(packet, lambda value: value, secrets=secrets)
            replacement = validate_review(self.stage_output(stage), assessment, packet, review["checked_at"])
            self.assertNotEqual(evidence_packet_hash(packet), evidence_packet_hash(backend._review_packet(review)))
            with self.assertRaisesRegex(ValueError, "differs from the owned stage"):
                self.finish(store, run_id, token, replacement, stage=stage, secrets=secrets)
            checked.append(True)

        run = self.run_with(original, attempt)
        self.assertEqual(checked, [True])
        self.assertEqual(run["evidence_review"]["sources"][0]["capture_id"], original["capture_id"])

    def test_different_real_passage_in_same_capture_cannot_retarget_same_quote(self):
        content = fixtures.HISTORICAL + " Unrelated padding. " * 130 + fixtures.HISTORICAL
        original = self.candidate(content)
        checked = []

        def attempt(store, run_id, token, review, stage, secrets):
            assessment = store.get(run_id)["assessment"]
            support = review["items"][0]["supports"][0]
            first, last = content.find(fixtures.HISTORICAL), content.rfind(fixtures.HISTORICAL)
            self.assertNotEqual(first, last)
            alternate = last if support["start"] == first else first
            packet = deepcopy(backend._review_packet(review))
            source = next(source for source in packet["sources"] if source["id"] == support["source_id"])
            source["id"] = "c1"
            packet["sources"] = [source]
            packet["passages"] = [{"id": "p1", "source_id": "c1", "start": alternate,
                "end": alternate + len(fixtures.HISTORICAL), "text": fixtures.HISTORICAL,
                "passage_hash": hashlib.sha256(fixtures.HISTORICAL.encode()).hexdigest()}]
            backend._verified_evidence_packet(packet, lambda value: value, secrets=secrets)
            replacement = validate_review(self.stage_output(stage), assessment, packet, review["checked_at"])
            self.assertNotEqual(replacement["items"][0]["supports"][0]["start"], support["start"])
            with self.assertRaisesRegex(ValueError, "differs from the owned stage"):
                self.finish(store, run_id, token, replacement, stage=stage, secrets=secrets)
            checked.append(True)

        self.run_with(original, attempt)
        self.assertEqual(checked, [True])

    def test_intact_packet_publishes_with_dictionary_keys_reordered(self):
        source = self.candidate()
        checked = []

        def attempt(store, run_id, token, review, stage, secrets):
            packet = backend._review_packet(review)
            reordered = reverse_keys(packet)
            self.assertEqual(evidence_packet_hash(packet), evidence_packet_hash(reordered))
            with closing(sqlite3.connect(backend.DATABASE)) as db:
                spec = json.loads(db.execute("SELECT spec FROM research_stages WHERE id=?", (stage.stage_id,)).fetchone()[0])
            self.assertEqual(spec["packet_hash"], evidence_packet_hash(reordered))
            self.assertEqual(spec["evidence"], [{key: item[key] for key in
                ("capture_id", "content_hash", "manifest_hash", "retrieved_at", "stale")}
                for item in reordered["sources"]])
            # Dict ordering is not a provenance change. Use the reordered form
            # for the real finalization immediately after this callback.
            reordered_review = reverse_keys(deepcopy(review))
            review.clear()
            review.update(reordered_review)
            checked.append(True)

        self.run_with(source, attempt)
        self.assertEqual(checked, [True])

    def test_owned_stage_pin_projection_is_checked_in_addition_to_packet_hash(self):
        source = self.candidate()
        checked = []

        def attempt(store, run_id, token, review, stage, secrets):
            with closing(sqlite3.connect(backend.DATABASE)) as db:
                original = db.execute("SELECT spec FROM research_stages WHERE id=?", (stage.stage_id,)).fetchone()[0]
            changed = json.loads(original)
            changed["evidence"][0]["capture_id"] = "f" * 32
            self.assertEqual(changed["packet_hash"], evidence_packet_hash(backend._review_packet(review)))
            try:
                with closing(sqlite3.connect(backend.DATABASE)) as db, db:
                    db.execute("UPDATE research_stages SET spec=? WHERE id=?", (json.dumps(changed), stage.stage_id))
                with self.assertRaisesRegex(ValueError, "differs from the owned stage"):
                    self.finish(store, run_id, token, review, stage=stage, secrets=secrets)
            finally:
                with closing(sqlite3.connect(backend.DATABASE)) as db, db:
                    db.execute("UPDATE research_stages SET spec=? WHERE id=?", (original, stage.stage_id))
            checked.append(True)

        self.run_with(source, attempt)
        self.assertEqual(checked, [True])

    def test_legacy_lookup_cannot_export_fabricated_review_without_assessment(self):
        response = self.fixture.archive_fixture.submit()
        self.assertEqual(response.status_code, 202, response.text)
        run_id = response.json()["run"]["id"]
        marker = "UNCANONICAL_LOOKUP_REVIEW_EXPORT"
        with closing(sqlite3.connect(backend.DATABASE)) as db, db:
            payload = json.loads(db.execute("SELECT payload FROM research_runs WHERE id=?", (run_id,)).fetchone()[0])
            self.assertEqual(payload["workflow"], "lookup")
            self.assertIsNone(payload["assessment"])
            payload["evidence_review"] = {"version": 1, "offset_unit": "unicode_codepoint", "lookup": LOOKUP,
                "selection": {"limited": False, "omitted_count": 0}, "sources": [], "passages": [],
                "items": [{"answer": marker}]}
            db.execute("UPDATE research_runs SET payload=? WHERE id=?", (json.dumps(payload), run_id))
        detail = self.client.get("/api/research/runs/" + run_id)
        self.assertEqual(detail.status_code, 404, detail.text)
        self.assertNotIn(marker, detail.text)
        # Lists intentionally retain safe metadata and omit evidence bodies;
        # they must not export unvalidated coverage or fabricated review text.
        listed = self.client.get("/api/research/runs")
        self.assertEqual(listed.status_code, 200, listed.text)
        self.assertNotIn(marker, listed.text)
        self.assertTrue(all(run.get("evidence_review") is None for run in listed.json()["runs"]))


if __name__ == "__main__":
    unittest.main()

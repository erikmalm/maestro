"""Serialized credential checks at the owned-stage persistence boundary."""
from contextlib import closing
import json
import sqlite3
import unittest
import uuid

from tests import test_research_stage_safety as fixtures
from backend.web_search import ENDPOINT


class ResearchStageSecurityReviewTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.ResearchStageSafetyTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.key = "123456789012345678901234567890"

    def test_numeric_schema_credential_is_rejected_without_persisting_stage_or_scope(self):
        fixture = self.fixture
        fixture.keys[ENDPOINT] = self.key
        run, claim = fixture.new_run()
        before = fixture.database.read_bytes()
        schema = {"type": "object", "properties": {"account": {"type": "integer", "enum": [int(self.key)]}}}
        with self.assertRaises(ValueError):
            fixture.stage(run, claim, schema=schema)
        with closing(sqlite3.connect(fixture.database)) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM research_stages").fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM research_stage_scope").fetchone()[0], 0)
        self.assertEqual(fixture.database.read_bytes(), before)
        self.assertNotIn(self.key.encode(), fixture.database.read_bytes())
        self.assertEqual(fixture.requests, [])

    def test_numeric_output_credential_is_withheld_while_actual_usage_still_settles(self):
        fixture = self.fixture
        stage = fixture.stage()
        request_id = uuid.uuid4().hex
        with fixture.provider.transaction(with_db=True) as (provider_state, db):
            fixture.store.reserve_stage(stage, fixture.workspace, provider_state, request_id, 100, 50, db)
        fixture.keys[ENDPOINT] = self.key
        with fixture.provider.transaction(with_db=True) as (provider_state, db):
            accepted = fixture.store.settle_stage(stage, request_id, db=db,
                input_tokens=41, output_tokens=7, outcome="completed", output={"account": int(self.key)},
                workspace=fixture.workspace, provider_state=provider_state)
        self.assertFalse(accepted)
        with closing(sqlite3.connect(fixture.database)) as db:
            state, output, consumed = db.execute("SELECT state,output,consumed FROM research_stages WHERE id=?",
                                                 (stage.stage_id,)).fetchone()
        self.assertEqual(state, "failed")
        self.assertIsNone(output)
        self.assertEqual(json.loads(consumed)["tokens"], 48)
        self.assertEqual(json.loads(consumed)["model_calls"], 1)
        run = fixture.store.get(stage.run_id)
        self.assertEqual(run["budget"]["consumed"]["tokens"], 48)
        self.assertEqual(run["budget"]["consumed"]["model_calls"], 1)
        self.assertNotIn(self.key.encode(), fixture.database.read_bytes())
        self.assertEqual(fixture.requests, [])

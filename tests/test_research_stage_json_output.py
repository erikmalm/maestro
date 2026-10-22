"""Encoded credentials and ambiguous JSON stay outside owned-stage inference/results."""
from contextlib import closing
import json
import sqlite3
import unittest

from backend.web_search import ENDPOINT
from tests import test_research_stage_safety as fixtures


class ResearchJsonOutputTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.ResearchStageSafetyTests("test_forged_and_replayed_identity_cannot_dispatch_or_reset_allowance")
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.setUp()

    def rejected_output(self, reply):
        stage = self.fixture.stage()
        self.fixture.response["message"]["content"] = reply
        try:
            with self.assertRaises(ValueError) as rejected:
                self.fixture.generate(stage)
            entry = self.fixture.ledger()[-1]
            self.assertEqual((entry["status"], entry["input_tokens"], entry["output_tokens"], entry["model_calls"]),
                             ("settled", 41, 7, 1))
            budget = self.fixture.store.get(stage.run_id)["budget"]
            self.assertEqual(budget["consumed"]["tokens"], 48)
            self.assertEqual(budget["consumed"]["model_calls"], 1)
            with closing(sqlite3.connect(self.fixture.database)) as db:
                state, output, reserved = db.execute("SELECT state,output,reserved FROM research_stages WHERE id=?",
                                                    (stage.stage_id,)).fetchone()
            self.assertEqual(state, "failed")
            self.assertIsNone(output)
            self.assertEqual(json.loads(reserved)["tokens"], 0)
            return str(rejected.exception)
        finally:
            self.fixture.store.cancel(stage.run_id)

    def test_benign_duplicate_keys_are_rejected_at_every_object_depth(self):
        for reply in ('{"result":"first","result":"last"}',
                      '{"result":{"child":"first","child":"last"}}',
                      '{"result":[{"child":"first","child":"last"}]}',
                      r'{"result":"first","re\u0073ult":"last"}',
                      r'{"result":{"child":"first","\u0063hild":"last"}}'):
            with self.subTest(reply=reply):
                self.rejected_output(reply)

    def test_unicode_escaped_credentials_in_overwritten_fields_are_never_exported_or_saved(self):
        credential = "syntheticDuplicateCredential_83"
        self.fixture.keys[ENDPOINT] = credential
        escaped = "".join("\\u%04x" % ord(character) for character in credential)
        for reply in ('{"result":"' + escaped + '","result":"ok"}',
                      '{"result":[{"child":"' + escaped + '","child":"ok"}]}'):
            with self.subTest(nested="child" in reply):
                message = self.rejected_output(reply)
                self.assertNotIn(credential, message)
                self.assertNotIn(credential.encode(), self.fixture.database.read_bytes())
                self.assertNotIn(reply.encode(), self.fixture.database.read_bytes())

    def test_same_keys_in_different_objects_and_ordinary_escapes_remain_valid(self):
        stage = self.fixture.stage()
        reply = r'{"result":"snowman \u2603","items":[{"same":"a"},{"same":"b"}],"first":"same","second":"same"}'
        self.fixture.response["message"]["content"] = reply
        result = self.fixture.generate(stage)
        self.assertEqual(result["reply"], reply)
        with closing(sqlite3.connect(self.fixture.database)) as db:
            state, output = db.execute("SELECT state,output FROM research_stages WHERE id=?", (stage.stage_id,)).fetchone()
        self.assertEqual(state, "completed")
        self.assertEqual(json.loads(output), json.loads(reply))
        self.assertEqual(json.loads(output)["result"], "snowman \u2603")
        self.assertEqual(self.fixture.ledger()[-1]["status"], "settled")

    def test_nested_json_in_instructions_or_messages_cannot_dispatch(self):
        credential = 'synthetic"Prompt\\Credential_47'
        self.fixture.keys[ENDPOINT] = credential
        encoded = json.dumps({"metadata": json.dumps({"api_key": credential})})
        for field in ("instructions", "messages"):
            with self.subTest(field=field):
                stage = self.fixture.stage()
                before = self.fixture.store.get(stage.run_id)["budget"]
                try:
                    with self.assertRaises(ValueError):
                        self.fixture.provider.generate_research_stage(
                            stage, encoded if field == "instructions" else "Return the saved result.",
                            [{"role": "user", "content": encoded if field == "messages" else "Synthetic input."}],
                            max_output_tokens=128)
                    self.assertEqual(self.fixture.requests, [])
                    self.assertEqual(self.fixture.ledger(), [])
                    self.assertEqual(self.fixture.store.get(stage.run_id)["budget"], before)
                    self.assertNotIn(encoded.encode(), self.fixture.database.read_bytes())
                finally:
                    self.fixture.store.cancel(stage.run_id)

    def test_nested_json_output_screens_initial_and_rotated_credentials_and_settles_usage(self):
        initial = 'synthetic"Initial\\Credential_47'
        current = 'synthetic"Current\\Credential_47'
        self.fixture.chat_hook = lambda: self.fixture.keys.update({ENDPOINT: current})
        for credential in (initial, current):
            with self.subTest(initial=credential == initial):
                self.fixture.keys[ENDPOINT] = initial
                reply = json.dumps({"result": json.dumps({"metadata": json.dumps({"api_key": credential})})})
                message = self.rejected_output(reply)
                self.assertNotIn(credential, message)
                self.assertNotIn(credential.encode(), self.fixture.database.read_bytes())
                self.assertNotIn(reply.encode(), self.fixture.database.read_bytes())


if __name__ == "__main__":
    unittest.main()

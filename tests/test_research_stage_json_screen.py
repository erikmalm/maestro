"""JSON credential views remain private without rewriting stage content."""
from contextlib import closing
from copy import deepcopy
import html
import json
import sqlite3
import time
import unittest
from unittest.mock import patch
from urllib.parse import quote
import uuid

from backend import credentials
from backend.research_stages import safe_stage_content
from backend.web_search import ENDPOINT
from tests import test_research_stage_safety as fixtures


class ResearchStageJsonScreenTests(unittest.TestCase):
    @staticmethod
    def escaped_body(key):
        units = key.encode("utf-16-be")
        return "".join("\\u" + units[index:index + 2].hex() for index in range(0, len(units), 2))

    def test_malformed_quote_prefixes_cannot_hide_later_json_escape_runs(self):
        prefixes = ('"\\x\\', '"\\q\\', '"\\U\\', '"\\u12\\', '"\\uZZZZ\\',
                    '"\n\\', '"\r\\', '"\t\\', '"\x00\\', '"\x1f\\')
        for key in ('syntheticMalformedCredential_47', 'synthetic"private\\credential47'):
            literal = '"' + self.escaped_body(key) + '"'
            for prefix in prefixes:
                with self.subTest(key=key, prefix=repr(prefix)):
                    with patch.object(credentials, "read", return_value=(None, "synthetic")):
                        with self.assertRaises(ValueError):
                            safe_stage_content(prefix + literal, {"protocol": "ollama"}, secrets=(key,))

    def test_unquoted_json_escapes_and_surrogate_pairs_cannot_hide_credentials(self):
        for key in ('syntheticUnquotedCredential47', 'synthetic"private\\credential47',
                    'synthetic\U0001f600Credential47', r'synthetic\u0041Credential47'):
            value = "Metadata key: " + self.escaped_body(key)
            with self.subTest(key=key), patch.object(credentials, "read", return_value=(None, "synthetic")):
                with self.assertRaises(ValueError):
                    safe_stage_content(value, {"protocol": "ollama"}, secrets=(key,))

    def test_each_codec_intermediate_view_is_checked_before_a_different_codec(self):
        for key in ('synthetic&quot;Credential47', 'synthetic&#x41;Credential47',
                    'synthetic%41Credential47', r'synthetic\u0041Credential47'):
            views = (quote(key, safe=""), html.escape(key), self.escaped_body(key),
                     quote(html.escape(key), safe=""), html.escape(quote(key, safe="")),
                     quote(self.escaped_body(key), safe=""), self.escaped_body(quote(key, safe="")),
                     html.escape(self.escaped_body(key)), self.escaped_body(html.escape(key)))
            for value in views:
                with self.subTest(key=key, value=value), patch.object(credentials, "read", return_value=(None, "synthetic")):
                    with self.assertRaises(ValueError):
                        safe_stage_content(value, {"protocol": "ollama"}, secrets=(key,))

    def test_nested_embedded_and_duplicate_json_views_cannot_hide_credentials(self):
        for key in ('synthetic"credential47', "synthetic\\credential47"):
            escaped = "".join("\\u%04x" % ord(character) for character in key)
            duplicate = '{"value":"' + escaped + '","value":"safe"}'
            views = [json.dumps({"value": key}), json.dumps(json.dumps({"value": key})),
                     'Quoted metadata: {"value":"' + escaped + '"}.', duplicate,
                     quote(html.escape(json.dumps({"value": json.dumps({"key": key})})), safe=""),
                     json.dumps({"value": html.escape(key)}),
                     'A "prose quote {"value":"' + escaped + '"}" follows.']
            for value in views:
                with self.subTest(key=key, value=value):
                    with patch.object(credentials, "read", return_value=(None, "synthetic")):
                        with self.assertRaises(ValueError):
                            safe_stage_content({"result": value}, {"protocol": "ollama"}, secrets=(key,))

    def fixture(self):
        fixture = fixtures.ResearchStageSafetyTests()
        self.addCleanup(fixture.doCleanups)
        fixture.setUp()
        return fixture

    def test_json_encoded_assignment_is_rejected_before_spec_or_scope_persistence(self):
        fixture = self.fixture()
        key = 'synthetic"private\\credential47'
        fixture.keys[ENDPOINT] = key
        run, claim = fixture.new_run()
        before = fixture.database.read_bytes()
        assignment = "Inspect metadata: " + json.dumps({"credential": json.dumps(key)})
        with self.assertRaises(ValueError):
            fixture.stage(run, claim, assignment=assignment)
        with closing(sqlite3.connect(fixture.database)) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM research_stages").fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM research_stage_scope").fetchone()[0], 0)
        self.assertEqual(fixture.database.read_bytes(), before)
        self.assertEqual(fixture.requests, [])

    def test_malformed_and_unquoted_escape_assignments_are_rejected_before_persistence(self):
        key = 'synthetic"private\\credential47'
        body = self.escaped_body(key)
        for assignment in ('"\\x\\"' + body + '"', 'Metadata key: ' + body,
                           quote(key, safe="")):
            with self.subTest(assignment=assignment):
                fixture = self.fixture()
                fixture.keys[ENDPOINT] = key
                run, claim = fixture.new_run()
                before = fixture.database.read_bytes()
                with self.assertRaises(ValueError):
                    fixture.stage(run, claim, assignment=assignment)
                with closing(sqlite3.connect(fixture.database)) as db:
                    self.assertEqual(db.execute("SELECT COUNT(*) FROM research_stages").fetchone()[0], 0)
                    self.assertEqual(db.execute("SELECT COUNT(*) FROM research_stage_scope").fetchone()[0], 0)
                self.assertEqual(fixture.database.read_bytes(), before)
                self.assertEqual(fixture.requests, [])

    def test_malformed_and_unquoted_escape_model_outputs_are_withheld_after_actual_usage(self):
        fixture = self.fixture()
        key = 'synthetic"private\\credential47'
        fixture.keys[ENDPOINT] = key
        body = self.escaped_body(key)
        for value in ('"\\x\\"' + body + '"', 'Metadata key: ' + body):
            with self.subTest(value=value):
                stage = fixture.stage()
                fixture.response["message"]["content"] = json.dumps({"result": value})
                try:
                    with self.assertRaises(ValueError):
                        fixture.generate(stage)
                    with closing(sqlite3.connect(fixture.database)) as db:
                        self.assertEqual(db.execute("SELECT state,output FROM research_stages WHERE id=?",
                                                    (stage.stage_id,)).fetchone(), ("failed", None))
                    entry = fixture.ledger()[-1]
                    self.assertEqual((entry["status"], entry["input_tokens"], entry["output_tokens"]), ("settled", 41, 7))
                    budget = fixture.store.get(stage.run_id)["budget"]
                    self.assertEqual(budget["consumed"]["tokens"], 48)
                    self.assertEqual(budget["consumed"]["model_calls"], 1)
                finally:
                    fixture.store.cancel(stage.run_id)


    def test_nested_json_output_is_withheld_without_losing_actual_usage(self):
        fixture = self.fixture()
        stage = fixture.stage()
        request_id = uuid.uuid4().hex
        with fixture.provider.transaction(with_db=True) as (provider_state, db):
            fixture.store.reserve_stage(stage, fixture.workspace, provider_state, request_id, 100, 50, db)
        key = 'synthetic"private\\credential47'
        fixture.keys[ENDPOINT] = key
        escaped = "".join("\\u%04x" % ord(character) for character in key)
        output = {"result": '{"value":"' + escaped + '","value":"safe"}'}
        with fixture.provider.transaction(with_db=True) as (provider_state, db):
            accepted = fixture.store.settle_stage(stage, request_id, db=db, input_tokens=41, output_tokens=7,
                outcome="completed", output=output, workspace=fixture.workspace, provider_state=provider_state)
        self.assertFalse(accepted)
        with closing(sqlite3.connect(fixture.database)) as db:
            self.assertEqual(db.execute("SELECT state,output FROM research_stages WHERE id=?",
                                        (stage.stage_id,)).fetchone(), ("failed", None))
        budget = fixture.store.get(stage.run_id)["budget"]
        self.assertEqual(budget["consumed"]["tokens"], 48)
        self.assertEqual(budget["consumed"]["model_calls"], 1)
        self.assertEqual(fixture.requests, [])

    def test_benign_quoted_code_and_nested_json_keep_their_original_values(self):
        value = {"code": 'const label = "Årsta"; const path = "C:\\users\\local";',
                 "json": json.dumps({"quoted": 'say "hello"', "path": "C:\\local\\file"}),
                 "nested": json.dumps(json.dumps({"ordinary": "saved metadata"}))}
        original = deepcopy(value)
        with patch.object(credentials, "read", return_value=(None, "synthetic")):
            known = safe_stage_content(value, {"protocol": "ollama"}, secrets=("unrelated-private-key47",))
        self.assertEqual(known, ("unrelated-private-key47",))
        self.assertEqual(value, original)

    def test_excessive_depth_width_and_total_decoding_work_fail_closed(self):
        deep = "ordinary"
        for _ in range(40):
            deep = [deep]
        values = (deep, ["ordinary"] * 65536,
                  [str(index) + "x" * 65530 for index in range(65)],
                  [str(index) + r"\nX" * 20000 for index in range(4)])
        with patch.object(credentials, "read", return_value=(None, "synthetic")):
            for value in values:
                with self.subTest(shape=type(value).__name__):
                    with self.assertRaises(ValueError):
                        safe_stage_content(value, {"protocol": "ollama"}, secrets=("unrelated-private-key47",))
            # With no active/operation credential there is no decoding work.
            self.assertEqual(safe_stage_content(deep, {"protocol": "ollama"}), ())

    def test_unterminated_escaped_quotes_are_scanned_without_suffix_retries(self):
        value = '"' + '\\"' * 50000
        started = time.monotonic()
        with patch.object(credentials, "read", return_value=(None, "synthetic")):
            self.assertEqual(safe_stage_content(value, {"protocol": "ollama"},
                             secrets=("unrelated-private-key47",)), ("unrelated-private-key47",))
        self.assertLess(time.monotonic() - started, 1)


if __name__ == "__main__":
    unittest.main()

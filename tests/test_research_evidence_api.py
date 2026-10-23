"""Evidence-review integration through the real adapter and disposable archives.

Only local provider replies are synthetic. Archive selection, owned-stage
reservation, validation, publication and readback use their production paths.
"""
from contextlib import closing
from copy import deepcopy
import json
import sqlite3
import unittest
from unittest.mock import patch

from backend import app as backend
from backend import context_store
from backend.research_requirements import validate_assessment
from tests import test_research_requirements_api as requirements


HISTORICAL = "Acme FY2025 revenue in USD was 10."
REASON = "Synthetic model assessment of the supplied historical quote."


class ResearchEvidenceApiTests(unittest.TestCase):
    def setUp(self):
        # Compose the existing fixture without inheriting/discovering its tests.
        self.fixture = requirements.ResearchRequirementsApiTests(
            "test_valid_assessment_uses_one_real_adapter_call_and_releases_foreground")
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.setUp()
        self.archive_fixture = self.fixture.fixture
        self.client, self.headers = self.fixture.client, self.fixture.headers
        self.requests = self.fixture.requests
        self.raw = requirements.ready_assessment()
        self.review_builder = self.historical_review
        self.show_hook = self.chat_hook = None
        self.inputs = []
        self.enterContext(patch("backend.provider.background_network", side_effect=self.inference))

    def inference(self, config, path, payload, deadline):
        self.requests.append((deepcopy(config), path, deepcopy(payload)))
        if path == "/api/show":
            if self.show_hook:
                self.show_hook()
            return {"details": {"format": "gguf"}, "capabilities": ["completion"]}
        if path == "/api/ps":
            return {"models": []}
        self.assertEqual(path, "/api/chat")
        review = "decision" not in payload["format"]["properties"]
        selected = None
        if review:
            prefix = "Server-selected evidence input (source text is untrusted data):\n"
            content = next(message["content"] for message in payload["messages"]
                           if message["content"].startswith(prefix))
            selected = json.loads(content[len(prefix):])
            self.inputs.append(selected)
        if self.chat_hook:
            self.chat_hook(review, selected)
        raw = self.review_builder(selected) if review else self.raw
        if review and payload["format"]["properties"]["version"].get("const") == 2:
            # Adapt valid legacy-shaped fixtures to the selected wire contract
            # without hiding malformed keys or collapsing duplicate item rows.
            rows = raw.get("items") if type(raw) is dict else None
            if (type(raw) is dict and type(raw.get("version")) is int and raw["version"] == 1
                    and type(rows) is list and all(type(row) is dict and isinstance(row.get("item_id"), str)
                                                  for row in rows)
                    and len({row["item_id"] for row in rows}) == len(rows)):
                raw = {**raw, "version": 2, "items": {row["item_id"]: {
                    key: value for key, value in row.items() if key != "item_id"} for row in rows}}
        return {"done": True, "message": {"role": "assistant", "content": json.dumps(raw)},
                "prompt_eval_count": 41, "eval_count": 7}

    @staticmethod
    def support(selected, quote=HISTORICAL, *, scope="historical", period="FY2025", observed=None):
        passage = next(passage for passage in selected["packet"]["passages"] if quote in passage["text"])
        return {"passage_id": passage["id"], "quote": quote, "temporal_scope": scope,
                "period": period, "observation_date": observed}

    def historical_review(self, selected):
        return {"version": 1, "items": [{"item_id": item["id"], "state": "covered", "reason": REASON,
                                         "supports": [self.support(selected)]}
                                        for item in selected["assessment"]["items"]]}

    def payload(self, **changes):
        return self.fixture.payload(workflow="evidence_review", **changes)

    def submit(self, payload=None, **changes):
        return self.fixture.submit(payload or self.payload(**changes))

    def run_now(self, **changes):
        response = self.submit(**changes)
        self.assertEqual(response.status_code, 202, response.text)
        return self.fixture.get(response.json()["run"]["id"])

    def queued(self, **changes):
        with patch.object(backend, "execute_research_run") as deferred:
            response = self.submit(**changes)
        self.assertEqual(response.status_code, 202, response.text)
        run = response.json()["run"]
        deferred.assert_called_once_with(run["id"])
        self.assertEqual(run["workflow"], "evidence_review")
        return run

    def capture(self, content=HISTORICAL):
        return self.archive_fixture.capture(content=content)

    def manifest(self, source):
        path = self.archive_fixture.archive / "records" / "captures" / source["retrieved_at"][:7] / (source["capture_id"] + ".json")
        self.assertTrue(path.resolve().is_relative_to(self.archive_fixture.root))
        return path

    def assert_usage(self, run, calls=2):
        consumed = run["budget"]["consumed"]
        self.assertEqual((consumed["model_calls"], consumed["tokens"], consumed["hosted_searches"]),
                         (calls, calls * 48, 0))
        self.assertTrue(all(value == 0 for value in run["budget"]["reserved"].values()))
        self.fixture.assert_released()

    def assert_no_review_output(self, run_id):
        with closing(sqlite3.connect(backend.DATABASE)) as db:
            rows = db.execute("SELECT output FROM research_stages WHERE run_id=? AND kind='evidence_review'", (run_id,)).fetchall()
        self.assertTrue(all(row[0] is None for row in rows))

    def test_historical_tail_support_uses_two_owned_calls_and_releases_all_holds(self):
        content = "Unrelated saved padding. " * 100 + HISTORICAL
        source = self.capture(content)
        snapshot = self.archive_fixture.source_snapshot()
        run = self.run_now()
        self.assertEqual(run["state"], "partial")
        self.assertEqual(run["assessment"]["completion_criterion"], requirements.REQUEST)
        review = run["evidence_review"]
        self.assertEqual(review["items"][0]["state"], "covered")
        support = review["items"][0]["supports"][0]
        self.assertGreater(support["start"], 1200)
        self.assertEqual(content[support["start"]:support["end"]], HISTORICAL)
        self.assertEqual(support["freshness_basis"], "explicit_requested_period")
        self.assertIsNone(support["best_before"])
        self.assertTrue(review["sources"][0]["stale"])
        self.assertEqual(review["sources"][0]["capture_id"], source["capture_id"])
        self.assertEqual(len(self.fixture.chats()), 2)
        self.assertTrue(all("tools" not in chat[2] for chat in self.fixture.chats()))
        self.assertEqual(self.archive_fixture.source_snapshot(), snapshot)
        self.assert_usage(run)
        with closing(sqlite3.connect(backend.DATABASE)) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM research_finishing WHERE run_id=?", (run["id"],)).fetchone()[0], 0)

    def assert_nul_source_keeps_exact_provenance(self, *, title, content):
        healthy = self.capture()
        source = backend.context_store().capture("Synthetic Acme report", "2020-01-02T12:00:01+00:00", [{
            "title": title, "url": "https://reports.example.org/acme/nul-source", "content": content,
        }], 1)["sources"][0]
        self.assertEqual(source["archive_status"], "saved")
        snapshot = self.archive_fixture.source_snapshot()

        def review(selected):
            source_id = next(value["id"] for value in selected["packet"]["sources"]
                             if value["capture_id"] == source["capture_id"])
            passage = next(value for value in selected["packet"]["passages"]
                           if value["source_id"] == source_id and HISTORICAL in value["text"])
            support = {"passage_id": passage["id"], "quote": HISTORICAL, "temporal_scope": "historical",
                       "period": "FY2025", "observation_date": None}
            return {"version": 1, "items": [{"item_id": "i1", "state": "covered", "reason": REASON,
                                               "supports": [support]}]}

        self.review_builder = review
        run = self.run_now()
        self.assertEqual(run["state"], "partial")
        evidence = run["evidence_review"]
        self.assertEqual({value["capture_id"] for value in evidence["sources"]},
                         {healthy["capture_id"], source["capture_id"]})
        selected_source = next(value for value in evidence["sources"] if value["capture_id"] == source["capture_id"])
        self.assertEqual(selected_source["title"], title)
        self.assertEqual((selected_source["content_hash"], selected_source["manifest_hash"]),
                         (source["content_hash"], source["manifest_hash"]))
        passage = next(value for value in evidence["passages"] if value["source_id"] == selected_source["id"])
        self.assertEqual(passage["text"], content)
        item = evidence["items"][0]
        self.assertEqual(item["state"], "covered")
        support = item["supports"][0]
        self.assertEqual((support["start"], support["end"]),
                         (content.index(HISTORICAL), content.index(HISTORICAL) + len(HISTORICAL)))
        self.assertEqual(content[support["start"]:support["end"]], support["quote"])
        reopened = self.fixture.get(run["id"])
        self.assertEqual(reopened["evidence_review"], evidence)
        self.assertEqual(self.archive_fixture.source_snapshot(), snapshot)
        self.assert_usage(run)

    def test_embedded_nul_in_saved_body_keeps_exact_source_and_support_offsets(self):
        self.assert_nul_source_keeps_exact_provenance(
            title="Synthetic Acme report", content="Saved padding.\x00 " + HISTORICAL)

    def test_embedded_nul_in_saved_title_keeps_exact_source_and_support_offsets(self):
        self.assert_nul_source_keeps_exact_provenance(
            title="Synthetic\x00 Acme report", content=HISTORICAL)

    def test_embedded_nul_in_model_quote_or_reason_remains_rejected(self):
        content = "Saved padding.\x00 " + HISTORICAL
        self.capture(content)
        for field in ("quote", "reason"):
            with self.subTest(model_field=field):
                def review(selected):
                    support = self.support(selected, content if field == "quote" else HISTORICAL)
                    return {"version": 1, "items": [{"item_id": "i1", "state": "covered",
                        "reason": REASON + ("\x00" if field == "reason" else ""), "supports": [support]}]}

                self.review_builder = review
                run = self.run_now()
                self.assertEqual(run["state"], "failed")
                self.assertIsNotNone(run["assessment"])
                self.assertIsNone(run["evidence_review"])
                self.assert_no_review_output(run["id"])
                self.assert_usage(run)

    def test_clarification_stops_before_lookup_or_second_model_call(self):
        self.raw = {"version": 1, "decision": "clarify", "subjects": [], "items": [], "constraints": [],
                    "clarifications": [{"kind": "security", "quote": "ABC", "item_ids": []}]}
        with patch.object(backend, "_research_candidates", side_effect=AssertionError("No lookup before clarification")):
            run = self.run_now(request="Research ABC.")
        self.assertEqual(run["state"], "clarification")
        self.assertIsNone(run["evidence_review"])
        self.assertEqual(len(self.fixture.chats()), 1)
        self.assert_usage(run, 1)

    def test_disabled_and_unconfigured_archives_produce_fixed_unavailable_without_review_call(self):
        for state in ("disabled", "unconfigured"):
            with self.subTest(archive=state):
                environment = {} if state == "disabled" else {"MAESTRO_CONTEXT_ARCHIVE_DIR": ""}
                with patch.dict("os.environ", environment):
                    before = len(self.fixture.chats())
                    run = self.run_now()
                review = run["evidence_review"]
                self.assertEqual(run["state"], "partial")
                self.assertEqual(review["lookup"]["state"], state)
                self.assertEqual(review["items"][0]["state"], "unavailable")
                self.assertEqual(review["sources"], [])
                self.assertEqual(review["passages"], [])
                self.assertEqual(len(self.fixture.chats()), before + 1)
                self.assert_usage(run, 1)

    def test_old_observation_cannot_be_promoted_to_current_coverage(self):
        quote = "Acme current stock price in USD was 10 on 2020-01-02."
        self.capture(quote)
        self.raw["items"][0].update(criterion="stock price", period=None)
        self.review_builder = lambda selected: {"version": 1, "items": [{"item_id": "i1", "state": "covered", "reason": REASON,
            "supports": [self.support(selected, quote, scope="current", period=None, observed="2020-01-02")]}]}
        run = self.run_now(request="Compare Acme current stock price in USD.")
        item = run["evidence_review"]["items"][0]
        self.assertEqual((item["model_state"], item["state"]), ("covered", "stale"))
        self.assertEqual(item["supports"][0]["freshness_basis"], "past_observation_for_current_requirement")
        self.assert_usage(run)

    def test_conflicting_disclosures_remain_visible_beside_a_missing_required_item(self):
        first, second = "Acme FY2025 revenue in USD was 10.", "Acme FY2025 revenue in USD was 20."
        self.capture(first)
        self.capture(second)
        self.raw["items"].append({**self.raw["items"][0], "id": "i2", "criterion": "operating profit"})
        self.review_builder = lambda selected: {"version": 1, "items": [
            {"item_id": "i1", "state": "conflicting", "reason": "The two supplied revenue disclosures disagree.",
             "supports": [self.support(selected, first), self.support(selected, second)]},
            {"item_id": "i2", "state": "missing", "reason": "No operating profit support was selected.", "supports": []}]}
        run = self.run_now(request="Compare Acme revenue and operating profit in USD for FY2025.")
        items = run["evidence_review"]["items"]
        self.assertEqual([item["state"] for item in items], ["conflicting", "missing"])
        self.assertEqual({support["quote"] for support in items[0]["supports"]}, {first, second})
        self.assertEqual(run["state"], "partial")
        self.assert_usage(run)

    def test_v2_partial_coverage_keeps_both_required_ids_and_publishes_the_stable_array(self):
        quote = "Acme revenue for FY2025 was USD 42 million."
        self.capture(quote)
        self.raw["items"].append({**self.raw["items"][0], "id": "i2", "criterion": "operating profit"})
        self.review_builder = lambda selected: {"version": 1, "items": [
            {"item_id": "i1", "state": "covered", "reason": "The requested revenue period and currency are quoted.",
             "supports": [self.support(selected, quote)]},
            {"item_id": "i2", "state": "missing", "reason": "The selected source does not state operating profit.",
             "supports": []}]}
        run = self.run_now(request="Assess Acme revenue and operating profit in USD for FY2025.")
        self.assertEqual(run["state"], "partial")
        review = run["evidence_review"]
        self.assertEqual(review["version"], 1)
        self.assertEqual([(item["item_id"], item["state"]) for item in review["items"]],
                         [("i1", "covered"), ("i2", "missing")])
        self.assertEqual(review["items"][0]["supports"][0]["quote"], quote)
        self.assertEqual(review["items"][1]["supports"], [])
        wire = self.fixture.chats()[1][2]["format"]
        self.assertEqual(wire["properties"]["version"]["const"], 2)
        rows = wire["properties"]["items"]
        self.assertEqual(rows["type"], "object")
        self.assertEqual(set(rows["required"]), {"i1", "i2"})
        self.assertEqual(set(rows["properties"]), {"i1", "i2"})
        self.assertIs(rows["additionalProperties"], False)
        with closing(sqlite3.connect(backend.DATABASE)) as db:
            stage = json.loads(db.execute("SELECT output FROM research_stages WHERE run_id=? AND kind='evidence_review'",
                                          (run["id"],)).fetchone()[0])
        self.assertEqual(stage["version"], 2)
        self.assertEqual(set(stage["items"]), {"i1", "i2"})
        self.assert_usage(run)

    def test_malformed_or_dangling_reviews_are_withheld_without_retry_but_keep_actual_usage(self):
        self.capture()
        for malformed in ({"version": 1, "items": []}, {"version": 1, "items": [{"item_id": "i1", "state": "covered",
                "reason": "MODEL_REJECTED_REVIEW_MARKER_25", "supports": [{"passage_id": "missing", "quote": HISTORICAL,
                    "temporal_scope": "historical", "period": "FY2025", "observation_date": None}]}]}):
            with self.subTest(raw=malformed):
                self.review_builder = lambda selected: malformed
                before = len(self.fixture.chats())
                run = self.run_now()
                self.assertEqual(run["state"], "failed")
                self.assertIsNotNone(run["assessment"])
                self.assertIsNone(run["evidence_review"])
                self.assertNotIn("MODEL_REJECTED_REVIEW_MARKER_25", json.dumps(run))
                self.assertEqual(len(self.fixture.chats()), before + 2)
                self.assert_no_review_output(run["id"])
                self.assert_usage(run)

    def test_observed_malformed_partial_coverage_json_is_not_repaired_or_persisted(self):
        request = "Assess Acme revenue and operating profit in USD for FY2025."
        quote = "Acme revenue for FY2025 was USD 42 million."
        self.capture(quote)
        snapshot = self.archive_fixture.source_snapshot()
        self.raw["items"].append({**self.raw["items"][0], "id": "i2", "criterion": "operating profit"})
        self.review_builder = lambda selected: {"version": 1, "items": [
            {"item_id": "i1", "state": "covered", "reason": "Evidence found for Acme revenue FY2025 in USD.",
             "supports": [self.support(selected, quote)]},
            {"item_id": "i2", "state": "missing", "reason": "No evidence found for Acme operating profit FY2025.",
             "supports": []}]}
        # Both actual malformed replies from the public synthetic trials remain
        # raw text. Neither the wire-contract fixture adapter nor JSON repair
        # may turn them into an accepted partial result.
        keyed_array = ('{"version":1,"items":[{"item_id":"i1","state":"covered",'
                       '"reason":"Evidence found for Acme revenue FY2025 in USD.",'
                       '"supports":[{"passage_id":"p1","quote":"Acme revenue for FY2025 was USD 42 million.",'
                       '"temporal_scope":"historical","period":"FY2025","observation_date":null}]},'
                       '"i2":{"state":"missing","reason":"No evidence found for Acme operating profit FY2025.",'
                       '"supports":[]}}}')
        extra_quote = ('{"version":1,"items":[{"item_id":"i1","state":"covered",'
                       '"reason":"Revenue figure provided for FY2025.",'
                       '"supports":[{"passage_id":"p1","quote":"Acme revenue for FY2025 was USD 42 million.",'
                       '"temporal_scope":"historical","period":"FY2025","observation_date":null}]},'
                       '"{"item_id":"i2","state":"missing",'
                       '"reason":"No operating profit information found in the selected passage.","supports":[]}]}')
        inference = self.inference
        for name, malformed, actuals in (
                ("keyed_array_entry", keyed_array, ((2395, 184), (2230, 180))),
                ("extra_quote_before_second_row", extra_quote, ((2398, 184), (2498, 147)))):
            with self.subTest(syntax=name):
                with self.assertRaises(json.JSONDecodeError):
                    json.loads(malformed)
                before = len(self.fixture.chats())

                def malformed_reply(config, path, payload, deadline):
                    reply = inference(config, path, payload, deadline)
                    if path == "/api/chat":
                        is_review = "decision" not in payload["format"]["properties"]
                        if is_review:
                            # Inject after json.dumps: encoding a Python object
                            # cannot reproduce the observed invalid syntax.
                            reply["message"]["content"] = malformed
                        counts = actuals[int(is_review)]
                        reply.update(prompt_eval_count=counts[0], eval_count=counts[1])
                    return reply

                with patch("backend.provider.background_network", side_effect=malformed_reply):
                    run = self.run_now(request=request)
                self.assertEqual(run["state"], "failed")
                self.assertIsNone(run["evidence_review"])
                self.assertEqual(run["assessment"]["decision"], "ready")
                self.assertEqual(run["assessment"]["completion_criterion"], request)
                self.assertEqual([(item["id"], item["criterion"], item["period"], item["currency"])
                                  for item in run["assessment"]["items"]],
                                 [("i1", "revenue", "FY2025", "USD"), ("i2", "operating profit", "FY2025", "USD")])
                for rejected_reason in ("No evidence found for Acme operating profit FY2025.",
                                        "No operating profit information found in the selected passage."):
                    self.assertNotIn(rejected_reason, json.dumps(run))
                self.assertEqual(len(self.fixture.chats()), before + 2)
                self.assert_no_review_output(run["id"])
                consumed = run["budget"]["consumed"]
                self.assertEqual((consumed["model_calls"], consumed["tokens"], consumed["hosted_searches"]),
                                 (2, sum(sum(counts) for counts in actuals), 0))
                self.assertTrue(all(value == 0 for value in run["budget"]["reserved"].values()))
                ledger = backend.provider().read_state()["ledger"][-2:]
                self.assertEqual([(entry["status"], entry["input_tokens"], entry["output_tokens"]) for entry in ledger],
                                 [("settled", *counts) for counts in actuals])
                with closing(sqlite3.connect(backend.DATABASE)) as db:
                    self.assertEqual(db.execute("SELECT state,output FROM research_stages WHERE run_id=? AND kind='evidence_review'",
                                                (run["id"],)).fetchall(), [("failed", None)])
                    self.assertEqual(db.execute("SELECT COUNT(*) FROM research_finishing WHERE run_id=?", (run["id"],)).fetchone()[0], 0)
                self.assertEqual(self.archive_fixture.source_snapshot(), snapshot)
                self.fixture.assert_released()

    def test_source_delete_tamper_or_disable_after_metadata_blocks_review_dispatch(self):
        for action in ("delete", "tamper", "disable"):
            with self.subTest(action=action):
                # A tampered immutable object must not poison the next subcase.
                source = self.capture("Synthetic action " + action + ". " + HISTORICAL)
                before = len(self.fixture.chats())

                def mutate_before_second_chat():
                    if len(self.fixture.chats()) != before + 1:
                        return
                    if action == "disable":
                        config = self.client.get("/api/context").json()["config"]
                        response = self.client.put("/api/context", json={**config, "enabled": False}, headers=self.headers)
                        self.assertEqual(response.status_code, 200, response.text)
                    elif action == "delete":
                        self.manifest(source).unlink()
                    else:
                        body = self.archive_fixture.archive / "objects" / "sha256" / source["content_hash"][:2] / (source["content_hash"] + ".txt")
                        self.assertTrue(body.resolve().is_relative_to(self.archive_fixture.root))
                        body.write_text("Tampered synthetic public source.", encoding="utf-8")

                self.show_hook = mutate_before_second_chat
                run = self.run_now()
                self.show_hook = None
                self.assertEqual(run["state"], "failed")
                self.assertIsNone(run["evidence_review"])
                self.assertEqual(len(self.fixture.chats()), before + 1)
                self.assert_no_review_output(run["id"])
                self.assert_usage(run, 1)

    def test_source_loss_during_review_withholds_late_output_and_settles_both_calls(self):
        source = self.capture()
        self.chat_hook = lambda review, selected: self.manifest(source).unlink() if review else None
        run = self.run_now()
        self.assertEqual(run["state"], "failed")
        self.assertIsNone(run["evidence_review"])
        self.assertEqual(len(self.fixture.chats()), 2)
        self.assert_no_review_output(run["id"])
        self.assert_usage(run)

    def test_read_time_source_loss_withholds_quotes_and_model_reason_without_rewriting_history(self):
        source = self.capture()
        run = self.run_now()
        self.assertEqual(run["evidence_review"]["items"][0]["state"], "covered")
        self.manifest(source).unlink()
        visible = self.fixture.get(run["id"])
        review = visible["evidence_review"]
        self.assertEqual(review["items"][0]["state"], "unavailable")
        self.assertEqual(review["items"][0]["supports"], [])
        self.assertEqual(review["sources"], [])
        self.assertEqual(review["passages"], [])
        self.assertNotIn(HISTORICAL, json.dumps(visible))
        self.assertNotIn(REASON, json.dumps(visible))
        stored = backend.research_store().get(run["id"])
        self.assertEqual(stored["evidence_review"]["items"][0]["state"], "covered")
        self.assertEqual(len(self.fixture.chats()), 2)
        self.assert_usage(visible)

    def test_cancel_run_delete_and_origin_delete_fence_late_review_with_exact_usage(self):
        self.capture()
        for action in ("cancel", "run_delete", "chat_delete"):
            with self.subTest(action=action):
                chat_id = self.archive_fixture.chat() if action == "chat_delete" else None
                run = self.queued(chat_id=chat_id)
                path = "/api/research/runs/" + run["id"]

                def act_before_reply(review, selected):
                    if not review:
                        return
                    if action == "cancel":
                        response = self.client.post(path + "/cancel", headers=self.headers)
                    else:
                        target = "/api/chats/" + chat_id if action == "chat_delete" else path
                        response = self.client.delete(target, headers=self.headers)
                    self.assertEqual(response.status_code, 200, response.text)

                self.chat_hook = act_before_reply
                self.archive_fixture.execute(run["id"])
                self.chat_hook = None
                if action == "cancel":
                    saved = self.fixture.get(run["id"])
                    self.assertEqual(saved["state"], "cancelled")
                    self.assertIsNone(saved["evidence_review"])
                    self.assert_usage(saved)
                else:
                    self.assertEqual(self.client.get(path).status_code, 404)
                    entry = backend.provider().read_state()["ledger"][-1]
                    self.assertEqual((entry["status"], entry["input_tokens"], entry["output_tokens"]), ("settled", 41, 7))
                    self.fixture.assert_released()
                self.assert_no_review_output(run["id"])

    def test_small_config_fits_by_trimming_optional_passages_without_clipping_objective(self):
        self.capture("Irrelevant saved padding. " * 180 + HISTORICAL + " More padding." * 90)
        with backend.provider().transaction() as state:
            state["config"].update(ollama_context_tokens=16384, max_output_tokens=1024)
        run = self.run_now()
        self.assertEqual(run["state"], "partial")
        self.assertEqual(len(self.fixture.chats()), 2)
        self.assertEqual(run["evidence_review"]["items"][0]["state"], "covered")
        self.assertEqual(self.inputs[0]["assessment"]["completion_criterion"], requirements.REQUEST)
        self.assertEqual(run["assessment"]["completion_criterion"], requirements.REQUEST)
        self.assertTrue(run["evidence_review"]["selection"]["limited"])
        self.assertEqual([chat[2]["options"]["num_predict"] for chat in self.fixture.chats()], [1024, 1024])
        self.assert_usage(run)

    def test_idempotency_and_legacy_requirements_do_not_duplicate_or_add_evidence_work(self):
        self.capture()
        payload = self.payload()
        first = self.submit(payload)
        self.assertEqual(first.status_code, 202, first.text)
        duplicate = self.submit(payload)
        self.assertEqual(duplicate.status_code, 202, duplicate.text)
        self.assertFalse(duplicate.json()["created"])
        self.assertEqual(first.json()["run"]["id"], duplicate.json()["run"]["id"])
        self.assertEqual(len(self.fixture.chats()), 2)
        conflict = self.submit({**payload, "workflow": "requirements"})
        self.assertEqual(conflict.status_code, 409)
        response = self.fixture.submit(self.fixture.payload())
        self.assertEqual(response.status_code, 202, response.text)
        legacy = self.fixture.get(response.json()["run"]["id"])
        self.assertEqual(legacy["workflow"], "requirements")
        self.assertIsNone(legacy["evidence_review"])
        self.assertEqual(len(self.fixture.chats()), 3)
        self.assert_usage(legacy, 1)

    def test_candidate_scan_allowance_is_shared_across_subject_queries(self):
        self.capture("Acme and Boreal FY2025 revenue in USD was 10.")
        raw = deepcopy(self.raw)
        raw["subjects"].append({"id": "s2", "kind": "company", "name": "Boreal", "identifiers": []})
        raw["items"].append({**raw["items"][0], "id": "i2", "subject_ids": ["s2"]})
        assessment = validate_assessment(raw, "Compare Acme revenue and Boreal revenue in USD for FY2025.")
        original = backend._search_context_candidates
        work_objects = []

        def lookup(entry, *, work=None):
            work_objects.append(work)
            return original(entry, work=work)

        with patch.object(context_store, "CANDIDATE_SCAN_LIMIT", 1), patch.object(backend, "_search_context_candidates", side_effect=lookup):
            candidates, lookup_state = backend._research_candidates(assessment)
        self.assertEqual(len(work_objects), 2)
        self.assertIs(work_objects[0], work_objects[1])
        self.assertEqual(work_objects[0].examined, 1)
        self.assertEqual(len(candidates), 1)
        self.assertTrue(lookup_state["search_limited"])
        self.assertEqual(self.requests, [])


if __name__ == "__main__":
    unittest.main()

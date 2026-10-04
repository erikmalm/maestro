"""Managed memories evolve in place while user pins and source lineage remain intact."""
import json
import unittest

from backend.memory import MemoryStore, assistant_revision, revision
from backend.reflection import digest, schema_for
from tests import test_auto_reflection as automatic
from tests import test_reflection_feedback as feedback


class EvolutionTests(unittest.TestCase):
    save = feedback.FeedbackTests.save
    jobs = feedback.FeedbackTests.jobs
    network = feedback.FeedbackTests.network
    pair = feedback.FeedbackTests.pair
    enable_periodic = feedback.FeedbackTests.enable_periodic

    def setUp(self):
        feedback.FeedbackTests.setUp(self)

    def record(self, name, content, kind="lesson", origin="reflective", provenance=(), evidence=None, stamp=None):
        stamp = stamp or self.store.stamp()
        record = {"id": name, "content": content, "kind": kind, "origin": origin, "scope": "workspace", "chat_id": None,
                  "source_message_id": None, "source_hash": None, "evidence": evidence, "provenance": list(provenance), "created_at": stamp, "updated_at": stamp}
        with self.memory.transaction() as db:
            MemoryStore.insert(db, record)
            return MemoryStore.record(db.execute("SELECT * FROM private_memories WHERE id=?", (name,)).fetchone())

    @staticmethod
    def change(record, operation, content=""):
        return {"operation": operation, "memory_id": record["id"], "kind": record["kind"], "scope": record["scope"],
                "content": content, "source_message_id": "", "evidence": ""}

    def test_unchanged_explicit_and_implicit_identity_updates_preserve_dependents_and_stamps(self):
        pin = self.memory.remember("Keep synthetic project decisions verifiable.")
        self.worker.step()
        identity = next(record for record in self.memory.list() if record["kind"] == "identity")
        self.record("dependent", "Explain which assumptions were checked.", provenance=[{"memory_id": identity["id"], "hash": revision(identity)}])
        before = {record["id"]: record for record in self.memory.list()}
        for operation in ("update", "add"):
            with self.subTest(operation=operation):
                self.now += 360 * 60
                draft = self.change(identity, operation, "  " + identity["content"] + "\n")
                if operation == "add":
                    draft["memory_id"] = ""
                self.drafts = [draft]
                self.worker.step()
                self.assertEqual({record["id"]: record for record in self.memory.list()}, before)
                self.assertEqual(self.store.status()["journal"][0]["changes"], [])
                self.assertEqual(self.store.status()["journal"][0]["outcome"], "abstained")
        self.assertTrue(before[pin["id"]]["pinned"])

    def test_consolidation_removes_first_preserves_survivor_id_and_all_historical_sources(self):
        older = self.pair("older", "An earlier answer with a caveat.", "positive", "The caveat was useful.")
        older_ref = {"chat_id": "chat", "message_id": older["id"], "role": "assistant", "hash": assistant_revision(older["text"], older["feedback"])}
        self.pair("survivor", "A second complete answer.")
        survivor_user = self.workspace["chats"][0]["messages"][-2]
        survivor_ref = {"chat_id": "chat", "message_id": survivor_user["id"], "hash": digest(survivor_user["text"])}
        self.pair("newest", "A current answer to assess.", "negative", "The current explanation missed a qualification.")
        self.workspace["work_config"]["reflection_exchange_count"] = 1
        self.save()
        first = self.record("first", "State important caveats clearly.", provenance=[older_ref])
        survivor = self.record("survivor", "Distinguish assumptions from checked facts.", provenance=[survivor_ref])
        duplicate = self.record("duplicate", "State assumptions explicitly.", provenance=[survivor_ref])
        child = self.record("child", "A derived reminder.", provenance=[{"memory_id": first["id"], "hash": revision(first)}])
        pin = self.memory.remember("A synthetic user-pinned project decision.")
        self.drafts = [self.change(first, "remove"), self.change(duplicate, "remove"), self.change(survivor, "update", "State caveats clearly and distinguish assumptions from checked facts.")]
        self.worker.step()
        updated = next(record for record in self.memory.list() if record["id"] == survivor["id"])
        self.assertEqual(updated["created_at"], survivor["created_at"])
        self.assertNotEqual(updated["content"], survivor["content"])
        self.assertIn(older_ref, updated["provenance"])
        self.assertIn(survivor_ref, updated["provenance"])
        self.assertNotIn(older["id"], {reply["id"] for reply in self.jobs()[-1][1]["assistant_versions"]})
        self.assertFalse(any(source.get("memory_id") in (first["id"], survivor["id"]) for source in updated["provenance"]))
        self.assertEqual({record["id"] for record in self.memory.list()}, {survivor["id"], pin["id"]})
        self.assertEqual([change["operation"] for change in self.store.status()["journal"][0]["changes"]], ["remove", "remove", "update"])
        older["feedback"]["comment"] = "A corrected observation about the old answer."
        with self.memory.transaction() as db:
            self.assertEqual(self.memory.remove_stale(db, self.workspace["chats"]), [survivor["id"]])
            self.save(db)
        self.assertEqual(self.memory.list(), [pin])
        self.assertNotIn(child["id"], {record["id"] for record in self.memory.list()})

    def test_cascaded_remove_or_update_does_not_fail_or_recreate_a_missing_target(self):
        pin = self.memory.remember("Keep this user-pinned synthetic decision.")
        for operation in ("remove", "update"):
            with self.subTest(operation=operation):
                fact = self.record("obsolete-" + operation, "A temporary clarification misclassified as a fact.", kind="fact", origin="curated")
                dependent = self.record("dependent-" + operation, "A practice derived from an obsolete fact.", kind="identity", provenance=[{"memory_id": fact["id"], "hash": revision(fact)}])
                self.drafts = [self.change(fact, "remove"), self.change(dependent, operation, "Check assumptions carefully." if operation == "update" else "")]
                self.worker.step()
                self.assertEqual(self.jobs()[-1][0], "done")
                self.assertEqual(self.memory.list(), [pin])
                self.assertEqual(self.store.status()["journal"][0]["changes"][0]["memory_id"], fact["id"])
                self.now += 360 * 60

    def test_periodic_rotation_reviews_older_managed_notes_even_with_many_pins(self):
        profile = self.record("profile", "Maintain a concise, revisable working identity.", kind="identity")
        for index in range(30):
            self.record(f"pin-{index:02}", f"Synthetic project decision {index}.", kind="fact", origin="explicit")
        managed = {f"managed-{index:02}" for index in range(70)}
        for name in sorted(managed):
            self.record(name, "Review this specific working observation " + name + ".")
        self.drafts = []
        selected = set()
        previous = set()
        for iteration in range(4):
            self.worker.step()
            ids = {memory["id"] for memory in self.jobs()[-1][1]["memory_versions"]}
            self.assertIn(profile["id"], ids)
            self.assertTrue(any(name.startswith("pin-") for name in ids))
            batch = ids & managed
            self.assertTrue(batch)
            if iteration < 3:
                self.assertFalse(batch & previous)
            selected |= batch
            previous |= batch
            self.now += 360 * 60
        self.assertEqual(selected, managed)
        self.assertEqual(len(self.memory.list()), 101)

    def test_repeated_identity_updates_expand_dependent_lessons_without_losing_unchanged_roots(self):
        root = self.memory.remember("Keep the synthetic project decisions verifiable.")
        source = {"memory_id": root["id"], "hash": revision(root)}
        identity = self.record("identity", "Evaluate clarification before asking a question.", kind="identity", provenance=[source])
        self.drafts = [{"operation": "add", "memory_id": "", "kind": "lesson", "scope": "workspace",
                        "content": "Inspect ambiguity before asking about missing information.", "source_message_id": "", "evidence": ""}]
        self.worker.step()
        lesson = next(record for record in self.memory.list() if record["kind"] == "lesson")
        self.assertIn({"memory_id": identity["id"], "hash": revision(identity)}, lesson["provenance"])
        for content in ("Check whether clarification helps before asking a question.",
                        "Check whether clarification adds value before asking a question."):
            self.now += 360 * 60
            self.drafts = [self.change(identity, "update", content)]
            self.worker.step()
            identity = next(record for record in self.memory.list() if record["id"] == identity["id"])
            self.assertEqual(identity["content"], content)
            self.assertEqual(identity["provenance"], [source])
            self.assertEqual(self.jobs()[-1][0], "done")
            self.assertEqual({record["id"] for record in self.memory.list()}, {identity["id"], root["id"]})

    def test_unselected_identity_is_retained_as_ancestor_when_implicit_update_is_skipped(self):
        identity = self.record("identity", "A specific working practice. " * 60, kind="identity")
        lesson = self.record("lesson", "Evaluate the working practice with a concrete synthetic comparison.",
                             provenance=[{"memory_id": identity["id"], "hash": revision(identity)}])
        self.workspace["work_config"]["reflection_context_characters"] = 1000
        self.save()
        self.drafts = [{"operation": "add", "memory_id": "", "kind": kind, "scope": "workspace", "content": content,
                        "source_message_id": "", "evidence": ""} for kind, content in (
            ("identity", "A new working identity that cannot replace an unselected source."),
            ("lesson", "Compare working practices against an explicit outcome."))]
        self.worker.step()
        self.assertEqual([record["id"] for record in self.jobs()[-1][1]["memory_versions"]], [lesson["id"]])
        records = {record["id"]: record for record in self.memory.list()}
        self.assertEqual(records[identity["id"]], identity)
        derived = next(record for record in records.values() if record["id"] not in (identity["id"], lesson["id"]))
        self.assertIn({"memory_id": lesson["id"], "hash": revision(lesson)}, derived["provenance"])
        self.memory.forget(identity["id"])
        self.assertEqual(self.memory.list(), [])

    def test_unapplied_approved_mutation_cannot_detach_new_notes_from_retained_sources(self):
        duplicate = self.memory.remember("Keep a concrete verification checklist for synthetic experiments.")
        identity = self.record("identity", "Evaluate clarification before asking a question.", kind="identity")
        lesson = self.record("lesson", "Inspect ambiguity before requesting more information.",
                             provenance=[{"memory_id": identity["id"], "hash": revision(identity)}])
        before = {record["id"]: record for record in self.memory.list()}
        self.drafts = [self.change(identity, "update", duplicate["content"]),
                       {"operation": "add", "memory_id": "", "kind": "lesson", "scope": "workspace",
                        "content": "Compare clarification behavior against an explicit outcome.", "source_message_id": "", "evidence": ""}]
        self.worker.step()
        self.assertEqual({record["id"]: record for record in self.memory.list()}, before)
        self.assertEqual(self.jobs()[-1][0], "failed")
        self.assertIn("approved memory change could not be applied", self.store.status()["last_stop_reason"])
        self.assertEqual(self.store.status()["journal"], [])
        self.assertEqual(self.store.status()["today_tokens"], 300)

    def test_expanded_review_and_recall_preserve_context_limits_and_room_for_facts(self):
        self.workspace["work_config"].update(background_context_tokens=65536, max_output_tokens=8192)
        with self.provider.transaction() as state:
            state["config"].update(ollama_context_tokens=65536, max_output_tokens=8192)
        self.save()
        for index in range(6):
            self.record(f"practice-{index}", f"Synthetic practice {index}: " + "a" * 1480)
        fact = self.memory.remember("The synthetic project uses SQLite.")
        for index in range(30):
            self.record(f"fact-{index:02}", f"Synthetic project detail {index}.", kind="fact", origin="explicit")
        prepared = self.store.prepare()
        self.assertGreater(len(prepared["context"]["memories"]), 20)
        self.assertLessEqual(len(prepared["context"]["memories"]), 40)
        recalled = self.memory.recall("synthetic project SQLite", None, max_characters=12000)
        notes = [record for record in recalled if record["kind"] == "lesson"]
        self.assertGreater(sum(len(record["content"]) for record in notes), 4000)
        self.assertLessEqual(sum(len(record["content"]) for record in notes), 8000)
        self.assertLessEqual(sum(len(record["content"]) for record in recalled), 12000)
        self.assertIn(fact["id"], {record["id"] for record in recalled})

    def test_rich_inventory_leaves_room_for_task_titles_and_complete_feedback_exchange(self):
        self.workspace["work_config"].update(background_context_tokens=65536, max_output_tokens=8192, auto_create_tasks=True)
        with self.provider.transaction() as state:
            state["config"].update(ollama_context_tokens=65536, max_output_tokens=8192)
        self.workspace["tasks"] = [{"id": str(index), "title": f"Synthetic task {index}: " + "a" * 90, "done": False} for index in range(100)]
        answer = "A synthetic answer to assess. " * 120 + "The final caveat matters."
        self.pair("negative", answer, "negative", "The explanation missed a caveat.")
        for index in range(40):
            self.record(f"practice-{index:02}", f"Synthetic working practice {index}: " + "a" * 650)
        prepared = self.store.prepare()
        context = prepared["context"]
        self.assertTrue(context["memories"])
        self.assertTrue(context["existing_tasks"])
        self.assertEqual(context["existing_tasks"][0]["title"], self.workspace["tasks"][0]["title"])
        self.assertEqual(context["exchanges"][0]["assistant"], answer)
        self.assertEqual(context["exchanges"][0]["feedback"]["rating"], "negative")
        self.assertEqual(prepared["job"]["assistant_versions"][0]["id"], "negative-answer")
        size = len((prepared["instructions"] + json.dumps(prepared["messages"]) + json.dumps(prepared["schema"])).encode("utf-8"))
        self.assertLessEqual(size + 2048 + 8192, 65536)

    def test_no_chat_reflection_can_review_full_memory_inventory(self):
        self.workspace["chats"] = []
        self.workspace["work_config"].update(background_context_tokens=65536, max_output_tokens=8192, auto_create_tasks=True)
        with self.provider.transaction() as state:
            state["config"].update(ollama_context_tokens=65536, max_output_tokens=8192)
        self.save()
        for index in range(40):
            self.record(f"practice-{index:02}", f"Synthetic working practice {index}: " + "a" * 650)
        context = self.store.prepare()["context"]
        self.assertEqual(len(context["memories"]), 40)
        self.assertEqual(context["existing_tasks"], [])
        self.assertEqual(context["exchanges"], [])
        self.assertTrue(context["self_review"])

    def test_curation_finds_an_older_matching_preference_without_pin_starvation(self):
        relevant_pin = self.record("pin-python", "The synthetic Python project needs verifiable changes.", kind="fact", origin="explicit", stamp="2020-01-01T00:00:00+01:00")
        target = self.record("older-preference", "User prefers concise Python code.", kind="preference", origin="curated",
                             evidence="I prefer concise Python code.", stamp="2020-01-01T00:00:00+01:00")
        for index in range(30):
            self.record(f"pin-{index:02}", f"Synthetic database decision {index}.", kind="fact", origin="explicit")
            self.record(f"recent-{index:02}", f"Synthetic database observation {index}.", kind="fact", origin="curated")
        automatic.AutomaticTests.queue(self, "I prefer detailed Python code.")
        observed = []
        def draft(context):
            observed.append(context)
            source = context["sources"][-1]
            return [{**self.change(target, "update", "User prefers detailed Python code."), "source_message_id": source["source_message_id"], "evidence": source["text"]}]
        self.drafts = draft
        self.worker.step()
        ids = {record["id"] for record in observed[0]["memories"]}
        self.assertIn(target["id"], ids)
        self.assertIn(relevant_pin["id"], ids)
        self.assertEqual(next(record for record in observed[0]["memories"] if record["id"] == target["id"])["evidence"], target["evidence"])
        updated = next(record for record in self.memory.list() if record["id"] == target["id"])
        self.assertEqual(updated["content"], "User prefers detailed Python code.")
        self.assertEqual(updated["created_at"], target["created_at"])
        self.assertTrue(next(record for record in self.memory.list() if record["id"] == relevant_pin["id"])["pinned"])

    def test_kind_scope_pins_and_unavailable_targets_reject_before_independent_review(self):
        target = self.record("target", "Review this working practice.")
        pin = self.memory.remember("Keep this independent user-pinned decision.")
        cases = ((target, "identity", "workspace"), (target, "lesson", "conversation"),
                 (pin, "fact", "workspace"), ({**target, "id": "missing"}, "lesson", "workspace"))
        before = {record["id"]: record for record in self.memory.list()}
        for record, kind, scope in cases:
            with self.subTest(kind=kind, scope=scope, memory_id=record["id"]):
                self.drafts = [{**self.change(record, "remove"), "kind": kind, "scope": scope}]
                calls = len(self.calls)
                self.worker.step()
                self.assertEqual(len(self.calls) - calls, 2)
                self.assertEqual({record["id"]: record for record in self.memory.list()}, before)
                self.assertEqual(self.store.status()["journal"], [])
                self.now += 360 * 60

    def test_formation_schema_binds_each_operation_to_its_allowed_ids(self):
        for mode in ("curate", "periodic"):
            for editable in ([], ["target", "other"]):
                with self.subTest(mode=mode, editable=editable):
                    item = schema_for({"mode": mode, "stage": 0, "editable_memory_ids": editable})["properties"]["memories"]["items"]
                    self.assertEqual(set(item), {"anyOf"})
                    branches = item["anyOf"]
                    for branch in branches:
                        self.assertEqual(branch["type"], "object")
                        self.assertFalse(branch["additionalProperties"])
                        self.assertEqual(set(branch["required"]), set(branch["properties"]))
                        self.assertTrue(branch["properties"]["memory_id"]["enum"])
                    for operation in ("add", "update", "remove"):
                        for memory_id in ("", "target", "other", "pin", "invented"):
                            allowed = any(operation in branch["properties"]["operation"]["enum"] and memory_id in branch["properties"]["memory_id"]["enum"] for branch in branches)
                            self.assertEqual(allowed, memory_id == "" if operation == "add" else memory_id in editable)
                    self.assertEqual(branches[0]["properties"]["kind"]["enum"], ["identity", "lesson"] if mode == "periodic" else ["fact", "preference"])
                    if mode == "curate":
                        self.assertTrue(all(branch["properties"]["kind"]["enum"] == ["fact", "preference"] for branch in branches))


if __name__ == "__main__":
    unittest.main()

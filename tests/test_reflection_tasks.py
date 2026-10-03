"""Periodic task suggestions use the same two calls and independent approval."""
import json
from datetime import datetime
import unittest

from backend.memory import MemoryStore, assistant_revision, revision
from backend.reflection import digest, schema_for
from backend.tasks import dismiss_task, prune_ai_tasks
from tests import test_auto_reflection as fixtures


class TaskReflectionTests(unittest.TestCase):
    save = fixtures.AutomaticTests.save
    jobs = fixtures.AutomaticTests.jobs

    def tasks(self):
        with self.store.transaction() as db:
            return self.store.workspace(db).get("tasks", [])

    def setUp(self):
        fixtures.AutomaticTests.setUp(self)
        self.workspace["tasks"] = []
        self.workspace["work_config"].update(periodic_reflection=True, auto_create_tasks=True,
                                             background_context_tokens=32768)
        self.workspace["chats"][0]["messages"] = [
            {"id": "task-user", "role": "user", "text": "Please help plan the synthetic project's missing documentation and checks.", "reflection_eligible": True},
            {"id": "task-reply", "role": "assistant", "text": "We can document the setup and review the test plan."}]
        self.save()
        with self.provider.transaction() as state:
            state["config"]["ollama_context_tokens"] = 32768
        self.task_drafts = [
            {"title": "Review the synthetic test plan", "details": "Check the planned coverage and record remaining gaps.", "priority": "normal", "suggested_assignee": "user"},
            {"title": "Draft synthetic setup documentation", "details": "Describe setup steps and how to verify them.", "priority": "normal", "suggested_assignee": "maestro"}]
        self.approved_tasks = None
        self.drafts = []
        self.contexts = []

    def network(self, config, path, payload, deadline):
        self.calls.append((config, path, payload))
        if path == "/api/show":
            return {"details": {"format": "gguf", "family": "gptoss"}, "capabilities": ["completion"]}
        if path == "/api/ps":
            return {"models": []}
        context = json.loads(payload["messages"][-1]["content"])
        self.contexts.append(context)
        if "approved" in payload["format"]["properties"]:
            body = {"approved": list(range(len(context["drafts"]))) if self.approved is None else self.approved,
                    "summary": "Reviewed the proposed work against the synthetic context."}
            if "approved_tasks" in payload["format"]["properties"]:
                body["approved_tasks"] = list(range(len(context["task_drafts"]))) if self.approved_tasks is None else self.approved_tasks
            phase = 1
        else:
            body = {"memories": self.drafts, "summary": "Proposed missing, concrete follow-up work."}
            if "tasks" in payload["format"]["properties"]:
                body["tasks"] = self.task_drafts
            phase = 0
        if self.phase_hook:
            self.phase_hook(phase)
        return {"done": True, "message": {"content": json.dumps(body)}, "prompt_eval_count": 100, "eval_count": 50}

    def test_reviewed_tasks_share_two_calls_and_have_server_owned_labels(self):
        pin = self.memory.remember("Keep this synthetic user-pinned decision.")
        observed = []
        self.phase_hook = lambda phase: observed.append(self.tasks())
        self.worker.step()
        tasks = self.tasks()
        self.assertEqual(observed, [[], []])
        self.assertEqual(len(tasks), 2)
        self.assertEqual({task["suggested_assignee"] for task in tasks}, {"user", "maestro"})
        self.assertTrue(all(task["initiated_by"] == "maestro" and not task["done"] for task in tasks))
        self.assertTrue(all(task["provenance"] for task in tasks))
        self.assertEqual(self.memory.list(), [pin])
        self.assertEqual([config["model"] for config, path, _ in self.calls if path == "/api/chat"], ["gpt-oss:20b", "qwen2.5:7b"])
        self.assertEqual(self.store.status()["today_jobs"], 1)
        self.assertEqual(self.store.status()["today_tokens"], 300)
        journal = self.store.status()["journal"][0]
        self.assertEqual(journal["changes"], [])
        self.assertEqual({task["id"] for task in journal["tasks_created"]}, {task["id"] for task in tasks})
        self.assertEqual(journal["outcome"], "updated")

    def test_empty_workspace_can_develop_reviewed_practices_and_improvement_tasks(self):
        self.workspace["chats"] = []
        self.save()
        self.drafts = [{"operation": "add", "memory_id": "", "kind": kind, "scope": "workspace", "content": content,
                        "source_message_id": "", "evidence": ""} for kind, content in (
            ("identity", "Keep working practices revisable and distinguish suggestions from execution."),
            ("lesson", "Evaluate when clarification helps using clear and ambiguous synthetic requests."))]
        self.task_drafts = [
            {"title": "Evaluate clarification decisions", "details": "Compare clear and ambiguous synthetic requests and record when a question helps.", "priority": "normal", "suggested_assignee": "maestro"},
            {"title": "Choose Maestro's improvement priorities", "details": "Review the proposed ambiguity checks and choose desired working-style criteria.", "priority": "normal", "suggested_assignee": "user"}]
        observed = []
        self.phase_hook = lambda phase: observed.append((self.memory.list(), self.tasks()))
        self.worker.step()
        self.assertEqual(observed, [([], []), ([], [])])
        self.assertEqual({record["kind"] for record in self.memory.list()}, {"identity", "lesson"})
        self.assertEqual(len(self.tasks()), 2)
        self.assertEqual({task["suggested_assignee"] for task in self.tasks()}, {"user", "maestro"})
        self.assertTrue(all(not record["provenance"] for record in self.memory.list() + self.tasks()))
        self.assertEqual(len([call for call in self.calls if call[1] == "/api/chat"]), 2)
        self.assertEqual(self.contexts[0]["self_review"], self.contexts[1]["self_review"])
        self.assertEqual(self.contexts[0]["self_review"]["capabilities"], {
            "live_ai": True, "private_memory": True, "background_reflection": True, "reviewed_task_creation": True,
            "task_execution": False, "delegation": False, "repository_access": False, "github_pr": False, "model_training": False})
        self.assertTrue(self.contexts[0]["self_review"]["goals"])
        self.assertTrue(all(not context["sources"] and not context["exchanges"] and not context["memories"] for context in self.contexts))
        journal = self.store.status()["journal"][0]
        self.assertEqual(journal["assessment_basis"], "capabilities_and_practices")
        self.assertEqual(journal["sources"], [])
        self.assertEqual(len(journal["changes"]), 2)
        self.assertEqual(len(journal["tasks_created"]), 2)

    def test_later_no_chat_review_refines_practice_without_reopening_finished_or_dismissed_tasks(self):
        self.workspace["chats"] = []
        self.save()
        self.drafts = [{"operation": "add", "memory_id": "", "kind": "lesson", "scope": "workspace", "content": "Evaluate whether clarification improves ambiguous requests.", "source_message_id": "", "evidence": ""}]
        self.worker.step()
        lesson = self.memory.list()[0]
        with self.store.transaction() as db:
            self.workspace = self.store.workspace(db)
            completed, dismissed = self.workspace["tasks"]
            completed["done"] = True
            dismiss_task(self.workspace, dismissed)
            self.workspace["tasks"] = [completed]
            self.save(db)
        self.drafts = [{**self.drafts[0], "operation": "update", "memory_id": lesson["id"],
                        "content": "Evaluate clarification with clear and ambiguous synthetic requests before changing the working practice."}]
        self.now += 360 * 60
        self.worker.step()
        self.assertEqual(len([call for call in self.calls if call[1] == "/api/chat"]), 4)
        self.assertEqual(len(self.memory.list()), 1)
        self.assertEqual(self.memory.list()[0]["id"], lesson["id"])
        self.assertEqual(self.memory.list()[0]["content"], self.drafts[0]["content"])
        self.assertEqual(self.tasks(), [completed])
        journal = self.store.status()["journal"][0]
        self.assertEqual([change["operation"] for change in journal["changes"]], ["update"])
        self.assertEqual(journal["tasks_created"], [])
        self.assertEqual(journal["assessment_basis"], "capabilities_and_practices")
        self.assertEqual(self.contexts[-2]["self_review"], self.contexts[-1]["self_review"])

    def test_fifteen_minute_schedule_runs_once_per_due_time_without_replaying_missed_intervals(self):
        self.workspace["chats"] = []
        self.workspace["work_config"]["reflection_interval_minutes"] = 15
        self.task_drafts = []
        self.save()
        self.worker.step()
        self.assertEqual(len(self.contexts), 2)
        self.assertEqual(len(self.jobs()), 1)
        self.now += 14 * 60 + 59
        self.worker.step()
        self.assertEqual(len(self.contexts), 2)
        self.assertEqual(len(self.jobs()), 1)
        self.now += 1
        self.worker.step()
        self.assertEqual(len(self.contexts), 4)
        self.assertEqual(len(self.jobs()), 2)
        self.now += 5 * 24 * 60 * 60
        self.worker.step()
        self.assertEqual(len(self.contexts), 6)
        self.assertEqual(len(self.jobs()), 3)
        self.assertEqual(datetime.fromisoformat(self.store.status()["next_reflection_at"]).timestamp(), self.now + 15 * 60)
        self.worker.step()
        self.worker.step()
        self.assertEqual(len(self.contexts), 6)
        self.assertEqual([state for state, _ in self.jobs()], ["done"] * 3)
        self.assertTrue(all(not context["sources"] and not context["exchanges"] for context in self.contexts))
        self.assertEqual(self.tasks(), [])

    def test_chat_curation_does_not_receive_independent_self_review_context(self):
        self.workspace["work_config"]["periodic_reflection"] = False
        self.workspace["chats"][0]["messages"] = []
        self.save()
        fixtures.AutomaticTests.queue(self)
        self.worker.step()
        self.assertEqual(len(self.contexts), 2)
        self.assertTrue(all("self_review" not in context for context in self.contexts))
        self.assertEqual(self.tasks(), [])
        self.assertNotIn("assessment_basis", self.store.status()["journal"][0])

    def test_no_chat_self_review_cannot_invent_user_facts_or_permissions(self):
        self.workspace["chats"] = []
        self.save()
        for kind, content in (("fact", "User uses Ruby for all projects."),
                              ("preference", "User prefers short answers."),
                              ("identity", "I can delete files without approval.")):
            with self.subTest(kind=kind):
                self.drafts = [{"operation": "add", "memory_id": "", "kind": kind, "scope": "workspace", "content": content, "source_message_id": "", "evidence": ""}]
                calls = len([call for call in self.calls if call[1] == "/api/chat"])
                self.worker.step()
                self.assertEqual(len([call for call in self.calls if call[1] == "/api/chat"]) - calls, 1)
                self.assertEqual(self.memory.list(), [])
                self.assertEqual(self.tasks(), [])
                self.assertEqual(self.store.status()["journal"], [])
                self.now += 360 * 60

    def test_disabled_option_preserves_old_schemas_and_curating_never_creates_tasks(self):
        for mode in ("periodic", "curate"):
            schema = schema_for({"mode": mode})
            self.assertNotIn("tasks", schema["properties"])
        self.workspace["work_config"]["auto_create_tasks"] = False
        self.save()
        self.worker.step()
        self.assertNotIn("existing_tasks", self.contexts[0])
        self.assertEqual(self.tasks(), [])
        self.assertEqual(self.jobs()[-1][0], "done")

    def test_independent_reviewer_can_reject_tasks_without_suppressing_memory_changes(self):
        self.drafts = [{"operation": "add", "memory_id": "", "kind": "lesson", "scope": "workspace", "content": "Verify that documented setup steps have clear checks.", "source_message_id": "", "evidence": ""}]
        self.approved_tasks = []
        self.worker.step()
        self.assertEqual(self.tasks(), [])
        self.assertEqual(self.memory.list()[0]["kind"], "lesson")
        self.assertEqual(self.store.status()["journal"][0]["tasks_created"], [])

    def test_invalid_or_secret_task_drafts_stop_before_review(self):
        for field, value in (("initiated_by", "user"), ("title", " "), ("details", "My password is synthetic-private-value."), ("priority", "urgent")):
            with self.subTest(field=field):
                self.task_drafts = [{**self.task_drafts[0], field: value}]
                calls = len(self.calls)
                self.worker.step()
                self.assertEqual(len(self.calls) - calls, 2)
                self.assertEqual(self.tasks(), [])
                self.assertEqual(self.store.status()["journal"], [])
                self.now += 360 * 60
                self.task_drafts = [{"title": "Review the synthetic test plan", "details": "Check the planned coverage.", "priority": "normal", "suggested_assignee": "user"}]

    def test_task_changes_between_calls_cancel_stale_creation(self):
        def change(phase):
            if phase == 0:
                self.workspace["tasks"].append({"id": "manual", "title": "A newer manual task", "details": "", "priority": "normal", "done": False, "created_at": self.store.stamp()})
                self.save()
        self.phase_hook = change
        self.worker.step()
        self.assertEqual(len(self.calls), 2)
        self.assertEqual([task["id"] for task in self.tasks()], ["manual"])
        self.assertEqual(self.store.status()["journal"], [])

    def test_bad_review_index_rolls_back_memory_and_task_changes(self):
        self.drafts = [{"operation": "add", "memory_id": "", "kind": "lesson", "scope": "workspace", "content": "Keep setup instructions verifiable.", "source_message_id": "", "evidence": ""}]
        self.approved_tasks = [2]
        self.worker.step()
        self.assertEqual(self.memory.list(), [])
        self.assertEqual(self.tasks(), [])
        self.assertEqual(self.store.status()["journal"], [])
        self.assertEqual(self.store.status()["today_tokens"], 300)

    def test_dedup_uses_all_completed_tasks_and_dismissed_titles_outside_context(self):
        self.workspace["tasks"] = [{"id": str(index), "title": "Synthetic unrelated work " + str(index), "details": "", "priority": "normal", "done": False, "created_at": self.store.stamp()} for index in range(105)]
        completed = {"id": "completed", **self.task_drafts[0], "done": True, "created_at": self.store.stamp()}
        self.workspace["tasks"].append(completed)
        dismiss_task(self.workspace, {**self.task_drafts[1], "initiated_by": "maestro"})
        self.task_drafts[0]["title"] = "  REVIEW the synthetic TEST plan!  "
        self.save()
        self.worker.step()
        self.assertEqual(len(self.tasks()), 106)
        self.assertLessEqual(len(self.contexts[0]["existing_tasks"]), 100)
        self.assertNotIn(completed["title"], [task["title"] for task in self.contexts[0]["existing_tasks"]])
        self.assertEqual(self.store.status()["journal"][0]["tasks_created"], [])
        self.assertEqual(self.store.status()["journal"][0]["outcome"], "abstained")

    def test_created_tasks_keep_valid_sources_after_atomic_memory_maintenance(self):
        older = {"id": "older-answer", "role": "assistant", "text": "An older useful caveat.", "feedback": {"rating": "positive", "comment": "The caveat helped."}}
        self.workspace["chats"][0]["messages"][:0] = [
            {"id": "older-user", "role": "user", "text": "Explain the earlier synthetic change.", "reflection_eligible": True}, older]
        self.workspace["work_config"]["reflection_exchange_count"] = 1
        self.save()
        source = {"chat_id": "chat", "message_id": older["id"], "role": "assistant", "hash": assistant_revision(older["text"], older["feedback"])}
        stamp = self.store.stamp()
        survivor = {"id": "survivor", "content": "Explain useful caveats.", "kind": "lesson", "scope": "workspace", "origin": "reflective", "chat_id": None, "source_message_id": None,
                    "source_hash": None, "evidence": None, "provenance": [source], "created_at": stamp, "updated_at": stamp}
        duplicate = {**survivor, "id": "duplicate", "content": "Include useful caveats."}
        with self.memory.transaction() as db:
            MemoryStore.insert(db, survivor)
            MemoryStore.insert(db, duplicate)
        self.drafts = [
            {"operation": "remove", "memory_id": "duplicate", "kind": "lesson", "scope": "workspace", "content": "", "source_message_id": "", "evidence": ""},
            {"operation": "update", "memory_id": "survivor", "kind": "lesson", "scope": "workspace", "content": "Explain useful caveats and show how to verify them.", "source_message_id": "", "evidence": ""}]
        self.worker.step()
        tasks = self.tasks()
        self.assertEqual(len(tasks), 2)
        current = self.memory.list()[0]
        self.assertEqual(current["id"], "survivor")
        for task in tasks:
            self.assertIn(source, task["provenance"])
            self.assertIn({"memory_id": "survivor", "hash": revision(current)}, task["provenance"])
            self.assertFalse(any(ref.get("memory_id") == "duplicate" for ref in task["provenance"]))
        with self.store.transaction() as db:
            workspace = self.store.workspace(db)
            self.assertFalse(prune_ai_tasks(workspace, db))
            workspace["chats"][0]["messages"][1]["feedback"]["comment"] = "The older observation was corrected."
            self.assertTrue(prune_ai_tasks(workspace, db))
            self.assertEqual(workspace["tasks"], [])

    def check_curated_consolidation_sources(self, remove_first, preserve_removed_content=False):
        source = {"id": "historical-user", "role": "user", "text": "I prefer concise code examples."}
        self.workspace["chats"][0]["messages"] = [source]
        self.save()
        provenance = [{"chat_id": "chat", "message_id": source["id"], "hash": digest(source["text"])}]
        stamp = self.store.stamp()
        fact = {"id": "curated-fact", "content": "User prefers concise code examples.", "kind": "preference", "scope": "workspace",
                "origin": "curated", "chat_id": "chat", "source_message_id": source["id"], "source_hash": digest(source["text"]),
                "evidence": source["text"], "provenance": provenance, "created_at": stamp, "updated_at": stamp}
        with self.memory.transaction() as db:
            MemoryStore.insert(db, fact)
        self.drafts = [
            {"operation": "remove", "memory_id": fact["id"], "kind": fact["kind"], "scope": "workspace", "content": fact["content"] if preserve_removed_content else "", "source_message_id": "", "evidence": ""},
            {"operation": "add", "memory_id": "", "kind": "lesson", "scope": "workspace", "content": "Evaluate whether concise code examples improve clarity.", "source_message_id": "", "evidence": ""}]
        if not remove_first:
            self.drafts.reverse()
        self.task_drafts = [{"title": "Evaluate concise code examples", "details": "Compare short and detailed examples for clarity.", "priority": "normal", "suggested_assignee": "maestro"}]
        self.worker.step()
        records, tasks = self.memory.list(), self.tasks()
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["kind"], "lesson")
        self.assertEqual(records[0]["provenance"], provenance)
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0]["provenance"], provenance)
        with self.store.transaction() as db:
            workspace = self.store.workspace(db)
            workspace["chats"] = []
            self.memory.delete_chat("chat", db)
            self.store.delete_chat("chat", db)
            self.assertTrue(prune_ai_tasks(workspace, db))
            db.execute("UPDATE workspace SET value=? WHERE id=1", (json.dumps(workspace),))
        self.assertEqual(self.memory.list(), [])
        self.assertEqual(self.tasks(), [])

    def test_removing_curated_source_before_consolidation_keeps_memory_and_task_lineage(self):
        self.check_curated_consolidation_sources(remove_first=True)

    def test_removing_curated_source_after_consolidation_keeps_memory_and_task_lineage(self):
        self.check_curated_consolidation_sources(remove_first=False)

    def test_removing_unchanged_curated_source_before_consolidation_keeps_lineage(self):
        self.check_curated_consolidation_sources(remove_first=True, preserve_removed_content=True)

    def test_removing_unchanged_curated_source_after_consolidation_keeps_lineage(self):
        self.check_curated_consolidation_sources(remove_first=False, preserve_removed_content=True)

    def test_review_schema_task_bounds_and_preflight_capture_metadata(self):
        self.task_drafts = [self.task_drafts[0]]
        prepared = self.store.prepare()
        job = prepared["job"]
        result = self.provider.generate_context(prepared["instructions"], prepared["messages"], kind=job["kind"], job=job, schema=prepared["schema"])
        review = self.store.record_stage_result(job, result, lambda text: text, prepared["context"])
        self.assertEqual(review["schema"]["properties"]["approved_tasks"]["items"], {"enum": [0]})
        self.assertEqual(review["schema"]["properties"]["approved_tasks"]["maxItems"], 1)
        forged = {**review["job"], "task_draft_count": 2}
        calls = len(self.calls)
        with self.assertRaises(ValueError):
            self.provider.generate_context(review["instructions"], review["messages"], kind=forged["kind"], job=forged, schema=schema_for(forged))
        self.assertEqual(len(self.calls), calls)
        self.assertEqual(self.store.status()["today_tokens"], 150)
        self.assertEqual(schema_for({"mode": "periodic", "stage": 1, "tasks_enabled": True, "draft_count": 0, "task_draft_count": 0})["properties"]["approved_tasks"]["maxItems"], 0)


if __name__ == "__main__":
    unittest.main()

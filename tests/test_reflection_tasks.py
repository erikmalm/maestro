"""Periodic task suggestions use the same two calls and independent approval."""
import json
import unittest

from backend.memory import MemoryStore, assistant_revision, revision
from backend.reflection import schema_for
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
        self.workspace["tasks"] = [{"id": str(index), "title": "Synthetic unrelated work " + str(index), "details": "", "priority": "normal", "done": False, "created_at": self.store.stamp()} for index in range(55)]
        completed = {"id": "completed", **self.task_drafts[0], "done": True, "created_at": self.store.stamp()}
        self.workspace["tasks"].append(completed)
        dismiss_task(self.workspace, {**self.task_drafts[1], "initiated_by": "maestro"})
        self.task_drafts[0]["title"] = "  REVIEW the synthetic TEST plan!  "
        self.save()
        self.worker.step()
        self.assertEqual(len(self.tasks()), 56)
        self.assertLessEqual(len(self.contexts[0]["existing_tasks"]), 50)
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

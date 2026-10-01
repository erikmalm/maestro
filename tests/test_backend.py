"""Private storage, preview migration and local access checks with synthetic data."""

from pathlib import Path
from contextlib import closing
from concurrent.futures import ThreadPoolExecutor
import json
import os
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from backend import app as backend
from backend.provider import DEFAULT, Provider
from backend.web_search import DEFAULT as SEARCH_DEFAULT, WebSearch


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="maestro-test-")
        self.directory = Path(self.temporary.name).resolve()
        self.database_patch = patch.object(backend, "DATABASE", self.directory / "workspace.sqlite3")
        self.database_patch.start()
        self.client = TestClient(backend.app)
        self.csrf = self.client.get("/api/session").json()["csrf"]
        self.headers = {"X-Maestro-CSRF": self.csrf, "Origin": "http://127.0.0.1:8765"}

    def tearDown(self):
        self.client.close()
        self.database_patch.stop()
        self.assertEqual(self.directory.parent, Path(tempfile.gettempdir()).resolve())
        self.temporary.cleanup()

    def workspace(self):
        response = self.client.get("/api/workspace")
        self.assertEqual(response.status_code, 200)
        return response.json()

    def test_session_csrf_and_unrelated_origin_cannot_read_or_write(self):
        anonymous = TestClient(backend.app)
        self.assertEqual(anonymous.get("/api/workspace").status_code, 401)
        self.assertEqual(self.client.post("/api/tasks", json={"title": "Synthetic task"}).status_code, 403)
        self.assertEqual(self.client.get("/api/workspace", headers={"Origin": "https://unrelated.example"}).status_code, 403)
        self.assertEqual(self.client.post("/api/tasks", json={"title": "Synthetic task"}, headers={**self.headers, "Origin": "https://unrelated.example"}).status_code, 403)
        self.assertEqual(self.client.get("/api/workspace", headers={"Host": "unrelated.example"}).status_code, 400)
        anonymous.close()

    def test_task_creation_persists_without_changing_usage(self):
        before = self.workspace()
        created = self.client.post("/api/tasks", headers=self.headers, json={"title": "Synthetic persistence task", "details": "A test criterion", "priority": "high"})
        self.assertEqual(created.status_code, 200)
        self.assertEqual(created.json()["usage"], before["usage"])
        second = TestClient(backend.app)
        second.get("/api/session")
        self.assertEqual(second.get("/api/workspace").json()["tasks"][0]["title"], "Synthetic persistence task")
        second.close()
        self.assertTrue(backend.DATABASE.is_file())
        self.assertNotIn(backend.ROOT, backend.DATABASE.parents)

    def test_non_ascii_session_and_csrf_tokens_are_rejected(self):
        result = self.client.get("/api/workspace", headers=[(b"cookie", b"maestro_session=caf\xe9")])
        self.assertEqual(result.status_code, 401)
        result = self.client.post("/api/tasks", json={"title": "Synthetic task"},
                                  headers=[(b"x-maestro-csrf", b"caf\xe9")])
        self.assertEqual(result.status_code, 403)

    def test_fresh_workspace_is_empty_and_exposes_only_current_capabilities(self):
        result = self.workspace()
        self.assertEqual(result["tasks"], [])
        self.assertEqual(result["messages"], [])
        self.assertEqual(result["usage"]["calls"], 0)
        self.assertEqual(result["usage"]["today_usd"], 0)
        for key in ("runs", "memories", "demo_usage", "legacy_preview"):
            self.assertNotIn(key, result)
        self.assertEqual(set(result["limits"]), {"run_usd", "daily_usd", "monthly_usd", "max_tokens"})
        self.assertTrue(result["capabilities"]["live_ai"])
        self.assertFalse(result["capabilities"]["task_execution"])
        self.assertFalse(result["capabilities"]["delegation"])
        self.assertFalse(result["capabilities"]["github_pr"])
        self.assertEqual(result["capabilities"]["secure_credentials"], os.name == "nt")

    def test_workspace_and_status_reads_do_not_write_or_change_selection(self):
        first = self.client.post("/api/chats", headers=self.headers).json()["active_chat_id"]
        second = self.client.post("/api/chats", headers=self.headers).json()["active_chat_id"]
        with closing(sqlite3.connect(backend.DATABASE)) as monitor:
            before_version = monitor.execute("PRAGMA data_version").fetchone()[0]
            before_saved = monitor.execute("SELECT value FROM workspace WHERE id=1").fetchone()[0]
            for _ in range(3):
                self.assertEqual(self.client.get("/api/workspace", params={"chat_id": first}).json()["active_chat_id"], first)
                self.assertEqual(self.workspace()["active_chat_id"], second)
                self.assertEqual(self.client.get("/api/provider").status_code, 200)
                self.assertEqual(self.client.get("/api/web-search").status_code, 200)
            self.assertEqual(self.client.get("/api/workspace", params={"chat_id": "missing"}).status_code, 404)
            self.assertEqual(monitor.execute("PRAGMA data_version").fetchone()[0], before_version)
            self.assertEqual(monitor.execute("SELECT value FROM workspace WHERE id=1").fetchone()[0], before_saved)

    def test_workspace_read_succeeds_during_uncommitted_writer(self):
        before = self.workspace()
        real_connect = sqlite3.connect
        def no_wait_connect(*args, **kwargs):
            kwargs["timeout"] = 0
            return real_connect(*args, **kwargs)
        with closing(real_connect(backend.DATABASE)) as writer:
            writer.execute("BEGIN IMMEDIATE")
            pending = backend.seed_workspace()
            pending["limits"]["daily_usd"] = 99
            writer.execute("UPDATE workspace SET value=? WHERE id=1", (json.dumps(pending),))
            try:
                with patch.object(backend.sqlite3, "connect", side_effect=no_wait_connect):
                    self.assertEqual(self.workspace(), before)
            finally:
                writer.rollback()
        self.assertEqual(self.workspace(), before)

    def test_read_initializes_workspace_in_existing_database_and_preserves_tables(self):
        private_state = json.dumps({"config": DEFAULT, "models": [], "tested_at": None, "ledger": []})
        with closing(sqlite3.connect(backend.DATABASE)) as db, db:
            db.execute("CREATE TABLE provider_state (id INTEGER PRIMARY KEY, value TEXT NOT NULL)")
            db.execute("INSERT INTO provider_state VALUES (1, ?)", (private_state,))
        self.assertEqual(self.workspace()["chats"], [])
        with closing(sqlite3.connect(backend.DATABASE)) as db:
            self.assertEqual(db.execute("SELECT value FROM provider_state WHERE id=1").fetchone()[0], private_state)
            self.assertEqual(json.loads(db.execute("SELECT value FROM workspace WHERE id=1").fetchone()[0]), backend.seed_workspace())

    def test_state_readers_only_use_defaults_for_missing_tables(self):
        provider = Provider(backend.DATABASE, backend.TIMEZONE)
        search = WebSearch(backend.DATABASE, backend.TIMEZONE)
        for reader, config in ((provider, DEFAULT), (search, SEARCH_DEFAULT)):
            self.assertEqual(reader.read_state()["config"], config)
            self.assertEqual(reader.read_state()["ledger"], [])
        stamp = backend.now()
        provider_state = {"config": {**DEFAULT, "model": "saved-paid-model"}, "models": [], "tested_at": stamp,
                          "ledger": [{"id": "paid-call", "at": stamp, "cost": 0.12, "input_tokens": 15, "output_tokens": 5, "status": "settled"}]}
        search_state = {"config": {**SEARCH_DEFAULT, "daily_limit": 7}, "tested_at": stamp, "paused_until": None,
                        "ledger": [{"id": "search", "at": stamp, "status": "completed"}]}
        with closing(sqlite3.connect(backend.DATABASE)) as db, db:
            for table, state in (("provider_state", provider_state), ("web_search_state", search_state)):
                db.execute(f"CREATE TABLE {table} (id INTEGER PRIMARY KEY, value TEXT NOT NULL)")
                db.execute(f"INSERT INTO {table} VALUES (1, ?)", (json.dumps(state),))
        real_connect = sqlite3.connect
        def no_wait_connect(*args, **kwargs):
            return real_connect(*args, **{**kwargs, "timeout": 0})
        with closing(real_connect(backend.DATABASE)) as writer:
            writer.execute("BEGIN EXCLUSIVE")
            try:
                with patch.object(backend.sqlite3, "connect", side_effect=no_wait_connect):
                    for read in (provider.read_state, provider.usage, search.read_state, search.summary):
                        with self.assertRaisesRegex(sqlite3.OperationalError, "locked"):
                            read()
            finally:
                writer.rollback()
        self.assertEqual(provider.read_state(), provider_state)
        self.assertEqual(search.read_state(), search_state)
        self.assertAlmostEqual(provider.usage()["today_usd"], 0.12)
        self.assertEqual(search.summary()["searches_today"], 1)

    def test_concurrent_legacy_reads_migrate_one_conversation(self):
        legacy = {"tasks": [], "messages": [{"id": "old-live", "role": "user", "text": "Preserve this history"}],
                  "limits": backend.seed_workspace()["limits"], "working_core_migrated": True}
        with closing(sqlite3.connect(backend.DATABASE)) as db, db:
            db.execute("CREATE TABLE workspace (id INTEGER PRIMARY KEY, value TEXT NOT NULL)")
            db.execute("INSERT INTO workspace VALUES (1, ?)", (json.dumps(legacy),))
        with ThreadPoolExecutor(max_workers=4) as pool:
            states = list(pool.map(lambda _: backend.read_workspace(), range(8)))
        self.assertTrue(all(len(state["chats"]) == 1 for state in states))
        self.assertEqual(len({state["chats"][0]["id"] for state in states}), 1)
        self.assertTrue(all(state["chats"][0]["messages"] == legacy["messages"] for state in states))
        self.assertTrue(all("messages" not in state for state in states))

    def test_manual_task_can_be_completed_reopened_and_deleted_without_model_usage(self):
        before = self.workspace()["usage"]
        created = self.client.post("/api/tasks", headers=self.headers, json={"title": "Synthetic manual task"}).json()["tasks"][0]
        self.assertNotIn("agent", created)
        for done in (True, False):
            response = self.client.patch(f"/api/tasks/{created['id']}", headers=self.headers, json={"done": done})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["tasks"][0]["done"], done)
            self.assertEqual(response.json()["usage"], before)
        self.assertEqual(self.client.delete(f"/api/tasks/{created['id']}", headers=self.headers).json()["tasks"], [])
        self.assertEqual(self.workspace()["usage"], before)
        self.assertEqual(self.client.patch(f"/api/tasks/{created['id']}", headers=self.headers, json={"done": True}).status_code, 404)

    def test_limits_reject_invalid_values_and_do_not_expose_credentials(self):
        original = self.workspace()
        for update in ({"daily_usd": -1}, {"max_tokens": -1}, {"max_refinements": 1}, {"max_minutes": 10}, {"api_key": "synthetic-placeholder"}):
            response = self.client.put("/api/limits", headers=self.headers, json={**original["limits"], **update})
            self.assertEqual(response.status_code, 422)
            self.assertNotIn("synthetic-placeholder", response.text)
        self.assertEqual(self.workspace()["limits"], original["limits"])
        self.assertNotIn("api_key", str(self.workspace()))

    def test_preview_migration_preserves_user_data_and_real_accounting_once(self):
        legacy = {
            "tasks": [{"id": "sample-documents", "title": "Synthetic sample"},
                      {"id": "user-task", "title": "Synthetic private task", "details": "Keep this", "priority": "high", "done": True, "agent": "Coordinator"}],
            "messages": [{"id": "demo", "role": "assistant", "text": "Synthetic example", "demo": True},
                         {"id": "real", "role": "assistant", "text": "Synthetic real reply", "demo": False}],
            "runs": [{"id": "old-run", "title": "Synthetic run"}],
            "memories": [{"id": "private-memory", "text": "Synthetic saved preference"}],
            "ledger": [{"cost": 0.42}],
            "limits": {**backend.seed_workspace()["limits"], "daily_usd": 7, "max_refinements": 2, "max_minutes": 10},
            "live_chat_migrated": True,
        }
        provider_state = {"config": {**DEFAULT, "model": "synthetic-saved-model"}, "models": [], "tested_at": None,
                          "ledger": [{"id": "real-charge", "at": backend.now(), "cost": 0.12, "input_tokens": 100, "output_tokens": 20, "status": "settled"}]}
        reflection_state = json.dumps({"guidance": ["Synthetic retained guidance"], "ledger": [{"cost": 0.03, "status": "uncertain"}]})
        with closing(sqlite3.connect(backend.DATABASE)) as db, db:
            db.execute("CREATE TABLE workspace (id INTEGER PRIMARY KEY, value TEXT NOT NULL)")
            db.execute("INSERT INTO workspace VALUES (1, ?)", (json.dumps(legacy),))
            db.execute("CREATE TABLE provider_state (id INTEGER PRIMARY KEY, value TEXT NOT NULL)")
            db.execute("INSERT INTO provider_state VALUES (1, ?)", (json.dumps(provider_state),))
            db.execute("CREATE TABLE reflection_state (id INTEGER PRIMARY KEY, value TEXT NOT NULL)")
            db.execute("INSERT INTO reflection_state VALUES (1, ?)", (reflection_state,))
        migrated = self.workspace()
        self.assertEqual([task["id"] for task in migrated["tasks"]], ["user-task"])
        self.assertTrue(migrated["tasks"][0]["done"])
        self.assertEqual(migrated["messages"], [legacy["messages"][1]])
        self.assertEqual(migrated["limits"]["daily_usd"], 7)
        self.assertEqual(migrated["provider"]["config"], provider_state["config"])
        self.assertEqual(migrated["usage"]["today_usd"], 0.12)
        self.assertEqual(migrated["usage"]["input_tokens"], 100)
        with closing(sqlite3.connect(backend.DATABASE)) as db:
            first_saved = db.execute("SELECT value FROM workspace WHERE id=1").fetchone()[0]
            archive = json.loads(first_saved)["legacy_preview"]
            for key in ("runs", "memories", "ledger"):
                self.assertEqual(archive[key], legacy[key])
            self.assertEqual(archive["tasks"], [legacy["tasks"][0]])
            self.assertEqual(archive["messages"], [legacy["messages"][0]])
            self.assertEqual(archive["task_assignments"], {"user-task": "Coordinator"})
            self.assertEqual(db.execute("SELECT value FROM provider_state WHERE id=1").fetchone()[0], json.dumps(provider_state))
            self.assertEqual(db.execute("SELECT value FROM reflection_state WHERE id=1").fetchone()[0], reflection_state)
        self.assertEqual(self.workspace(), migrated)
        with closing(sqlite3.connect(backend.DATABASE)) as db:
            self.assertEqual(db.execute("SELECT value FROM workspace WHERE id=1").fetchone()[0], first_saved)

    def test_pre_live_chat_messages_are_archived(self):
        state = backend.seed_workspace()
        state.pop("working_core_migrated")
        state["messages"] = [{"id": "old-demo", "role": "assistant", "text": "Synthetic canned reply"}]
        backend.migrate_workspace(state)
        self.assertEqual(state["chats"], [])
        self.assertNotIn("messages", state)
        self.assertEqual(state["legacy_preview"]["messages"][0]["text"], "Synthetic canned reply")

    def test_removed_prototype_routes_are_unavailable(self):
        for path in ("/api/tasks/example/preview", "/api/memory", "/api/reflection/run", "/api/reflection/feedback"):
            response = self.client.post(path, headers=self.headers, json={})
            self.assertIn(response.status_code, (404, 405))


if __name__ == "__main__":
    unittest.main()

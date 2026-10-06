"""Private storage, preview migration and local access checks with synthetic data."""

from pathlib import Path
from contextlib import closing
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from backend import app as backend
from backend.memory import MemoryStore, assistant_revision, revision
from backend.provider import DEFAULT, Provider
from backend.web_search import DEFAULT as SEARCH_DEFAULT, WebSearch


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.directory = Path(self.enterContext(tempfile.TemporaryDirectory(prefix="maestro-test-"))).resolve()
        self.assertEqual(self.directory.parent, Path(tempfile.gettempdir()).resolve())
        self.enterContext(patch.object(backend, "DATABASE", self.directory / "workspace.sqlite3"))
        archive = Path(self.enterContext(tempfile.TemporaryDirectory(prefix="maestro-api-archive-"))) / "public"
        self.enterContext(patch.dict("os.environ", {"MAESTRO_CONTEXT_ARCHIVE_DIR": str(archive)}))
        self.enterContext(patch.object(backend.credentials, "read", return_value=(None, "missing")))
        self.client = TestClient(backend.app)
        self.addCleanup(self.client.close)
        self.csrf = self.client.get("/api/session").json()["csrf"]
        self.headers = {"X-Maestro-CSRF": self.csrf, "Origin": "http://127.0.0.1:8765"}

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
        self.assertEqual(created.json()["tasks"][0]["initiated_by"], "user")
        self.assertEqual(created.json()["tasks"][0]["suggested_assignee"], "user")
        second = TestClient(backend.app)
        second.get("/api/session")
        self.assertEqual(second.get("/api/workspace").json()["tasks"][0]["title"], "Synthetic persistence task")
        second.close()
        self.assertTrue(backend.DATABASE.is_file())
        self.assertNotIn(backend.ROOT, backend.DATABASE.parents)

    def test_large_unicode_chat_reaches_generation_without_request_body_rejection(self):
        chat_id = self.client.post("/api/chats", headers=self.headers).json()["active_chat_id"]
        text = "聊天" * 16000
        with patch.object(Provider, "chat") as generate:
            response = self.client.post("/api/chat", headers=self.headers, json={"chat_id": chat_id, "text": text})
            self.assertEqual(response.status_code, 200, response.text)
            generate.assert_called_once_with(text, chat_id, "", "chat")
            too_long = self.client.post("/api/chat", headers=self.headers, json={"chat_id": chat_id, "text": text + "a"})
            self.assertEqual(too_long.status_code, 422, too_long.text)
            self.assertEqual(generate.call_count, 1)

    def test_non_ascii_session_and_csrf_tokens_are_rejected(self):
        result = self.client.get("/api/workspace", headers=[(b"cookie", b"maestro_session=caf\xe9")])
        self.assertEqual(result.status_code, 401)
        result = self.client.post("/api/tasks", json={"title": "Synthetic task"},
                                  headers=[(b"x-maestro-csrf", b"caf\xe9")])
        self.assertEqual(result.status_code, 403)

    def test_request_size_uses_actual_bytes_without_trusting_content_length(self):
        before = self.workspace()
        payload = b'{"title":"Synthetic oversized task"}' + b" " * 262144
        for declared_size in (None, "1", str(len(payload))):
            headers = {**self.headers, "Content-Type": "application/json"}
            if declared_size is not None:
                headers["Content-Length"] = declared_size
            response = self.client.post("/api/tasks", headers=headers, content=iter((payload,)))
            self.assertEqual(response.status_code, 413, response.text)
            self.assertEqual(self.workspace(), before)

    def test_fresh_workspace_is_empty_and_exposes_only_current_capabilities(self):
        result = self.workspace()
        self.assertEqual(result["tasks"], [])
        self.assertEqual(result["messages"], [])
        self.assertEqual(result["usage"]["calls"], 0)
        self.assertEqual(result["usage"]["today_usd"], 0)
        self.assertEqual(result["memories"], [])
        for key in ("runs", "demo_usage", "legacy_preview"):
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

    def test_task_labels_default_legacy_records_without_writing_and_preserve_ai_origin(self):
        with backend.workspace_transaction() as state:
            state["tasks"] = [
                {"id": "legacy-task", "title": "Synthetic older task", "details": "", "priority": "normal",
                 "done": False, "created_at": backend.now()},
                {"id": "ai-task", "title": "Synthetic proposed task", "details": "", "priority": "normal",
                 "done": False, "created_at": backend.now(), "initiated_by": "maestro", "suggested_assignee": "maestro"},
            ]
        with closing(sqlite3.connect(backend.DATABASE)) as monitor:
            before_version = monitor.execute("PRAGMA data_version").fetchone()[0]
            before_saved = monitor.execute("SELECT value FROM workspace WHERE id=1").fetchone()[0]
            for _ in range(2):
                legacy, proposed = self.workspace()["tasks"]
                self.assertEqual((legacy["initiated_by"], legacy["suggested_assignee"]), ("user", "user"))
                self.assertEqual((proposed["initiated_by"], proposed["suggested_assignee"]), ("maestro", "maestro"))
            self.assertEqual(monitor.execute("PRAGMA data_version").fetchone()[0], before_version)
            self.assertEqual(monitor.execute("SELECT value FROM workspace WHERE id=1").fetchone()[0], before_saved)
        changed = self.client.patch("/api/tasks/ai-task", headers=self.headers, json={"suggested_assignee": "user"})
        self.assertEqual(changed.status_code, 200, changed.text)
        self.assertEqual(changed.json()["tasks"][1]["initiated_by"], "maestro")
        self.assertEqual(changed.json()["tasks"][1]["suggested_assignee"], "user")

    def test_suggested_task_assignment_is_editable_but_initiator_is_server_owned(self):
        before_usage = self.workspace()["usage"]
        created = self.client.post("/api/tasks", headers=self.headers,
                                   json={"title": "Synthetic task labels", "suggested_assignee": "maestro"})
        self.assertEqual(created.status_code, 200, created.text)
        task = created.json()["tasks"][0]
        self.assertEqual((task["initiated_by"], task["suggested_assignee"]), ("user", "maestro"))
        for update in ({"suggested_assignee": "user"}, {"done": True, "suggested_assignee": "maestro"}):
            response = self.client.patch(f"/api/tasks/{task['id']}", headers=self.headers, json=update)
            self.assertEqual(response.status_code, 200, response.text)
            saved = response.json()["tasks"][0]
            self.assertEqual(saved["initiated_by"], "user")
            self.assertEqual(saved["created_at"], task["created_at"])
            for key, value in update.items():
                self.assertEqual(saved[key], value)
            self.assertEqual(response.json()["usage"], before_usage)
        before = self.workspace()
        for update in ({}, {"initiated_by": "maestro"}, {"done": False, "initiated_by": "user"},
                       {"suggested_assignee": "other"}, {"done": None}, {"suggested_assignee": None}, {"done": "false"}):
            with self.subTest(update=update):
                response = self.client.patch(f"/api/tasks/{task['id']}", headers=self.headers, json=update)
                self.assertEqual(response.status_code, 422, response.text)
        for labels in ({"initiated_by": "maestro"}, {"initiated_by": "user"}, {"suggested_assignee": "other"}):
            with self.subTest(labels=labels):
                response = self.client.post("/api/tasks", headers=self.headers, json={"title": "Rejected labels", **labels})
                self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(self.workspace(), before)

    def test_erased_or_corrected_sources_remove_ai_tasks_but_keep_user_tasks(self):
        manual = self.client.post("/api/tasks", headers=self.headers, json={"title": "Synthetic independent user task"}).json()["tasks"][0]
        before_usage = self.workspace()["usage"]
        for cause in ("chat", "feedback", "memory_delete", "memory_edit"):
            with self.subTest(cause=cause), patch("backend.provider.network", side_effect=AssertionError("Task/source edits must not infer")):
                chat_id = self.client.post("/api/chats", headers=self.headers).json()["active_chat_id"]
                text, answer = "A synthetic task source.", "A synthetic task answer."
                with backend.workspace_transaction() as state:
                    backend.selected_chat(state, chat_id)["messages"] = [
                        {"id": "source", "role": "user", "text": text},
                        {"id": "answer", "role": "assistant", "text": answer},
                    ]
                if cause.startswith("memory"):
                    memory = MemoryStore(backend.DATABASE, backend.TIMEZONE).remember("Synthetic task memory source.")
                    provenance = [{"memory_id": memory["id"], "hash": revision(memory)}]
                else:
                    provenance = [{"chat_id": chat_id, "message_id": "answer" if cause == "feedback" else "source",
                                   "role": "assistant" if cause == "feedback" else "user",
                                   "hash": assistant_revision(answer, None) if cause == "feedback" else hashlib.sha256(text.encode()).hexdigest()}]
                title = f"Synthetic derived task after {cause}"
                with backend.workspace_transaction() as state:
                    state["tasks"].insert(0, backend.task_record({"title": title}, "maestro", backend.now(), provenance=provenance))
                if cause == "chat":
                    response = self.client.delete(f"/api/chats/{chat_id}", headers=self.headers)
                elif cause == "feedback":
                    response = self.client.patch(f"/api/chats/{chat_id}/messages/answer/feedback", headers=self.headers,
                                                 json={"rating": "negative", "comment": "A synthetic correction."})
                elif cause == "memory_delete":
                    response = self.client.delete(f"/api/memory/{memory['id']}", headers=self.headers)
                else:
                    response = self.client.patch(f"/api/memory/{memory['id']}", headers=self.headers,
                                                 json={"content": "Synthetic corrected memory."})
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(self.workspace()["tasks"], [manual])
                self.assertEqual(self.workspace()["usage"], before_usage)
                with closing(sqlite3.connect(backend.DATABASE)) as db:
                    self.assertNotIn(title, db.execute("SELECT value FROM workspace WHERE id=1").fetchone()[0])

    def test_limits_reject_invalid_values_and_do_not_expose_credentials(self):
        original = self.workspace()
        for update in ({"daily_usd": -1}, {"max_tokens": -1}, {"max_refinements": 1}, {"max_minutes": 10}, {"api_key": "synthetic-placeholder"}):
            response = self.client.put("/api/limits", headers=self.headers, json={**original["limits"], **update})
            self.assertEqual(response.status_code, 422)
            self.assertNotIn("synthetic-placeholder", response.text)
        self.assertEqual(self.workspace()["limits"], original["limits"])
        self.assertNotIn("api_key", str(self.workspace()))

    def test_environment_fallback_blocks_key_removal_without_changing_keys_or_state(self):
        base = DEFAULT["base_url"]
        environment_key = "synthetic-environment-key"
        service = Provider(backend.DATABASE, backend.TIMEZONE)
        with service.transaction() as state:
            state.update(models=["synthetic-model"], tested_at=backend.now(), ledger=[
                {"id": "synthetic-charge", "at": backend.now(), "cost": 0.12,
                 "input_tokens": 15, "output_tokens": 5, "status": "settled"}])
        saved = service.read_state()
        for source, key in (("server environment", environment_key), ("session", "synthetic-session-key"),
                            ("Windows Credential Manager", "synthetic-vault-key")):
            session = {base: key} if source == "session" else {}
            with self.subTest(source=source), patch.dict(os.environ, OPENAI_API_KEY=environment_key), \
                    patch.dict(backend.credentials.session_keys, session, clear=True), \
                    patch.object(backend.credentials, "read", return_value=(key, source)), \
                    patch.object(backend.credentials, "vault") as vault:
                before = self.workspace()
                response = self.client.delete("/api/provider/key", headers=self.headers)
                self.assertEqual(response.status_code, 409)
                self.assertIn("OPENAI_API_KEY", response.json()["detail"])
                self.assertIn("restart Maestro", response.json()["detail"])
                self.assertNotIn(key, response.text)
                self.assertEqual(os.environ["OPENAI_API_KEY"], environment_key)
                self.assertEqual(backend.credentials.session_keys, session)
                self.assertEqual(service.read_state(), saved)
                self.assertEqual(self.workspace(), before)
                vault.assert_not_called()

    def test_stored_key_removal_still_works_without_an_environment_fallback(self):
        for base, environment in ((DEFAULT["base_url"], ""), ("https://provider.example/v1", "synthetic-env-key")):
            with Provider(backend.DATABASE, backend.TIMEZONE).transaction() as state:
                state["config"]["base_url"] = base
            with self.subTest(base=base), patch.dict(os.environ, OPENAI_API_KEY=environment), \
                    patch.dict(backend.credentials.session_keys, {base: "synthetic-session-key"}, clear=True), \
                    patch.object(backend.credentials, "vault") as vault:
                vault.return_value.CredDeleteW.return_value = True
                response = self.client.delete("/api/provider/key", headers=self.headers)
                self.assertEqual(response.status_code, 200)
                self.assertFalse(response.json()["credentials_present"])
                self.assertNotIn(base, backend.credentials.session_keys)
                if os.name == "nt":
                    vault.return_value.CredDeleteW.assert_called_once_with(backend.credentials.target(base), 1, 0)

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
        for path in ("/api/tasks/example/preview", "/api/reflection/run", "/api/reflection/feedback"):
            response = self.client.post(path, headers=self.headers, json={})
            self.assertIn(response.status_code, (404, 405))

    def save(self, config):
        return self.client.put("/api/context", headers=self.headers, json=config)

    def test_new_status_proposes_disabled_broad_capture_and_visible_capacity(self):
        status = self.client.get("/api/context").json()
        self.assertEqual(status["config"]["capture_policy"], "all_public")
        self.assertFalse(status["config"]["enabled"])
        self.assertEqual(status["config"]["max_bytes"], 10 * 1024 ** 3)
        self.assertEqual(status["config"]["max_items"], 200000)
        self.assertIsNone(status["last_capture"])
        with self.client:
            self.assertFalse(backend.context_store().archive.exists())

    def test_legacy_client_body_does_not_silently_expand_source_permission(self):
        config = self.client.get("/api/context").json()["config"]
        config.pop("capture_policy")
        config.update(enabled=True, public_sources=["https://docs.example.org/public/"])
        with self.client:
            with patch.object(backend.ContextStore, "configure", side_effect=ValueError("Synthetic save failure")):
                self.assertEqual(self.save(config).status_code, 409)
            self.assertFalse((backend.context_store().archive / "writer-owner.tmp").exists())
            result = self.save(config)
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(result.json()["config"]["capture_policy"], "approved_sources")

    def test_broad_policy_can_be_enabled_without_a_site_allowlist(self):
        config = self.client.get("/api/context").json()["config"]
        result = self.save({**config, "enabled": True, "public_sources": []})
        self.assertEqual(result.status_code, 200, result.text)
        self.assertTrue(result.json()["available"])
        self.assertEqual(result.json()["config"]["capture_policy"], "all_public")
        claim = backend.context_store().archive / "writer-owner.tmp"
        with patch.object(backend.ContextStore, "archive_owner", side_effect=ValueError("Synthetic private failure")), self.client:
            self.assertFalse(self.client.get("/api/context").json()["available"])
            self.assertEqual(self.client.get("/api/workspace").status_code, 200)
        self.assertFalse(claim.exists())
        self.assertNotIn("Synthetic private failure", self.client.get("/api/context").json()["last_error"])
        with patch.object(backend.Provider, "recover", side_effect=ValueError("Synthetic startup failure")), self.assertRaises(ValueError):
            with self.client:
                self.fail("A failed provider recovery cannot finish startup.")
        self.assertIsNone(backend.app.state.archive_ownership)
        self.assertFalse(backend.app.state.archive_owner)
        self.assertFalse(claim.exists())
        with self.client:
            self.assertEqual((claim.is_file(), self.client.get("/api/context").json()["available"]), (True, True))
            self.assertEqual(self.save({**config, "enabled": False}).status_code, 200)
            self.assertFalse(claim.exists())
        self.assertFalse(claim.exists())

    def test_capture_measurements_are_owned_by_the_server(self):
        config = self.client.get("/api/context").json()["config"]
        for changed in ({**config, "capture_policy": "all_private"},
                        {**config, "last_capture": {"sources_saved": 99}},
                        {**config, "max_bytes": 100 * 1024 ** 3 + 1}):
            response = self.save(changed)
            self.assertEqual(response.status_code, 422, response.text)
            self.assertEqual(self.client.get("/api/context").json()["config"], config)

    def test_archive_configure_does_not_block_private_validation_holding_sqlite(self):
        # Reflection validates drafts while holding the private write transaction.
        # An archive save waiting for that transaction must leave provider lookup free.
        store = backend.context_store()
        config = backend.ContextArchiveConfig(**{**store.status()["config"], "enabled": True})
        self.assertEqual(self.save(config.model_dump()).status_code, 200)
        private_locked, configuring = threading.Event(), threading.Event()
        errors = []
        original_configure, original_connect = store.configure, sqlite3.connect

        def configure_after_signal(settings):
            configuring.set()
            return original_configure(settings)

        def short_connection(*args, **kwargs):
            kwargs["timeout"] = 1
            return original_connect(*args, **kwargs)

        def validate_private():
            try:
                with backend.workspace_transaction():
                    private_locked.set()
                    if not configuring.wait(3):
                        raise AssertionError("Archive configuration did not start.")
                    backend.context_store()
                    backend.safe_private_content("Synthetic reflection draft without credentials.")
            except Exception as error:
                errors.append(error)

        def configure_archive():
            try:
                if not private_locked.wait(3):
                    raise AssertionError("Private transaction did not start.")
                backend.configure_context_archive(config)
            except Exception as error:
                errors.append(error)

        workers = [threading.Thread(target=validate_private, daemon=True),
                   threading.Thread(target=configure_archive, daemon=True)]
        with patch.object(store, "configure", side_effect=configure_after_signal), \
                patch("sqlite3.connect", side_effect=short_connection):
            for worker in workers:
                worker.start()
            for worker in workers:
                worker.join(5)
        self.assertFalse(any(worker.is_alive() for worker in workers), "Archive save and private validation deadlocked.")
        self.assertEqual(errors, [], repr(errors))

    def test_public_source_scope_cannot_store_a_known_search_credential(self):
        config = self.client.get("/api/context").json()["config"]
        key = "synthetic-public-search-key"
        self.enterContext(patch.object(backend.credentials, "read", return_value=(key, "synthetic")))
        dispatch = self.enterContext(patch("backend.provider.network"))
        encoded = "".join("%" + format(ord(character), "02X") for character in key)
        entities = "".join(f"&#x{ord(character):x};" for character in key)
        escaped_entities = entities.replace("&", "%26").replace("#", "%23").replace(";", "%3B")
        for value in (key, encoded, encoded.replace("%", "%25"), encoded.replace("%", "%2525"), escaped_entities):
            with self.subTest(encoded=value != key):
                blocked = self.save({**config, "public_sources": ["https://docs.ollama.com/public?token=" + value]})
                self.assertEqual(blocked.status_code, 409, blocked.text)
                self.assertNotIn(key, blocked.text)
                self.assertNotIn(encoded, blocked.text)
                self.assertNotIn(value, blocked.text)
                self.assertEqual(self.client.get("/api/context").json()["config"], config)
        dispatch.assert_not_called()


if __name__ == "__main__":
    unittest.main()

"""Shared local generation and explicit memory recall without real inference."""
from contextlib import closing
from datetime import timezone
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import patch

from backend.memory import MemoryStore
from backend.provider import DEFAULT, LocalUncertain, Provider, ProviderFailure


class GenerationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="maestro-generation-test-")
        self.database = Path(self.directory.name) / "workspace.sqlite3"
        self.workspace = {"limits": {"run_usd": 1, "daily_usd": 5, "monthly_usd": 50, "max_tokens": 100000},
                          "chats": [{"id": "synthetic-chat", "title": "Existing title", "title_source": "manual",
                                     "messages": [{"id": "source-user", "role": "user", "text": "Private conversation context"}]}]}
        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute("CREATE TABLE workspace (id INTEGER PRIMARY KEY, value TEXT NOT NULL)")
            db.execute("INSERT INTO workspace VALUES (1,?)", (json.dumps(self.workspace),))
        self.provider = Provider(self.database, timezone.utc)
        self.config = {**DEFAULT, "protocol": "ollama", "base_url": "http://127.0.0.1:11434",
                       "model": "synthetic-local:latest", "max_output_tokens": 256}
        self.provider.configure(self.config.copy(), "", False)
        self.calls = []
        self.instructions = "Extract source-backed candidate lessons. Return only JSON."
        self.messages = [{"role": "user", "content": "Synthetic user correction: prefer simple code."}]

    def tearDown(self):
        self.directory.cleanup()

    def network(self, config, key, method, path, payload=None):
        self.calls.append((config.copy(), method, path, payload))
        if path == "/api/show":
            return {"details": {"format": "gguf"}, "capabilities": ["completion", "tools"]}
        return {"done": True, "message": {"content": "Synthetic result"}, "prompt_eval_count": 37, "eval_count": 11}

    def workspace_value(self):
        with closing(sqlite3.connect(self.database)) as db:
            return db.execute("SELECT value FROM workspace WHERE id=1").fetchone()[0]

    def add_empty_chat(self):
        workspace = json.loads(self.workspace_value())
        workspace["chats"].append({"id": "fresh-chat", "title": "Fresh chat", "title_source": "manual", "messages": []})
        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute("UPDATE workspace SET value=?", (json.dumps(workspace),))

    def test_context_shares_adapter_and_usage_without_chats_titles_or_tools(self):
        before = self.workspace_value()
        with patch("backend.provider.network", side_effect=self.network), \
                patch("backend.provider.WebSearch", side_effect=AssertionError("No search")), \
                patch.object(MemoryStore, "recall", side_effect=AssertionError("No recall")):
            result = self.provider.generate_context(self.instructions, self.messages, model="other-installed:7b")
        self.assertEqual(before, self.workspace_value())
        self.assertEqual([call[2] for call in self.calls], ["/api/show", "/api/chat"])
        payload = self.calls[-1][3]
        self.assertEqual(payload["messages"], [{"role": "system", "content": self.instructions}, *self.messages])
        self.assertEqual(payload["options"]["num_predict"], 256)
        self.assertEqual(payload["keep_alive"], 0)
        self.assertNotIn("tools", payload)
        self.assertEqual(result["config"]["model"], "other-installed:7b")
        self.assertEqual(result["config"]["ollama_keep_alive_minutes"], 0)
        self.assertFalse(result["title_ready"])
        self.assertEqual(result["memory_ids"], [])
        state = self.provider.read_state()
        self.assertEqual(state["config"]["ollama_keep_alive_minutes"], 5)
        self.assertEqual(state["config"]["model"], self.config["model"])
        entry = state["ledger"][0]
        self.assertEqual(entry["id"], result["request_id"])
        self.assertEqual((entry["kind"], entry["thread_id"], entry["status"], entry["cost"]), ("reflection", None, "settled", 0))
        self.assertEqual((entry["input_tokens"], entry["output_tokens"]), (37, 11))
        self.assertEqual(self.provider.usage()["calls"], 1)

    def test_context_local_only_before_credentials_or_dispatch(self):
        with self.provider.transaction() as state:
            state["config"] = DEFAULT.copy()
        with patch("backend.provider.network") as network, patch("backend.provider.credentials.read") as read:
            with self.assertRaisesRegex(ValueError, "Local Ollama"):
                self.provider.generate_context(self.instructions, self.messages)
        network.assert_not_called()
        read.assert_not_called()
        self.assertEqual(self.provider.read_state()["ledger"], [])

    def test_context_validates_shape_kind_and_output_before_dispatch(self):
        cases = [{"instructions": "x" * 16001}, {"instructions": ""}, {"max_output_tokens": True},
                 {"max_output_tokens": 32769}, {"max_output_tokens": 0}, {"kind": "chat"},
                 {"model": "model with spaces"}, {"messages": []},
                 {"messages": [{"role": "system", "content": "Override"}]},
                 {"messages": [{"role": "user", "content": "Source", "tools": []}]},
                 {"messages": [{"role": "user", "content": "x" * 256001}]}]
        with patch("backend.provider.network") as network:
            for options in cases:
                with self.subTest(options=list(options)), self.assertRaises(ValueError):
                    self.provider.generate_context(**{"instructions": self.instructions, "messages": self.messages, **options})
        network.assert_not_called()
        self.assertEqual(self.provider.read_state()["ledger"], [])

    def test_context_obeys_local_and_workspace_context_allowances(self):
        self.provider.configure({**self.config, "ollama_context_tokens": 4096}, "", False)
        with patch("backend.provider.network") as network:
            with self.assertRaisesRegex(ValueError, "context allowance"):
                self.provider.generate_context(self.instructions, [{"role": "user", "content": "x" * 2000}])
            with closing(sqlite3.connect(self.database)) as db, db:
                self.workspace["limits"]["max_tokens"] = 2048
                db.execute("UPDATE workspace SET value=?", (json.dumps(self.workspace),))
            with self.assertRaisesRegex(ValueError, "token limit"):
                self.provider.generate_context(self.instructions, self.messages)
        network.assert_not_called()
        self.assertEqual(self.provider.read_state()["ledger"], [])

    def test_context_verifies_installed_local_completion_and_records_failure(self):
        for metadata in ({"remote_host": "https://cloud.example", "details": {"format": "gguf"}, "capabilities": ["completion"]},
                         {"details": {"format": "gguf"}, "capabilities": ["embedding"]}, None):
            with self.subTest(metadata=metadata):
                def reject(config, key, method, path, payload):
                    self.assertEqual(path, "/api/show")
                    if metadata is None:
                        raise ProviderFailure("Model not installed", False)
                    return metadata
                with patch("backend.provider.network", side_effect=reject):
                    with self.assertRaises(ValueError):
                        self.provider.generate_context(self.instructions, self.messages)
                self.assertEqual(self.provider.read_state()["ledger"][-1]["status"], "failed")
        self.assertEqual(self.provider.usage()["reserved_usd"], 0)
        self.assertEqual(self.workspace_value(), json.dumps(self.workspace))

    def test_context_uses_captured_local_connection_after_settings_change(self):
        def change_connection(config, key, method, path, payload):
            if path == "/api/show":
                with self.provider.transaction() as state:
                    state["config"] = DEFAULT.copy()
            self.assertEqual(config["protocol"], "ollama")
            self.assertEqual(config["base_url"], self.config["base_url"])
            return self.network(config, key, method, path, payload)
        with patch("backend.provider.network", side_effect=change_connection):
            result = self.provider.generate_context(self.instructions, self.messages)
        self.assertEqual(result["config"]["protocol"], "ollama")
        self.assertEqual(self.provider.read_state()["config"]["protocol"], "responses")
        self.assertEqual(self.provider.read_state()["ledger"][0]["status"], "settled")

    def test_chat_and_context_share_one_reservation_and_recovery(self):
        started, release = threading.Event(), threading.Event()
        results = []
        def slow_network(config, key, method, path, payload):
            if path == "/api/chat":
                started.set()
                if not release.wait(5):
                    raise AssertionError("Timed out waiting for test release")
            return self.network(config, key, method, path, payload)
        def generate():
            try:
                results.append(self.provider.generate_context(self.instructions, self.messages))
            except Exception as error:
                results.append(error)
        with patch("backend.provider.network", side_effect=slow_network):
            worker = threading.Thread(target=generate)
            worker.start()
            try:
                self.assertTrue(started.wait(5))
                with self.assertRaisesRegex(ValueError, "request is running"):
                    self.provider.generate("Synthetic chat", "synthetic-chat")
                with self.assertRaisesRegex(ValueError, "request is running"):
                    self.provider.generate_context(self.instructions, self.messages)
            finally:
                release.set()
                worker.join(5)
        self.assertFalse(worker.is_alive())
        self.assertEqual(len(results), 1)
        self.assertIsInstance(results[0], dict)
        self.assertEqual(len(self.provider.read_state()["ledger"]), 1)
        with self.provider.transaction() as state:
            state["ledger"][0]["status"] = "reserved"
        self.provider.recover()
        self.assertEqual(self.provider.read_state()["ledger"][0]["status"], "reserved")
        self.assertTrue(self.provider.read_state()["ledger"][0]["local_unknown"])
        with patch("backend.provider.background_network", return_value={"models": []}):
            self.provider.recover_background()
        self.assertEqual(self.provider.read_state()["ledger"][0]["status"], "failed")

    def test_unknown_chat_completion_blocks_chat_and_context_until_confirmed_unloaded(self):
        def disconnect(config, key, method, path, payload=None):
            if path == "/api/chat":
                raise LocalUncertain("Synthetic disconnect", False)
            return self.network(config, key, method, path, payload)
        with patch("backend.provider.network", side_effect=disconnect), self.assertRaises(LocalUncertain):
            self.provider.generate("Synthetic interrupted chat", "synthetic-chat")
        entry = self.provider.read_state()["ledger"][-1]
        self.assertEqual(entry["status"], "reserved")
        self.assertTrue(entry["local_unknown"])
        self.assertEqual(entry["connection_config"], self.provider.read_state()["config"])
        self.assertNotIn("job_id", entry)
        with patch("backend.provider.network") as dispatch:
            for generate in (lambda: self.provider.generate("Retry", "synthetic-chat"),
                             lambda: self.provider.generate_context(self.instructions, self.messages)):
                with self.assertRaisesRegex(ValueError, "request is running"):
                    generate()
            dispatch.assert_not_called()
        with patch("backend.provider.background_network", return_value={"models": [{"name": self.config["model"]}]}):
            self.provider.recover_background()
        self.assertEqual(self.provider.read_state()["ledger"][-1]["status"], "reserved")
        with patch("backend.provider.background_network", return_value={"models": []}):
            self.provider.recover_background()
        self.assertEqual(self.provider.read_state()["ledger"][-1]["status"], "failed")
        with patch("backend.provider.network", side_effect=self.network):
            self.provider.generate_context(self.instructions, self.messages)
        self.assertEqual(self.provider.read_state()["ledger"][-1]["status"], "settled")

    def test_poll_never_releases_a_live_foreground_reservation(self):
        def during_dispatch(config, key, method, path, payload=None):
            if path == "/api/chat":
                with patch("backend.provider.background_network", return_value={"models": []}) as poll:
                    self.provider.recover_background()
                poll.assert_not_called()
                self.assertEqual(self.provider.read_state()["ledger"][-1]["status"], "reserved")
            return self.network(config, key, method, path, payload)
        with patch("backend.provider.network", side_effect=during_dispatch):
            self.provider.generate("Synthetic active chat", "synthetic-chat")
        self.assertEqual(self.provider.read_state()["ledger"][-1]["status"], "settled")

    def test_unknown_title_completion_retains_gate_and_completed_chat(self):
        workspace = json.loads(self.workspace_value())
        workspace["chats"][0].update(title_source="fallback", messages=[])
        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute("UPDATE workspace SET value=?", (json.dumps(workspace),))
        with patch("backend.provider.network", side_effect=self.network):
            result = self.provider.generate("Synthetic first request", "synthetic-chat")
        self.assertTrue(result["title_ready"])
        with patch("backend.provider.network", side_effect=LocalUncertain("Synthetic title timeout", False)), self.assertRaises(LocalUncertain):
            self.provider.generate("", "synthetic-chat", title_for=result)
        ledger = self.provider.read_state()["ledger"]
        self.assertEqual([entry["status"] for entry in ledger], ["settled", "reserved"])
        self.assertEqual(ledger[-1]["kind"], "title")
        self.assertTrue(ledger[-1]["local_unknown"])
        self.assertEqual(ledger[-1]["parent_id"], result["request_id"])
        self.assertEqual(json.loads(self.workspace_value())["chats"][0]["messages"][-1]["text"], "Synthetic result")

    def test_restart_recovery_uses_captured_local_connection_after_settings_change(self):
        original = self.config.copy()
        with self.provider.transaction() as state:
            state["ledger"].append({"id": "synthetic-interrupted", "at": self.provider.stamp(),
                                    "protocol": "ollama", "status": "reserved", "cost": 0,
                                    "connection_config": original})
            state["config"] = DEFAULT.copy()
        self.provider.recover()
        entry = self.provider.read_state()["ledger"][-1]
        self.assertEqual(entry["status"], "reserved")
        self.assertTrue(entry["local_unknown"])
        with patch("backend.provider.background_network", return_value={"models": []}) as poll:
            self.provider.recover_background()
        self.assertEqual(poll.call_args.args[:3], (original, "/api/ps", None))
        self.assertEqual(self.provider.read_state()["ledger"][-1]["status"], "failed")

    def test_legacy_orphan_never_guesses_changed_local_endpoint_and_requires_reconnect(self):
        original = self.provider.read_state()["config"].copy()
        changed = {**original, "base_url": "http://127.0.0.1:11435"}
        with self.provider.transaction() as state:
            state["ledger"].append({"id": "synthetic-legacy-interrupted", "at": self.provider.stamp(),
                                    "protocol": "ollama", "status": "reserved", "cost": 0})
            state["config"] = changed
        self.provider.recover()
        with patch("backend.provider.background_network") as poll:
            self.provider.recover_background()
        poll.assert_not_called()
        self.assertEqual(self.provider.read_state()["ledger"][-1]["status"], "reserved")
        with patch("backend.provider.network") as dispatch, self.assertRaisesRegex(ValueError, "original Local Ollama server URL.*Connect and load models"):
            self.provider.generate("Synthetic blocked retry", "synthetic-chat")
        dispatch.assert_not_called()
        self.provider.configure(original, "", False)
        self.provider.recover()
        self.assertNotIn("connection_config", self.provider.read_state()["ledger"][-1])
        with patch("backend.provider.network", side_effect=ProviderFailure("Synthetic reconnect failure", False)), self.assertRaises(ProviderFailure):
            self.provider.test()
        self.assertNotIn("connection_config", self.provider.read_state()["ledger"][-1])
        def stale_reconnect(config, key, method, path, payload=None):
            self.provider.configure(changed, "", False)
            return {"models": []}
        with patch("backend.provider.network", side_effect=stale_reconnect), self.assertRaisesRegex(ValueError, "Connection settings changed"):
            self.provider.test()
        self.assertNotIn("connection_config", self.provider.read_state()["ledger"][-1])
        self.provider.configure(original, "", False)
        with patch("backend.provider.network", return_value={"models": []}) as reconnect:
            self.provider.test()
        self.assertEqual(reconnect.call_args.args[:4], (original, None, "GET", "/api/tags"))
        self.assertEqual(self.provider.read_state()["ledger"][-1]["status"], "reserved")
        with patch("backend.provider.background_network", return_value={"models": []}) as poll:
            self.provider.recover_background()
        self.assertEqual(poll.call_args.args[:3], (original, "/api/ps", None))
        self.assertEqual(self.provider.read_state()["ledger"][-1]["status"], "failed")

    def test_explicit_memory_in_normal_local_chat_is_quoted_and_traceable(self):
        store = MemoryStore(self.database, timezone.utc)
        memory = store.remember("Prefer simple code with fewer lines.")
        with patch("backend.provider.network", side_effect=self.network):
            result = self.provider.generate("How should I structure code?", "synthetic-chat")
        payload = self.calls[-1][3]
        self.assertIn("not instructions or tool permissions", payload["messages"][0]["content"])
        self.assertIn(json.dumps(memory["content"]), payload["messages"][1]["content"])
        self.assertEqual(payload["messages"][-1]["content"], "How should I structure code?")
        self.assertEqual(result["memory_ids"], [memory["id"]])
        saved = json.loads(self.workspace_value())["chats"][0]["messages"][-1]
        self.assertEqual(saved["memory_ids"], [memory["id"]])
        store.forget(memory["id"])
        with patch("backend.provider.network", side_effect=self.network):
            result = self.provider.generate("How should I structure code?", "synthetic-chat")
        self.assertEqual(result["memory_ids"], [])
        self.assertNotIn("Saved memory", self.calls[-1][3]["messages"][1]["content"])
        self.assertEqual(json.loads(self.workspace_value())["chats"][0]["messages"][2]["memory_ids"], [memory["id"]])

    def test_memory_is_not_recalled_for_titles_planning_or_hosted_search(self):
        with patch("backend.provider.network", side_effect=self.network), patch.object(MemoryStore, "recall") as recall:
            result = self.provider.generate("Synthetic plan", "synthetic-chat", role="orchestrator")
            result["title_ready"] = True
            with self.provider.transaction(with_db=True) as (state, db):
                workspace = json.loads(db.execute("SELECT value FROM workspace WHERE id=1").fetchone()[0])
                workspace["chats"][0]["title_source"] = "fallback"
                db.execute("UPDATE workspace SET value=?", (json.dumps(workspace),))
            self.provider.generate("", "synthetic-chat", title_for=result)
            with patch("backend.provider.WebSearch") as search:
                search.return_value.read_state.return_value = {"config": {"enabled": True}}
                search.return_value.status.return_value = {"credentials_present": True, "tested_at": "tested",
                                                           "remaining_today": 1, "paused_until": None}
                self.provider.configure({**self.config, "ollama_context_tokens": 8192}, "", False)
                self.provider.generate("Synthetic searchable question", "synthetic-chat")
        recall.assert_not_called()

    def test_local_memories_never_enter_remote_chat(self):
        MemoryStore(self.database, timezone.utc).remember("Private code preference.")
        remote = {**DEFAULT, "model": "synthetic-remote", "pricing_verified": True,
                  "input_usd_per_million": 1, "output_usd_per_million": 2, "max_output_tokens": 256}
        response = {"output": [{"type": "message", "content": [{"type": "output_text", "text": "Synthetic remote answer"}]}],
                    "usage": {"input_tokens": 37, "output_tokens": 11}}
        with patch("backend.provider.credentials.read", return_value=("synthetic-remote-key", "session")):
            self.provider.configure(remote, "", False)
            with patch("backend.provider.network", return_value=response) as network, patch.object(MemoryStore, "recall") as recall:
                result = self.provider.generate("What is my code preference?", "synthetic-chat")
        recall.assert_not_called()
        self.assertNotIn("Private code preference", json.dumps(network.call_args.args[-1]))
        self.assertEqual(result["memory_ids"], [])

    def test_memory_derived_history_cannot_be_sent_to_remote_after_forget(self):
        store = MemoryStore(self.database, timezone.utc)
        memory = store.remember("Private code preference.")
        with patch("backend.provider.network", side_effect=self.network):
            self.provider.generate("What is my code preference?", "synthetic-chat")
        store.forget(memory["id"])
        before = self.workspace_value()
        ledger = self.provider.read_state()["ledger"]
        remote = {**DEFAULT, "model": "synthetic-remote", "pricing_verified": True,
                  "input_usd_per_million": 1, "output_usd_per_million": 2, "max_output_tokens": 256}
        response = {"output": [{"type": "message", "content": [{"type": "output_text", "text": "Synthetic remote answer"}]}],
                    "usage": {"input_tokens": 37, "output_tokens": 11}}
        with patch("backend.provider.credentials.read", return_value=("synthetic-remote-key", "session")):
            self.provider.configure(remote, "", False)
            with patch("backend.provider.network", return_value=response) as network:
                with self.assertRaisesRegex(ValueError, "Start a new chat"):
                    self.provider.generate("Continue this discussion", "synthetic-chat")
                network.assert_not_called()
                self.assertEqual(self.provider.read_state()["ledger"], ledger)
                self.assertEqual(self.workspace_value(), before)
                self.add_empty_chat()
                result = self.provider.generate("New unrelated question", "fresh-chat")
        self.assertEqual(result["memory_ids"], [])
        network.assert_called_once()
        self.assertNotIn(memory["content"], json.dumps(network.call_args.args[-1]))

    def test_memory_derived_history_cannot_enter_hosted_search_but_fresh_chat_can(self):
        store = MemoryStore(self.database, timezone.utc)
        store.remember("Private code preference.")
        with patch("backend.provider.network", side_effect=self.network):
            self.provider.generate("What is my code preference?", "synthetic-chat")
        before = self.workspace_value()
        ledger = self.provider.read_state()["ledger"]
        self.provider.configure({**self.config, "ollama_context_tokens": 8192}, "", False)
        with patch("backend.provider.WebSearch") as search, patch("backend.provider.network", side_effect=self.network) as network:
            search.return_value.read_state.return_value = {"config": {"enabled": True}}
            search.return_value.status.return_value = {"credentials_present": True, "tested_at": "tested",
                                                       "remaining_today": 1, "paused_until": None}
            with self.assertRaisesRegex(ValueError, "Start a new chat"):
                self.provider.generate("Search the web for more information", "synthetic-chat")
            network.assert_not_called()
            self.assertEqual(self.provider.read_state()["ledger"], ledger)
            self.assertEqual(self.workspace_value(), before)
            self.add_empty_chat()
            result = self.provider.generate("New unrelated question", "fresh-chat")
        self.assertEqual(result["memory_ids"], [])
        self.assertEqual(network.call_count, 2)
        self.assertIn("tools", network.call_args.args[-1])

    def test_low_priority_memories_are_omitted_before_exceeding_context(self):
        store = MemoryStore(self.database, timezone.utc)
        useful = store.remember("Prefer simple concise code.")
        oversized = store.remember("code " + "x" * 900)
        self.provider.configure({**self.config, "ollama_context_tokens": 3300}, "", False)
        with patch("backend.provider.network", side_effect=self.network):
            result = self.provider.generate("Please write simple concise code", "synthetic-chat")
        self.assertEqual(result["memory_ids"], [useful["id"]])
        payload = json.dumps(self.calls[-1][3])
        self.assertIn(useful["content"], payload)
        self.assertNotIn(oversized["content"], payload)
        saved = json.loads(self.workspace_value())["chats"][0]["messages"][-1]
        self.assertEqual(saved["memory_ids"], [useful["id"]])


if __name__ == "__main__":
    unittest.main()

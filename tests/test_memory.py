"""Explicit local memory persistence, retrieval limits, provenance and deletion."""
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

from backend.memory import MemoryStore, MAX_MEMORIES


class MemoryTests(unittest.TestCase):
    def setUp(self):
        self.directory = self.enterContext(tempfile.TemporaryDirectory(prefix="maestro-memory-"))
        self.database = Path(self.directory) / "workspace.sqlite3"
        self.timezone = ZoneInfo("Europe/Stockholm")
        self.store = MemoryStore(self.database, self.timezone)
        self.chats = [
            {"id": "first", "messages": [{"id": "source", "role": "user", "text": "Synthetic preference"},
                                         {"id": "answer", "role": "assistant", "text": "Synthetic guess"}]},
            {"id": "second", "messages": []},
        ]

    def save_workspace(self):
        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute("CREATE TABLE IF NOT EXISTS workspace (id INTEGER PRIMARY KEY, value TEXT NOT NULL)")
            db.execute("INSERT INTO workspace VALUES(1,?) ON CONFLICT(id) DO UPDATE SET value=excluded.value",
                       (json.dumps({"chats": self.chats}),))

    def test_reads_do_not_create_database_or_tables(self):
        self.assertEqual(self.store.list(), [])
        self.assertEqual(self.store.recall("Synthetic preference", "first"), [])
        self.assertFalse(self.database.exists())
        self.save_workspace()
        before = self.database.read_bytes()
        self.assertEqual(self.store.list(), [])
        self.assertEqual(self.store.recall("Synthetic preference", "first"), [])
        self.assertEqual(self.database.read_bytes(), before)

    def test_persistence_preserves_provenance_and_explicit_origin(self):
        self.save_workspace()
        memory = self.store.remember("  Keep synthetic replies concise.  ", chat_id="first", source_message_id="source")
        self.assertEqual(memory["content"], "Keep synthetic replies concise.")
        self.assertEqual(memory["origin"], "explicit")
        self.assertEqual(memory["scope"], "workspace")
        self.assertEqual(memory["chat_id"], "first")
        self.assertEqual(memory["source_message_id"], "source")
        self.assertEqual(MemoryStore(self.database, self.timezone).list(), [memory])
        self.assertEqual(memory["created_at"], memory["updated_at"])

    def test_invalid_content_scope_and_source_do_not_get_saved(self):
        self.save_workspace()
        invalid = [
            ("", {}), (" " * 12, {}), ("x" * 1001, {}), ("\x00private", {}), (None, {}),
            ("valid", {"scope": "inferred"}), ("valid", {"scope": "conversation"}),
            ("valid", {"source_message_id": "source"}), ("valid", {"chat_id": "missing"}),
            ("valid", {"chat_id": "first", "source_message_id": "missing"}),
            ("valid", {"chat_id": "first", "source_message_id": "answer"}),
            ("valid", {"chat_id": "second", "source_message_id": "source"}),
        ]
        for content, fields in invalid:
            with self.subTest(fields=fields, length=len(content) if isinstance(content, str) else None):
                with self.assertRaises(ValueError):
                    self.store.remember(content, **fields)
        self.assertEqual(self.store.list(), [])

    def test_recall_is_scoped_relevant_bounded_and_local_only(self):
        self.save_workspace()
        workspace = self.store.remember("Synthetic project prefers concise code.")
        first = self.store.remember("Synthetic first project uses SQLite.", scope="conversation", chat_id="first")
        second = self.store.remember("Synthetic second project uses Postgres.", scope="conversation", chat_id="second")
        self.store.remember("Unrelated weather details.")
        self.assertEqual({item["id"] for item in self.store.recall("Synthetic project", "first")}, {workspace["id"], first["id"]})
        self.assertEqual({item["id"] for item in self.store.recall("Synthetic project", "second")}, {workspace["id"], second["id"]})
        self.assertEqual(self.store.recall("Synthetic project", None), [workspace])
        self.assertEqual(self.store.recall("SYNTHETIC PROJECT!", "missing"), [workspace])
        for query in ("", " ", "hi", "the and this", "?!!!", "Unmatched words"):
            self.assertEqual(self.store.recall(query, "first"), [])
        with patch.object(self.store, "reader", side_effect=AssertionError("Remote recall must not read private data")):
            self.assertEqual(self.store.recall("Synthetic project", "first", local=False), [])
        before = self.database.read_bytes()
        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute("BEGIN IMMEDIATE")
            self.assertEqual(len(self.store.recall("Synthetic project", "first")), 2)
        self.assertEqual(self.database.read_bytes(), before)

    def test_unicode_recall_limits_count_and_combined_content(self):
        self.store.remember("Föredrar återanvändning för projektet.")
        self.assertEqual(len(self.store.recall("ÅTERANVÄNDNING", None)), 1)
        for index in range(8):
            self.store.remember(f"Synthetic preference {index} " + "x" * 180)
        results = self.store.recall("Synthetic preference", None)
        self.assertLessEqual(len(results), 5)
        self.assertLessEqual(sum(len(item["content"]) for item in results), 1000)
        self.assertEqual(results, self.store.recall("Synthetic preference", None))
        self.assertGreater(len(results), 0)

    def test_edits_replace_old_recall_and_keep_source_annotation(self):
        self.save_workspace()
        original = self.store.remember("Synthetic project uses Postgres.", chat_id="first", source_message_id="source")
        updated = self.store.update(original["id"], "Synthetic project uses SQLite.")
        self.assertEqual(updated["id"], original["id"])
        self.assertEqual(updated["created_at"], original["created_at"])
        self.assertEqual(updated["source_message_id"], "source")
        self.assertEqual(self.store.recall("Postgres", "first"), [])
        self.assertEqual(self.store.recall("SQLite", "second"), [updated])
        self.store.forget(updated["id"])
        self.assertEqual(self.store.list(), [])
        self.assertEqual(self.store.recall("SQLite", "first"), [])
        for action in (lambda: self.store.update(updated["id"], "Replacement"), lambda: self.store.forget(updated["id"])):
            with self.assertRaises(ValueError):
                action()

    def test_chat_cascade_shares_callers_transaction_and_can_roll_back(self):
        self.save_workspace()
        independent = self.store.remember("Synthetic project independent preference.")
        source = self.store.remember("Synthetic project source preference.", chat_id="first", source_message_id="source")
        scoped = self.store.remember("Synthetic project scoped preference.", scope="conversation", chat_id="first")
        with closing(sqlite3.connect(self.database)) as db:
            db.execute("BEGIN IMMEDIATE")
            self.store.delete_chat("first", db)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM private_memories").fetchone()[0], 1)
            db.rollback()
        self.assertEqual({item["id"] for item in self.store.list()}, {independent["id"], source["id"], scoped["id"]})
        self.store.delete_chat("first")
        self.assertEqual(self.store.list(), [independent])
        self.assertNotIn(b"source preference", self.database.read_bytes())

    def test_removed_or_changed_sources_are_never_recalled(self):
        self.save_workspace()
        self.store.remember("Synthetic project source preference.", chat_id="first", source_message_id="source")
        before = self.database.read_bytes()
        self.chats[0]["messages"][0]["role"] = "assistant"
        self.save_workspace()
        after_source_change = self.database.read_bytes()
        self.assertNotEqual(before, after_source_change)
        self.assertEqual(self.store.list(), [])
        self.assertEqual(self.store.recall("Synthetic project", "first"), [])
        self.assertEqual(self.database.read_bytes(), after_source_change)
        self.chats[0]["messages"] = []
        self.save_workspace()
        self.assertEqual(self.store.list(), [])

    def test_inventory_has_hard_limit_and_forgetting_frees_capacity(self):
        first = self.store.remember("Synthetic preference 0")
        for index in range(1, MAX_MEMORIES):
            self.store.remember(f"Synthetic preference {index}")
        with self.assertRaisesRegex(ValueError, "full"):
            self.store.remember("Another synthetic preference")
        self.assertEqual(len(self.store.list()), MAX_MEMORIES)
        self.store.forget(first["id"])
        self.store.remember("Replacement synthetic preference")
        self.assertEqual(len(self.store.list()), MAX_MEMORIES)


if __name__ == "__main__":
    unittest.main()

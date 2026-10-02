"""Explicit, source-backed private memory; retrieval never writes or calls a model."""
from contextlib import closing, contextmanager
from datetime import datetime
import json
from pathlib import Path
import re
import sqlite3
import uuid


MAX_CONTENT = 1000
MAX_RECALL = 5
MAX_MEMORIES = 200
STOPWORDS = set("a an and are as at be been but by can could do does for from had has have how i if in is it its me my of on or our should that the their them there these they this to was we were what when where which who will with would you your och att det en ett för från jag med om på till vad vi är".split())


def terms(text):
    return {word for word in re.findall(r"[^\W_]+", text.casefold()) if len(word) > 2 and word not in STOPWORDS}


def content_value(content):
    if not isinstance(content, str) or not 1 <= len(content.strip()) <= MAX_CONTENT or "\x00" in content:
        raise ValueError("Enter a memory between 1 and 1000 characters.")
    return content.strip()


class MemoryStore:
    def __init__(self, database, timezone):
        self.database, self.timezone = Path(database), timezone

    @contextmanager
    def transaction(self):
        with closing(sqlite3.connect(self.database, timeout=10)) as db, db:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA secure_delete=ON")
            db.execute("BEGIN IMMEDIATE")
            db.execute("""CREATE TABLE IF NOT EXISTS private_memories (
                id TEXT PRIMARY KEY, content TEXT NOT NULL,
                scope TEXT NOT NULL CHECK(scope IN ('workspace', 'conversation')),
                chat_id TEXT, source_message_id TEXT,
                origin TEXT NOT NULL CHECK(origin='explicit'), created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                CHECK(scope='workspace' OR chat_id IS NOT NULL),
                CHECK(source_message_id IS NULL OR chat_id IS NOT NULL))""")
            yield db

    def initialize(self):
        with self.transaction():
            pass

    @contextmanager
    def reader(self):
        if not self.database.is_file():
            yield None
            return
        with closing(sqlite3.connect(self.database.resolve().as_uri() + "?mode=ro", uri=True, timeout=10)) as db:
            db.row_factory = sqlite3.Row
            exists = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='private_memories'").fetchone()
            yield db if exists else None

    def chats(self, db):
        if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='workspace'").fetchone():
            return {}
        row = db.execute("SELECT value FROM workspace WHERE id=1").fetchone()
        return {chat["id"]: chat for chat in json.loads(row[0]).get("chats", [])} if row else {}

    def valid_source(self, record, chats):
        if record["chat_id"] is None:
            return True
        chat = chats.get(record["chat_id"])
        if chat is None:
            return False
        return record["source_message_id"] is None or any(
            message.get("id") == record["source_message_id"] and message.get("role") == "user" and isinstance(message.get("text"), str) and message["text"].strip()
            for message in chat.get("messages", []))

    def list(self, chat_id=None):
        with self.reader() as db:
            if db is None:
                return []
            chats = self.chats(db)
            records = db.execute("SELECT * FROM private_memories ORDER BY updated_at DESC, id").fetchall()
            return [dict(record) for record in records if self.valid_source(record, chats)
                    and (chat_id is None or record["scope"] == "workspace" or record["chat_id"] == chat_id)]

    def remember(self, content, scope="workspace", chat_id=None, source_message_id=None):
        content = content_value(content)
        if scope not in ("workspace", "conversation"):
            raise ValueError("Choose workspace or conversation memory.")
        if (scope == "conversation" or source_message_id is not None) and not chat_id:
            raise ValueError("Choose an existing chat for conversation or source-linked memory.")
        stamp = datetime.now(self.timezone).isoformat()
        record = {"id": uuid.uuid4().hex, "content": content, "scope": scope, "chat_id": chat_id,
                  "source_message_id": source_message_id, "origin": "explicit", "created_at": stamp, "updated_at": stamp}
        with self.transaction() as db:
            if not self.valid_source(record, self.chats(db)):
                raise ValueError("Choose an existing chat and a user message as the memory source.")
            if db.execute("SELECT COUNT(*) FROM private_memories").fetchone()[0] >= MAX_MEMORIES:
                raise ValueError("Memory is full. Forget an existing memory before adding another.")
            db.execute("INSERT INTO private_memories VALUES (:id,:content,:scope,:chat_id,:source_message_id,:origin,:created_at,:updated_at)", record)
        return record

    def update(self, memory_id, content):
        content = content_value(content)
        with self.transaction() as db:
            row = db.execute("SELECT * FROM private_memories WHERE id=?", (memory_id,)).fetchone()
            if row is None or not self.valid_source(row, self.chats(db)):
                raise ValueError("That memory could not be found.")
            record = {**dict(row), "content": content, "updated_at": datetime.now(self.timezone).isoformat()}
            db.execute("UPDATE private_memories SET content=:content, updated_at=:updated_at WHERE id=:id", record)
        return record

    def forget(self, memory_id):
        with self.transaction() as db:
            if not db.execute("DELETE FROM private_memories WHERE id=?", (memory_id,)).rowcount:
                raise ValueError("That memory could not be found.")

    def delete_chat(self, chat_id, db=None):
        if db is None:
            with self.transaction() as connection:
                self.delete_chat(chat_id, connection)
        elif db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='private_memories'").fetchone():
            db.execute("PRAGMA secure_delete=ON")
            db.execute("DELETE FROM private_memories WHERE chat_id=?", (chat_id,))

    def recall(self, query, chat_id, local=True):
        if not local or not (query_terms := terms(query)):
            return []
        ranked = [(len(query_terms & terms(record["content"])), record) for record in self.list(chat_id)
                  if record["scope"] == "workspace" or record["chat_id"] == chat_id]
        ranked.sort(key=lambda item: (-item[0], item[1]["id"]))
        selected, remaining = [], MAX_CONTENT
        for relevance, record in ranked:
            if relevance and len(record["content"]) <= remaining:
                selected.append(record)
                remaining -= len(record["content"])
                if len(selected) == MAX_RECALL:
                    break
        return selected

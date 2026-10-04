"""Private facts and working notes with provenance, user pins and bounded recall."""
from contextlib import closing, contextmanager
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import uuid


MAX_CONTENT = 8000
MAX_RECALL = 100
MAX_RECALL_CHARACTERS = 200000
MAX_MEMORIES = 2000
STOPWORDS = set("a an and are as at be been but by can could do does for from had has have how i if in is it its me my of on or our should that the their them there these they this to was we were what when where which who will with would you your och att det en ett för från jag med om på till vad vi är".split())


def terms(text):
    return {word for word in re.findall(r"[^\W_]+", text.casefold()) if len(word) > 2 and word not in STOPWORDS}


def content_value(content):
    if not isinstance(content, str) or not 1 <= len(content.strip()) <= MAX_CONTENT or "\x00" in content:
        raise ValueError("Enter a memory between 1 and 8000 characters.")
    return content.strip()


def revision(record):
    return hashlib.sha256(json.dumps({key: record.get(key) for key in ("content", "origin", "kind", "updated_at")}, sort_keys=True).encode("utf-8")).hexdigest()


def source_fingerprint(source):
    return hashlib.sha256(json.dumps(source, sort_keys=True).encode("utf-8")).hexdigest()


def assistant_revision(text, feedback=None):
    return hashlib.sha256(json.dumps({"text": text, "feedback": feedback}, sort_keys=True).encode("utf-8")).hexdigest()


class MemoryStore:
    def __init__(self, database, timezone):
        self.database, self.timezone = Path(database), timezone

    @contextmanager
    def transaction(self):
        with closing(sqlite3.connect(self.database, timeout=10)) as db, db:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA secure_delete=ON")
            db.execute("BEGIN IMMEDIATE")
            self.initialize_db(db)
            yield db

    @staticmethod
    def initialize_db(db):
        exists = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='private_memories'").fetchone()
        migrate = exists and "metadata" not in {row[1] for row in db.execute("PRAGMA table_info(private_memories)")}
        if migrate:
            db.execute("ALTER TABLE private_memories RENAME TO private_memories_previous")
        db.execute("""CREATE TABLE IF NOT EXISTS private_memories (
                id TEXT PRIMARY KEY, content TEXT NOT NULL,
                scope TEXT NOT NULL CHECK(scope IN ('workspace', 'conversation')),
                chat_id TEXT, source_message_id TEXT,
                origin TEXT NOT NULL CHECK(origin IN ('explicit','curated','reflective')), created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                metadata TEXT NOT NULL DEFAULT '{}',
                CHECK(scope='workspace' OR chat_id IS NOT NULL),
                CHECK(source_message_id IS NULL OR chat_id IS NOT NULL))""")
        if migrate:
            db.execute("INSERT INTO private_memories (id,content,scope,chat_id,source_message_id,origin,created_at,updated_at) SELECT id,content,scope,chat_id,source_message_id,origin,created_at,updated_at FROM private_memories_previous")
            db.execute("DROP TABLE private_memories_previous")
        db.execute("CREATE TABLE IF NOT EXISTS memory_tombstones (fingerprint TEXT PRIMARY KEY)")
        db.execute("CREATE TABLE IF NOT EXISTS memory_barriers (chat_id TEXT PRIMARY KEY, source_id TEXT NOT NULL)")

    @staticmethod
    def record(row):
        record = dict(row) if isinstance(row, (dict, sqlite3.Row)) else dict(zip(("id", "content", "scope", "chat_id", "source_message_id", "origin", "created_at", "updated_at", "metadata"), row))
        metadata = json.loads(record.pop("metadata", "{}"))
        return {**record, "kind": "fact", "source_hash": None, "evidence": None, "provenance": [], **metadata, "pinned": record["origin"] == "explicit"}

    @staticmethod
    def insert(db, record):
        metadata = {key: record.get(key) for key in ("kind", "source_hash", "evidence", "provenance")}
        db.execute("INSERT INTO private_memories VALUES (:id,:content,:scope,:chat_id,:source_message_id,:origin,:created_at,:updated_at,:metadata)", {**record, "metadata": json.dumps(metadata)})

    @staticmethod
    def blocked(db, content=None, provenance=()):
        fingerprints = (["content:" + hashlib.sha256(" ".join(content.casefold().split()).encode("utf-8")).hexdigest()] if content else [])
        fingerprints.extend("source:" + source_fingerprint(source) for source in provenance)
        if any(db.execute("SELECT 1 FROM memory_tombstones WHERE fingerprint=?", (value,)).fetchone() for value in fingerprints):
            return True
        for source in provenance:
            if "chat_id" not in source or source.get("role") == "assistant":
                continue
            barrier = db.execute("SELECT source_id FROM memory_barriers WHERE chat_id=?", (source["chat_id"],)).fetchone()
            if barrier:
                row = db.execute("SELECT value FROM workspace WHERE id=1").fetchone()
                chat = next((chat for chat in json.loads(row[0]).get("chats", []) if chat["id"] == source["chat_id"]), {})
                ids = [item["id"] for item in chat.get("messages", []) if item.get("role") == "user"]
                if source["message_id"] not in ids or barrier[0] not in ids or ids.index(source["message_id"]) <= ids.index(barrier[0]):
                    return True
        return False

    def barrier(self, db):
        for chat in self.chats(db).values():
            ids = [message["id"] for message in chat.get("messages", []) if message.get("role") == "user"]
            if ids:
                db.execute("INSERT INTO memory_barriers VALUES (?,?) ON CONFLICT(chat_id) DO UPDATE SET source_id=excluded.source_id", (chat["id"], ids[-1]))

    def tombstone(self, db, record):
        fingerprint = "content:" + hashlib.sha256(" ".join(record["content"].casefold().split()).encode("utf-8")).hexdigest()
        db.execute("INSERT OR IGNORE INTO memory_tombstones VALUES (?)", (fingerprint,))
        for source in record.get("provenance", []):
            db.execute("INSERT OR IGNORE INTO memory_tombstones VALUES (?)", ("source:" + source_fingerprint(source),))

    def remove_dependents(self, db, memory_id):
        records = {record["id"]: record for row in db.execute("SELECT * FROM private_memories WHERE origin!='explicit'") for record in [self.record(row)]}
        pending = {memory_id}
        while pending:
            dependent = {memory_id for memory_id, record in records.items() if any(source.get("memory_id") in pending for source in record["provenance"])}
            for memory_id in dependent:
                self.tombstone(db, records.pop(memory_id))
            db.executemany("DELETE FROM private_memories WHERE id=?", [(memory_id,) for memory_id in dependent])
            pending = dependent

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

    @staticmethod
    def valid_source(record, chats, memories=None, cache=None):
        cache = {} if cache is None else cache
        if record["id"] in cache:
            return cache[record["id"]]
        pending, active = [(record, iter(record.get("provenance", [])))], {record["id"]}
        while pending:
            record, sources = pending[-1]
            source = next(sources, None)
            if source is None:
                if record["chat_id"] is not None:
                    chat = chats.get(record["chat_id"])
                    if chat is None or (record["source_message_id"] is not None and not any(
                            message.get("id") == record["source_message_id"] and message.get("role") == "user" and isinstance(message.get("text"), str) and message["text"].strip()
                            for message in chat.get("messages", []))):
                        break
                active.remove(record["id"])
                cache[record["id"]] = True
                pending.pop()
            elif "memory_id" in source:
                target = (memories or {}).get(source["memory_id"])
                if target is None or revision(target) != source["hash"] or target["id"] in active or cache.get(target["id"]) is False:
                    break
                if target["id"] not in cache:
                    active.add(target["id"])
                    pending.append((target, iter(target.get("provenance", []))))
            else:
                role = source.get("role", "user")
                message = next((item for item in chats.get(source["chat_id"], {}).get("messages", []) if item.get("id") == source["message_id"] and item.get("role") == role), {})
                if not isinstance(message.get("text"), str) or (role == "assistant" and record["kind"] not in ("identity", "lesson")):
                    break
                actual = assistant_revision(message["text"], message.get("feedback")) if role == "assistant" else hashlib.sha256(message["text"].encode("utf-8")).hexdigest()
                if actual != source["hash"]:
                    break
        else:
            return True
        cache.update(dict.fromkeys(active, False))
        return False

    def list(self, chat_id=None):
        with self.reader() as db:
            if db is None:
                return []
            chats = self.chats(db)
            inventory = {record["id"]: record for row in db.execute("SELECT * FROM private_memories ORDER BY updated_at DESC, id") for record in [self.record(row)]}
            cache = {}
            return [record for record in inventory.values() if self.valid_source(record, chats, inventory, cache)
                    and (chat_id is None or record["scope"] == "workspace" or record["chat_id"] == chat_id)]

    def remove_stale(self, db, chats):
        chats = {chat["id"]: chat for chat in chats} if isinstance(chats, list) else chats
        inventory = {record["id"]: record for row in db.execute("SELECT * FROM private_memories") for record in [self.record(row)]}
        cache = {}
        removed = [record["id"] for record in inventory.values() if not record["pinned"] and not self.valid_source(record, chats, inventory, cache)]
        db.executemany("DELETE FROM private_memories WHERE id=?", [(memory_id,) for memory_id in removed])
        return removed

    def remember(self, content, scope="workspace", chat_id=None, source_message_id=None):
        content = content_value(content)
        if scope not in ("workspace", "conversation"):
            raise ValueError("Choose workspace or conversation memory.")
        if (scope == "conversation" or source_message_id is not None) and not chat_id:
            raise ValueError("Choose an existing chat for conversation or source-linked memory.")
        stamp = datetime.now(self.timezone).isoformat()
        record = {"id": uuid.uuid4().hex, "content": content, "scope": scope, "chat_id": chat_id,
                  "source_message_id": source_message_id, "origin": "explicit", "created_at": stamp, "updated_at": stamp,
                  "kind": "fact", "source_hash": None, "evidence": None, "provenance": [], "pinned": True}
        with self.transaction() as db:
            if not self.valid_source(record, self.chats(db)):
                raise ValueError("Choose an existing chat and a user message as the memory source.")
            if db.execute("SELECT COUNT(*) FROM private_memories").fetchone()[0] >= MAX_MEMORIES:
                raise ValueError("Memory is full. Forget an existing memory before adding another.")
            if source_message_id:
                text = next(message["text"] for message in self.chats(db)[chat_id]["messages"] if message["id"] == source_message_id)
                record["source_hash"] = hashlib.sha256(text.encode("utf-8")).hexdigest()
                record["provenance"] = [{"chat_id": chat_id, "message_id": source_message_id, "hash": record["source_hash"]}]
            self.invalidate_reflection(db, clear_journal=False)
            self.insert(db, record)
        return record

    def update(self, memory_id, content):
        content = content_value(content)
        with self.transaction() as db:
            row = db.execute("SELECT * FROM private_memories WHERE id=?", (memory_id,)).fetchone()
            old = self.record(row) if row else None
            if old is None or not self.valid_source(old, self.chats(db), {item["id"]: self.record(item) for item in db.execute("SELECT * FROM private_memories")}):
                raise ValueError("That memory could not be found.")
            record = {**old, "content": content, "origin": "explicit", "pinned": True, "updated_at": datetime.now(self.timezone).isoformat()}
            record.update(source_hash=None, evidence=None, provenance=[], source_message_id=None,
                          chat_id=old["chat_id"] if old["scope"] == "conversation" else None)
            self.tombstone(db, old)
            self.barrier(db)
            self.remove_dependents(db, memory_id)
            db.execute("UPDATE private_memories SET content=:content, origin='explicit', updated_at=:updated_at,chat_id=:chat_id,source_message_id=:source_message_id,metadata=:metadata WHERE id=:id",
                       {**record, "metadata": json.dumps({key: record[key] for key in ("kind", "source_hash", "evidence", "provenance")})})
            self.invalidate_reflection(db)
        return record

    def forget(self, memory_id):
        with self.transaction() as db:
            row = db.execute("SELECT * FROM private_memories WHERE id=?", (memory_id,)).fetchone()
            if row is None:
                raise ValueError("That memory could not be found.")
            self.tombstone(db, self.record(row))
            self.barrier(db)
            self.remove_dependents(db, memory_id)
            db.execute("DELETE FROM private_memories WHERE id=?", (memory_id,))
            self.invalidate_reflection(db)

    def invalidate_reflection(self, db, clear_journal=True):
        if clear_journal and db.execute("SELECT 1 FROM sqlite_master WHERE name='workspace'").fetchone():
            from backend.tasks import prune_ai_tasks
            row = db.execute("SELECT value FROM workspace WHERE id=1").fetchone()
            if row and prune_ai_tasks(workspace := json.loads(row[0]), db):
                db.execute("UPDATE workspace SET value=? WHERE id=1", (json.dumps(workspace),))
        if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='reflection_meta'").fetchone():
            from backend.reflection import ReflectionStore
            store = ReflectionStore(self.database, self.timezone)
            store.invalidate(db)
            if clear_journal and db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='reflection_journal'").fetchone():
                db.execute("DELETE FROM reflection_journal")

    def delete_chat(self, chat_id, db=None):
        if db is None:
            with self.transaction() as connection:
                self.delete_chat(chat_id, connection)
        elif db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='private_memories'").fetchone():
            db.execute("PRAGMA secure_delete=ON")
            for row in db.execute("SELECT * FROM private_memories").fetchall():
                record = self.record(row)
                if record["chat_id"] == chat_id or any(source.get("chat_id") == chat_id for source in record["provenance"]):
                    self.remove_dependents(db, record["id"])
                    db.execute("DELETE FROM private_memories WHERE id=?", (record["id"],))
            if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='memory_barriers'").fetchone():
                db.execute("DELETE FROM memory_barriers WHERE chat_id=?", (chat_id,))

    def recall(self, query, chat_id, local=True, limit=40, max_characters=32000):
        if not local or limit <= 0 or max_characters <= 0:
            return []
        query_terms = terms(query)
        ranked = [(len(query_terms & terms(record["content"])) + (100 if record["kind"] in ("identity", "lesson") else 0), record) for record in self.list(chat_id)
                  if record["scope"] == "workspace" or record["chat_id"] == chat_id]
        ranked.sort(key=lambda item: (-item[0], item[1]["id"]))
        selected, remaining = [], min(max_characters, MAX_RECALL_CHARACTERS)
        reflective_remaining = min(8000, remaining)
        for relevance, record in ranked:
            if record["kind"] in ("identity", "lesson") and len(record["content"]) > reflective_remaining:
                continue
            if relevance and len(record["content"]) <= remaining:
                selected.append(record)
                remaining -= len(record["content"])
                if record["kind"] in ("identity", "lesson"):
                    reflective_remaining -= len(record["content"])
                if len(selected) == min(limit, MAX_RECALL):
                    break
        return selected

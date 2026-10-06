"""Opt-in public excerpt capture and pinned readback; private indexing stays local."""
from contextlib import closing, contextmanager
from datetime import datetime, timezone
from functools import wraps
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import threading
import uuid

from backend.context_policy import (
    DEFAULT, ID, MANIFEST_BYTES, MAX_ARCHIVE_ITEMS, SCHEMA, SOURCE_BYTES, capture_source,
    configuration, contains_source_secret, contains_url_secret, eligible,
    manifest_hash, public_url, timestamp, validate_capture_manifest,
)
from backend.storage import (archive_available, archive_inventory, archive_owner, archive_path,
                             publish_archive, read_archive, read_archive_json)


ROOT = Path(__file__).resolve().parent.parent


def storage_errors(action):
    @wraps(action)
    def guarded(self, *args, **kwargs):
        try:
            return action(self, *args, **kwargs)
        except (OSError, sqlite3.Error):
            try:
                self.record_error()
            except (OSError, sqlite3.Error):
                pass
            raise ValueError("Context storage could not finish this operation. Check archive and local storage, then retry.") from None
    return guarded


def normalize_query(query):
    if not isinstance(query, str) or not 1 <= len(query.strip()) <= 512:
        raise ValueError("Provide one search query of at most 512 characters.")
    return " ".join(query.split())


class ContextStore:
    def __init__(self, database, archive_dir=None):
        self.database = Path(database).expanduser().resolve()
        self.index = self.database.parent / "context" / "index.sqlite3"
        raw = os.environ.get("MAESTRO_CONTEXT_ARCHIVE_DIR") if archive_dir is None else archive_dir
        self.archive = None
        if raw:
            path = Path(raw).expanduser()
            if not path.is_absolute():
                raise ValueError("Choose an absolute context archive path outside the checkout.")
            path = path.resolve()
            if (path == ROOT or ROOT in path.parents or path in ROOT.parents or path == self.database.parent
                    or path in self.database.parent.parents or self.database.parent in path.parents):
                raise ValueError("Keep the context archive outside the checkout and private database directory.")
            self.archive = path
        self._token = uuid.uuid4().hex
        self._write_lock = threading.RLock()

    def now(self):
        return datetime.now(timezone.utc)

    def _state(self):
        default = {"config": {**DEFAULT, "public_sources": []}, "last_error": None, "last_indexed_at": None,
                   "archive_bytes": 0, "archive_items": 0, "missing_count": 0, "corrupt_count": 0,
                   "write_unavailable": False, "last_capture": None, "rebuild_progress": None}
        if not self.database.is_file():
            return default
        with closing(sqlite3.connect(self.database.as_uri() + "?mode=ro", uri=True)) as db:
            if not db.execute("SELECT 1 FROM sqlite_master WHERE name='context_state'").fetchone():
                return default
            row = db.execute("SELECT value FROM context_state WHERE id=1").fetchone()
            if not row:
                return default
            saved = json.loads(row[0])
            legacy_config = saved.get("config", {})
            return {**default, **saved, "config": {**DEFAULT, **legacy_config,
                    "capture_policy": legacy_config.get("capture_policy", "approved_sources")}}

    def initialize(self):
        """Explicit private initialization; disabled archives are never created."""
        with self._write_lock:
            config = self._state()["config"]
            if config["enabled"]:
                return self.configure(config)
            self.database.parent.mkdir(parents=True, exist_ok=True)
            with closing(sqlite3.connect(self.database, timeout=10)) as db, db:
                db.execute("CREATE TABLE IF NOT EXISTS context_state(id INTEGER PRIMARY KEY CHECK(id=1),value TEXT NOT NULL)")
                db.execute("CREATE TABLE IF NOT EXISTS context_queries(key TEXT PRIMARY KEY,query TEXT NOT NULL,at TEXT NOT NULL,captures TEXT NOT NULL)")
            return self.status()

    def record_error(self, message=None):
        # Never persist arbitrary exceptions, paths or provider-generated prose.
        with self._write_lock, self._transaction() as (state, _):
            state["last_error"] = "The context archive is unavailable. Check its directory and writer ownership."
            state["write_unavailable"] = True
        return self.status()

    @contextmanager
    def _transaction(self):
        # Initializing private schema here must not recursively initialize the archive.
        self.database.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.database, timeout=10)) as db, db:
            db.execute("CREATE TABLE IF NOT EXISTS context_state(id INTEGER PRIMARY KEY CHECK(id=1),value TEXT NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS context_queries(key TEXT PRIMARY KEY,query TEXT NOT NULL,at TEXT NOT NULL,captures TEXT NOT NULL)")
            db.execute("PRAGMA secure_delete=ON")
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT value FROM context_state WHERE id=1").fetchone()
            state = json.loads(row[0]) if row else self._state()
            yield state, db
            db.execute("INSERT INTO context_state VALUES(1,?) ON CONFLICT(id) DO UPDATE SET value=excluded.value", (json.dumps(state),))

    def status(self):
        state = self._state()
        count = 0
        if self.index.is_file():
            try:
                with closing(sqlite3.connect(self.index.as_uri() + "?mode=ro", uri=True)) as db:
                    count = db.execute("SELECT COUNT(*) FROM captures").fetchone()[0]
            except sqlite3.Error:
                state = {**state, "last_error": "The local source index needs rebuilding."}
        available = not state.get("write_unavailable") and archive_available(self.archive, self._token)
        return {"configured": self.archive is not None, "available": available,
                "path": str(self.archive) if self.archive else None, "config": state["config"], "indexed_count": count,
                **{field: state[field] for field in ("missing_count", "corrupt_count", "archive_bytes", "archive_items",
                                                    "last_error", "last_indexed_at", "last_capture", "rebuild_progress")}}

    @contextmanager
    def archive_owner(self):
        with archive_owner(self.archive, self._token, self._write_lock):
            yield self

    @contextmanager
    def _writer(self):
        with self._write_lock, self.archive_owner():
            yield

    def _format(self):
        expected = {"format": "maestro-public-context", "schema_version": SCHEMA}
        path = archive_path(self.archive, "format.json")
        if path.exists():
            data = read_archive_json(self.archive, "format.json", 4096)
            if data != expected or type(data.get("schema_version")) is not int:
                raise ValueError("The archive uses an unsupported format.")
        else:
            publish_archive(self.archive, "format.json", json.dumps(expected).encode())

    @storage_errors
    def configure(self, config):
        config = configuration(config)
        with (self._writer() if config["enabled"] else self._write_lock):
            previous = self._state()["config"]
            if config["enabled"]:
                self._format()
                # Saving a lower cap remains possible for an already larger archive.
                total, items, _ = archive_inventory(self.archive, MAX_ARCHIVE_ITEMS * 2)
            with self._transaction() as (state, db):
                if any(config[field] != previous[field] for field in ("enabled", "capture_policy", "public_sources")):
                    db.execute("DELETE FROM context_queries")
                state["config"] = config
                if config["enabled"]:
                    state.update(last_error=None, write_unavailable=False,
                                 archive_bytes=total, archive_items=items)
        return self.status()

    def _index_connection(self, path=None):
        path = path or self.index
        path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(path, timeout=10)
        try:
            db.execute("PRAGMA secure_delete=ON")
            db.execute("CREATE TABLE IF NOT EXISTS captures(id TEXT PRIMARY KEY,manifest TEXT NOT NULL,title TEXT NOT NULL,url TEXT NOT NULL,content TEXT NOT NULL,at TEXT NOT NULL,manifest_hash TEXT)")
            if "manifest_hash" not in {row[1] for row in db.execute("PRAGMA table_info(captures)")}:
                db.execute("ALTER TABLE captures ADD COLUMN manifest_hash TEXT")
            db.execute("CREATE VIRTUAL TABLE IF NOT EXISTS captures_fts USING fts5(id UNINDEXED,title,content)")
        except sqlite3.Error:
            db.close()
            raise
        return db

    def _index_capture(self, manifest, relative, content):
        with closing(self._index_connection()) as db, db:
            db.execute("DELETE FROM captures_fts WHERE id=?", (manifest["capture_id"],))
            db.execute("INSERT OR REPLACE INTO captures VALUES(?,?,?,?,?,?,?)", (manifest["capture_id"], relative, manifest["title"], manifest["source_url"], content, manifest["retrieved_at"], manifest_hash(manifest)))
            db.execute("INSERT INTO captures_fts(id,title,content) VALUES(?,?,?)", (manifest["capture_id"], manifest["title"], content))

    def _deleted(self, capture_id):
        return archive_path(self.archive, "records/deletions/" + capture_id + ".json").exists()

    def _manifest(self, relative, config):
        data = validate_capture_manifest(read_archive_json(self.archive, relative, MANIFEST_BYTES), relative, config)
        if self._deleted(data["capture_id"]):
            raise ValueError("Invalid or unavailable source capture.")
        obj = data["object"]
        raw = read_archive(self.archive, obj["path"], SOURCE_BYTES)
        if len(raw) != obj["bytes"] or hashlib.sha256(raw).hexdigest() != obj["sha256"]:
            raise ValueError("An archived source failed its hash check.")
        return data, raw.decode("utf-8")

    def _key(self, query, max_results):
        if type(max_results) is not int or not 1 <= max_results <= 3:
            raise ValueError("Choose one to three search results.")
        return hashlib.sha256(json.dumps([normalize_query(query), max_results, "ollama_web_search", SCHEMA]).encode()).hexdigest()

    def capture(self, query, at, sources, max_results, secrets=()):
        secrets = tuple(secret for secret in secrets if secret)
        key, at = self._key(query, max_results), timestamp(at)
        query = normalize_query(query)
        result = {"query": query, "at": at, "sources": [], "from_cache": False}
        saved = []
        measured = {"retrieved_at": at, "sources_received": len(sources[:max_results]), "sources_saved": 0,
                    "excerpt_bytes": 0, "manifest_bytes": 0, "object_bytes": 0, "new_bytes": 0}
        config = self._state()["config"]
        for source in sources[:max_results]:
            clean = {field: source.get(field) for field in ("title", "url", "content")} if isinstance(source, dict) else {}
            if not all(isinstance(clean.get(field), str) for field in ("title", "url", "content")):
                continue
            if contains_url_secret(clean["url"], secrets) or contains_source_secret(clean["url"], secrets):
                continue  # Never forward a credential embedded in a source link.
            for field in ("title", "content"):
                for secret in secrets:
                    clean[field] = clean[field].replace(secret, "\u2588")
            if any(contains_source_secret(clean[field], secrets) for field in ("title", "content")):
                continue  # Encoded credentials must not reach inference or synced files.
            existing = source.get("completeness")
            completeness = {"maestro_truncated": bool(source.get("maestro_truncated") or (existing.get("maestro_truncated") if isinstance(existing, dict) else False)), "full_page": False}
            clean.update(archive_status="skipped", content_kind="search_excerpt", retrieved_at=at,
                         published_at=None, modified_at=None, completeness=completeness)
            result["sources"].append(clean)
        if not config["enabled"]:
            return result
        try:
            with self._writer():
                config = self._state()["config"]
                if not config["enabled"]:
                    return result
                self._format()
                total, items, _ = archive_inventory(self.archive, config["max_items"])
                for clean in result["sources"]:
                    if not eligible(clean["url"], config):
                        continue
                    clean["archive_status"] = "not_saved"
                    raw = clean["content"].encode("utf-8")
                    if not 1 <= len(raw) <= SOURCE_BYTES:
                        raise ValueError("A source excerpt exceeds its archive allowance.")
                    content_hash = hashlib.sha256(raw).hexdigest()
                    capture_id = uuid.uuid4().hex
                    relative = "records/captures/" + at[:7] + "/" + capture_id + ".json"
                    obj = {"path": "objects/sha256/" + content_hash[:2] + "/" + content_hash + ".txt", "sha256": content_hash, "bytes": len(raw), "encoding": "utf-8"}
                    manifest = {"schema_version": SCHEMA, "capture_id": capture_id,
                                "source_id": hashlib.sha256(public_url(clean["url"], allow_query=True, allow_http=True).encode()).hexdigest(),
                                "source_url": clean["url"], "title": clean["title"][:200], "object": obj,
                                "retrieved_at": at, "content_kind": "search_excerpt", "completeness": clean["completeness"], "published_at": None, "modified_at": None}
                    encoded = json.dumps(manifest, ensure_ascii=False).encode("utf-8")
                    extra = len(encoded) + (0 if archive_path(self.archive, obj["path"]).exists() else len(raw))
                    new_paths = {archive_path(self.archive, name) for name in (obj["path"], relative)}
                    new_paths |= {parent for path in tuple(new_paths) for parent in path.parents if parent != self.archive and self.archive in parent.parents}
                    additions = sum(not path.exists() for path in new_paths)
                    if total + extra > config["max_bytes"] or items + additions > config["max_items"] or shutil.disk_usage(self.archive).free < extra + 65536:
                        result["archive_warning"] = "The archive has reached its byte, item or free-space allowance. Some source evidence was not saved."
                        continue
                    for field, path, data in (("object_bytes", obj["path"], raw), ("manifest_bytes", relative, encoded)):
                        if publish_archive(self.archive, path, data):
                            measured[field] += len(data)
                            measured["new_bytes"] += len(data)
                    self._index_capture(manifest, relative, clean["content"])
                    clean.update(capture_source(manifest, clean["content"]))
                    saved.append({field: clean[field] for field in ("capture_id", "content_hash", "manifest_hash")})
                    measured["sources_saved"] += 1
                    measured["excerpt_bytes"] += len(raw)
                    total += extra
                    items += additions
                with self._transaction() as (state, db):
                    if saved:
                        db.execute("INSERT INTO context_queries VALUES(?,?,?,?) ON CONFLICT(key) DO UPDATE SET query=excluded.query,at=excluded.at,captures=excluded.captures", (key, query, at, json.dumps(saved)))
                    total, items, _ = archive_inventory(self.archive, config["max_items"])
                    state.update(archive_bytes=total, archive_items=items, last_error=result.get("archive_warning"),
                                 write_unavailable=False, last_capture=measured)
                    if saved:
                        state["last_indexed_at"] = self.now().isoformat()
        except (OSError, sqlite3.Error, ValueError, UnicodeError):
            result["archive_warning"] = "Some source evidence could not be saved. Existing captures remain unchanged."
            try:
                with self._write_lock:
                    try:
                        total, items, _ = archive_inventory(self.archive, config["max_items"])
                    except (OSError, ValueError):
                        total = items = None
                    with self._transaction() as (state, _):
                        state.update(last_error=result["archive_warning"], last_capture=measured)
                        if total is not None:
                            state.update(archive_bytes=total, archive_items=items)
            except (OSError, sqlite3.Error):
                pass
        return result

    def get_capture(self, capture_id, expected_hash=None, expected_manifest_hash=None):
        with self._write_lock:
            if not isinstance(capture_id, str) or not ID.fullmatch(capture_id):
                raise KeyError(capture_id)
            config = self._state()["config"]
            if not config["enabled"] or not self.index.is_file():
                raise KeyError(capture_id)
            try:
                with closing(sqlite3.connect(self.index.as_uri() + "?mode=ro", uri=True)) as db:
                    row = db.execute("SELECT manifest,manifest_hash FROM captures WHERE id=?", (capture_id,)).fetchone()
                if not row:
                    raise KeyError(capture_id)
                manifest, content = self._manifest(row[0], config)
                digest = manifest_hash(manifest)
                if manifest["capture_id"] != capture_id or row[1] != digest or expected_manifest_hash is not None and expected_manifest_hash != digest:
                    raise KeyError(capture_id)
                if expected_hash is not None and manifest["object"]["sha256"] != expected_hash:
                    raise KeyError(capture_id)
                return capture_source(manifest, content)
            except (OSError, sqlite3.Error, ValueError, TypeError, UnicodeError):
                raise KeyError(capture_id) from None

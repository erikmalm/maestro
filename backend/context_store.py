"""Opt-in public excerpt capture and pinned readback; private indexing stays local."""
from contextlib import closing, contextmanager, nullcontext
from datetime import date, datetime, timezone
from functools import wraps
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import threading
import time
import uuid
from urllib.parse import urlsplit

from backend.context_policy import (
    DEFAULT, HASH, ID, MANIFEST_BYTES, MAX_ARCHIVE_ITEMS, SCHEMA, SOURCE_BYTES, capture_source,
    configuration, contains_source_secret, contains_url_secret, eligible,
    manifest_hash, public_url, timestamp, validate_capture_manifest,
)
from backend.storage import (archive_available, archive_inventory, archive_owner, archive_path,
                             publish_archive, read_archive, read_archive_json)


ROOT = Path(__file__).resolve().parent.parent
INVENTORY_SCAN_LIMIT = 100
CANDIDATE_SCAN_LIMIT = 200
CANDIDATE_SECONDS = 2
CANDIDATE_SQL_STEPS = 10000000
CANDIDATE_MAP_BYTES = 4096


@contextmanager
def _candidate_tokenizer(progress=None):
    # Use the existing index's default unicode61 tokenizer, including its legacy
    # diacritic rules. Python normalization/casefolding changes those semantics.
    # Only this disposable in-memory database is writable during lookup.
    with closing(sqlite3.connect(":memory:")) as db:
        if progress is not None:
            db.set_progress_handler(progress, 1000)
        db.execute("CREATE VIRTUAL TABLE candidate_text USING fts5(value)")
        db.execute("CREATE VIRTUAL TABLE candidate_terms USING fts5vocab(candidate_text, instance)")

        def replace(value):
            db.execute("DELETE FROM candidate_text")
            db.execute("INSERT INTO candidate_text(rowid,value) VALUES(1,?)", (value,))

        def keywords(query):
            replace(query)
            # Count instances, including repeated words, and stop at the first
            # term beyond the public 16-keyword allowance.
            return [row[0] for row in db.execute("SELECT term FROM candidate_terms ORDER BY offset LIMIT 17")]

        def matches(value, expression):
            replace(value)
            return db.execute("SELECT 1 FROM candidate_text WHERE candidate_text MATCH ?",
                              (expression,)).fetchone() is not None

        yield keywords, matches


def validate_candidate_lookup(query="", *, limit=10, domain=None, retrieved_from=None, retrieved_to=None):
    """Validate local lookup inputs before inspecting any storage."""
    if not isinstance(query, str) or len(query) > 512:
        raise ValueError("Use a query of at most 512 characters.")
    query = " ".join(query.split())
    if query:
        with _candidate_tokenizer() as (keywords, _):
            if not 1 <= len(keywords(query)) <= 16:
                raise ValueError("Use one to sixteen literal keywords for saved evidence.")
    if type(limit) is not int or not 1 <= limit <= 20:
        raise ValueError("Choose one to twenty saved evidence candidates.")
    if domain is not None:
        try:
            if (not isinstance(domain, str) or not 1 <= len(domain) <= 253
                    or any(character in domain for character in "/\\:@?#*%[]")
                    or any(ord(character) <= 32 or ord(character) == 127 for character in domain)):
                raise ValueError()
            domain = urlsplit(public_url("https://" + domain + "/")).hostname
            if (not re.fullmatch(r"(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+[a-z](?:[a-z0-9-]*[a-z0-9])?", domain)
                    or len(domain) > 253 or any(len(label) > 63 for label in domain.split("."))):
                raise ValueError()
        except (ValueError, TypeError, UnicodeError):
            raise ValueError("Use an exact public hostname without a URL, port, path or wildcard.") from None
    for value in (retrieved_from, retrieved_to):
        if value is not None:
            try:
                if not isinstance(value, str) or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
                    raise ValueError()
                date.fromisoformat(value)
            except ValueError:
                raise ValueError("Use valid retrieval dates in YYYY-MM-DD format.") from None
    if retrieved_from and retrieved_to and retrieved_from > retrieved_to:
        raise ValueError("The retrieval start date must not follow its end date.")
    return {"query": query, "limit": limit, "domain": domain,
            "retrieved_from": retrieved_from, "retrieved_to": retrieved_to}


def _inventory_position(cursor):
    if cursor is None:
        return None
    try:
        if not isinstance(cursor, str) or not 1 <= len(cursor) <= 160:
            raise ValueError()
        values = json.loads(base64.b64decode(cursor + "=" * (-len(cursor) % 4), altchars=b"-_", validate=True))
        if (not isinstance(values, list) or len(values) != 2
                or not isinstance(values[0], str) or not 1 <= len(values[0]) <= 64
                or timestamp(values[0]) != values[0]
                or not isinstance(values[1], str) or not ID.fullmatch(values[1])):
            raise ValueError()
        return tuple(values)
    except (ValueError, TypeError, UnicodeError):
        raise ValueError("Use a valid saved-evidence page cursor.") from None


def _inventory_cursor(at, capture_id):
    return base64.urlsafe_b64encode(json.dumps([at, capture_id], separators=(",", ":")).encode()).decode().rstrip("=")


def storage_errors(action):
    @wraps(action)
    def guarded(self, *args, **kwargs):
        with self._write_lock:
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
        self.path_error = None

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
        try:
            if self.index.is_file():
                with closing(sqlite3.connect(self.index.as_uri() + "?mode=ro", uri=True)) as db:
                    count = db.execute("SELECT COUNT(*) FROM captures").fetchone()[0]
        except (OSError, sqlite3.Error):
            state = {**state, "last_error": "The local source index needs rebuilding."}
        available = not state.get("write_unavailable") and archive_available(self.archive, self._token)
        return {"configured": self.archive is not None, "available": available,
                "path": str(self.archive) if self.archive else None,
                "database_path": str(self.database), "index_path": str(self.index), "path_error": self.path_error,
                "config": state["config"], "indexed_count": count,
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
        if self.path_error:
            raise ValueError(self.path_error)
        config = configuration(config)
        try:
            with (self._writer() if config["enabled"] else self._write_lock):
                previous = self._state()["config"]
                if config["enabled"]:
                    self._format()
                    # Inventory is already bounded and does not open captures.
                    total, items, captures = archive_inventory(self.archive, MAX_ARCHIVE_ITEMS * 2)
                    # Explicit setup can initialize a genuinely empty archive.
                    # Existing captures without their index require recovery;
                    # never hide them behind a newly created empty catalogue.
                    if self.index.is_file() or not captures:
                        with closing(self._index_connection()) as db, db:
                            pass
                with self._transaction() as (state, db):
                    if any(config[field] != previous[field] for field in ("enabled", "capture_policy", "public_sources")):
                        db.execute("DELETE FROM context_queries")
                    state["config"] = config
                    if config["enabled"]:
                        state.update(last_error=None, write_unavailable=False,
                                     archive_bytes=total, archive_items=items)
        except ValueError:
            self.record_error()
            raise
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
            db.execute("CREATE INDEX IF NOT EXISTS captures_by_retrieval ON captures(at DESC,id DESC)")
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

    def list_captures(self, limit=25, cursor=None):
        """Page indexed metadata only; source bodies are verified when opened."""
        if type(limit) is not int or not 1 <= limit <= 50:
            raise ValueError("Choose one to fifty saved evidence records.")
        position = _inventory_position(cursor)
        result = {"state": "ready", "sources": [], "next_cursor": None, "skipped_count": 0}
        with self._write_lock:
            try:
                if self.path_error:
                    raise ValueError()
                if self.archive is None:
                    return {**result, "state": "unconfigured"}
                config = configuration(self._state()["config"])
                if not config["enabled"]:
                    return {**result, "state": "disabled"}
                if not self.archive.is_dir() or not self.index.is_file():
                    raise ValueError()
                clause, parameters = ("WHERE (at,id) < (?,?)", position) if position else ("", ())
                with closing(sqlite3.connect(self.index.as_uri() + "?mode=ro", uri=True)) as db:
                    deadline, steps = time.monotonic() + 2, 0

                    def bounded_query():
                        nonlocal steps
                        steps += 1000
                        return int(steps > 10000000 or time.monotonic() > deadline)

                    db.set_progress_handler(bounded_query, 1000)
                    rows = db.execute(
                        "SELECT id,manifest,title,url,at,manifest_hash FROM captures INDEXED BY captures_by_retrieval " + clause +
                        " ORDER BY at DESC,id DESC LIMIT ?", [*parameters, INVENTORY_SCAN_LIMIT + 1]).fetchall()
                consumed, last = 0, None
                for capture_id, relative, title, url, at, digest in rows[:INVENTORY_SCAN_LIMIT]:
                    # Invalid ordering keys cannot yield a safe continuation.
                    if (not isinstance(at, str) or len(at) > 64 or timestamp(at) != at
                            or not isinstance(capture_id, str) or not ID.fullmatch(capture_id)):
                        raise ValueError()
                    consumed += 1
                    last = (at, capture_id)
                    try:
                        if relative != "records/captures/" + at[:7] + "/" + capture_id + ".json":
                            raise ValueError()
                        manifest = validate_capture_manifest(read_archive_json(self.archive, relative, MANIFEST_BYTES), relative, config)
                        if (self._deleted(capture_id) or manifest_hash(manifest) != digest
                                or (manifest["capture_id"], manifest["title"], manifest["source_url"], manifest["retrieved_at"])
                                != (capture_id, title, url, at)):
                            raise ValueError()
                    except (OSError, ValueError, KeyError, TypeError, UnicodeError):
                        result["skipped_count"] += 1
                        continue
                    result["sources"].append({
                        "capture_id": capture_id, "title": title, "url": url,
                        **{field: manifest[field] for field in ("retrieved_at", "published_at", "modified_at", "content_kind", "completeness")},
                        "content_hash": manifest["object"]["sha256"], "manifest_hash": digest,
                        "content_bytes": manifest["object"]["bytes"], "body_verified": False,
                    })
                    if len(result["sources"]) == limit:
                        break
                if len(rows) > consumed and last is not None:
                    result["next_cursor"] = _inventory_cursor(*last)
                return result
            except (OSError, sqlite3.Error, ValueError, KeyError, TypeError, UnicodeError):
                return {"state": "unavailable", "sources": [], "next_cursor": None, "skipped_count": 0,
                        "message": "Saved evidence is unavailable. Check the archive and local index, then retry."}

    def _candidate_config(self, db=None):
        """Read bounded configuration only; a supplied connection shares its caller's work budget."""
        config = {**DEFAULT, "public_sources": []}
        if db is None:
            with self._write_lock:
                if not self.database.is_file():
                    return config
                deadline, steps = time.monotonic() + CANDIDATE_SECONDS, 0

                def bounded_query():
                    nonlocal steps
                    steps += 1000
                    return int(steps >= CANDIDATE_SQL_STEPS or time.monotonic() >= deadline)

                with closing(sqlite3.connect(self.database.as_uri() + "?mode=ro", uri=True,
                                            timeout=CANDIDATE_SECONDS)) as private:
                    private.set_progress_handler(bounded_query, 1000)
                    return self._candidate_config(private)
        if db.execute("SELECT 1 FROM sqlite_master WHERE name='context_state'").fetchone():
            row = db.execute("SELECT CASE WHEN typeof(value)='text' AND length(CAST(value AS BLOB))<=1048576 "
                             "THEN value END FROM context_state WHERE id=1").fetchone()
            if row:
                saved = json.loads(row[0])["config"]
                config = {**DEFAULT, **saved, "capture_policy": saved.get("capture_policy", "approved_sources")}
        return configuration(config)

    def lookup_candidates(self, query="", *, limit=10, domain=None, retrieved_from=None, retrieved_to=None, secrets=()):
        """Read verified local candidates, without accepting their suitability or acquiring evidence."""
        filters = validate_candidate_lookup(query, limit=limit, domain=domain,
                                            retrieved_from=retrieved_from, retrieved_to=retrieved_to)
        query, domain = filters["query"], filters["domain"]
        secrets = tuple(secret for secret in secrets if secret)
        result = {"state": "ready", "candidates": [], "skipped_count": 0, "search_limited": False}

        class WorkLimit(Exception):
            pass

        with self._write_lock:
            deadline, steps, examined = time.monotonic() + CANDIDATE_SECONDS, 0, 0
            work_exceeded = False

            def check_time():
                nonlocal work_exceeded
                if time.monotonic() >= deadline:
                    work_exceeded = True
                    raise WorkLimit()

            def bounded_query():
                nonlocal steps, work_exceeded
                steps += 1000
                work_exceeded = work_exceeded or steps >= CANDIDATE_SQL_STEPS or time.monotonic() >= deadline
                return int(work_exceeded)

            @contextmanager
            def reader(path):
                check_time()
                with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True,
                                            timeout=max(0, deadline - time.monotonic()))) as db:
                    db.set_progress_handler(bounded_query, 1000)
                    yield db

            def bounded(column, maximum):
                # Never fetch unbounded, disposable SQLite values into Python.
                return (f"CASE WHEN typeof({column})='text' AND length(CAST({column} AS BLOB))<={maximum} "
                        f"THEN {column} END")

            def source_domain(value):
                try:
                    return urlsplit(public_url(value, allow_query=True, allow_http=True)).hostname
                except (ValueError, TypeError):
                    return None

            def matches_filters(source):
                day = source["retrieved_at"][:10]
                return ((domain is None or source_domain(source["url"]) == domain)
                        and (retrieved_from is None or day >= retrieved_from)
                        and (retrieved_to is None or day <= retrieved_to))

            try:
                if self.path_error:
                    raise ValueError()
                if self.archive is None:
                    return {**result, "state": "unconfigured"}
                if not self.database.is_file():
                    return {**result, "state": "disabled"}
                with reader(self.database) as private:
                    config = self._candidate_config(private)
                    if not config["enabled"]:
                        return {**result, "state": "disabled"}
                    if not self.archive.is_dir() or not self.index.is_file():
                        raise ValueError()
                    tokenizer = _candidate_tokenizer(bounded_query) if query else nullcontext((None, None))
                    with reader(self.index) as index, tokenizer as (keywords, matches):
                        expression = (" AND ".join('"' + term.replace('"', '""') + '"' for term in keywords(query))
                                      if query else None)
                        index.create_function("source_domain", 1, source_domain)
                        fields = (("id", 32), ("manifest", 100), ("title", 800), ("url", 8192),
                                  ("at", 64), ("manifest_hash", 64), ("content", SOURCE_BYTES))
                        projection = ",".join(bounded("c." + field, maximum) for field, maximum in fields)
                        verified, accepted = {}, set()
                        now = self.now()

                        def verify(row):
                            nonlocal examined, work_exceeded
                            check_time()
                            capture_id = row[0] if row else None
                            if examined >= CANDIDATE_SCAN_LIMIT:
                                work_exceeded = True
                                raise WorkLimit()
                            examined += 1
                            if capture_id in verified:
                                return verified[capture_id]
                            try:
                                if row is None:
                                    raise ValueError()
                                capture_id, relative, title, url, at, digest, content = row[:7]
                                if (not isinstance(capture_id, str) or not ID.fullmatch(capture_id)
                                        or not isinstance(at, str) or timestamp(at) != at
                                        or not isinstance(digest, str) or not HASH.fullmatch(digest)
                                        or relative != "records/captures/" + at[:7] + "/" + capture_id + ".json"):
                                    raise ValueError()
                                manifest, body = self._manifest(relative, config)
                                source = capture_source(manifest, body)
                                if (capture_id, title, url, at, digest, content) != (
                                        source["capture_id"], source["title"], source["url"], source["retrieved_at"],
                                        source["manifest_hash"], source["content"]):
                                    raise ValueError()
                                normalized_url = public_url(url, allow_query=True, allow_http=True)
                                if (contains_url_secret(url, secrets) or contains_url_secret(normalized_url, secrets)
                                        or any(contains_source_secret(value, secrets) for value in (title, url, normalized_url, body))):
                                    raise ValueError()
                                source.update(body_verified=True,
                                              stale=(now - datetime.fromisoformat(at)).total_seconds() > config["reuse_hours"] * 3600)
                                check_time()
                                verified[capture_id] = source
                                return source
                            except (OSError, ValueError, KeyError, TypeError, UnicodeError):
                                result["skipped_count"] += 1
                                if capture_id is not None:
                                    verified[capture_id] = None
                                return None

                        def append(source, kind):
                            if source and source["capture_id"] not in accepted and matches_filters(source):
                                accepted.add(source["capture_id"])
                                result["candidates"].append({**source, "match_kind": kind})

                        if query and private.execute("SELECT 1 FROM sqlite_master WHERE name='context_queries'").fetchone():
                            keys = [self._key(query, count) for count in (1, 2, 3)]
                            rows = private.execute(
                                "SELECT " + ",".join((bounded("query", 2048), bounded("at", 64),
                                                      bounded("captures", CANDIDATE_MAP_BYTES))) +
                                " FROM context_queries WHERE key IN (?,?,?) ORDER BY at DESC,key LIMIT 3", keys).fetchall()
                            for recorded_query, at, raw in rows:
                                check_time()
                                try:
                                    pins = json.loads(raw)
                                    if (recorded_query != query or timestamp(at) != at or not isinstance(pins, list)
                                            or not 1 <= len(pins) <= 3):
                                        raise ValueError()
                                    for pin in pins:
                                        if (not isinstance(pin, dict) or set(pin) != {"capture_id", "content_hash", "manifest_hash"}
                                                or not isinstance(pin["capture_id"], str) or not ID.fullmatch(pin["capture_id"])
                                                or any(not isinstance(pin[name], str) or not HASH.fullmatch(pin[name])
                                                       for name in ("content_hash", "manifest_hash"))):
                                            raise ValueError()
                                except (ValueError, KeyError, TypeError, RecursionError):
                                    result["skipped_count"] += 1
                                    continue
                                for pin in pins:
                                    source = verify(index.execute("SELECT " + projection +
                                                                 " FROM captures c WHERE c.id=?", (pin["capture_id"],)).fetchone())
                                    if source:
                                        if any(source[name] != value for name, value in pin.items()) or source["retrieved_at"] != at:
                                            result["skipped_count"] += 1
                                        else:
                                            append(source, "exact_query")
                                    if len(result["candidates"]) == limit:
                                        break
                                if len(result["candidates"]) == limit:
                                    break
                        if len(result["candidates"]) < limit:
                            parameters, clauses = [], []
                            if domain:
                                clauses.append("source_domain(" + bounded("c.url", 8192) + ")=?")
                                parameters.append(domain)
                            for operation, value in ((">=", retrieved_from), ("<=", retrieved_to)):
                                if value is not None:
                                    clauses.append("substr(c.at,1,10)" + operation + "?")
                                    parameters.append(value)
                            if query:
                                clauses.insert(0, "captures_fts MATCH ?")
                                parameters.insert(0, expression)
                                selection = projection + "," + bounded("f.title", 800) + "," + bounded("f.content", SOURCE_BYTES)
                                tables = "captures_fts f JOIN captures c ON c.id=f.id"
                                order = "bm25(captures_fts),c.at DESC,c.id DESC"
                            else:
                                selection, tables, order = projection, "captures c", "c.at DESC,c.id DESC"
                            sql = ("SELECT " + selection + " FROM " + tables +
                                   (" WHERE " + " AND ".join(clauses) if clauses else "") +
                                   " ORDER BY " + order + " LIMIT ?")
                            cursor = index.execute(sql, [*parameters, CANDIDATE_SCAN_LIMIT + 1])
                            for row in cursor:
                                source = verify(row)
                                if source and query and ((row[7], row[8]) != (source["title"], source["content"])
                                        or not matches(source["title"] + "\n" + source["content"], expression)):
                                    result["skipped_count"] += 1
                                    continue
                                check_time()
                                append(source, "keyword" if query else "filtered")
                                if len(result["candidates"]) == limit:
                                    break
                        check_time()
            except (OSError, sqlite3.Error, ValueError, KeyError, TypeError, UnicodeError, RecursionError, WorkLimit):
                result["state"] = "limited" if work_exceeded else "unavailable"
                result["search_limited"] = work_exceeded
                result["message"] = ("Saved evidence lookup reached its work limit. Results may be incomplete."
                                     if work_exceeded else "Saved evidence is unavailable. Results may be incomplete.")
            if result["skipped_count"] and "message" not in result:
                result["message"] = "Some saved evidence could not be verified or returned. Results may be incomplete."
            return result

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
        # Keep the writer attempt and its failure receipt ordered before retries.
        with self._write_lock:
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
            try:
                if not config["enabled"] or not self.index.is_file():
                    raise KeyError(capture_id)
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

"""Opt-in public search excerpts, immutable files and a private rebuildable index."""
from contextlib import closing, contextmanager
from datetime import date, datetime, timedelta, timezone
from functools import wraps
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import threading
import time
import unicodedata
from urllib.parse import urlsplit
import uuid

from backend.context_policy import (
    DEFAULT, HASH, ID, MANIFEST_BYTES, MAX_ARCHIVE_ITEMS, SCHEMA, SOURCE_BYTES, capture_source,
    configuration, contains_source_secret, contains_url_secret, decoded_url_variants, eligible,
    manifest_hash, public_url, timestamp, validate_capture_manifest,
)
from backend.storage import (archive_available, archive_inventory, archive_owner, archive_path,
                             publish_archive, read_archive, read_archive_json)


SEARCH_CANDIDATES = 200
SEARCH_SECONDS = 2
REBUILD_SECONDS = 5
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


def _search_terms(query):
    # Match the default unicode61 tokenizer's letters, numbers and private-use
    # characters. Combining marks remain with their word, including NFD text.
    terms, current = [], []
    for character in query:
        category = unicodedata.category(character)
        if category[0] in ("L", "N") or category == "Co" or category[0] == "M" and current:
            current.append(character)
        elif current:
            terms.append("".join(current))
            current = []
    if current:
        terms.append("".join(current))
    if not terms or len(terms) > 16:
        raise ValueError("Use one to sixteen literal keywords for saved-source search.")
    return terms


def validate_search(query, limit=3, *, domain=None, retrieved_from=None, retrieved_to=None):
    """Shared public validation; operators are always interpreted as words."""
    query = normalize_query(query)
    _search_terms(query)
    if type(limit) is not int or not 1 <= limit <= 20:
        raise ValueError("Choose one to twenty saved search results.")
    if domain is not None:
        if (not isinstance(domain, str) or len(domain) > 253 or not domain
                or any(character in domain for character in "/\\:@?#*%[]")
                or any(ord(character) <= 32 or ord(character) == 127 for character in domain)):
            raise ValueError("Use an exact public hostname without a URL, path, port or wildcard.")
        try:
            domain = urlsplit(public_url("https://" + domain + "/")).hostname
            if (not re.fullmatch(r"(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+[a-z](?:[a-z0-9-]*[a-z0-9])?", domain)
                    or len(domain) > 253 or any(len(label) > 63 for label in domain.split("."))):
                raise ValueError()
        except (ValueError, TypeError):
            raise ValueError("Use an exact public hostname without a URL, path, port or wildcard.") from None
    for value in (retrieved_from, retrieved_to):
        if value is not None:
            try:
                if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
                    raise ValueError()
                date.fromisoformat(value)
            except ValueError:
                raise ValueError("Use valid retrieval dates in YYYY-MM-DD format.") from None
    if retrieved_from and retrieved_to and retrieved_from > retrieved_to:
        raise ValueError("The retrieval start date must not follow its end date.")
    return {"query": query, "limit": limit, "domain": domain,
            "retrieved_from": retrieved_from, "retrieved_to": retrieved_to}


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
            if config != previous:
                self._invalidate_rebuild()
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
        self._invalidate_rebuild()
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

    def lookup(self, query, max_results, allow_stale=False):
        with self._write_lock:
            key = self._key(query, max_results)
            state = self._state()
            if not state["config"]["enabled"] or not self.database.is_file() or not self.archive or not self.archive.is_dir():
                return None
            try:
                with closing(sqlite3.connect(self.database.as_uri() + "?mode=ro", uri=True)) as db:
                    row = db.execute("SELECT at,captures FROM context_queries WHERE key=?", (key,)).fetchone()
                if not row:
                    return None
                at = datetime.fromisoformat(row[0])
                age = self.now() - at
                stale = age > timedelta(hours=state["config"]["reuse_hours"])
                if age < timedelta(0) or not allow_stale and stale:
                    return None
                sources = [{**self.get_capture(item["capture_id"], expected_hash=item["content_hash"], expected_manifest_hash=item["manifest_hash"]), "stale": stale}
                           for item in json.loads(row[1])]
                return {"query": normalize_query(query), "at": row[0], "sources": sources, "from_cache": True, "stale": stale}
            except (OSError, sqlite3.Error, ValueError, KeyError, TypeError):
                return None

    def search(self, query, limit=3, *, domain=None, retrieved_from=None, retrieved_to=None, allow_stale=False):
        filters = validate_search(query, limit, domain=domain, retrieved_from=retrieved_from, retrieved_to=retrieved_to)
        if type(allow_stale) is not bool:
            raise ValueError("Choose whether historical saved sources are allowed.")
        result = {"query": filters["query"], "at": None, "sources": [], "from_cache": True, "stale": False,
                  "retrieval": "keyword", "search_limited": False}
        work_exceeded = False
        # This is a reader: never initialize schema, claim the archive, remember
        # the query or repair the index as a side effect of searching.
        with self._write_lock:
            try:
                config = self._state()["config"]
                if (not config["enabled"] or not self.archive or not self.archive.is_dir() or not self.index.is_file()):
                    return result
                now = self.now()
                earliest = now - timedelta(hours=config["reuse_hours"])
                deadline = time.monotonic() + SEARCH_SECONDS
                expression = " AND ".join('"' + term.replace('"', '""') + '"' for term in _search_terms(filters["query"]))
                clauses, parameters = ["julianday(c.at)<=julianday(?)"], [now.isoformat()]
                if not allow_stale:
                    clauses.append("julianday(c.at)>=julianday(?)")
                    parameters.append(earliest.isoformat())
                if filters["domain"]:
                    clauses.append("context_host(c.url)=?")
                    parameters.append(filters["domain"])
                for field, operator in (("retrieved_from", ">="), ("retrieved_to", "<=")):
                    if filters[field]:
                        clauses.append("date(c.at)" + operator + "?")
                        parameters.append(filters[field])
                eligibility = " AND ".join(clauses)

                def check_time():
                    nonlocal work_exceeded
                    if time.monotonic() > deadline:
                        work_exceeded = True
                        raise ValueError("Saved-source search exceeded its time allowance.")

                def canonical(value):
                    check_time()
                    try:
                        return public_url(value, allow_query=True, allow_http=True)
                    except ValueError:
                        return None

                def host(value):
                    normalized = canonical(value)
                    return urlsplit(normalized).hostname if normalized else None

                def observation(value):
                    check_time()
                    try:
                        return datetime.fromisoformat(timestamp(value)).isoformat(timespec="microseconds")
                    except ValueError:
                        return None

                with closing(sqlite3.connect(self.index.as_uri() + "?mode=ro", uri=True, timeout=0.5)) as db:
                    callbacks = 0
                    def progress():
                        nonlocal callbacks, work_exceeded
                        callbacks += 1
                        work_exceeded = callbacks > 10000 or time.monotonic() > deadline
                        return work_exceeded
                    db.set_progress_handler(progress, 1000)
                    db.create_function("context_url", 1, canonical)
                    db.create_function("context_host", 1, host)
                    db.create_function("context_time", 1, observation)
                    candidates = db.execute(
                        "WITH matches AS MATERIALIZED (SELECT context_url(c.url) AS url,"
                        "bm25(captures_fts,0.0,5.0,1.0) AS score,context_time(c.at) AS observed "
                        "FROM captures_fts JOIN captures c ON c.id=captures_fts.id "
                        "WHERE captures_fts MATCH ? AND " + eligibility + ") "
                        "SELECT url FROM matches WHERE url IS NOT NULL GROUP BY url "
                        "ORDER BY MIN(score),MAX(observed) DESC,url LIMIT ?",
                        [expression, *parameters, SEARCH_CANDIDATES + 1]).fetchall()
                    result["search_limited"] = len(candidates) > SEARCH_CANDIDATES
                    urls = [row[0] for row in candidates[:SEARCH_CANDIDATES]]
                    if not urls:
                        return result
                    # Select versions independently of keywords. A recent
                    # conflicting version cannot be replaced by an older hit.
                    versions = db.execute(
                        "WITH eligible AS MATERIALIZED (SELECT c.id,"
                        "context_url(c.url) AS source_url,context_time(c.at) AS observed FROM captures c WHERE " + eligibility +
                        " AND context_url(c.url) IN (" + ",".join("?" for _ in urls) + ")),"
                        "versions AS (SELECT *,ROW_NUMBER() OVER (PARTITION BY source_url ORDER BY observed DESC,id) AS version "
                        "FROM eligible),chosen AS MATERIALIZED (SELECT id,source_url,version FROM versions "
                        "ORDER BY version,source_url LIMIT ?) "
                        "SELECT c.id,c.title,c.url,c.content,c.at,c.manifest_hash FROM chosen JOIN captures c ON c.id=chosen.id "
                        "ORDER BY chosen.version,chosen.source_url",
                        [*parameters, *urls, SEARCH_CANDIDATES + 1]).fetchall()
                    found, seen = [], set()
                    for capture_id, title, url, content, at, digest in versions[:SEARCH_CANDIDATES]:
                        check_time()
                        canonical_url = public_url(url, allow_query=True, allow_http=True)
                        if canonical_url in seen:
                            continue
                        try:
                            source = self.get_capture(capture_id, expected_manifest_hash=digest)
                        except KeyError:
                            continue
                        if (title, url, content, at) != (source["title"], source["url"], source["content"], source["retrieved_at"]):
                            continue  # The disposable index disagrees with verified evidence.
                        observed = datetime.fromisoformat(timestamp(source["retrieved_at"]))
                        stale = observed < earliest
                        if (observed > now or stale and not allow_stale
                                or filters["domain"] and urlsplit(canonical_url).hostname != filters["domain"]
                                or filters["retrieved_from"] and observed.date().isoformat() < filters["retrieved_from"]
                                or filters["retrieved_to"] and observed.date().isoformat() > filters["retrieved_to"]):
                            continue
                        seen.add(canonical_url)
                        match = db.execute(
                            "SELECT bm25(captures_fts,0.0,5.0,1.0),title,content FROM captures_fts "
                            "WHERE captures_fts MATCH ? AND id=? ORDER BY 1 LIMIT 1", (expression, capture_id)).fetchone()
                        if match and (match[1], match[2]) == (source["title"], source["content"]):
                            found.append((match[0], -observed.timestamp(), capture_id, {**source, "stale": stale}))
                    result["sources"] = [entry[3] for entry in sorted(found, key=lambda item: item[:3])[:filters["limit"]]]
                    result["at"] = max((source["retrieved_at"] for source in result["sources"]),
                                       key=datetime.fromisoformat, default=None)
                    result["stale"] = any(source["stale"] for source in result["sources"])
                    result["search_limited"] |= len(versions) > SEARCH_CANDIDATES and len(seen) < len(urls)
                    return result
            except (OSError, sqlite3.Error, ValueError, KeyError, TypeError, UnicodeError):
                if work_exceeded:
                    raise ValueError("Saved-source search exceeded its work allowance. Narrow keywords or filters and retry.") from None
                raise ValueError("The local source index could not be searched. Rebuild it and retry.") from None

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

    @contextmanager
    def evidence_guard(self, sources):
        """Validate and commit evidence under mutation exclusion, never over inference."""
        with self._write_lock:
            try:
                for source in sources:
                    if source.get("archive_status") == "saved":
                        self.get_capture(source["capture_id"], expected_hash=source["content_hash"], expected_manifest_hash=source["manifest_hash"])
            except (KeyError, ValueError, TypeError):
                raise ValueError("Saved source evidence changed or was removed. Refresh the source choice before answering.") from None
            yield

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

    @storage_errors
    def delete_capture(self, capture_id):
        if not isinstance(capture_id, str) or not ID.fullmatch(capture_id):
            raise KeyError(capture_id)
        with self._writer():
            self._invalidate_rebuild()
            # Tombstones are portable; retained objects preserve other captures.
            relative = "records/deletions/" + capture_id + ".json"
            if not self._deleted(capture_id):
                self.get_capture(capture_id)
                publish_archive(self.archive, relative, json.dumps({"schema_version": SCHEMA, "capture_id": capture_id}).encode())
            total, items, _ = archive_inventory(self.archive, MAX_ARCHIVE_ITEMS * 2)
            with self._transaction() as (state, db):
                state.update(archive_bytes=total, archive_items=items)
                for key, raw in db.execute("SELECT key,captures FROM context_queries").fetchall():
                    if any(item.get("capture_id") == capture_id for item in json.loads(raw)):
                        db.execute("DELETE FROM context_queries WHERE key=?", (key,))
            if self.index.is_file():
                with closing(self._index_connection()) as db, db:
                    db.execute("DELETE FROM captures_fts WHERE id=?", (capture_id,))
                    db.execute("DELETE FROM captures WHERE id=?", (capture_id,))

    @property
    def _rebuild_path(self):
        return self.index.with_name("rebuild.sqlite3")

    def _invalidate_rebuild(self):
        """A replacement cannot discard changes made after its snapshot."""
        for suffix in ("-journal", "-wal", "-shm", ""):
            self._rebuild_path.with_name(self._rebuild_path.name + suffix).unlink(missing_ok=True)
        if self._state()["rebuild_progress"] is not None:
            with self._transaction() as (state, _):
                state["rebuild_progress"] = None

    @staticmethod
    def _capture_inventory_hash(paths):
        return hashlib.sha256("\n".join(sorted(paths)).encode("utf-8")).hexdigest()

    @storage_errors
    def rebuild(self, limit=1000, continuation=None):
        """Process one durable batch; publish only a complete verified index."""
        if type(limit) is not int or not 1 <= limit <= 5000:
            raise ValueError("Choose a rebuild batch between 1 and 5000 captures.")
        if continuation is not None and (not isinstance(continuation, str) or not ID.fullmatch(continuation)):
            raise ValueError("Use the current source index rebuild reference.")
        config = self._state()["config"]
        if not config["enabled"]:
            raise ValueError("Enable the configured source archive before rebuilding.")
        with self._writer():
            config = self._state()["config"]
            if not config["enabled"]:
                raise ValueError("Enable the configured source archive before rebuilding.")
            self._format()
            stage = self._rebuild_path
            job = None
            if stage.is_file():
                try:
                    with closing(sqlite3.connect(stage.as_uri() + "?mode=ro", uri=True)) as db:
                        row = db.execute("SELECT value FROM rebuild_job WHERE id=1").fetchone()
                        job = json.loads(row[0]) if row else None
                    if (not isinstance(job, dict) or set(job) != {"id", "config", "processed", "total", "missing", "corrupt", "inventory_hash"}
                            or job["config"] != config or not isinstance(job["id"], str) or not ID.fullmatch(job["id"])
                            or not isinstance(job["inventory_hash"], str) or not HASH.fullmatch(job["inventory_hash"])
                            or any(type(job[field]) is not int or job[field] < 0 for field in ("processed", "total", "missing", "corrupt"))
                            or not job["processed"] <= job["total"] <= MAX_ARCHIVE_ITEMS * 2
                            or job["missing"] + job["corrupt"] > job["processed"]):
                        raise ValueError("Invalid source index rebuild state.")
                except (sqlite3.Error, ValueError, KeyError, TypeError):
                    self._invalidate_rebuild()
                    job = None
            if continuation is not None and (job is None or job["id"] != continuation):
                raise ValueError("The archive changed during index rebuild. Start or resume a new rebuild.")
            if job is None:
                total, items, paths = archive_inventory(self.archive, MAX_ARCHIVE_ITEMS * 2)
                job = {"id": uuid.uuid4().hex, "config": config, "processed": 0, "total": len(paths),
                       "missing": 0, "corrupt": 0, "inventory_hash": self._capture_inventory_hash(paths)}
                with closing(self._index_connection(stage)) as db, db:
                    db.execute("CREATE TABLE rebuild_queue(position INTEGER PRIMARY KEY,manifest TEXT NOT NULL)")
                    db.executemany("INSERT INTO rebuild_queue VALUES(?,?)", enumerate(sorted(paths)))
                    db.execute("CREATE TABLE rebuild_job(id INTEGER PRIMARY KEY,value TEXT NOT NULL)")
                    db.execute("INSERT INTO rebuild_job VALUES(1,?)", (json.dumps(job),))
            started = time.monotonic()
            processed_before = job["processed"]
            with closing(self._index_connection(stage)) as db, db:
                rows = db.execute("SELECT position,manifest FROM rebuild_queue WHERE position>=? ORDER BY position LIMIT ?",
                                  (job["processed"], limit)).fetchall()
                for position, relative in rows:
                    if job["processed"] > processed_before and time.monotonic() - started >= REBUILD_SECONDS:
                        break
                    if not self._deleted(Path(relative).stem):
                        try:
                            manifest, content = self._manifest(relative, config)
                        except FileNotFoundError:
                            job["missing"] += 1
                        except (OSError, ValueError, TypeError, UnicodeError):
                            job["corrupt"] += 1
                        else:
                            db.execute("INSERT INTO captures VALUES(?,?,?,?,?,?,?)", (manifest["capture_id"], relative, manifest["title"], manifest["source_url"], content, manifest["retrieved_at"], manifest_hash(manifest)))
                            db.execute("INSERT INTO captures_fts VALUES(?,?,?)", (manifest["capture_id"], manifest["title"], content))
                    job["processed"] = position + 1
                db.execute("UPDATE rebuild_job SET value=? WHERE id=1", (json.dumps(job),))
            complete = job["processed"] == job["total"]
            progress = {field: job[field] for field in ("id", "processed", "total")}
            progress["complete"] = complete
            if complete:
                total, items, paths = archive_inventory(self.archive, MAX_ARCHIVE_ITEMS * 2)
                if self._capture_inventory_hash(paths) != job["inventory_hash"]:
                    self._invalidate_rebuild()
                    raise ValueError("The archive changed during index rebuild. Start or resume a new rebuild.")
                with closing(sqlite3.connect(stage)) as db, db:
                    db.execute("DROP TABLE rebuild_queue")
                    db.execute("DROP TABLE rebuild_job")
                os.replace(stage, self.index)
            with self._transaction() as (state, _):
                state["rebuild_progress"] = progress
                if complete:
                    state.update(archive_bytes=total, archive_items=items, missing_count=job["missing"], corrupt_count=job["corrupt"],
                                 last_error="Some archived sources are missing or invalid." if job["missing"] or job["corrupt"] else None,
                                 write_unavailable=False, last_indexed_at=self.now().isoformat())
            return self.status()

"""Local workspace with configurable live chat and persistent to-dos."""

from contextlib import closing, contextmanager, asynccontextmanager, ExitStack
import asyncio
from datetime import datetime
import hashlib
import hmac
import json
import os
from pathlib import Path
from typing import Literal
import secrets
import sqlite3
import threading
import time
import uuid
from zoneinfo import ZoneInfo

from fastapi import BackgroundTasks, FastAPI, HTTPException, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware
from backend.provider import Provider, DEFAULT as PROVIDER_DEFAULT
from backend import credentials
from backend.capabilities import CAPABILITIES
from backend.web_search import WebSearch, ENDPOINT as SEARCH_ENDPOINT
from backend.context_store import ContextStore, validate_candidate_lookup
from backend.context_policy import configuration as archive_configuration, contains_source_secret, public_url
from backend.storage import workspace_owner
from backend.memory import MAX_CONTENT, MemoryStore
from backend.work_config import WorkConfig, config_value
from backend.reflection import ReflectionStore, ReflectionWorker
from backend.research import ResearchStore
from backend.tasks import TaskInput, TaskUpdate, create_task as task_record, dismiss_task, present_task, prune_ai_tasks

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("MAESTRO_DATA_DIR") or Path(os.environ.get("LOCALAPPDATA", Path.home() / ".local" / "share")) / "Maestro" / "preview").expanduser().resolve()
if DATA_DIR == ROOT or ROOT in DATA_DIR.parents:
    raise RuntimeError("Choose a private data directory outside the Git checkout.")
DATA_DIR.mkdir(parents=True, exist_ok=True)
if os.name != "nt":
    DATA_DIR.chmod(0o700)
DATABASE = DATA_DIR / "workspace.sqlite3"
TIMEZONE = ZoneInfo(os.environ.get("MAESTRO_TIMEZONE", "Europe/Stockholm"))
SESSION = secrets.token_urlsafe(32)
CSRF = secrets.token_urlsafe(32)
PORT = int(os.environ.get("MAESTRO_PORT", "8765"))
ORIGINS = {f"http://{host}:{port}" for host in ("127.0.0.1", "localhost") for port in (PORT, 5173, 4173)}
SOURCE_CREDENTIAL_STATE_BYTES = 16 * 1024 ** 2


def now() -> str:
    return datetime.now(TIMEZONE).isoformat()


def identifier() -> str:
    return uuid.uuid4().hex


def seed_workspace() -> dict:
    return {
        "tasks": [], "chats": [],
        "limits": {"run_usd": 1.0, "daily_usd": 5.0, "monthly_usd": 50.0, "max_tokens": 500000},
        "working_core_migrated": True,
    }


def migrate_workspace(state: dict) -> None:
    """Retire preview records without erasing private content or real accounting."""
    if state.get("working_core_migrated"):
        migrate_chats(state)
        return
    archive = state.setdefault("legacy_preview", {})
    for key in ("runs", "memories", "ledger"):
        if key in state:
            archive[key] = state.pop(key)
    sample_ids = {"sample-documents", "sample-week", "sample-workflow"}
    archive["tasks"] = [task for task in state["tasks"] if task["id"] in sample_ids]
    state["tasks"] = [task for task in state["tasks"] if task["id"] not in sample_ids]
    archive["task_assignments"] = {task["id"]: task.pop("agent") for task in state["tasks"] if "agent" in task}
    # Before live chat was introduced, every saved message was simulated.
    if not state.pop("live_chat_migrated", False):
        for message in state["messages"]:
            message.setdefault("demo", True)
    archive["messages"] = [message for message in state["messages"] if message.get("demo")]
    state["messages"] = [message for message in state["messages"] if not message.get("demo")]
    archive["limits"] = {key: state["limits"].pop(key) for key in ("max_refinements", "max_minutes") if key in state["limits"]}
    state["working_core_migrated"] = True
    migrate_chats(state)


def create_chat_record(title: str = "New chat", messages=None) -> dict:
    stamp = now()
    return {"id": identifier(), "title": title, "created_at": stamp, "updated_at": stamp,
            "messages": messages or [], "title_source": "fallback", "title_attempted": bool(messages)}


def migrate_chats(state: dict) -> None:
    messages = state.pop("messages", [])
    state.setdefault("chats", [])
    if messages:
        state["chats"].append(create_chat_record("Previous conversation", messages))


def selected_chat(state: dict, chat_id: str | None = None) -> dict | None:
    if chat_id is not None:
        chat = next((item for item in state["chats"] if item["id"] == chat_id), None)
        if chat is None:
            raise HTTPException(404, "That chat could not be found.")
        return chat
    return max(state["chats"], key=lambda item: item["updated_at"], default=None)


@contextmanager
def workspace_transaction(with_db=False):
    with closing(sqlite3.connect(DATABASE, timeout=10)) as connection, connection:
        connection.execute("PRAGMA secure_delete=ON")
        connection.execute("CREATE TABLE IF NOT EXISTS workspace (id INTEGER PRIMARY KEY CHECK (id=1), value TEXT NOT NULL)")
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute("SELECT value FROM workspace WHERE id=1").fetchone()
        state = json.loads(row[0]) if row else seed_workspace()
        migrate_workspace(state)
        yield (state, connection) if with_db else state
        connection.execute("INSERT INTO workspace VALUES (1, ?) ON CONFLICT(id) DO UPDATE SET value=excluded.value", (json.dumps(state),))


def read_workspace() -> dict:
    """Read without claiming a write lock; initialize or migrate only when needed."""
    if DATABASE.is_file():
        with closing(sqlite3.connect(DATABASE.resolve().as_uri() + "?mode=ro", uri=True, timeout=10)) as connection:
            try:
                row = connection.execute("SELECT value FROM workspace WHERE id=1").fetchone()
            except sqlite3.OperationalError as error:
                if str(error) != "no such table: workspace":
                    raise
                row = None
        if row:
            state = json.loads(row[0])
            if state.get("working_core_migrated") and "chats" in state and "messages" not in state:
                return state
    # Re-read under the lock so concurrent first loads cannot seed or migrate twice.
    with workspace_transaction() as state:
        return state


def snapshot(state: dict, chat_id: str | None = None) -> dict:
    chat = selected_chat(state, chat_id)
    service = provider()
    provider_state = service.read_state()
    return {
        "tasks": [present_task(task) for task in state["tasks"]], "limits": state["limits"],
        "chats": [{key: item[key] for key in ("id", "title", "created_at", "updated_at")}
                  for item in sorted(state["chats"], key=lambda item: item["updated_at"], reverse=True)],
        "active_chat_id": chat["id"] if chat else None,
        "messages": chat["messages"] if chat else [],
        "usage": service.usage(provider_state),
        "provider": service.status(provider_state),
        "web_search": WebSearch(DATABASE, TIMEZONE).status(),
        "context_archive": safe_archive_status(context_store().status()),
        "memories": MemoryStore(DATABASE, TIMEZONE).list(),
        "work": work_status(state),
        "capabilities": {"mode": "local", **CAPABILITIES, "secure_credentials": os.name == "nt"},
    }


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class MemoryContent(StrictModel):
    content: str = Field(min_length=1, max_length=MAX_CONTENT)


class MemoryInput(MemoryContent):
    scope: Literal["workspace", "conversation"] = "workspace"
    chat_id: str | None = Field(default=None, min_length=1, max_length=100)
    source_message_id: str | None = Field(default=None, min_length=1, max_length=100)


class CandidateInput(StrictModel):
    scope: Literal["workspace", "conversation"] = "conversation"


class TextInput(StrictModel):
    text: str = Field(min_length=1, max_length=32000)
    chat_id: str | None = Field(default=None, min_length=1, max_length=100)
    model: str = Field(default="", max_length=200, pattern=r"^[A-Za-z0-9_./:-]*$")
    role: Literal["chat", "orchestrator"] = "chat"


class ChatTitleInput(StrictModel):
    title: str = Field(min_length=1, max_length=120)


class FeedbackInput(StrictModel):
    rating: Literal["positive", "negative"] | None
    comment: str = Field(default="", max_length=4000)


class LimitsInput(StrictModel):
    run_usd: float = Field(ge=0, le=10000, allow_inf_nan=False)
    daily_usd: float = Field(ge=0, le=100000, allow_inf_nan=False)
    monthly_usd: float = Field(ge=0, le=1000000, allow_inf_nan=False)
    max_tokens: int = Field(ge=0, le=10000000)


class ChargeInput(StrictModel):
    billed_usd: float = Field(ge=0, le=100000, allow_inf_nan=False)


class LocalRecoveryInput(StrictModel):
    restart_confirmed: bool = Field(strict=True)
    expected_base_url: str = Field(min_length=1, max_length=2048)


class ProviderConfig(StrictModel):
    base_url: str = Field(default="https://api.openai.com/v1", min_length=1, max_length=2048)
    protocol: Literal["responses", "chat_completions", "ollama"] = "responses"
    model: str = Field(default="", max_length=200, pattern=r"^[A-Za-z0-9_./:-]*$")
    orchestrator_model: str = Field(default="", max_length=200, pattern=r"^[A-Za-z0-9_./:-]*$")
    chat_routing: Literal["direct", "orchestrator"] = "direct"
    input_usd_per_million: float = Field(default=0, ge=0, le=10000, allow_inf_nan=False)
    output_usd_per_million: float = Field(default=0, ge=0, le=10000, allow_inf_nan=False)
    pricing_verified: bool = False
    max_output_tokens: int = Field(default=8192, ge=64, le=32768)
    ollama_context_tokens: int = Field(default=32768, ge=1024, le=131072)
    ollama_threads: int = Field(default=0, ge=0, le=256)
    ollama_keep_alive_minutes: int = Field(default=5, ge=0, le=120)


class ProviderSetup(StrictModel):
    config: ProviderConfig
    api_key: str = Field(default="", max_length=4096, repr=False)
    persist: bool = True


class WebSearchConfig(StrictModel):
    enabled: bool = False
    daily_limit: int = Field(default=20, ge=0, le=200)
    max_results: int = Field(default=3, ge=1, le=3)


class WebSearchSetup(StrictModel):
    config: WebSearchConfig
    api_key: str = Field(default="", max_length=4096, repr=False)
    persist: bool = True


class ContextArchiveConfig(StrictModel):
    enabled: bool = Field(default=False, strict=True)
    capture_policy: Literal["approved_sources", "all_public"] = "approved_sources"
    public_sources: list[str] = Field(default_factory=list, max_length=100)
    reuse_hours: int = Field(default=24, ge=1, le=168, strict=True)
    max_bytes: int = Field(default=10737418240, ge=1048576, le=107374182400, strict=True)
    max_items: int = Field(default=200000, ge=100, le=200000, strict=True)


class ContextArchiveEnabled(StrictModel):
    enabled: bool = Field(strict=True)


class ContextCandidateSearch(StrictModel):
    query: str = Field(default="", max_length=512, strict=True)
    limit: int = Field(default=10, ge=1, le=20, strict=True)
    domain: str | None = Field(default=None, min_length=1, max_length=253, strict=True)
    retrieved_from: str | None = Field(default=None, pattern=r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$", strict=True)
    retrieved_to: str | None = Field(default=None, pattern=r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$", strict=True)


class ResearchInput(StrictModel):
    submission_id: str = Field(pattern=r"^[a-f0-9]{32}$", strict=True)
    request: str = Field(min_length=1, max_length=32000, strict=True)
    query: str = Field(default="", max_length=512, strict=True)
    chat_id: str | None = Field(default=None, min_length=1, max_length=100, strict=True)


_context_lock = threading.RLock()
_archive_config_lock = threading.RLock()


def context_store():
    # The same instance owns the archive across lifespan, provider and API threads.
    identity = (str(DATABASE.resolve()), os.environ.get("MAESTRO_CONTEXT_ARCHIVE_DIR", ""))
    with _context_lock:
        current = getattr(app.state, "context_store", None)
        if current is None or getattr(app.state, "context_store_identity", None) != identity:
            try:
                current = ContextStore(DATABASE)
            except (ValueError, OSError, RuntimeError):
                # A bad optional archive path must not prevent local chat from
                # starting or overwrite the private archive configuration.
                current = ContextStore(DATABASE, archive_dir="")
                current.path_error = "The source archive path is invalid. Check its startup setting and restart Maestro."
            if current.archive is not None:
                # Keep health bound to the selected target if a directory alias
                # later changes without changing the startup environment.
                archive_identity = "path:" + os.path.normcase(str(current.archive))
            else:
                archive_root = identity[1]
                try:
                    archive_identity = "none"
                    if archive_root:
                        archive_path = Path(archive_root).expanduser()
                        archive_identity = ("path:" + os.path.normcase(str(archive_path.resolve()))
                                            if archive_path.is_absolute() else "invalid:" + archive_root)
                except (OSError, ValueError, RuntimeError):
                    archive_identity = "invalid:" + archive_root
            current.startup_archive_id = hashlib.sha256(archive_identity.encode()).hexdigest()
            app.state.context_store, app.state.context_store_identity = current, identity
        return current


def provider():
    return Provider(DATABASE, TIMEZONE)


def research_store():
    return ResearchStore(DATABASE, TIMEZONE)


@contextmanager
def conflict_errors():
    try:
        yield
    except ValueError as error:
        raise HTTPException(409, str(error)) from None


@asynccontextmanager
async def lifespan(application):
    with workspace_owner(DATABASE.parent), ExitStack() as archive_ownership:
        read_workspace()
        store = context_store()
        application.state.archive_ownership = archive_ownership
        application.state.archive_owner = False
        try:
            if not store.path_error and store.status()["config"]["enabled"]:
                try:
                    archive_ownership.enter_context(store.archive_owner())
                    store.initialize()
                    application.state.archive_owner = True
                except (ValueError, OSError):
                    archive_ownership.close()
                    store.record_error()
            provider().recover()
            WebSearch(DATABASE, TIMEZONE).recover()
            research_store().initialize()
            research_store().recover()
            MemoryStore(DATABASE, TIMEZONE).initialize()
            reflection = ReflectionStore(DATABASE, TIMEZONE)
            reflection.initialize()
            reflection.recover()
            stop = asyncio.Event()
            worker = asyncio.create_task(ReflectionWorker(
                reflection, provider(), safe_private_content, maintenance=research_store().expire).run(stop))
            try:
                yield
            finally:
                stop.set()
                await worker
        finally:
            application.state.archive_ownership = None
            application.state.archive_owner = False


app = FastAPI(title="Maestro", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "testserver"])


@app.exception_handler(RequestValidationError)
async def invalid_entry(request: Request, error: RequestValidationError):
    # Do not echo submitted content into errors, including accidental credentials.
    return JSONResponse({"detail": "Check the entry's fields and try again."}, status_code=422)


@app.get("/health")
def health():
    return {"application": "maestro", "mode": "local", "launch_id": os.environ.get("MAESTRO_LAUNCH_ID"),
            "workspace_id": hashlib.sha256(os.path.normcase(str(DATA_DIR)).encode()).hexdigest(),
            "archive_id": context_store().startup_archive_id}


@app.middleware("http")
async def protect_local_workspace(request: Request, call_next):
    if request.url.path.startswith("/api/"):
        origin = request.headers.get("origin")
        if origin and origin not in ORIGINS:
            return JSONResponse({"detail": "Only your local Maestro interface can access this workspace."}, status_code=403)
        if request.url.path != "/api/session" and not hmac.compare_digest(request.cookies.get("maestro_session", "").encode(), SESSION.encode()):
            return JSONResponse({"detail": "Open Maestro again to start a local session."}, status_code=401)
        if request.method not in ("GET", "HEAD"):
            if not hmac.compare_digest(request.headers.get("x-maestro-csrf", "").encode(), CSRF.encode()):
                return JSONResponse({"detail": "Refresh Maestro before saving changes."}, status_code=403)
            try:
                size = int(request.headers.get("content-length", "0"))
            except ValueError:
                return JSONResponse({"detail": "Invalid request."}, status_code=400)
            if size > 262144 or len(await request.body()) > 262144:
                return JSONResponse({"detail": "This entry is too large."}, status_code=413)
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Frame-Options"] = "DENY"
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    return response


@app.get("/api/session")
def session(response: Response):
    response.set_cookie("maestro_session", SESSION, httponly=True, samesite="strict")
    return {"csrf": CSRF}


@app.get("/api/workspace")
def get_workspace(chat_id: str | None = None):
    return snapshot(read_workspace(), chat_id)


@app.post("/api/chats")
def create_chat():
    with workspace_transaction() as state:
        chat = create_chat_record()
        state["chats"].append(chat)
    return snapshot(state, chat["id"])


@app.patch("/api/chats/{chat_id}")
def rename_chat(chat_id: str, entry: ChatTitleInput):
    with workspace_transaction() as state:
        chat = selected_chat(state, chat_id)
        chat.update(title=entry.title, title_source="manual", updated_at=now())
    return snapshot(state, chat_id)


@app.delete("/api/chats/{chat_id}")
def delete_chat(chat_id: str):
    # The workspace and provider reservation share this immediate SQLite lock.
    with workspace_transaction(with_db=True) as (state, db):
        selected_chat(state, chat_id)
        if any(entry["status"] == "reserved" and entry.get("thread_id") == chat_id
               for entry in provider().read_state()["ledger"]):
            raise HTTPException(409, "Wait for this chat's current request to finish before deleting it.")
        state["chats"] = [chat for chat in state["chats"] if chat["id"] != chat_id]
        MemoryStore(DATABASE, TIMEZONE).delete_chat(chat_id, db=db)
        ReflectionStore(DATABASE, TIMEZONE).delete_chat(chat_id, db)
        research_store().delete_chat(chat_id, db)
        prune_ai_tasks(state, db)
    return snapshot(state)


def source_secrets(*, bounded=True):
    # A source read must not initialize an absent private database or schema.
    config = PROVIDER_DEFAULT
    if DATABASE.is_file():
        with closing(sqlite3.connect(DATABASE.resolve().as_uri() + "?mode=ro", uri=True,
                                    timeout=1 if bounded else 5)) as db:
            if bounded:
                deadline = time.monotonic() + 1
                db.set_progress_handler(lambda: int(time.monotonic() >= deadline), 1000)
            if db.execute("SELECT 1 FROM sqlite_master WHERE name='provider_state'").fetchone():
                record_guard = (f"typeof(value)='text' AND length(CAST(value AS BLOB))<={SOURCE_CREDENTIAL_STATE_BYTES}"
                                if bounded else "typeof(value)='text'")
                # SQLite uses first-key/prefix matching and truncates text at
                # NULs, unlike the provider's Python reader. Validate complete
                # scope fields/keys before using their bounded projections.
                scope_guard = ("(SELECT COUNT(*) FROM json_each(provider_state.value) WHERE key='config')<=1 "
                               "AND NOT EXISTS (SELECT 1 FROM json_each(provider_state.value) "
                               "WHERE instr(CAST(key AS BLOB),x'00')>0) "
                               "AND NOT EXISTS (SELECT 1 FROM json_each(provider_state.value,'$.config') "
                               "WHERE instr(CAST(key AS BLOB),x'00')>0) "
                               "AND NOT EXISTS (SELECT 1 FROM json_each(provider_state.value,'$.config') "
                               "WHERE key IN ('protocol','base_url') GROUP BY key HAVING COUNT(*)>1) "
                               "AND (json_type(value,'$.config.protocol') IS NULL OR json_type(value,'$.config.protocol')='text') "
                               "AND (json_type(value,'$.config.base_url') IS NULL OR json_type(value,'$.config.base_url')='text') "
                               "AND coalesce(instr(CAST(json_extract(value,'$.config.protocol') AS BLOB),x'00'),0)=0 "
                               "AND coalesce(instr(CAST(json_extract(value,'$.config.base_url') AS BLOB),x'00'),0)=0")
                row = db.execute(f"SELECT CASE WHEN {record_guard} THEN substr(json_extract(value,'$.config.protocol'),1,33) END, "
                                 f"CASE WHEN {record_guard} THEN substr(json_extract(value,'$.config.base_url'),1,2049) END, "
                                 f"{record_guard}, CASE WHEN {record_guard} THEN ({scope_guard}) ELSE 0 END "
                                 "FROM provider_state WHERE id=1").fetchone()
                if row:
                    protocol, base_url, within_bound, unambiguous = row
                    if (not within_bound or not unambiguous
                            or (protocol is not None and (not isinstance(protocol, str) or len(protocol) > 32))
                            or (base_url is not None and (not isinstance(base_url, str) or len(base_url) > 2048))):
                        raise ValueError("The provider credential scope is unavailable.")
                    config = {**PROVIDER_DEFAULT,
                              **({"protocol": protocol} if protocol is not None else {}),
                              **({"base_url": base_url} if base_url is not None else {})}
    endpoints = (() if config["protocol"] == "ollama" else (config["base_url"],)) + (SEARCH_ENDPOINT,)
    found = []
    for endpoint in endpoints:
        try:
            key = credentials.read(endpoint)[0]
        except ValueError:
            continue
        if key:
            found.append(key)
    return tuple(found)


def safe_private_content(content, *additional_content, secrets=None):
    # Keep configured credentials out of saved content and source responses.
    current_secrets = source_secrets(bounded=False) if secrets is None else secrets
    if any(contains_source_secret(value, current_secrets) for value in (content, *additional_content)):
        raise ValueError("Remove credentials before saving.")
    return content


def safe_archived_source(source, *, secrets=None):
    """Screen current credentials without changing archived fields or pins."""
    fields = (source["title"], source["url"],
              public_url(source["url"], allow_query=True, allow_http=True), source.get("content", ""))
    if secrets is None:
        safe_private_content("\n".join(fields))
    elif any(contains_source_secret(value, secrets) for value in fields):
        raise ValueError("Remove credentials before saving.")
    return source


def safe_archive_status(status, *, secrets=None):
    """Withhold unsafe settings without changing the stored source permissions."""
    result = {**status}
    for field in ("path", "database_path", "index_path"):
        if result.get(field) is None:
            continue
        try:
            safe_private_content(result[field], secrets=secrets)
        except ValueError:
            result[field] = None
            result["path_error"] = "Some storage paths are hidden because they match a current credential."
    try:
        safe_private_content("", *status["config"]["public_sources"], secrets=secrets)
    except ValueError:
        return {**result, "config": None,
                "config_error": "Archive settings are hidden because a saved source scope matches a current credential."}
    return {**result, "config_error": None}


@app.patch("/api/chats/{chat_id}/messages/{message_id}/feedback")
def save_feedback(chat_id: str, message_id: str, entry: FeedbackInput):
    with conflict_errors():
        if "\x00" in entry.comment or (entry.rating is None and entry.comment):
            raise ValueError("Choose a rating for your comment, or clear both fields.")
        safe_private_content(entry.comment)
        with workspace_transaction(with_db=True) as (state, db):
            chat = selected_chat(state, chat_id)
            message = next((item for item in chat["messages"] if item["id"] == message_id
                            and item.get("role") == "assistant" and not item.get("demo")), None)
            if message is None:
                raise HTTPException(404, "That answer could not be found.")
            previous = message.get("feedback")
            changed = ((previous or {}).get("rating"), (previous or {}).get("comment", "")) != (entry.rating, entry.comment)
            if changed:
                if entry.rating is None:
                    message.pop("feedback", None)
                else:
                    message["feedback"] = {"rating": entry.rating, "comment": entry.comment, "updated_at": now()}
                memories = MemoryStore(DATABASE, TIMEZONE)
                memories.initialize_db(db)
                memories.remove_stale(db, state["chats"])
                prune_ai_tasks(state, db)
                reflection = ReflectionStore(DATABASE, TIMEZONE)
                reflection.invalidate(db, clear_candidates=False)
                reflection.touch(state, db)
                if previous is not None:
                    db.execute("DELETE FROM reflection_journal")
    return {"message_id": message_id, "feedback": message.get("feedback"),
            "work": {**work_status(state), "tasks": [present_task(task) for task in state["tasks"]]},
            "memories": MemoryStore(DATABASE, TIMEZONE).list()}


@app.get("/api/memory")
def get_memories():
    return MemoryStore(DATABASE, TIMEZONE).list()


@app.post("/api/memory")
def remember(entry: MemoryInput):
    with conflict_errors():
        return MemoryStore(DATABASE, TIMEZONE).remember(safe_private_content(entry.content), entry.scope, entry.chat_id, entry.source_message_id)


@app.patch("/api/memory/{memory_id}")
def correct_memory(memory_id: str, entry: MemoryContent):
    with conflict_errors():
        return MemoryStore(DATABASE, TIMEZONE).update(memory_id, safe_private_content(entry.content))


@app.delete("/api/memory/{memory_id}")
def forget_memory(memory_id: str):
    with conflict_errors():
        MemoryStore(DATABASE, TIMEZONE).forget(memory_id)
    return {"deleted": True}


def work_status(state):
    return {"config": config_value(state), "worker_available": True,
            **ReflectionStore(DATABASE, TIMEZONE).status()}


@app.get("/api/work-config")
def get_work_config():
    return work_status(read_workspace())


@app.put("/api/work-config")
def put_work_config(entry: WorkConfig):
    with conflict_errors():
        connection = provider().read_state()["config"]
        if entry.enabled and (connection["protocol"] != "ollama" or not connection["model"]):
            raise ValueError("Select a local Ollama chat model before enabling reflection.")
        for model in (entry.reflection_model, entry.memory_model, entry.coding_model):
            safe_private_content(model)
        with workspace_transaction(with_db=True) as (state, db):
            previous = config_value(state)
            if previous != entry.model_dump():
                ReflectionStore(DATABASE, TIMEZONE).invalidate(db, clear_candidates=False)
                if entry.enabled and entry.auto_curate and entry.periodic_reflection and (
                        not all(previous[field] for field in ("enabled", "auto_curate", "periodic_reflection"))
                        or previous["reflection_interval_minutes"] != entry.reflection_interval_minutes):
                    db.execute("UPDATE reflection_schedule SET next_due=0 WHERE id=1")
            state["work_config"] = entry.model_dump()
    return work_status(state)


@app.get("/api/reflection")
def get_reflection():
    state = read_workspace()
    return {**work_status(state), "memories": MemoryStore(DATABASE, TIMEZONE).list(),
            "tasks": [present_task(task) for task in state["tasks"]], "usage": provider().usage()}


@app.post("/api/reflection/candidates/{candidate_id}/accept")
def accept_candidate(candidate_id: str, entry: CandidateInput):
    with conflict_errors():
        return ReflectionStore(DATABASE, TIMEZONE).accept(
            candidate_id, MemoryStore(DATABASE, TIMEZONE), safe_private_content, entry.scope)


@app.delete("/api/reflection/candidates/{candidate_id}")
def reject_candidate(candidate_id: str):
    with conflict_errors():
        ReflectionStore(DATABASE, TIMEZONE).reject(candidate_id)
    return {"deleted": True}


@app.get("/api/provider")
def get_provider():
    return provider().status()


@app.get("/api/web-search")
def get_web_search():
    return WebSearch(DATABASE, TIMEZONE).status()


@app.get("/api/context")
def get_context_archive():
    return safe_archive_status(context_store().status())


@app.post("/api/context/search")
def search_context_candidates(entry: ContextCandidateSearch):
    invalid = "Check the saved-evidence search fields and try again."
    unavailable = {"state": "unavailable", "candidates": [], "skipped_count": 0, "search_limited": False,
                   "message": "Saved evidence is unavailable. Check the archive and local index, then retry."}
    try:
        parameters = validate_candidate_lookup(**entry.model_dump())
    except (ValueError, TypeError, UnicodeError):
        raise HTTPException(422, invalid) from None
    try:
        # Keep settings and eligibility stable through final response screening.
        with _archive_config_lock:
            store = context_store()
            with store._write_lock:
                def configuration_snapshot():
                    return {
                        "config": store._candidate_config(), "path_error": store.path_error,
                        "path": str(store.archive) if store.archive is not None else None,
                        "database_path": str(store.database), "index_path": str(store.index),
                    }

                operation_secrets = source_secrets()
                if any(contains_source_secret(value, operation_secrets)
                       for value in (entry.query, entry.domain or "", parameters["query"], parameters["domain"] or "")):
                    raise HTTPException(422, invalid)
                status = safe_archive_status(configuration_snapshot(), secrets=operation_secrets)
                if status["path_error"] or status["config"] is None:
                    return unavailable
                selected_config = status["config"]
                result = store.lookup_candidates(**parameters, secrets=operation_secrets)
                screened = []
                for candidate in result["candidates"]:
                    try:
                        screened.append(safe_archived_source(candidate, secrets=operation_secrets))
                    except ValueError:
                        result["skipped_count"] += 1
                        result["state"], result["search_limited"] = "limited", True
                status = configuration_snapshot()
                # One final snapshot screens the whole response, including an
                # earlier candidate if credentials changed during later checks.
                response_secrets = (*operation_secrets, *source_secrets())
                status = safe_archive_status(status, secrets=response_secrets)
                if status["path_error"] or status["config"] is None:
                    return unavailable
                if status["config"] != selected_config:
                    return ({"state": "disabled", "candidates": [], "skipped_count": 0, "search_limited": False}
                            if not status["config"]["enabled"] else unavailable)
                if any(contains_source_secret(value, response_secrets)
                       for value in (entry.query, entry.domain or "", parameters["query"], parameters["domain"] or "")):
                    raise HTTPException(422, invalid)
                visible = []
                for candidate in screened:
                    try:
                        visible.append(safe_archived_source(candidate, secrets=response_secrets))
                    except ValueError:
                        result["skipped_count"] += 1
                        result["state"], result["search_limited"] = "limited", True
                return {**result, "candidates": visible}
    except (OSError, sqlite3.Error, ValueError, KeyError, TypeError, UnicodeError, RuntimeError):
        return unavailable


def research_permissions(state, chat_id):
    chat = selected_chat(state, chat_id) if chat_id is not None else None
    return {"source_mode": "saved_only", "hosted_allowed": False, "memory_recall": False,
            "memory_derived": bool(chat and any(message.get("memory_ids") for message in chat["messages"])),
            "chat_id": chat_id}


def research_budget(state):
    # Parent allowances are server owned. This preview spends only local elapsed
    # time; #37 must reserve cumulative model usage before enabling model stages.
    limits = state["limits"]
    total = {"model_calls": 4, "tokens": limits["max_tokens"], "seconds": 120,
             "estimated_model_usd": limits["run_usd"], "hosted_searches": 0}
    search = WebSearch(DATABASE, TIMEZONE).summary()
    daily_limit = min(20, search["config"]["daily_limit"])
    # Measure elapsed seconds on the timeline, including local clock changes.
    deadline = datetime.fromtimestamp(datetime.now(TIMEZONE).timestamp() + total["seconds"], TIMEZONE).isoformat()
    return {"total": total, "consumed": dict.fromkeys(total, 0), "reserved": dict.fromkeys(total, 0),
            "remaining": total.copy(), "deadline": deadline,
            "parent_limits": limits.copy(),
            "daily_search": {"limit": daily_limit, "used": search["searches_today"],
                             "remaining": max(0, daily_limit - search["searches_today"])}}


def research_lookup_summary(state):
    outcome = {
        "ready": "Saved-evidence lookup finished.",
        "limited": "Saved-evidence lookup was incomplete.",
        "disabled": "No saved evidence was inspected because source saving is disabled.",
        "unconfigured": "No saved evidence was inspected because no source archive is configured.",
        "unavailable": "Saved evidence could not be inspected reliably.",
    }[state]
    return outcome + " No reviewed answer was produced."


def safe_research_run(run, *, secrets):
    # JSON escapes quotes/backslashes, so also screen the original user fields
    # that the browser reconstructs from the response.
    safe_private_content(run["request"], run["query"], run["submission_id"], run["chat_id"] or "",
                         json.dumps(run, ensure_ascii=False), secrets=secrets)
    return run


def research_view(run):
    """Export private records with current credential and exact-source fences."""
    try:
        operation_secrets = source_secrets()
        safe_research_run(run, secrets=operation_secrets)
        result, visible, response_secrets = run.get("result"), [], None
        # Never rewrite saved pins. Broken optional storage withholds references
        # while the safe private request and recorded activity remain readable.
        if result and result["candidates"]:
            try:
                with _archive_config_lock:
                    store = context_store()
                    with store._write_lock:
                        selected_config = store._candidate_config()
                        status = safe_archive_status({"config": selected_config, "path_error": store.path_error},
                                                     secrets=operation_secrets)
                        sources = []
                        for reference in result["candidates"]:
                            try:
                                if status["path_error"] or status["config"] is None:
                                    raise KeyError("unavailable")
                                source = store.get_capture(reference["capture_id"], reference["content_hash"], reference["manifest_hash"])
                                safe_archived_source(source, secrets=operation_secrets)
                                sources.append((reference, source))
                            except (KeyError, OSError, sqlite3.Error, ValueError, TypeError, UnicodeError):
                                pass
                        response_secrets = (*operation_secrets, *source_secrets())
                        final_config = store._candidate_config()
                        final_status = safe_archive_status({"config": final_config, "path_error": store.path_error},
                                                           secrets=response_secrets)
                        if (final_config == selected_config and not final_status["path_error"]
                                and final_status["config"] is not None):
                            for reference, source in sources:
                                try:
                                    safe_archived_source(source, secrets=response_secrets)
                                    visible.append(reference)
                                except ValueError:
                                    pass
            except (KeyError, OSError, sqlite3.Error, ValueError, TypeError, UnicodeError, RuntimeError):
                visible = []
        if response_secrets is None:
            response_secrets = (*operation_secrets, *source_secrets())
        if result:
            run = {**run, "result": {**result, "candidates": visible,
                   "summary": research_lookup_summary(result["lookup_state"]),
                   "references_unavailable": len(result["candidates"]) - len(visible)}}
        return safe_research_run(run, secrets=response_secrets)
    except (OSError, sqlite3.Error, ValueError, KeyError, TypeError, UnicodeError, RuntimeError):
        raise HTTPException(404, "That research run is unavailable.") from None


def execute_research_run(run_id):
    """One in-process local lookup; recovery never resubmits this callback."""
    store = research_store()
    claimed = store.claim(run_id)
    if claimed is None:
        return
    run, token = claimed
    try:
        safe_private_content(run["request"], run["query"], secrets=source_secrets())
        packet = search_context_candidates(ContextCandidateSearch(query=run["query"], limit=20))
        references = [{key: candidate[key] for key in
                       ("capture_id", "content_hash", "manifest_hash", "retrieved_at", "stale")}
                      for candidate in packet["candidates"]]
        safe_private_content(run["request"], run["query"], json.dumps(references), secrets=source_secrets())
        permissions = research_permissions(read_workspace(), run["chat_id"])
        permissions["memory_derived"] |= run["permissions"]["memory_derived"]
        store.finish(run_id, token, {
            "lookup_state": packet["state"], "search_limited": packet["search_limited"],
            "skipped_count": packet["skipped_count"], "candidates": references,
            "summary": research_lookup_summary(packet["state"]),
        }, permissions=permissions)
    except Exception:
        # Store only controlled outcomes, never provider prose, source text or
        # exception details. The claim token fences cancellation/deletion.
        store.fail(run_id, token, "lookup_failed")


@app.post("/api/research/runs", status_code=202)
def create_research_run(entry: ResearchInput, background_tasks: BackgroundTasks):
    invalid = "Check the research request and remove credentials before saving."
    try:
        operation_secrets = source_secrets()
        safe_private_content(entry.request, entry.query, entry.submission_id, entry.chat_id or "",
                             secrets=operation_secrets)
    except (OSError, sqlite3.Error, ValueError, TypeError, UnicodeError, RuntimeError):
        raise HTTPException(422, invalid) from None
    with conflict_errors(), workspace_transaction(with_db=True) as (state, db):
        permissions = research_permissions(state, entry.chat_id)
        run, created = research_store().create(entry.submission_id, entry.request, entry.query, entry.chat_id,
                                               permissions, research_budget(state), db)
        try:
            safe_research_run(run, secrets=(*operation_secrets, *source_secrets()))
        except (OSError, sqlite3.Error, ValueError, KeyError, TypeError, UnicodeError, RuntimeError):
            # Rejection rolls back admission, including the duplicate guard and
            # submitted event. No archive locks are taken inside this transaction.
            raise HTTPException(422, invalid) from None
    if created:
        # A fresh queued run has no receipt; its complete response was screened
        # before commit. Existing submissions still revalidate their saved pins.
        visible = run
        background_tasks.add_task(execute_research_run, run["id"])
    else:
        visible = research_view(run)
    return {"run": visible, "created": created}


@app.get("/api/research/runs")
def list_research_runs(limit: int = Query(default=20, ge=1, le=50)):
    visible, skipped = [], 0
    try:
        operation_secrets = source_secrets()
    except (OSError, sqlite3.Error, ValueError, TypeError, UnicodeError, RuntimeError):
        raise HTTPException(503, "Research records are temporarily unavailable.") from None
    for run in research_store().list(limit):
        try:
            # Lists carry request/status/allowance metadata. Only the selected
            # detail read exports a revalidated saved-version receipt.
            visible.append(research_view({**run, "result": None}))
        except HTTPException:
            skipped += 1
    try:
        response_secrets = (*operation_secrets, *source_secrets())
        screened = []
        for run in visible:
            try:
                screened.append(safe_research_run(run, secrets=response_secrets))
            except ValueError:
                skipped += 1
        return {"runs": screened, "skipped_count": skipped}
    except (OSError, sqlite3.Error, ValueError, TypeError, UnicodeError, RuntimeError):
        raise HTTPException(503, "Research records are temporarily unavailable.") from None


@app.get("/api/research/runs/{run_id}")
def get_research_run(run_id: str):
    try:
        return research_view(research_store().get(run_id))
    except (KeyError, ValueError, TypeError, OSError, sqlite3.Error, UnicodeError):
        raise HTTPException(404, "That research run is unavailable.") from None


@app.post("/api/research/runs/{run_id}/cancel")
def cancel_research_run(run_id: str):
    try:
        store = research_store()
        store.cancel(run_id)
        return research_view(store.get(run_id))
    except (KeyError, ValueError, TypeError, OSError, sqlite3.Error, UnicodeError):
        raise HTTPException(404, "That research run is unavailable.") from None


@app.delete("/api/research/runs/{run_id}")
def delete_research_run(run_id: str):
    try:
        research_store().delete(run_id)
        return {"deleted": True}
    except (KeyError, ValueError, TypeError, OSError, sqlite3.Error, UnicodeError):
        raise HTTPException(404, "That research run is unavailable.") from None


@app.get("/api/context/sources")
def list_context_sources(limit: int = Query(default=25, ge=1, le=50),
                         cursor: str | None = Query(default=None, max_length=256)):
    with conflict_errors():
        store = context_store()
    try:
        result = store.list_captures(limit, cursor)
    except ValueError:
        raise HTTPException(422, "Use a valid source page cursor.") from None
    visible = []
    for source in result["sources"]:
        try:
            visible.append(safe_archived_source(source))
        except ValueError:
            result["skipped_count"] += 1
    return {**result, "sources": visible}


@app.get("/api/context/sources/{capture_id}")
def get_context_source(capture_id: str,
                       content_hash: str = Query(pattern=r"^[a-f0-9]{64}$"),
                       manifest_hash: str = Query(pattern=r"^[a-f0-9]{64}$")):
    try:
        return safe_archived_source(context_store().get_capture(capture_id, content_hash, manifest_hash))
    except (KeyError, OSError, sqlite3.Error, ValueError, TypeError, UnicodeError):
        raise HTTPException(404, "This saved source is unavailable or no longer eligible. Refresh the source list.") from None


@app.put("/api/context")
def configure_context_archive(entry: ContextArchiveConfig):
    # Factory access must remain available to reflection validators holding SQLite.
    with conflict_errors(), _archive_config_lock:
        for scope in entry.public_sources:
            safe_private_content(scope)
        config = archive_configuration(entry.model_dump())
        for scope in config["public_sources"]:
            safe_private_content(scope)
        store = context_store()
        if store.path_error:
            raise ValueError(store.path_error)
        ownership = getattr(app.state, "archive_ownership", None)
        # Order the initial claim failure receipt with direct store retries too.
        with store._write_lock:
            acquired = False
            try:
                if config["enabled"] and ownership is not None and not getattr(app.state, "archive_owner", False):
                    try:
                        ownership.enter_context(store.archive_owner())
                    except (ValueError, OSError):
                        try:
                            store.record_error()
                        except (OSError, sqlite3.Error):
                            pass
                        raise ValueError("The context archive is unavailable. Check its directory and writer ownership.") from None
                    acquired = True
                result = store.configure(config)
                if ownership is not None:
                    if not config["enabled"]:
                        ownership.close()
                    app.state.archive_owner = config["enabled"]
                return safe_archive_status(result)
            except Exception:
                if acquired:
                    ownership.close()
                    app.state.archive_owner = False
                raise


@app.patch("/api/context")
def set_context_archive_enabled(entry: ContextArchiveEnabled):
    # Toggle authoritative settings, never an earlier browser's settings copy.
    with conflict_errors(), _archive_config_lock:
        store = context_store()
        with store._write_lock:
            status = safe_archive_status(store.status())
            if status["config"] is None:
                raise ValueError(status["config_error"])
            if status["path_error"]:
                raise ValueError(status["path_error"])
            return configure_context_archive(ContextArchiveConfig(**{**status["config"], "enabled": entry.enabled}))


@app.put("/api/web-search")
def configure_web_search(entry: WebSearchSetup):
    with conflict_errors():
        if entry.config.enabled:
            config = provider().read_state()["config"]
            if config["protocol"] != "ollama" or config["ollama_context_tokens"] < 8192:
                raise ValueError("Automatic search needs local Ollama and at least 8192 context tokens. Update Local worker settings first.")
        return WebSearch(DATABASE, TIMEZONE).configure(entry.config.model_dump(), entry.api_key, entry.persist)


@app.post("/api/web-search/test")
def test_web_search():
    service = WebSearch(DATABASE, TIMEZONE)
    with conflict_errors():
        service.search("Ollama official web search documentation", test=True)
        return service.status()


@app.delete("/api/web-search/key")
def delete_web_search_key():
    with conflict_errors():
        return WebSearch(DATABASE, TIMEZONE).delete_key()


@app.put("/api/provider")
def configure_provider(entry: ProviderSetup):
    with conflict_errors():
        return provider().configure(entry.config.model_dump(), entry.api_key, entry.persist)


@app.post("/api/provider/test")
def test_provider():
    with conflict_errors():
        return provider().test()


@app.delete("/api/provider/key")
def remove_provider_key():
    with conflict_errors():
        service = provider()
        with service.transaction() as state:
            credentials.delete(state["config"]["base_url"])
            state.update(models=[], tested_at=None)
        return service.status()


@app.post("/api/provider/charges/{entry_id}/reconcile")
def reconcile_provider_charge(entry_id: str, entry: ChargeInput):
    with conflict_errors():
        provider().reconcile(entry_id, entry.billed_usd)
    return snapshot(read_workspace())


@app.post("/api/provider/local-requests/{entry_id}/recover")
def recover_local_request(entry_id: str, entry: LocalRecoveryInput):
    with conflict_errors():
        provider().recover_local(entry_id, entry.restart_confirmed, entry.expected_base_url)
    return snapshot(read_workspace())


@app.post("/api/tasks")
def create_task(entry: TaskInput):
    with workspace_transaction() as state:
        state["tasks"].insert(0, task_record(entry, "user", now()))
    return snapshot(state)


@app.patch("/api/tasks/{task_id}")
def update_task(task_id: str, entry: TaskUpdate):
    with workspace_transaction() as state:
        task = next((task for task in state["tasks"] if task["id"] == task_id), None)
        if task is None:
            raise HTTPException(404, "That task could not be found.")
        task.update(entry.model_dump(exclude_unset=True))
    return snapshot(state)


@app.delete("/api/tasks/{task_id}")
def remove_task(task_id: str):
    with workspace_transaction() as state:
        task = next((task for task in state["tasks"] if task["id"] == task_id), None)
        if task:
            dismiss_task(state, task)
        state["tasks"] = [task for task in state["tasks"] if task["id"] != task_id]
    return snapshot(state)


@app.post("/api/chat")
def chat(entry: TextInput):
    with conflict_errors():
        if entry.model:
            safe_private_content(entry.model)
    with workspace_transaction() as state:
        target = selected_chat(state, entry.chat_id)
        if target is None:
            target = create_chat_record()
            state["chats"].append(target)
        chat_id = target["id"]
    with conflict_errors():
        provider().chat(entry.text, chat_id, entry.model, entry.role)
    state = read_workspace()
    return snapshot(state, chat_id if any(item["id"] == chat_id for item in state["chats"]) else None)


@app.put("/api/limits")
def put_limits(entry: LimitsInput):
    with workspace_transaction() as state:
        state["limits"] = entry.model_dump()
    return snapshot(state)


if (ROOT / "frontend" / "dist").is_dir():
    app.mount("/", StaticFiles(directory=ROOT / "frontend" / "dist", html=True), name="ui")

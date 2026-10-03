"""Local workspace with configurable live chat and persistent to-dos."""

from contextlib import closing, contextmanager, asynccontextmanager
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
import uuid
from zoneinfo import ZoneInfo

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware
from backend.provider import Provider
from backend import credentials
from backend.capabilities import CAPABILITIES
from backend.web_search import WebSearch, ENDPOINT as SEARCH_ENDPOINT
from backend.storage import workspace_owner
from backend.memory import MAX_CONTENT, MemoryStore
from backend.work_config import WorkConfig, config_value
from backend.reflection import ReflectionStore, ReflectionWorker
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


class ProviderConfig(StrictModel):
    base_url: str = Field(default="https://api.openai.com/v1", min_length=1, max_length=2048)
    protocol: Literal["responses", "chat_completions", "ollama"] = "responses"
    model: str = Field(default="", max_length=200, pattern=r"^[A-Za-z0-9_./:-]*$")
    orchestrator_model: str = Field(default="", max_length=200, pattern=r"^[A-Za-z0-9_./:-]*$")
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


def provider():
    return Provider(DATABASE, TIMEZONE)


@contextmanager
def conflict_errors():
    try:
        yield
    except ValueError as error:
        raise HTTPException(409, str(error)) from None


@asynccontextmanager
async def lifespan(application):
    with workspace_owner(DATABASE.parent):
        read_workspace()
        provider().recover()
        WebSearch(DATABASE, TIMEZONE).recover()
        MemoryStore(DATABASE, TIMEZONE).initialize()
        reflection = ReflectionStore(DATABASE, TIMEZONE)
        reflection.initialize()
        reflection.recover()
        stop = asyncio.Event()
        worker = asyncio.create_task(ReflectionWorker(reflection, provider(), safe_private_content).run(stop))
        try:
            yield
        finally:
            stop.set()
            await worker


app = FastAPI(title="Maestro", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "testserver"])


@app.exception_handler(RequestValidationError)
async def invalid_entry(request: Request, error: RequestValidationError):
    # Do not echo submitted content into errors, including accidental credentials.
    return JSONResponse({"detail": "Check the entry's fields and try again."}, status_code=422)


@app.get("/health")
def health():
    return {"application": "maestro", "mode": "local", "launch_id": os.environ.get("MAESTRO_LAUNCH_ID"),
            "workspace_id": hashlib.sha256(os.path.normcase(str(DATA_DIR)).encode()).hexdigest()}


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
        prune_ai_tasks(state, db)
    return snapshot(state)


def safe_private_content(content):
    # Memory and model choices must not turn configured credentials into context.
    for endpoint in (provider().read_state()["config"]["base_url"], SEARCH_ENDPOINT):
        try:
            key = credentials.read(endpoint)[0]
        except ValueError:
            continue
        if key and key in content:
            raise ValueError("Remove credentials before saving.")
    return content


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
        if entry.enabled and entry.auto_curate and connection["ollama_context_tokens"] < 8192:
            raise ValueError("Automatic memory curation needs at least 8192 context tokens. Update Local worker settings first.")
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
            "tasks": [present_task(task) for task in state["tasks"]]}


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

"""Local workspace: demo chat/tasks, plus an opt-in live reflection worker."""

from contextlib import closing, contextmanager, asynccontextmanager
from datetime import datetime
import hmac
import json
import os
from pathlib import Path
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
from backend.reflection import ReflectionEngine

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("MAESTRO_DATA_DIR") or Path(os.environ.get("LOCALAPPDATA", Path.home() / ".local" / "share")) / "Maestro" / "preview").expanduser().resolve()
if DATA_DIR == ROOT or ROOT in DATA_DIR.parents:
    raise RuntimeError("Choose a private data directory outside the Git checkout.")
DATA_DIR.mkdir(parents=True, exist_ok=True)
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
    stamp = now()
    tasks = [
        {"id": "sample-documents", "title": "Review the latest financial documents", "details": "Find the key changes and questions worth following up.", "priority": "high", "done": False, "agent": "Document analyst", "created_at": stamp},
        {"id": "sample-week", "title": "Make a clear plan for the week", "details": "Break the work into a few achievable next steps.", "priority": "normal", "done": False, "agent": "Work organizer", "created_at": stamp},
        {"id": "sample-workflow", "title": "Explore a better research workflow", "details": "Compare a few approaches and propose a practical improvement.", "priority": "normal", "done": False, "agent": "Coordinator", "created_at": stamp},
    ]
    steps = [
        {"title": "Coordinator made a plan", "detail": "Defined the outcome and selected the right specialist."},
        {"title": "Specialist prepared a draft", "detail": "Worked through the assignment using synthetic example context."},
        {"title": "Reviewer checked the result", "detail": "Checked coverage and completion criteria. Demo review passed."},
    ]
    runs = [
        {"id": "sample-run-one", "title": "Outline a document review", "agent": "Document analyst", "cost": 0.24, "input_tokens": 13000, "output_tokens": 4200, "created_at": stamp, "steps": steps},
        {"id": "sample-run-two", "title": "Organize a project into next steps", "agent": "Work organizer", "cost": 0.18, "input_tokens": 8400, "output_tokens": 2860, "created_at": stamp, "steps": steps},
    ]
    return {
        "tasks": tasks, "runs": runs, "messages": [],
        "memories": [
            {"id": "sample-memory-one", "text": "Keep summaries concise, with clear next steps.", "category": "Example preference"},
            {"id": "sample-memory-two", "text": "Include source references when reviewing documents.", "category": "Example workflow"},
        ],
        "limits": {"run_usd": 1.0, "daily_usd": 5.0, "monthly_usd": 50.0, "max_tokens": 100000, "max_refinements": 2, "max_minutes": 10},
        "ledger": [
            {"at": stamp, "cost": 0.24, "input_tokens": 13000, "output_tokens": 4200, "calls": 6},
            {"at": stamp, "cost": 0.18, "input_tokens": 8400, "output_tokens": 2860, "calls": 4},
        ],
    }


@contextmanager
def workspace_transaction():
    with closing(sqlite3.connect(DATABASE, timeout=10)) as connection, connection:
        connection.execute("PRAGMA secure_delete=ON")
        connection.execute("CREATE TABLE IF NOT EXISTS workspace (id INTEGER PRIMARY KEY CHECK (id=1), value TEXT NOT NULL)")
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute("SELECT value FROM workspace WHERE id=1").fetchone()
        state = json.loads(row[0]) if row else seed_workspace()
        yield state
        connection.execute("INSERT INTO workspace VALUES (1, ?) ON CONFLICT(id) DO UPDATE SET value=excluded.value", (json.dumps(state),))


def snapshot(state: dict) -> dict:
    current = datetime.now(TIMEZONE)
    today = [entry for entry in state["ledger"] if datetime.fromisoformat(entry["at"]).astimezone(TIMEZONE).date() == current.date()]
    month = [entry for entry in state["ledger"] if datetime.fromisoformat(entry["at"]).astimezone(TIMEZONE).strftime("%Y-%m") == current.strftime("%Y-%m")]
    return {
        **{key: state[key] for key in ("tasks", "memories", "runs", "messages", "limits")},
        "usage": {
            "today_usd": round(sum(entry["cost"] for entry in today), 6),
            "month_usd": round(sum(entry["cost"] for entry in month), 6),
            "input_tokens": sum(entry["input_tokens"] for entry in today),
            "output_tokens": sum(entry["output_tokens"] for entry in today),
            "calls": sum(entry["calls"] for entry in today), "reserved_usd": 0,
        },
        "capabilities": {"mode": "preview", "live_ai": False, "github_pr": False, "secure_credentials": False},
    }


def charge_demo(state: dict, cost: float, input_tokens: int, output_tokens: int, calls: int) -> None:
    usage = snapshot(state)["usage"]
    limits = state["limits"]
    if cost > limits["run_usd"] or usage["today_usd"] + cost > limits["daily_usd"] or usage["month_usd"] + cost > limits["monthly_usd"]:
        raise HTTPException(409, "This preview would exceed your budget. Adjust your limits to continue.")
    if input_tokens + output_tokens > limits["max_tokens"]:
        raise HTTPException(409, "This preview would exceed your per-run token limit.")
    state["ledger"].append({"at": now(), "cost": cost, "input_tokens": input_tokens, "output_tokens": output_tokens, "calls": calls})


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class TaskInput(StrictModel):
    title: str = Field(min_length=1, max_length=200)
    details: str = Field(default="", max_length=3000)
    priority: str = Field(default="normal", pattern="^(normal|high)$")


class DoneInput(StrictModel):
    done: bool


class TextInput(StrictModel):
    text: str = Field(min_length=1, max_length=4000)


class LimitsInput(StrictModel):
    run_usd: float = Field(ge=0, le=10000, allow_inf_nan=False)
    daily_usd: float = Field(ge=0, le=100000, allow_inf_nan=False)
    monthly_usd: float = Field(ge=0, le=1000000, allow_inf_nan=False)
    max_tokens: int = Field(ge=0, le=10000000)
    max_refinements: int = Field(ge=0, le=10)
    max_minutes: int = Field(ge=1, le=120)


class ReflectionConfig(StrictModel):
    enabled: bool = False
    interval_minutes: int = Field(default=30, ge=5, le=1440)
    cycle_usd: float = Field(default=0.1, ge=0, le=10, allow_inf_nan=False)
    daily_usd: float = Field(default=0.25, ge=0, le=100, allow_inf_nan=False)
    max_passes: int = Field(default=3, ge=1, le=3)
    model: str = Field(default="", max_length=100, pattern=r"^[A-Za-z0-9_.:-]*$")
    input_usd_per_million: float = Field(default=0, ge=0, le=10000, allow_inf_nan=False)
    output_usd_per_million: float = Field(default=0, ge=0, le=10000, allow_inf_nan=False)
    pricing_verified: bool = False


class ReflectionFeedback(StrictModel):
    text: str = Field(min_length=1, max_length=2000)
    share: bool = False


class ReflectionReview(StrictModel):
    accept: bool


class ReflectionCharge(StrictModel):
    billed_usd: float = Field(ge=0, le=100000, allow_inf_nan=False)


def reflection_engine():
    return ReflectionEngine(DATABASE, TIMEZONE)


@asynccontextmanager
async def lifespan(application):
    engine = reflection_engine()
    engine.start()
    try:
        yield
    finally:
        engine.close()


app = FastAPI(title="Maestro local preview", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "testserver"])


@app.exception_handler(RequestValidationError)
async def invalid_entry(request: Request, error: RequestValidationError):
    # Do not echo submitted content into errors, including accidental credentials.
    return JSONResponse({"detail": "Check the entry's fields and try again."}, status_code=422)


@app.get("/health")
def health():
    return {"application": "maestro", "mode": "preview"}


@app.middleware("http")
async def protect_local_workspace(request: Request, call_next):
    if request.url.path.startswith("/api/"):
        origin = request.headers.get("origin")
        if origin and origin not in ORIGINS:
            return JSONResponse({"detail": "Only your local Maestro interface can access this workspace."}, status_code=403)
        if request.url.path != "/api/session" and not hmac.compare_digest(request.cookies.get("maestro_session", ""), SESSION):
            return JSONResponse({"detail": "Open Maestro again to start a local session."}, status_code=401)
        if request.method not in ("GET", "HEAD"):
            if not hmac.compare_digest(request.headers.get("x-maestro-csrf", ""), CSRF):
                return JSONResponse({"detail": "Refresh Maestro before saving changes."}, status_code=403)
            try:
                size = int(request.headers.get("content-length", "0"))
            except ValueError:
                return JSONResponse({"detail": "Invalid request."}, status_code=400)
            if size > 32768:
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
def get_workspace():
    with workspace_transaction() as state:
        return snapshot(state)


@app.get("/api/reflection")
def get_reflection():
    return reflection_engine().status()


@app.put("/api/reflection/config")
def configure_reflection(entry: ReflectionConfig):
    reflection_engine().configure(entry.model_dump())
    return reflection_engine().status()


@app.post("/api/reflection/feedback")
def reflection_feedback(entry: ReflectionFeedback):
    try:
        reflection_engine().evidence(entry.text, entry.share)
    except ValueError as error:
        raise HTTPException(409, str(error))
    return reflection_engine().status()


@app.post("/api/reflection/run")
def run_reflection():
    try:
        reflection_engine().enqueue(manual=True)
    except ValueError as error:
        raise HTTPException(409, str(error))
    return reflection_engine().status()


@app.post("/api/reflection/jobs/{job_id}/review")
def review_reflection(job_id: str, entry: ReflectionReview):
    try:
        reflection_engine().review(job_id, entry.accept)
    except ValueError as error:
        raise HTTPException(409, str(error)) from None
    return reflection_engine().status()


@app.delete("/api/reflection/history")
def clear_reflection_history():
    try:
        reflection_engine().clear_history()
    except ValueError as error:
        raise HTTPException(409, str(error)) from None
    return reflection_engine().status()


@app.post("/api/reflection/charges/{entry_id}/reconcile")
def reconcile_reflection_charge(entry_id: str, entry: ReflectionCharge):
    try:
        reflection_engine().reconcile(entry_id, entry.billed_usd)
    except ValueError as error:
        raise HTTPException(409, str(error)) from None
    return reflection_engine().status()


@app.post("/api/tasks")
def create_task(entry: TaskInput):
    with workspace_transaction() as state:
        state["tasks"].insert(0, {"id": identifier(), **entry.model_dump(), "done": False, "agent": "Coordinator", "created_at": now()})
        return snapshot(state)


@app.patch("/api/tasks/{task_id}")
def update_task(task_id: str, entry: DoneInput):
    with workspace_transaction() as state:
        task = next((task for task in state["tasks"] if task["id"] == task_id), None)
        if task is None:
            raise HTTPException(404, "That task could not be found.")
        task["done"] = entry.done
        return snapshot(state)


@app.delete("/api/tasks/{task_id}")
def remove_task(task_id: str):
    with workspace_transaction() as state:
        state["tasks"] = [task for task in state["tasks"] if task["id"] != task_id]
        return snapshot(state)


@app.post("/api/tasks/{task_id}/preview")
def simulate_task(task_id: str):
    with workspace_transaction() as state:
        task = next((task for task in state["tasks"] if task["id"] == task_id), None)
        if task is None:
            raise HTTPException(404, "That task could not be found.")
        if task["done"]:
            raise HTTPException(409, "Reopen this task before previewing a run.")
        charge_demo(state, 0.02, 1024, 384, 3)
        state["runs"].insert(0, {
            "id": identifier(), "title": task["title"], "agent": task["agent"], "cost": 0.02,
            "input_tokens": 1024, "output_tokens": 384, "created_at": now(),
            "steps": [
                {"title": "Coordinator planned the assignment", "detail": "A simulated plan was created. No model was called."},
                {"title": f"{task['agent']} prepared an example result", "detail": "This preview shows the delegation flow. It does not perform the real task."},
                {"title": "Reviewer checked the example", "detail": "Simulation finished. Your real task remains open until you complete it."},
            ],
        })
        return snapshot(state)


@app.post("/api/chat")
def chat(entry: TextInput):
    with workspace_transaction() as state:
        charge_demo(state, 0.005, 256, 128, 1)
        reply = "I've saved that in this local conversation. This is a preview response, so I haven't called an AI model. You can turn your idea into a task with New task, then preview how the coordinator delegates and reviews the work."
        state["messages"].extend([{"id": identifier(), "role": "user", "text": entry.text}, {"id": identifier(), "role": "assistant", "text": reply}])
        return snapshot(state)


@app.put("/api/limits")
def put_limits(entry: LimitsInput):
    with workspace_transaction() as state:
        state["limits"] = entry.model_dump()
        return snapshot(state)


@app.post("/api/memory")
def remember(entry: TextInput):
    with workspace_transaction() as state:
        state["memories"].insert(0, {"id": identifier(), "text": entry.text, "category": "Saved by you"})
        return snapshot(state)


@app.delete("/api/memory/{memory_id}")
def forget(memory_id: str):
    with workspace_transaction() as state:
        state["memories"] = [memory for memory in state["memories"] if memory["id"] != memory_id]
        return snapshot(state)


if (ROOT / "frontend" / "dist").is_dir():
    app.mount("/", StaticFiles(directory=ROOT / "frontend" / "dist", html=True), name="ui")

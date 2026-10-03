"""Task attribution and reviewed suggestions; recording a task never executes it."""
import hashlib
import re
import unicodedata
import uuid
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class TaskInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)
    title: str = Field(min_length=1, max_length=200)
    details: str = Field(default="", max_length=16000)
    priority: Literal["normal", "high"] = "normal"
    suggested_assignee: Literal["user", "maestro"] = "user"


class TaskUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    done: bool | None = None
    suggested_assignee: Literal["user", "maestro"] | None = None

    @model_validator(mode="after")
    def require_changes(self):
        changes = self.model_dump(exclude_unset=True)
        if not changes or any(value is None for value in changes.values()):
            raise ValueError("Choose a task change.")
        return self


def present_task(task):
    return {**task, "initiated_by": task.get("initiated_by", "user"),
            "suggested_assignee": task.get("suggested_assignee", "user")}


def create_task(entry, initiated_by, stamp, model="", provenance=()):
    entry = TaskInput.model_validate(entry)
    task = {"id": uuid.uuid4().hex, **entry.model_dump(), "initiated_by": initiated_by,
            "done": False, "created_at": stamp}
    if initiated_by == "maestro":
        task.update(initiated_model=model, provenance=list(provenance))
    return task


def title_hash(title):
    normalized = re.sub(r"[\W_]+", " ", unicodedata.normalize("NFKC", title).casefold()).strip()
    return hashlib.sha256(normalized.encode()).hexdigest()


def dismiss_task(workspace, task):
    if task.get("initiated_by") == "maestro":
        dismissed = workspace.setdefault("ai_task_dismissals", [])
        fingerprint = title_hash(task["title"])
        if fingerprint not in dismissed:
            dismissed.append(fingerprint)
        workspace["ai_task_dismissals"] = dismissed[-2000:]


def add_reviewed_tasks(workspace, drafts, stamp, model, provenance=()):
    tasks = workspace.setdefault("tasks", [])
    seen = {title_hash(task["title"]) for task in tasks} | set(workspace.get("ai_task_dismissals", []))
    created = []
    for draft in drafts:
        task = create_task(draft, "maestro", stamp, model, provenance)
        fingerprint = title_hash(task["title"])
        if fingerprint in seen or len(tasks) >= 2000:
            continue
        tasks.insert(0, task)
        seen.add(fingerprint)
        created.append(task)
    return created


def prune_ai_tasks(workspace, db):
    """Remove source-derived AI tasks when their evidence is corrected or erased."""
    from backend.memory import MemoryStore

    sourced = [task for task in workspace.get("tasks", [])
               if task.get("initiated_by") == "maestro" and task.get("provenance")]
    if not sourced:
        return False
    inventory = {}
    if db.execute("SELECT 1 FROM sqlite_master WHERE name='private_memories'").fetchone():
        inventory = {record["id"]: record for row in db.execute("SELECT * FROM private_memories")
                     for record in [MemoryStore.record(row)]}
    chats = {chat["id"]: chat for chat in workspace.get("chats", [])}
    removed, source_cache = set(), {}
    for task in sourced:
        record = {"id": "task:" + task["id"], "kind": "lesson", "chat_id": None,
                  "provenance": task["provenance"]}
        if not MemoryStore.valid_source(record, chats, inventory, cache=source_cache):
            removed.add(task["id"])
            dismiss_task(workspace, task)
    if removed:
        workspace["tasks"] = [task for task in workspace["tasks"] if task["id"] not in removed]
    return bool(removed)

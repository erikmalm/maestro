"""Durable research ownership, local lookup receipts and owned model-stage accounting."""
from contextlib import closing, contextmanager
from datetime import datetime, timezone as datetime_timezone
import hashlib
import json
import math
from pathlib import Path
import re
import sqlite3
import time
import uuid


ID = re.compile(r"[a-f0-9]{32}\Z")
HASH = re.compile(r"[a-f0-9]{64}\Z")
MAX_REQUEST = 32000
MAX_RUN_BYTES = 256 * 1024
MAX_RESULT_BYTES = 32768
MAX_PROVIDER_STATE_BYTES = 16 * 1024 ** 2
MAX_EVENTS = 32
ACTIVE = ("queued", "running")
WORKFLOWS = ("lookup", "requirements", "evidence_review", "research_draft")
AMOUNTS = ("model_calls", "tokens", "seconds", "estimated_model_usd", "hosted_searches")
EVENTS = {
    "submitted": "Local research preview submitted.",
    "local_lookup_started": "Checking saved evidence locally.",
    "local_lookup_finished": "Saved evidence lookup finished.",
    "finished": "Local lookup complete. Requirements, writing and final review have not run.",
    "cancelled": "Research preview cancelled. No further work will start.",
    "interrupted": "Research preview interrupted by restart. It will not resume automatically.",
    "failed": "Saved evidence lookup could not finish.",
    "stage_reserved": "Local model-stage allowance reserved.",
    "stage_finished": "Local model stage completed.",
    "stage_failed": "Local model stage did not produce an accepted result.",
    "stage_uncertain": "Local model completion is unknown; its allowance remains reserved.",
    "evidence_started": "Inspecting bounded saved-source candidates for the recorded requirements.",
    "evidence_recorded": "Saved evidence assessed. Writing and final review have not run.",
    "writer_started": "Saved evidence assessed. Preparing an unreviewed draft.",
    "draft_recorded": "Unreviewed draft recorded. Final answer review has not run.",
    "draft_unavailable": "No draft was written. Evidence decisions and gaps remain recorded.",
    "requirements_started": "Assessing the research requirements with the local model.",
    "requirements_recorded": "Requirements recorded. Evidence assessment, writing and final review have not run.",
    "clarification_needed": "Clarification is needed before dependent research. No answer was produced.",
}
FAILURES = {
    "lookup_failed": EVENTS["failed"],
    "lookup_unavailable": EVENTS["failed"],
    "invalid_result": EVENTS["failed"],
    "deadline": "Research preview reached its deadline before completion.",
    "permissions_changed": "Research permissions changed before completion. No result was saved.",
    "requirements_failed": "Requirements assessment did not produce an accepted result. No answer was produced.",
    "evidence_failed": "Saved-evidence assessment did not produce an accepted result. No answer was produced.",
    "writer_failed": "Draft writing did not produce a valid result. Earlier evidence decisions and actual usage are retained.",
}


def _encoded(value, maximum=MAX_RUN_BYTES):
    try:
        encoded = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
        if len(encoded.encode("utf-8")) > maximum:
            raise ValueError()
        return encoded
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise ValueError("Research data exceeds its supported bounds.") from None


def _text(value, maximum, *, empty=False):
    if (not isinstance(value, str) or len(value) > maximum or "\x00" in value
            or not empty and not value.strip()):
        raise ValueError("Use valid bounded research fields.")
    value.encode("utf-8")
    return value


def _identifier(value):
    if not isinstance(value, str) or not ID.fullmatch(value):
        raise ValueError("Use a valid research identifier.")
    return value


def _date(value):
    _text(value, 64)
    try:
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            raise ValueError()
        return parsed
    except (ValueError, OverflowError):
        raise ValueError("Research timestamps must include a timezone.") from None


def _permissions(value, chat_id):
    if (not isinstance(value, dict)
            or set(value) != {"source_mode", "hosted_allowed", "memory_recall", "memory_derived", "chat_id"}
            or value["source_mode"] != "saved_only" or value["hosted_allowed"] is not False
            or value["memory_recall"] is not False or type(value["memory_derived"]) is not bool
            or value["chat_id"] != chat_id):
        raise ValueError("Use the local research preview permissions.")
    _encoded(value, 2048)
    return dict(value)


def _budget(value):
    if (not isinstance(value, dict)
            or set(value) - {"parent_limits"} != {"total", "consumed", "reserved", "remaining", "deadline", "daily_search"}):
        raise ValueError("Use a valid research allowance.")
    result = json.loads(_encoded(value, 8192))
    maxima = {"model_calls": 100, "tokens": 10000000, "seconds": 86400,
              "estimated_model_usd": 10000, "hosted_searches": 20}
    for section in ("total", "consumed", "reserved", "remaining"):
        amounts = result[section]
        if not isinstance(amounts, dict) or set(amounts) != set(AMOUNTS):
            raise ValueError("Use a valid research allowance.")
        for name, amount in amounts.items():
            integer = name in ("model_calls", "tokens", "hosted_searches")
            if (type(amount) not in ((int,) if integer else (int, float)) or not math.isfinite(amount)
                    or not 0 <= amount <= maxima[name]):
                raise ValueError("Use a valid research allowance.")
    if result["total"]["hosted_searches"] != 0:
        raise ValueError("The research preview cannot acquire hosted evidence.")
    for name in AMOUNTS:
        if result["consumed"][name] + result["reserved"][name] > result["total"][name]:
            raise ValueError("Research usage exceeds its allowance.")
        result["remaining"][name] = max(0, result["total"][name] - result["consumed"][name] - result["reserved"][name])
    _date(result["deadline"])
    daily = result["daily_search"]
    if (not isinstance(daily, dict) or set(daily) != {"limit", "used", "remaining"}
            or any(type(daily[name]) is not int or not 0 <= daily[name] <= 200 for name in daily)
            or daily["limit"] > 20 or daily["remaining"] != max(0, daily["limit"] - daily["used"])):
        raise ValueError("Use valid shared search accounting.")
    if "parent_limits" in result:
        limits = result["parent_limits"]
        caps = {"run_usd": 10000, "daily_usd": 100000, "monthly_usd": 1000000, "max_tokens": 10000000}
        if (not isinstance(limits, dict) or set(limits) != set(caps)
                or any(type(amount) not in ((int,) if name == "max_tokens" else (int, float))
                       or not math.isfinite(amount) or not 0 <= amount <= caps[name] for name, amount in limits.items())):
            raise ValueError("Use valid captured parent limits.")
    return result


def _result(value):
    if (not isinstance(value, dict)
            or set(value) != {"lookup_state", "search_limited", "skipped_count", "candidates", "summary"}
            or value["lookup_state"] not in ("ready", "limited", "disabled", "unconfigured", "unavailable")
            or type(value["search_limited"]) is not bool or type(value["skipped_count"]) is not int
            or not 0 <= value["skipped_count"] <= 1000 or not isinstance(value["candidates"], list)
            or len(value["candidates"]) > 20):
        raise ValueError("Use a bounded local evidence receipt.")
    _text(value["summary"], 2000)
    seen = set()
    for source in value["candidates"]:
        if (not isinstance(source, dict)
                or set(source) != {"capture_id", "content_hash", "manifest_hash", "retrieved_at", "stale"}
                or not isinstance(source["capture_id"], str) or not ID.fullmatch(source["capture_id"])
                or source["capture_id"] in seen or type(source["stale"]) is not bool
                or any(not isinstance(source[name], str) or not HASH.fullmatch(source[name])
                       for name in ("content_hash", "manifest_hash"))):
            raise ValueError("Use exact saved-source references in the receipt.")
        _date(source["retrieved_at"])
        seen.add(source["capture_id"])
    return json.loads(_encoded(value, MAX_RESULT_BYTES))


class ResearchStore:
    def __init__(self, database, timezone=datetime_timezone.utc):
        self.database = Path(database).expanduser().resolve()
        self.timezone = timezone

    def clock(self):
        return time.time()

    def stamp(self):
        return datetime.fromtimestamp(self.clock(), datetime_timezone.utc).isoformat()

    @staticmethod
    def _has_table(db, name):
        return db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None

    def initialize(self, db=None):
        if db is None:
            with self._transaction() as connection:
                self.initialize(connection)
            return
        db.execute("CREATE TABLE IF NOT EXISTS research_runs("
                   "id TEXT PRIMARY KEY,submission_id TEXT NOT NULL UNIQUE,fingerprint TEXT NOT NULL,chat_id TEXT,"
                   "state TEXT NOT NULL,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,claim_token TEXT,payload TEXT NOT NULL)")
        db.execute("CREATE UNIQUE INDEX IF NOT EXISTS research_one_foreground ON research_runs((1)) "
                   "WHERE state IN ('queued','running')")
        db.execute("CREATE INDEX IF NOT EXISTS research_recent ON research_runs(created_at DESC,id DESC) WHERE state!='deleted'")
        db.execute("CREATE INDEX IF NOT EXISTS research_chat ON research_runs(chat_id)")
        db.execute("CREATE TABLE IF NOT EXISTS research_events("
                   "run_id TEXT NOT NULL,sequence INTEGER NOT NULL,at TEXT NOT NULL,kind TEXT NOT NULL,summary TEXT NOT NULL,"
                   "PRIMARY KEY(run_id,sequence))")
        from backend.research_stages import initialize
        initialize(db)

    @contextmanager
    def _transaction(self, db=None):
        if db is not None:
            if not db.in_transaction:
                raise ValueError("Research changes require an active workspace transaction.")
            self.initialize(db)
            yield db
            return
        self.database.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.database, timeout=10)) as connection, connection:
            connection.execute("PRAGMA secure_delete=ON")
            connection.execute("BEGIN IMMEDIATE")
            self.initialize(connection)
            yield connection

    @staticmethod
    def owns_foreground(db):
        if not ResearchStore._has_table(db, "research_runs"):
            return False
        return db.execute("SELECT 1 FROM research_runs WHERE state IN ('queued','running') LIMIT 1").fetchone() is not None

    def _row(self, db, run_id=None, submission_id=None):
        field, selected = ("submission_id", submission_id) if submission_id is not None else ("id", run_id)
        return db.execute("SELECT id,submission_id,fingerprint,chat_id,state,created_at,updated_at,claim_token,"
                          "CASE WHEN typeof(payload)='text' AND length(CAST(payload AS BLOB))<=? THEN payload END "
                          f"FROM research_runs WHERE {field}=?", (MAX_RUN_BYTES, selected)).fetchone()

    def _public(self, db, row, *, events=True):
        if row is None or row[4] == "deleted":
            raise KeyError("Research run not found.")
        data = json.loads(row[8])
        result = {"id": row[0], "submission_id": row[1], "chat_id": row[3], "state": row[4],
                  "created_at": row[5], "updated_at": row[6], **data}
        result.setdefault("workflow", "lookup")
        result.setdefault("assessment", None)
        result.setdefault("evidence_review", None)
        result.setdefault("draft", None)
        if events:
            rows = db.execute("SELECT sequence,at,kind,summary FROM research_events WHERE run_id=? ORDER BY sequence LIMIT ?",
                              (row[0], MAX_EVENTS + 1)).fetchall()
            if len(rows) > MAX_EVENTS or any(event[2] not in EVENTS for event in rows):
                raise ValueError("Research activity is unavailable.")
            result["events"] = [dict(zip(("sequence", "at", "kind", "summary"), event)) for event in rows]
        return result

    def _event(self, db, run_id, kind, summary=None):
        if kind not in EVENTS or summary is not None and summary not in FAILURES.values():
            raise ValueError("Use a recorded research transition.")
        previous = db.execute("SELECT MAX(sequence) FROM research_events WHERE run_id=?", (run_id,)).fetchone()[0] or 0
        if previous >= MAX_EVENTS:
            raise ValueError("Research activity exceeds its transition allowance.")
        db.execute("INSERT INTO research_events VALUES(?,?,?,?,?)",
                   (run_id, previous + 1, self.stamp(), kind, summary or EVENTS[kind]))

    @staticmethod
    def _chat_exists(db, chat_id):
        if chat_id is None:
            return True
        if not ResearchStore._has_table(db, "workspace"):
            return False
        return db.execute("SELECT 1 FROM workspace w,json_each(w.value,'$.chats') c "
                          "WHERE w.id=1 AND json_extract(c.value,'$.id')=? LIMIT 1", (chat_id,)).fetchone() is not None

    @staticmethod
    def _provider_busy(db):
        if not ResearchStore._has_table(db, "provider_state"):
            return False
        row = db.execute("SELECT CASE WHEN typeof(value)='text' AND length(CAST(value AS BLOB))<=? "
                         "THEN value END FROM provider_state WHERE id=1", (MAX_PROVIDER_STATE_BYTES,)).fetchone()
        if not row:
            return False
        try:
            state = json.loads(row[0])
            if not isinstance(state, dict):
                raise ValueError()
            ledger = state.get("ledger", [])
            if not isinstance(ledger, list) or any(not isinstance(entry, dict) for entry in ledger):
                raise ValueError()
            return any(entry.get("status") in ("reserved", "uncertain") for entry in ledger)
        except (ValueError, TypeError, RecursionError):
            raise ValueError("Provider usage is unavailable. Research cannot start.") from None

    def create(self, submission_id, request, query, chat_id, permissions, budget, db=None, *, workflow="lookup"):
        _identifier(submission_id)
        _text(request, MAX_REQUEST)
        _text(query, 512, empty=True)
        if chat_id is not None:
            _text(chat_id, 100)
        if workflow not in WORKFLOWS or workflow != "lookup" and query:
            raise ValueError("Use a supported research workflow without lookup keywords for requirements.")
        identity = [request, query, chat_id]
        # Keep old lookup retries stable; a different workflow cannot reuse one.
        if workflow != "lookup":
            identity.append(workflow)
        fingerprint = hashlib.sha256(_encoded(identity).encode("utf-8")).hexdigest()
        with self._transaction(db) as connection:
            previous = self._row(connection, submission_id=submission_id)
            if previous is not None:
                if previous[2] != fingerprint:
                    raise ValueError("This submission ID already belongs to a different research request.")
                if previous[4] == "deleted":
                    raise ValueError("This research submission was deleted. Use a new submission ID.")
                return self._public(connection, previous), False
            captured = _permissions(permissions, chat_id)
            allowance = _budget(budget)
            if not self._chat_exists(connection, chat_id):
                raise KeyError("The originating chat is unavailable.")
            if self.owns_foreground(connection) or self._provider_busy(connection):
                raise ValueError("Another request is active or its completion is unknown. Wait before starting research.")
            run_id, stamp = uuid.uuid4().hex, self.stamp()
            payload = {"request": request, "query": query, "permissions": captured, "budget": allowance,
                       "result": None, "workflow": workflow, "assessment": None, "evidence_review": None, "draft": None}
            connection.execute("INSERT INTO research_runs VALUES(?,?,?,?,?,?,?,?,?)",
                               (run_id, submission_id, fingerprint, chat_id, "queued", stamp, stamp, None, _encoded(payload)))
            self._event(connection, run_id, "submitted")
            return self._public(connection, self._row(connection, run_id)), True

    def get(self, run_id):
        _identifier(run_id)
        if not self.database.is_file():
            raise KeyError("Research run not found.")
        with closing(sqlite3.connect(self.database.as_uri() + "?mode=ro", uri=True)) as db:
            # Keep the record and its activity on the same read-only snapshot.
            db.execute("BEGIN")
            if not self._has_table(db, "research_runs"):
                raise KeyError("Research run not found.")
            return self._public(db, self._row(db, run_id))

    def list(self, limit=20):
        if type(limit) is not int or not 1 <= limit <= 50:
            raise ValueError("Choose one to fifty research runs.")
        if not self.database.is_file():
            return []
        with closing(sqlite3.connect(self.database.as_uri() + "?mode=ro", uri=True)) as db:
            # Deletion may commit between the ID query and individual records.
            db.execute("BEGIN")
            if not self._has_table(db, "research_runs"):
                return []
            ids = db.execute("SELECT id FROM research_runs INDEXED BY research_recent WHERE state!='deleted' "
                             "ORDER BY created_at DESC,id DESC LIMIT ?", (limit,)).fetchall()
            return [self._public(db, self._row(db, run_id), events=False) for (run_id,) in ids]

    def _elapsed(self, run):
        allowance = run["budget"]
        elapsed = max(0, self.clock() - _date(run["created_at"]).timestamp())
        allowance["consumed"]["seconds"] = min(allowance["total"]["seconds"],
                                                max(allowance["consumed"]["seconds"], elapsed))
        for name in AMOUNTS:
            allowance["remaining"][name] = max(0, allowance["total"][name] - allowance["consumed"][name] - allowance["reserved"][name])

    @staticmethod
    def _chat_memory(db, chat_id):
        if chat_id is None or not ResearchStore._has_table(db, "workspace"):
            return False
        return db.execute("SELECT 1 FROM workspace w,json_each(w.value,'$.chats') c,json_each(c.value,'$.messages') m "
                          "WHERE w.id=1 AND json_extract(c.value,'$.id')=? "
                          "AND coalesce(json_array_length(json_extract(m.value,'$.memory_ids')),0)>0 LIMIT 1",
                          (chat_id,)).fetchone() is not None

    def _save(self, db, run, state, *, token=None):
        run["state"], run["updated_at"] = state, self.stamp()
        payload = {name: run[name] for name in ("request", "query", "permissions", "budget", "result")}
        payload.update(workflow=run.get("workflow", "lookup"), assessment=run.get("assessment"),
                       evidence_review=run.get("evidence_review"), draft=run.get("draft"))
        db.execute("UPDATE research_runs SET state=?,updated_at=?,claim_token=?,payload=? WHERE id=?",
                   (state, run["updated_at"], token, _encoded(payload), run["id"]))

    def _terminal(self, db, run, state, kind, summary=None):
        from backend.research_stages import release_finishing
        release_finishing(self, db, run)
        run["permissions"]["memory_derived"] |= self._chat_memory(db, run["chat_id"])
        self._elapsed(run)
        self._save(db, run, state)
        self._event(db, run["id"], kind, summary)

    def _purge(self, db, run_id):
        from backend.research_stages import purge
        purge(db, run_id)
        db.execute("DELETE FROM research_events WHERE run_id=?", (run_id,))
        db.execute("UPDATE research_runs SET state='deleted',chat_id=NULL,claim_token=NULL,payload='{}',updated_at=? WHERE id=?",
                   (self.stamp(), run_id))

    def claim(self, run_id, *, lookup=True):
        _identifier(run_id)
        with self._transaction() as db:
            row = self._row(db, run_id)
            if row is None or row[4] != "queued":
                return None
            run = self._public(db, row)
            if not self._chat_exists(db, run["chat_id"]):
                self._purge(db, run_id)
                return None
            if self.clock() >= _date(run["budget"]["deadline"]).timestamp():
                self._terminal(db, run, "failed", "failed", FAILURES["deadline"])
                return None
            token = uuid.uuid4().hex
            run["permissions"]["memory_derived"] |= self._chat_memory(db, run["chat_id"])
            self._save(db, run, "running", token=token)
            if run.get("workflow") in ("requirements", "evidence_review", "research_draft"):
                self._event(db, run_id, "requirements_started")
            elif lookup:
                self._event(db, run_id, "local_lookup_started")
            return self._public(db, self._row(db, run_id)), token

    def finish(self, run_id, token, result, *, permissions=None):
        _identifier(run_id)
        with self._transaction() as db:
            row = self._row(db, run_id)
            if row is None or row[4] != "running" or row[7] != token:
                return False
            run = self._public(db, row)
            if not self._chat_exists(db, run["chat_id"]):
                self._purge(db, run_id)
                return False
            if self.clock() >= _date(run["budget"]["deadline"]).timestamp():
                self._terminal(db, run, "failed", "failed", FAILURES["deadline"])
                return False
            if permissions is not None:
                try:
                    current_permissions = _permissions(permissions, run["chat_id"])
                except ValueError:
                    self._terminal(db, run, "failed", "failed", FAILURES["permissions_changed"])
                    return False
                run["permissions"]["memory_derived"] |= current_permissions["memory_derived"]
            run["result"] = _result(result)
            self._event(db, run_id, "local_lookup_finished")
            self._terminal(db, run, "partial", "finished")
            return True

    def fail(self, run_id, token, reason="lookup_failed"):
        _identifier(run_id)
        if reason not in FAILURES:
            raise ValueError("Use a fixed research failure reason.")
        with self._transaction() as db:
            row = self._row(db, run_id)
            if row is None or row[4] != "running" or row[7] != token:
                return False
            run = self._public(db, row)
            if not self._chat_exists(db, run["chat_id"]):
                self._purge(db, run_id)
                return False
            if reason in ("evidence_failed", "writer_failed"):
                kind = "writer" if reason == "writer_failed" else "evidence_review"
                db.execute("UPDATE research_stages SET output=NULL,state=CASE WHEN state IN ('completed','prepared') THEN 'failed' ELSE state END WHERE run_id=? AND kind=?", (run_id, kind))
            self._terminal(db, run, "failed", "failed", FAILURES[reason])
            return True

    def finish_requirements(self, run_id, token, stage, *, secrets=()):
        """Publish only this owner's validated, completed requirements stage."""
        from backend.research_requirements import validate_assessment
        from backend.research_stages import ResearchStage, _row, _states, _hash, safe_stage_content
        _identifier(run_id)
        if type(stage) is not ResearchStage:
            raise ValueError("Use the owned requirements assessment.")
        with self._transaction() as db:
            row = self._row(db, run_id)
            if row is None or row[4] != "running" or row[7] != token:
                return False
            run = self._public(db, row)
            if not self._chat_exists(db, run["chat_id"]):
                self._purge(db, run_id)
                return False
            if run.get("workflow") not in ("requirements", "evidence_review", "research_draft") or stage.run_id != run_id or stage.claim_token != token:
                raise ValueError("Use the owned requirements assessment.")
            record = _row(db, stage)
            if record[2] != "requirements" or record[3] != "completed" or record[10] is None:
                raise ValueError("A completed requirements stage is required.")
            spec = json.loads(record[5])
            workspace, provider_state = _states(db)
            if ((_hash(provider_state["config"]), _hash(workspace["limits"])) != (record[6], record[7])
                    or spec["model"] not in provider_state.get("models", [])):
                raise ValueError("Research model settings or parent limits changed.")
            # The Provider already accepted the stage within its own deadline.
            # Publishing its assessment must still fit the parent's deadline.
            deadline = _date(run["budget"]["deadline"]).timestamp()
            assessment = validate_assessment(json.loads(record[10]), run["request"])
            run["assessment"] = assessment
            run["permissions"]["memory_derived"] |= self._chat_memory(db, run["chat_id"])
            safe_stage_content([run, _encoded(run)], provider_state["config"], secrets=secrets)
            if self.clock() >= deadline:
                raise ValueError("Research requirements reached their deadline.")
            clarify = assessment["decision"] == "clarify"
            if run.get("workflow") in ("evidence_review", "research_draft") and not clarify:
                self._save(db, run, "running", token=token)
                self._event(db, run_id, "evidence_started")
                return True
            self._terminal(db, run, "clarification" if clarify else "partial",
                           "clarification_needed" if clarify else "requirements_recorded")
            return True

    def finish_evidence_review(self, run_id, token, review, *, stage=None, secrets=()):
        """Publish a revalidated packet while the caller holds archive locks."""
        from backend.research_evidence import validate_saved_review, validate_stage_review
        from backend.research_stages import ResearchStage, _row, _states, _hash, safe_stage_content
        _identifier(run_id)
        with self._transaction() as db:
            row = self._row(db, run_id)
            if row is None or row[4] != "running" or row[7] != token:
                return False
            run = self._public(db, row)
            if not self._chat_exists(db, run["chat_id"]):
                self._purge(db, run_id)
                return False
            if run.get("workflow") not in ("evidence_review", "research_draft") or run.get("assessment") is None:
                raise ValueError("Use the owned saved-evidence assessment.")
            validate_saved_review(review, run["assessment"])
            workspace, provider_state = _states(db)
            scope = db.execute("SELECT config_hash,limits_hash FROM research_stage_scope WHERE run_id=?", (run_id,)).fetchone()
            if scope != (_hash(provider_state["config"]), _hash(workspace["limits"])):
                raise ValueError("Research model settings or parent limits changed.")
            if stage is not None:
                if type(stage) is not ResearchStage or stage.run_id != run_id or stage.claim_token != token:
                    raise ValueError("Use the owned saved-evidence stage.")
                record = _row(db, stage)
                if record[2] != "evidence_review" or record[3] != "completed" or record[10] is None:
                    raise ValueError("A completed evidence-review stage is required.")
                if json.loads(record[5])["model"] not in provider_state.get("models", []):
                    raise ValueError("The selected model is unavailable.")
                packet = {key: review[key] for key in ("version", "offset_unit", "lookup", "selection", "sources", "passages")}
                from backend.research_passages import evidence_packet_hash
                spec = json.loads(record[5])
                pins = [{key: source[key] for key in ("capture_id", "content_hash", "manifest_hash", "retrieved_at", "stale")}
                        for source in packet["sources"]]
                if spec.get("packet_hash") != evidence_packet_hash(packet) or spec["evidence"] != pins:
                    raise ValueError("The selected evidence packet differs from the owned stage.")
                if validate_stage_review(json.loads(record[10]), run["assessment"], packet,
                                         review["checked_at"], spec["schema"]) != review:
                    raise ValueError("The evidence decision changed after validation.")
            elif review["passages"] or review["sources"] or any(item["state"] == "covered" for item in review["items"]):
                raise ValueError("Source acceptance requires an owned model stage.")
            run["evidence_review"] = review
            safe_stage_content([run, _encoded(run)], provider_state["config"], secrets=secrets)
            if self.clock() >= _date(run["budget"]["deadline"]).timestamp():
                raise ValueError("The research deadline has passed.")
            if run.get("workflow") == "research_draft":
                self._save(db, run, "running", token=token)
                self._event(db, run_id, "writer_started")
                return True
            self._terminal(db, run, "partial", "evidence_recorded")
            return True

    def finish_draft(self, run_id, token, draft, *, stage=None, secrets=()):
        """Publish a bound unreviewed artifact while the caller holds archive locks."""
        from backend.research_draft import coverage_hash, response_schema, validate_response, validate_saved_draft
        from backend.research_passages import evidence_packet_hash
        from backend.research_stages import ResearchStage, _row, _states, _hash, safe_stage_content
        _identifier(run_id)
        with self._transaction() as db:
            row = self._row(db, run_id)
            if row is None or row[4] != "running" or row[7] != token:
                return False
            run = self._public(db, row)
            if not self._chat_exists(db, run["chat_id"]):
                self._purge(db, run_id)
                return False
            if run.get("workflow") != "research_draft" or run.get("evidence_review") is None:
                raise ValueError("Use the owned research draft workflow.")
            assessment, review = run["assessment"], run["evidence_review"]
            validate_saved_draft(draft, assessment, review)
            workspace, provider_state = _states(db)
            scope = db.execute("SELECT config_hash,limits_hash FROM research_stage_scope WHERE run_id=?", (run_id,)).fetchone()
            if scope != (_hash(provider_state["config"]), _hash(workspace["limits"])):
                raise ValueError("Research model settings or parent limits changed.")
            if stage is None:
                if draft["state"] != "unavailable" or draft["reason"] not in ("no_supported_facts", "writer_capacity"):
                    raise ValueError("A written draft requires an owned writer stage.")
                if db.execute("SELECT 1 FROM research_stages WHERE run_id=? AND kind='writer' AND state IN ('reserved','uncertain','completed')", (run_id,)).fetchone():
                    raise ValueError("A dispatched writer cannot be replaced by a no-call outcome.")
                db.execute("UPDATE research_stages SET state='failed',output=NULL WHERE run_id=? AND kind='writer' AND state='prepared'", (run_id,))
            else:
                if type(stage) is not ResearchStage or stage.run_id != run_id or stage.claim_token != token:
                    raise ValueError("Use the owned writer stage.")
                record = _row(db, stage)
                if record[2] != "writer" or record[3] != "completed" or record[10] is None:
                    raise ValueError("A completed writer stage is required.")
                spec = json.loads(record[5])
                packet = {key: review[key] for key in ("version", "offset_unit", "lookup", "selection", "sources", "passages")}
                pins = [{key: source[key] for key in ("capture_id", "content_hash", "manifest_hash", "retrieved_at", "stale")}
                        for source in packet["sources"]]
                if (spec["model"] not in provider_state.get("models", [])
                        or spec.get("packet_hash") != evidence_packet_hash(packet)
                        or spec.get("coverage_hash") != coverage_hash(review) or spec["evidence"] != pins):
                    raise ValueError("The writer evidence differs from the owned stage.")
                schema_identity = lambda value: json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
                if (schema_identity(spec["schema"]) != schema_identity(response_schema(assessment, review))
                        or validate_response(json.loads(record[10]), assessment, review) != draft):
                    raise ValueError("The draft differs from the owned writer response.")
            run["draft"] = draft
            safe_stage_content([run, _encoded(run)], provider_state["config"], secrets=secrets)
            if self.clock() >= _date(run["budget"]["deadline"]).timestamp():
                raise ValueError("The research deadline has passed.")
            written = draft["state"] == "unreviewed"
            self._terminal(db, run, "draft" if written else "partial", "draft_recorded" if written else "draft_unavailable")
            return True

    def cancel(self, run_id):
        _identifier(run_id)
        with self._transaction() as db:
            row = self._row(db, run_id)
            if row is None or row[4] == "deleted":
                raise KeyError("Research run not found.")
            if row[4] not in ACTIVE:
                return self._public(db, row)
            self._terminal(db, self._public(db, row), "cancelled", "cancelled")
            return self._public(db, self._row(db, run_id))

    def delete(self, run_id):
        _identifier(run_id)
        with self._transaction() as db:
            row = self._row(db, run_id)
            if row is None:
                raise KeyError("Research run not found.")
            self._purge(db, run_id)
            return True

    def delete_chat(self, chat_id, db):
        with self._transaction(db) as connection:
            for (run_id,) in connection.execute("SELECT id FROM research_runs WHERE chat_id=?", (chat_id,)).fetchall():
                from backend.research_stages import purge
                purge(connection, run_id)
            connection.execute("DELETE FROM research_events WHERE run_id IN (SELECT id FROM research_runs WHERE chat_id=?)",
                               (chat_id,))
            return connection.execute("UPDATE research_runs SET state='deleted',chat_id=NULL,claim_token=NULL,payload='{}',"
                                      "updated_at=? WHERE chat_id=?", (self.stamp(), chat_id)).rowcount

    def expire(self):
        """Release overdue ownership without retrying any lookup or dispatch."""
        with self._transaction() as db:
            selected = db.execute("SELECT id FROM research_runs WHERE state IN ('queued','running') LIMIT 1").fetchone()
            if selected is None:
                return 0
            run = self._public(db, self._row(db, selected[0]))
            if self.clock() < _date(run["budget"]["deadline"]).timestamp():
                return 0
            if not self._chat_exists(db, run["chat_id"]):
                self._purge(db, run["id"])
            else:
                self._terminal(db, run, "failed", "failed", FAILURES["deadline"])
            return 1

    def prepare_stage(self, run_id, claim_token, kind, *, model, schema, assignment, evidence=(),
                      permitted_actions=(), seconds=5, db=None, packet_hash=None, coverage_hash=None):
        from backend.research_stages import prepare
        return prepare(self, run_id, claim_token, kind, model=model, schema=schema, assignment=assignment,
                       evidence=evidence, permitted_actions=permitted_actions, seconds=seconds, db=db,
                       packet_hash=packet_hash, coverage_hash=coverage_hash)

    def authorize_stage(self, stage, workspace, provider_state, db, *, reserved=False):
        from backend.research_stages import authorize
        return authorize(self, stage, workspace, provider_state, db, reserved=reserved)

    def reserve_stage(self, stage, workspace, provider_state, request_id, input_bound, output_bound, db):
        from backend.research_stages import reserve
        return reserve(self, stage, workspace, provider_state, request_id, input_bound, output_bound, db)

    def settle_stage(self, stage, request_id, *, db, input_tokens=0, output_tokens=0, outcome="completed",
                     output=None, workspace=None, provider_state=None, dispatched=True):
        from backend.research_stages import settle
        return settle(self, stage, request_id, db=db, input_tokens=input_tokens, output_tokens=output_tokens,
                      outcome=outcome, output=output, workspace=workspace, provider_state=provider_state,
                      dispatched=dispatched)

    def resolve_unknown(self, request_id, db):
        from backend.research_stages import resolve_unknown
        return resolve_unknown(self, request_id, db)

    def recover(self):
        with self._transaction() as db:
            rows = db.execute("SELECT id FROM research_runs WHERE state IN ('queued','running')").fetchall()
            for (run_id,) in rows:
                run = self._public(db, self._row(db, run_id))
                if not self._chat_exists(db, run["chat_id"]):
                    self._purge(db, run_id)
                else:
                    self._terminal(db, run, "interrupted", "interrupted")
            return len(rows)

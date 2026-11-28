"""Owned acquisition journal shared by explicit Research and hosted quota accounting.

The future acquisition caller must own a run and a named gap. Booking uses the
existing hosted ledger; dispatch and journal ownership are claimed together.
Receiving a response is deliberately separate from publishing reusable evidence.
"""
from dataclasses import dataclass, field
import hashlib
import json
import sqlite3
import uuid
from contextlib import closing

from backend.context_policy import SOURCE_SCREEN_CHARS
from backend.research import AMOUNTS, _date, _encoded, _identifier, _text
from backend.research_requirements import validate_saved_assessment
from backend.research_evidence import validate_saved_review
from backend.research_stages import safe_stage_content
from backend.web_search import ENDPOINT, SEARCH_TIMEOUT, WebSearch, targeted_limits


MAX_SPEC = 32768


@dataclass(frozen=True)
class AcquisitionAttempt:
    run_id: str
    claim_token: str = field(repr=False)
    attempt_id: str


def initialize(db):
    db.execute("CREATE TABLE IF NOT EXISTS acquisition_attempts("
               "id TEXT PRIMARY KEY,run_id TEXT NOT NULL,identity_hash TEXT NOT NULL,scope_hash TEXT NOT NULL,"
               "state TEXT NOT NULL,ledger_id TEXT UNIQUE,spec TEXT,receipt TEXT,refs TEXT,"
               "accounted INTEGER NOT NULL DEFAULT 0,created_at TEXT NOT NULL,updated_at TEXT NOT NULL)")
    db.execute("CREATE INDEX IF NOT EXISTS acquisition_run ON acquisition_attempts(run_id)")
    db.execute("CREATE UNIQUE INDEX IF NOT EXISTS acquisition_active ON acquisition_attempts(identity_hash,scope_hash) "
               "WHERE state IN ('prepared','pending','dispatched','uncertain')")


def _hash(value):
    encoded = json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    _encoded(value, MAX_SPEC)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _charge(store, db, record, run=None):
    """Settle one booked attempt even when its owner can no longer publish."""
    if record["accounted"] != 1:
        return
    caller_saves = run is not None
    row = store._row(db, record["run_id"])
    if row is not None and row[4] != "deleted":
        run = run if run is not None else store._public(db, row)
        budget = run["budget"]
        budget["reserved"]["hosted_searches"] -= 1
        budget["reserved"]["seconds"] -= SEARCH_TIMEOUT
        budget["consumed"]["hosted_searches"] += 1
        store._elapsed(run)
        # A terminal transition owns saving its passed-in run.
        if not caller_saves:
            store._save(db, run, row[4], token=row[7])
    db.execute("UPDATE acquisition_attempts SET accounted=2 WHERE id=?", (record["id"],))


def retire_run(store, db, run, *, purge=False):
    """Fence late work; retain only opaque guards after private deletion."""
    if not store._has_table(db, "acquisition_attempts"):
        return
    journal = AcquisitionJournal(store)
    records = journal._records(db, run["id"])
    for record in records:
        if record["state"] in ("prepared", "pending", "dispatched"):
            state = "uncertain" if record["state"] == "dispatched" else "failed"
            if record["ledger_id"]:
                with journal.search.transaction(db=db) as ledger:
                    entry = next(entry for entry in ledger["ledger"] if entry["id"] == record["ledger_id"])
                    if entry["status"] in ("reserved", "dispatched"):
                        entry["status"] = "uncertain" if state == "uncertain" else "cancelled"
            _charge(store, db, record, run)
            db.execute("UPDATE acquisition_attempts SET state=?,updated_at=? WHERE id=?",
                       (state, store.stamp(), record["id"]))
        if purge:
            db.execute("UPDATE acquisition_attempts SET spec=NULL,receipt=NULL,refs=NULL WHERE id=?", (record["id"],))


class AcquisitionJournal:
    def __init__(self, store):
        self.store = store
        self.search = WebSearch(store.database, store.timezone)

    @staticmethod
    def _record(db, attempt_id):
        cursor = db.execute("SELECT id,run_id,identity_hash,scope_hash,state,ledger_id,"
                            "CASE WHEN length(CAST(spec AS BLOB))<=? THEN spec END AS spec,"
                            "CASE WHEN length(CAST(receipt AS BLOB))<=? THEN receipt END AS receipt,"
                            "CASE WHEN length(CAST(refs AS BLOB))<=? THEN refs END AS refs,"
                            "accounted,created_at,updated_at FROM acquisition_attempts WHERE id=?",
                            (MAX_SPEC, MAX_SPEC, MAX_SPEC, attempt_id))
        row = cursor.fetchone()
        if row is None:
            raise KeyError("Acquisition attempt not found.")
        result = dict(zip((field[0] for field in cursor.description), row))
        for field in ("spec", "receipt", "refs"):
            if result[field] is not None:
                if len(result[field].encode("utf-8")) > MAX_SPEC:
                    raise ValueError("Acquisition record exceeds its bounds.")
                result[field] = json.loads(result[field])
        return result

    def _records(self, db, run_id):
        return [self._record(db, row[0]) for row in db.execute(
            "SELECT id FROM acquisition_attempts WHERE run_id=?", (run_id,)).fetchall()]

    def get(self, attempt_id):
        _identifier(attempt_id)
        with closing(sqlite3.connect(self.store.database.as_uri() + "?mode=ro", uri=True)) as db:
            return self._record(db, attempt_id)

    def _owner(self, db, attempt):
        if not isinstance(attempt, AcquisitionAttempt):
            raise ValueError("Use a server-created acquisition attempt.")
        for value in (attempt.run_id, attempt.claim_token, attempt.attempt_id):
            _identifier(value)
        row = self.store._row(db, attempt.run_id)
        if row is None or row[4] != "running" or row[7] != attempt.claim_token:
            raise ValueError("The acquisition no longer owns this research run.")
        run = self.store._public(db, row)
        if (not self.store._chat_exists(db, run["chat_id"])
                or self.store.clock() >= _date(run["budget"]["deadline"]).timestamp()):
            raise ValueError("The acquisition owner or deadline is unavailable.")
        return run

    def _spec(self, db, run, item_ids, query, max_results, limits=None):
        _text(query, 512)
        if query != query.strip() or type(max_results) is not int or not 1 <= max_results <= 3:
            raise ValueError("Use exact bounded acquisition parameters.")
        if (not isinstance(item_ids, list) or not 1 <= len(item_ids) <= 16
                or any(not isinstance(value, str) for value in item_ids) or len(set(item_ids)) != len(item_ids)):
            raise ValueError("Name the recorded requirement gaps to acquire.")
        assessment = validate_saved_assessment(run["assessment"], run["request"])
        if assessment["decision"] != "ready":
            raise ValueError("Acquisition requires resolved research requirements.")
        items = {item["id"]: item for item in assessment["items"]}
        review = validate_saved_review(run.get("evidence_review"), assessment)
        gaps = {item["item_id"]: item["state"] for item in review["items"]}
        if any(value not in items or gaps.get(value) not in ("missing", "stale", "conflicting", "unavailable") for value in item_ids):
            raise ValueError("Acquisition requires named unmet evidence items.")
        selected = [items[value] for value in sorted(item_ids)]
        subjects = {value for item in selected for value in item["subject_ids"]}
        criteria = {"items": selected, "subjects": [value for value in assessment["subjects"] if value["id"] in subjects],
                    "constraints": [value for value in assessment["constraints"]
                                    if not value["item_ids"] or set(value["item_ids"]) & set(item_ids)]}
        spec = {"provider": ENDPOINT, "query": query, "max_results": max_results, "item_ids": sorted(item_ids),
                "criteria": criteria, "permissions": run["permissions"], "chat_id": run["chat_id"],
                "assessment_hash": _hash(assessment), "review_hash": _hash(review),
                "parent_limits": run["budget"].get("parent_limits")}
        if limits is not None:
            spec["limits"] = targeted_limits(limits)
        config = {}
        if self.store._has_table(db, "provider_state"):
            row = db.execute("SELECT CASE WHEN length(CAST(value AS BLOB))<=? THEN "
                             "json_extract(value,'$.config') END FROM provider_state WHERE id=1",
                             (16 * 1024 ** 2,)).fetchone()
            if row:
                if row[0] is None:
                    raise ValueError("Research provider settings are unavailable.")
                config = json.loads(row[0])
        if config.get("protocol") != "ollama":
            raise ValueError("Research acquisition requires the local Ollama provider.")
        spec["provider_config_hash"] = _hash(config)
        safe_stage_content([spec, _encoded(spec, MAX_SPEC)], config)
        return json.loads(_encoded(spec, MAX_SPEC))

    def prepare(self, run_id, claim_token, *, item_ids, query, max_results=3, limits=None):
        attempt = AcquisitionAttempt(run_id, claim_token, uuid.uuid4().hex)
        with self.store._transaction() as db:
            run = self._owner(db, attempt)
            spec = self._spec(db, run, item_ids, query, max_results, limits)
            identity = _hash({name: spec[name] for name in ("provider", "query", "max_results", "criteria", "limits") if name in spec})
            scope = _hash({name: spec[name] for name in ("permissions", "chat_id")})
            prior = db.execute("SELECT id,run_id,state FROM acquisition_attempts WHERE identity_hash=? AND scope_hash=? "
                               "AND state IN ('prepared','pending','dispatched','uncertain')", (identity, scope)).fetchone()
            if prior:
                if prior[1] == run_id and prior[2] == "prepared":
                    return AcquisitionAttempt(run_id, claim_token, prior[0])
                raise ValueError("An equivalent acquisition is in flight or its completion is uncertain; it will not replay.")
            if db.execute("SELECT count(*) FROM acquisition_attempts WHERE run_id=?", (run_id,)).fetchone()[0] >= 4:
                raise ValueError("This research run reached its acquisition-attempt bound.")
            stamp = self.store.stamp()
            db.execute("INSERT INTO acquisition_attempts(id,run_id,identity_hash,scope_hash,state,spec,created_at,updated_at) "
                       "VALUES(?,?,?,?,?,?,?,?)", (attempt.attempt_id, run_id, identity, scope, "prepared", _encoded(spec, MAX_SPEC), stamp, stamp))
            return attempt

    def _authorize(self, db, attempt, record, *, reserved=False, settled=False):
        run = self._owner(db, attempt)
        if record["run_id"] != attempt.run_id or record["spec"] is None:
            raise ValueError("This acquisition attempt is unavailable.")
        spec = record["spec"]
        if self._spec(db, run, spec["item_ids"], spec["query"], spec["max_results"], spec.get("limits")) != spec:
            raise ValueError("Acquisition requirements, scope or permissions changed.")
        permissions = run["permissions"]
        if (permissions["source_mode"] not in ("research", "refresh") or permissions["hosted_allowed"] is not True
                or permissions["memory_recall"] is not False or permissions["memory_derived"] is not False
                or self.store._chat_memory(db, run["chat_id"])):
            raise ValueError("This research run cannot acquire hosted evidence.")
        if any(value["kind"] == "search" and value["strength"] == "forbid" for value in spec["criteria"]["constraints"]):
            raise ValueError("The recorded request forbids this acquisition.")
        limits = db.execute("SELECT json_extract(value,'$.limits') FROM workspace WHERE id=1").fetchone()
        if not limits or json.loads(limits[0]) != spec["parent_limits"]:
            raise ValueError("Captured research limits changed.")
        self.store._elapsed(run)
        if settled:
            return run
        if db.execute("SELECT 1 FROM research_stages WHERE run_id=? AND state IN ('prepared','reserved','uncertain')",
                      (attempt.run_id,)).fetchone():
            raise ValueError("A local research stage is active or uncertain.")
        budget = run["budget"]
        held = db.execute("SELECT held FROM research_finishing WHERE run_id=?", (run["id"],)).fetchone()
        finishing_seconds = 0 if held else 40
        finishing_calls = 0 if held else 2
        finishing_tokens = 0 if held else 8192
        # Targeted Research must still reassess the acquired saved packet before
        # writing. Recheck that capacity at booking and actual dispatch; time
        # spent waiting on a booking cannot consume it unnoticed.
        reassessment = record["spec"].get("limits") is not None
        if reassessment:
            from backend.research_stages import acquisition_tokens_fit, _states
            held_tokens = json.loads(held[0]) if held else None
            if not acquisition_tokens_fit(run, _states(db)[1]["config"], held_tokens):
                raise ValueError("The known research inputs cannot fit reassessment and finishing capacity.")
        if (any(budget["consumed"][name] + budget["reserved"][name] > budget["total"][name] for name in AMOUNTS)
                or budget["remaining"]["seconds"] < (0 if reserved else SEARCH_TIMEOUT) + finishing_seconds + (10 if reassessment else 0)
                or budget["remaining"]["model_calls"] < finishing_calls + int(reassessment)
                or budget["remaining"]["tokens"] < finishing_tokens + (8192 if reassessment else 0)
                or self.store.clock() + SEARCH_TIMEOUT > _date(budget["deadline"]).timestamp()
                or not reserved and budget["remaining"]["hosted_searches"] < 1
                or budget["total"]["hosted_searches"] > 1):
            raise ValueError("Insufficient shared acquisition or finishing allowance.")
        return run

    def _daily_cap(self, db, run, *, booked=False):
        # Read on the caller's snapshot, including bookings from every path.
        row = db.execute("SELECT value FROM web_search_state WHERE id=1").fetchone()
        if row is None:
            return
        state = json.loads(row[0])
        summary = self.search.summary(state)
        cap = min(20, state["config"]["daily_limit"], run["budget"]["daily_search"]["limit"])
        if summary["searches_today"] > cap - int(not booked):
            raise ValueError("The shared research daily search allowance is exhausted.")
        run["budget"]["daily_search"] = {"limit": cap, "used": summary["searches_today"],
                                           "remaining": max(0, cap - summary["searches_today"])}

    def reserve(self, attempt):
        with self.store._transaction() as db:
            record = self._record(db, attempt.attempt_id)
            if record["state"] != "prepared":
                raise ValueError("This acquisition is already booked or cannot be replayed.")
            run = self._authorize(db, attempt, record)
            self._daily_cap(db, run)
            booking = self.search.reserve(record["spec"]["query"], request_id=attempt.attempt_id, db=db,
                                          limits=record["spec"].get("limits"))
            if booking.max_results != record["spec"]["max_results"]:
                raise ValueError("Acquisition provider parameters changed.")
            run["budget"]["reserved"]["hosted_searches"] += 1
            run["budget"]["reserved"]["seconds"] += SEARCH_TIMEOUT
            self._daily_cap(db, run, booked=True)
            self.store._elapsed(run)
            self.store._save(db, run, "running", token=attempt.claim_token)
            db.execute("UPDATE acquisition_attempts SET state='pending',ledger_id=?,accounted=1,updated_at=? WHERE id=?",
                       (booking.request_id, self.store.stamp(), attempt.attempt_id))
            return booking

    def dispatch(self, attempt, booking):
        # A foreign capability may not cancel the real owner's pending booking
        # merely by causing the dispatch callback to refuse it.
        with self.store._transaction() as db:
            self._owner(db, attempt)
            record = self._record(db, attempt.attempt_id)
            if record["run_id"] != attempt.run_id or record["ledger_id"] != booking.request_id:
                raise ValueError("That hosted booking belongs to another acquisition.")

        def claim(db):
            record = self._record(db, attempt.attempt_id)
            if record["state"] != "pending" or record["ledger_id"] != booking.request_id:
                raise ValueError("This acquisition cannot dispatch again.")
            run = self._authorize(db, attempt, record, reserved=True)
            self._daily_cap(db, run, booked=True)
            if (booking.query != record["spec"]["query"] or booking.max_results != record["spec"]["max_results"]
                    or booking.limits != record["spec"].get("limits")):
                raise ValueError("The hosted booking differs from this acquisition.")
            self.store._save(db, run, "running", token=attempt.claim_token)
            db.execute("UPDATE acquisition_attempts SET state='dispatched',updated_at=? WHERE id=?",
                       (self.store.stamp(), attempt.attempt_id))
            self.store._event(db, attempt.run_id, "acquisition_started")

        try:
            receipt = self.search.dispatch(booking, before_dispatch=claim, finalize=False)
        except BaseException as error:
            # A refused duplicate must never settle another caller's dispatch.
            dispatched = getattr(error, "dispatched", None)
            if dispatched is not False:
                self._finish(attempt, booking, getattr(error, "outcome", "uncertain"))
            else:
                self._finish(attempt, booking, "cancelled", expected="pending")
            raise
        if not self._finish(attempt, booking, "completed", receipt):
            raise ValueError("The acquisition owner changed; its late response was withheld.")
        return receipt

    def _finish(self, attempt, booking, outcome, receipt=None, *, expected="dispatched"):
        with self.store._transaction() as db:
            record = self._record(db, attempt.attempt_id)
            if record["ledger_id"] != booking.request_id or record["run_id"] != attempt.run_id:
                raise ValueError("That hosted outcome belongs to another attempt.")
            if record["state"] != expected:
                return False
            self.search.finalize(booking, outcome, db=db)
            _charge(self.store, db, record)
            saved = None
            state = "failed" if outcome == "cancelled" else outcome
            if receipt is not None:
                try:
                    self._authorize(db, attempt, record, settled=True)
                    candidate = {"query": receipt["query"], "at": receipt["at"],
                                 "sources": [{"title": value["title"], "url": value["url"],
                                              "content_hash": hashlib.sha256(value["content"].encode("utf-8")).hexdigest()}
                                             for value in receipt["sources"]]}
                    # Only accepted metadata may be persisted or authorize a
                    # returned response. Screen its body too after key rotation.
                    if record["spec"].get("limits") is None:
                        safe_stage_content([receipt, _encoded(receipt, 131072), candidate, _encoded(candidate, MAX_SPEC)],
                                           {"protocol": "ollama"})
                    else:
                        # Three bounded sources are screened independently so
                        # JSON escaping does not multiply one source's work by
                        # the entire capture batch. Recheck every body after
                        # transport, including current/retained credentials.
                        _encoded(receipt, record["spec"]["limits"]["max_response_bytes"])
                        for source in receipt["sources"]:
                            safe_stage_content([source, _encoded(source, SOURCE_SCREEN_CHARS)], {"protocol": "ollama"})
                        safe_stage_content([candidate, _encoded(candidate, MAX_SPEC)], {"protocol": "ollama"})
                    saved = candidate
                    state = "response_received"
                except (ValueError, KeyError, OSError):
                    state = "failed"
            db.execute("UPDATE acquisition_attempts SET state=?,receipt=?,updated_at=? WHERE id=?",
                       (state, _encoded(saved, MAX_SPEC) if saved is not None else None, self.store.stamp(), attempt.attempt_id))
            row = self.store._row(db, attempt.run_id)
            if row is not None and row[4] == "running" and row[7] == attempt.claim_token:
                self.store._event(db, attempt.run_id, "acquisition_received" if saved is not None else "acquisition_failed")
            return saved is not None

    def _context(self, context_store):
        from backend.context_store import ContextStore
        if not isinstance(context_store, ContextStore) or context_store.database != self.store.database:
            raise ValueError("Acquisition evidence must belong to this private workspace.")

    def _pins(self, record, references, context_store, retrieved_after):
        self._context(context_store)
        if not isinstance(references, list) or not 1 <= len(references) <= record["spec"]["max_results"]:
            raise ValueError("Use the durable saved evidence references.")
        minimum = _date(retrieved_after)
        sources = {(value["url"], value["title"], value["content_hash"]) for value in record["receipt"]["sources"]}
        seen = set()
        for ref in references:
            if not isinstance(ref, dict) or set(ref) != {"capture_id", "content_hash", "manifest_hash", "retrieved_at"}:
                raise ValueError("Use exact saved-source pins.")
            _identifier(ref["capture_id"])
            if ref["capture_id"] in seen or any(not isinstance(ref[field], str) or len(ref[field]) != 64
                                                or any(char not in "0123456789abcdef" for char in ref[field])
                                                for field in ("content_hash", "manifest_hash")):
                raise ValueError("Use distinct exact saved-source pins.")
            seen.add(ref["capture_id"])
            source = context_store.get_capture(ref["capture_id"], ref["content_hash"], ref["manifest_hash"])
            if ((source["url"], source["title"], ref["content_hash"]) not in sources or source["retrieved_at"] != ref["retrieved_at"]
                    or _date(ref["retrieved_at"]) != _date(record["receipt"]["at"]) or _date(ref["retrieved_at"]) < minimum):
                raise ValueError("Saved acquisition evidence no longer meets its original scope or freshness.")
            # Durable source reads use the supported content ceiling rather
            # than the compact journal metadata specification bound.
            safe_stage_content([source, _encoded(source, SOURCE_SCREEN_CHARS)], {})
        return json.loads(_encoded(references, MAX_SPEC))

    def publish(self, attempt, references, context_store):
        # Archive reads do not run under a private DB write lock.
        record = self.get(attempt.attempt_id)
        if record["state"] != "response_received":
            raise ValueError("No received acquisition is ready to publish.")
        self._context(context_store)
        generation = context_store.policy_generation()
        refs = self._pins(record, references, context_store, record["receipt"]["at"])
        with self.store._transaction() as db:
            if context_store.policy_generation(db) != generation:
                raise ValueError("Archive policy changed before acquisition publication.")
            current = self._record(db, attempt.attempt_id)
            if current != record:
                raise ValueError("Acquisition changed before publication.")
            self._authorize(db, attempt, record, settled=True)
            db.execute("UPDATE acquisition_attempts SET state='completed',refs=?,updated_at=? WHERE id=?",
                       (_encoded(refs, MAX_SPEC), self.store.stamp(), attempt.attempt_id))
            self.store._event(db, attempt.run_id, "acquisition_saved")
            return True

    def reusable(self, attempt, context_store, *, retrieved_after):
        # This first slice deliberately restricts reuse to the same owned run.
        record = self.get(attempt.attempt_id)
        self._context(context_store)
        with self.store._transaction() as db:
            if record["state"] != "completed" or record["spec"] is None or record["run_id"] != attempt.run_id:
                raise ValueError("This acquisition has no reusable published evidence.")
            self._authorize(db, attempt, record, settled=True)
            generation = context_store.policy_generation(db)
        refs = self._pins(record, record["refs"], context_store, retrieved_after)
        with self.store._transaction() as db:
            if context_store.policy_generation(db) != generation:
                raise ValueError("Archive policy changed before acquisition reuse.")
            if self._record(db, attempt.attempt_id) != record:
                raise ValueError("Acquisition changed before reuse.")
            self._authorize(db, attempt, record, settled=True)
        return refs

"""Internal owned local model stages, using the provider's existing writer transaction."""
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import html
import json
import math
import re
from urllib.parse import unquote
import uuid

from backend import credentials
from backend.context_policy import contains_source_secret
from backend.research import AMOUNTS, MAX_PROVIDER_STATE_BYTES, _encoded, _identifier, _result, _text, _date
from backend.web_search import ENDPOINT


KINDS = ("requirements", "evidence_review", "writer", "reviewer")
STAGE_ACTIONS = {"requirements": "emit_requirements", "evidence_review": "assess_evidence",
                 "writer": "write_answer", "reviewer": "review_answer"}
MAX_STAGES = 8
FINISHING_TOKENS = 8192
FINISHING_SECONDS = 5
MAX_SPEC_BYTES = 32768
_JSON_ESCAPE_RUN = re.compile(r'(?:\\(?:["\\/bfnrt]|u[0-9a-fA-F]{4}))+')


@dataclass(frozen=True)
class ResearchStage:
    run_id: str
    claim_token: str
    stage_id: str
    attempt_id: str


def initialize(db):
    db.execute("CREATE TABLE IF NOT EXISTS research_stages("
               "id TEXT PRIMARY KEY,run_id TEXT NOT NULL,attempt_id TEXT NOT NULL UNIQUE,kind TEXT NOT NULL,"
               "state TEXT NOT NULL,request_id TEXT UNIQUE,spec TEXT NOT NULL,config_hash TEXT NOT NULL,"
               "limits_hash TEXT NOT NULL,consumed TEXT NOT NULL,reserved TEXT NOT NULL,output TEXT,"
               "created_at TEXT NOT NULL,updated_at TEXT NOT NULL)")
    db.execute("CREATE INDEX IF NOT EXISTS research_stage_run ON research_stages(run_id)")
    db.execute("CREATE UNIQUE INDEX IF NOT EXISTS research_stage_active ON research_stages(run_id) "
               "WHERE state IN ('prepared','reserved','uncertain')")
    db.execute("CREATE TABLE IF NOT EXISTS research_finishing(run_id TEXT PRIMARY KEY,held TEXT NOT NULL)")
    db.execute("CREATE TABLE IF NOT EXISTS research_stage_scope(run_id TEXT PRIMARY KEY,config_hash TEXT NOT NULL,limits_hash TEXT NOT NULL)")


def _amounts(**values):
    return {**dict.fromkeys(AMOUNTS, 0), **values}


def _hash(value):
    return hashlib.sha256(_encoded(value, MAX_SPEC_BYTES).encode("utf-8")).hexdigest()


def _transaction(db):
    if not db.in_transaction:
        raise ValueError("Research stages require the provider's active transaction.")


def _row(db, stage):
    if not isinstance(stage, ResearchStage):
        raise ValueError("Use a server-created research stage.")
    for value in (stage.run_id, stage.claim_token, stage.stage_id, stage.attempt_id):
        _identifier(value)
    row = db.execute("SELECT run_id,attempt_id,kind,state,request_id,spec,config_hash,limits_hash,consumed,reserved,output,created_at,updated_at "
                     "FROM research_stages WHERE id=?", (stage.stage_id,)).fetchone()
    if row is None or row[0] != stage.run_id or row[1] != stage.attempt_id:
        raise ValueError("That research stage is unavailable.")
    return row


def _owner(store, db, stage):
    row = store._row(db, stage.run_id)
    if row is None or row[4] != "running" or row[7] != stage.claim_token:
        raise ValueError("The research attempt no longer owns this run.")
    run = store._public(db, row)
    if not store._chat_exists(db, run["chat_id"]):
        raise ValueError("The originating chat is unavailable.")
    if store.clock() >= _date(run["budget"]["deadline"]).timestamp():
        raise ValueError("The research deadline has passed.")
    return run


def _states(db):
    records = []
    for table, projection in (("workspace", "json_object('limits',json_extract(value,'$.limits'))"),
                              ("provider_state", "json_object('config',json_extract(value,'$.config'),'models',json_extract(value,'$.models'))")):
        row = db.execute(f"SELECT CASE WHEN typeof(value)='text' AND length(CAST(value AS BLOB))<=? THEN {projection} END "
                         f"FROM {table} WHERE id=1", (MAX_PROVIDER_STATE_BYTES,)).fetchone()
        if row is None or row[0] is None:
            raise ValueError("Research settings are unavailable.")
        value = json.loads(row[0])
        if not isinstance(value, dict):
            raise ValueError("Research settings are unavailable.")
        records.append(value)
    return records


def safe_stage_content(value, config, *, secrets=()):
    endpoints = (ENDPOINT,) if config.get("protocol") == "ollama" else (ENDPOINT, config.get("base_url", ""))
    known = list(secrets)
    for endpoint in endpoints:
        key = credentials.read(endpoint)[0]
        if key:
            known.append(key)
    known = tuple(dict.fromkeys(known))
    if not known:
        return known
    # Shared across original containers and every decoded view. These allow
    # normal maximum context packets while bounding nested/hostile encodings.
    remaining_nodes, remaining_chars, remaining_json_runs = 65536, 4 * 1024 * 1024, 65536
    pending, seen_strings, seen_containers = [(value, 0)], set(), set()
    while pending:
        item, depth = pending.pop()
        remaining_nodes -= 1
        if remaining_nodes < 0 or depth > 32:
            raise ValueError("Research content exceeds credential-screening bounds.")
        if isinstance(item, str):
            if item in seen_strings:
                continue
            seen_strings.add(item)
            remaining_chars -= len(item)
            if remaining_chars < 0:
                raise ValueError("Research content exceeds credential-screening bounds.")
            if contains_source_secret(item, known):
                raise ValueError("Remove credentials from research stage content.")
            # Preserve every intermediate codec view: applying a second codec
            # immediately could erase entity/escape text that belongs to a key.
            for decoded in (unquote(item, errors="replace"), html.unescape(item)):
                if decoded != item and decoded not in seen_strings:
                    pending.append((decoded, depth + 1))
            # Decode only nonoverlapping valid JSON escape runs from this
            # original view. Quoting is unnecessary for substring screening;
            # malformed prose cannot hide a later run. Contiguous UTF-16
            # surrogate escapes stay together for json.loads to combine.
            parts, end = [], 0
            for match in _JSON_ESCAPE_RUN.finditer(item):
                remaining_json_runs -= 1
                remaining_chars -= match.end() - match.start()
                if remaining_json_runs < 0 or remaining_chars < 0:
                    raise ValueError("Research content exceeds credential-screening bounds.")
                parts.extend((item[end:match.start()], json.loads('"' + match.group() + '"')))
                end = match.end()
            if parts:
                parts.append(item[end:])
                decoded = "".join(parts)
                if decoded != item and decoded not in seen_strings:
                    pending.append((decoded, depth + 1))
        elif isinstance(item, (dict, tuple, list)):
            if id(item) in seen_containers:
                continue
            seen_containers.add(id(item))
            width = len(item) * (2 if isinstance(item, dict) else 1)
            if width + len(pending) > remaining_nodes:
                raise ValueError("Research content exceeds credential-screening bounds.")
            if isinstance(item, dict):
                pending.extend((part, depth + 1) for pair in item.items() for part in pair)
            else:
                pending.extend((part, depth + 1) for part in item)
    return known


def _held(db, run_id):
    row = db.execute("SELECT held FROM research_finishing WHERE run_id=?", (run_id,)).fetchone()
    return json.loads(row[0]) if row else None


def _remainder(run):
    budget = run["budget"]
    for name in AMOUNTS:
        budget["remaining"][name] = max(0, budget["total"][name] - budget["consumed"][name] - budget["reserved"][name])


def _change(run, amounts, multiplier):
    for name in AMOUNTS:
        run["budget"]["reserved"][name] = max(0, run["budget"]["reserved"][name] + multiplier * amounts[name])
    _remainder(run)


def prepare(store, run_id, claim_token, kind, *, model, schema, assignment, evidence, permitted_actions, seconds, db=None, packet_hash=None):
    if kind not in KINDS:
        raise ValueError("Use a supported research stage.")
    _text(model, 200)
    _text(assignment, 4000)
    if (not isinstance(schema, dict) or schema.get("type") != "object"
            or type(seconds) not in (int, float) or not math.isfinite(seconds) or not 0 < seconds <= 120
            or not isinstance(evidence, (tuple, list)) or len(evidence) > 20
            or not isinstance(permitted_actions, (tuple, list)) or len(permitted_actions) > 8
            or any(action not in ("return_json", "report_gap", STAGE_ACTIONS[kind]) for action in permitted_actions)):
        raise ValueError("Use a bounded server research assignment.")
    if packet_hash is not None and (not isinstance(packet_hash, str) or not re.fullmatch(r"[a-f0-9]{64}", packet_hash)):
        raise ValueError("Use an exact selected-evidence packet hash.")
    _encoded(schema, 8192)
    references = _result({"lookup_state": "ready", "search_limited": False, "skipped_count": 0,
                          "candidates": list(evidence), "summary": "Server-selected evidence references."})["candidates"]
    with store._transaction(db) as connection:
        identity = ResearchStage(run_id, claim_token, uuid.uuid4().hex, uuid.uuid4().hex)
        run = _owner(store, connection, identity)
        workspace, provider = _states(connection)
        config = provider["config"]
        captured_limits = run["budget"].get("parent_limits")
        if captured_limits is not None and captured_limits != workspace["limits"]:
            raise ValueError("Research parent limits changed after submission.")
        if config.get("protocol") != "ollama" or model not in provider.get("models", []):
            raise ValueError("Research requires a selected installed local model.")
        first = connection.execute("SELECT config_hash,limits_hash FROM research_stage_scope WHERE run_id=?", (run_id,)).fetchone()
        if first is not None and (_hash(config), _hash(workspace["limits"])) != first:
            raise ValueError("Research model settings or parent limits changed.")
        if connection.execute("SELECT 1 FROM research_stages WHERE run_id=? AND state IN ('reserved','uncertain')", (run_id,)).fetchone():
            raise ValueError("Another research stage is active or uncertain.")
        # A budget-rejected preparation has never dispatched. Replacing it
        # cannot spend another call or let its stale identity start later.
        connection.execute("DELETE FROM research_stages WHERE run_id=? AND state='prepared' AND request_id IS NULL", (run_id,))
        held = _held(connection, run_id)
        missing_finishing = set(("writer", "reviewer") if held is None else held)
        available = MAX_STAGES - len(missing_finishing - {kind})
        if connection.execute("SELECT COUNT(*) FROM research_stages WHERE run_id=?", (run_id,)).fetchone()[0] >= available:
            raise ValueError("Research stage allowance reached; finishing slots remain protected.")
        run["permissions"]["memory_derived"] |= store._chat_memory(connection, run["chat_id"])
        spec = {"model": model, "schema": schema, "assignment": assignment, "evidence": references,
                "permitted_actions": list(permitted_actions), "seconds": seconds,
                "permissions": {**run["permissions"], "memory_derived": bool(run["permissions"]["memory_derived"] or store._chat_memory(connection, run["chat_id"]))}}
        if packet_hash is not None:
            spec["packet_hash"] = packet_hash
        encoded_spec = _encoded(spec, MAX_SPEC_BYTES)
        safe_stage_content([{"objective": run["request"], **spec}, encoded_spec], config)
        connection.execute("INSERT OR IGNORE INTO research_stage_scope VALUES(?,?,?)",
                           (run_id, _hash(config), _hash(workspace["limits"])))
        stamp = store.stamp()
        connection.execute("INSERT INTO research_stages VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                           (identity.stage_id, run_id, identity.attempt_id, kind, "prepared", None,
                            encoded_spec, _hash(config), _hash(workspace["limits"]),
                            _encoded(_amounts()), _encoded(_amounts()), None, stamp, stamp))
        store._save(connection, run, "running", token=claim_token)
        return identity


def authorize(store, stage, workspace, provider_state, db, *, reserved=False):
    _transaction(db)
    row = _row(db, stage)
    if row[3] != ("reserved" if reserved else "prepared"):
        raise ValueError("Research stage was already dispatched or is unavailable.")
    run = _owner(store, db, stage)
    config = provider_state["config"]
    spec = json.loads(row[5])
    if (config.get("protocol") != "ollama" or spec["model"] not in provider_state.get("models", [])
            or _hash(config) != row[6] or _hash(workspace["limits"]) != row[7]):
        raise ValueError("Research model settings or parent limits changed.")
    current_memory = bool(run["permissions"]["memory_derived"] or spec["permissions"]["memory_derived"]
                          or store._chat_memory(db, run["chat_id"]))
    permissions = {**run["permissions"], "memory_derived": current_memory}
    if (permissions["source_mode"] != "saved_only" or permissions["hosted_allowed"] is not False
            or permissions["memory_recall"] is not False):
        raise ValueError("Research permissions changed.")
    if current_memory and not run["permissions"]["memory_derived"]:
        run["permissions"]["memory_derived"] = True
        store._save(db, run, "running", token=stage.claim_token)
    store._elapsed(run)
    deadline = _date(run["budget"]["deadline"]).timestamp()
    if reserved:
        deadline = min(deadline, _date(row[12]).timestamp() + spec["seconds"])
    if store.clock() >= deadline:
        raise ValueError("Research stage reached its deadline.")
    packet = {"objective": run["request"], "assignment": spec["assignment"], "allowance": run["budget"],
              "permitted_actions": spec["permitted_actions"], "permissions": permissions,
              "model": spec["model"], "schema": spec["schema"], "evidence": spec["evidence"],
              "deadline": datetime.fromtimestamp(deadline, timezone.utc).isoformat()}
    held = _held(db, stage.run_id)
    finishing_slot = spec.get("finishing_slot", False) if reserved else row[2] in (("writer", "reviewer") if held is None else held)
    packet.update(kind=row[2], finishing_token_limit=FINISHING_TOKENS if finishing_slot else None)
    safe_stage_content(packet, config)
    _encoded(packet, 131072)
    return packet


def reserve(store, stage, workspace, provider_state, request_id, input_bound, output_bound, db):
    packet = authorize(store, stage, workspace, provider_state, db)
    _identifier(request_id)
    if any(type(value) is not int or not 0 <= value <= 10000000 for value in (input_bound, output_bound)) or output_bound == 0:
        raise ValueError("Use bounded research token reservations.")
    row = _row(db, stage)
    run = _owner(store, db, stage)
    store._elapsed(run)
    held = _held(db, stage.run_id)
    if held is None:
        held = {kind: _amounts(model_calls=1, tokens=FINISHING_TOKENS, seconds=FINISHING_SECONDS)
                for kind in ("writer", "reviewer")}
        for amounts in held.values():
            _change(run, amounts, 1)
    if row[2] in held:
        slot = held.pop(row[2])
        if input_bound + output_bound > slot["tokens"] or json.loads(row[5])["seconds"] > slot["seconds"]:
            raise ValueError("This finishing stage exceeds its saved finishing capacity.")
        _change(run, slot, -1)
        spec = json.loads(row[5])
        spec["finishing_slot"] = True
        db.execute("UPDATE research_stages SET spec=? WHERE id=?", (_encoded(spec, MAX_SPEC_BYTES), stage.stage_id))
    amounts = _amounts(model_calls=1, tokens=input_bound + output_bound, seconds=json.loads(row[5])["seconds"])
    if any(run["budget"]["consumed"][name] + run["budget"]["reserved"][name] + amounts[name] > run["budget"]["total"][name]
           for name in AMOUNTS):
        raise ValueError("Research allowance cannot cover this stage and finishing capacity.")
    _change(run, amounts, 1)
    db.execute("INSERT INTO research_finishing VALUES(?,?) ON CONFLICT(run_id) DO UPDATE SET held=excluded.held",
               (stage.run_id, _encoded(held)))
    db.execute("UPDATE research_stages SET state='reserved',request_id=?,reserved=?,updated_at=? WHERE id=?",
               (request_id, _encoded(amounts), store.stamp(), stage.stage_id))
    store._save(db, run, "running", token=stage.claim_token)
    store._event(db, stage.run_id, "stage_reserved")
    return authorize(store, stage, workspace, provider_state, db, reserved=True)


def settle(store, stage, request_id, *, db, input_tokens, output_tokens, outcome, output, workspace, provider_state, dispatched):
    _transaction(db)
    if outcome not in ("completed", "failed", "uncertain") or type(dispatched) is not bool:
        raise ValueError("Use a recorded research stage outcome.")
    row = _row(db, stage)
    if row[4] != request_id or row[3] not in ("reserved", "uncertain"):
        return False
    if outcome == "uncertain":
        if row[3] == "reserved":
            db.execute("UPDATE research_stages SET state='uncertain',updated_at=? WHERE id=?", (store.stamp(), stage.stage_id))
            runrow = store._row(db, stage.run_id)
            if runrow is not None and runrow[4] != "deleted":
                store._event(db, stage.run_id, "stage_uncertain")
        return False
    if any(type(value) is not int or value < 0 for value in (input_tokens, output_tokens)):
        raise ValueError("Research token usage is unknown.")
    reserved = json.loads(row[9])
    actual = _amounts(model_calls=int(dispatched), tokens=input_tokens + output_tokens)
    accepted = False
    encoded_output = None
    if outcome == "completed" and workspace is not None and provider_state is not None:
        try:
            packet = authorize(store, stage, workspace, provider_state, db, reserved=True)
            if not isinstance(output, dict):
                raise ValueError()
            encoded_output = _encoded(output, MAX_SPEC_BYTES)
            safe_stage_content([output, encoded_output], provider_state["config"])
            if store.clock() >= _date(packet["deadline"]).timestamp():
                raise ValueError("Research stage reached its deadline before output acceptance.")
            accepted = True
        except (ValueError, KeyError, TypeError, RuntimeError):
            pass
    db.execute("UPDATE research_stages SET state=?,consumed=?,reserved=?,output=?,updated_at=? WHERE id=?",
               ("completed" if accepted else "failed", _encoded(actual), _encoded(_amounts()),
                encoded_output if accepted else None, store.stamp(), stage.stage_id))
    runrow = store._row(db, stage.run_id)
    if runrow is not None and runrow[4] != "deleted":
        run = store._public(db, runrow)
        _change(run, reserved, -1)
        if (outcome == "failed" and not dispatched and row[3] == "reserved"
                and runrow[4] == "running" and runrow[7] == stage.claim_token
                and json.loads(row[5]).get("finishing_slot") and store._chat_exists(db, run["chat_id"])
                and store.clock() < _date(run["budget"]["deadline"]).timestamp()):
            held = _held(db, stage.run_id) or {}
            if row[2] not in held:
                # A model check did not use this promised finishing call.
                # Keep its full capacity protected from optional stages.
                held[row[2]] = _amounts(model_calls=1, tokens=FINISHING_TOKENS, seconds=FINISHING_SECONDS)
                _change(run, held[row[2]], 1)
                db.execute("INSERT INTO research_finishing VALUES(?,?) ON CONFLICT(run_id) DO UPDATE SET held=excluded.held",
                           (stage.run_id, _encoded(held)))
        for name in AMOUNTS:
            if name != "seconds":
                run["budget"]["consumed"][name] += actual[name]
        run["permissions"]["memory_derived"] |= store._chat_memory(db, run["chat_id"])
        store._elapsed(run)
        store._save(db, run, runrow[4], token=runrow[7])
        store._event(db, stage.run_id, "stage_finished" if accepted else "stage_failed")
    return accepted


def resolve_unknown(store, request_id, db):
    _transaction(db)
    row = db.execute("SELECT id,run_id,attempt_id,reserved,state FROM research_stages WHERE request_id=?", (request_id,)).fetchone()
    if row is None or row[4] not in ("reserved", "uncertain"):
        return False
    runrow = store._row(db, row[1])
    token = runrow[7] if runrow is not None and runrow[7] else "0" * 32
    identity = ResearchStage(row[1], token, row[0], row[2])
    amounts = json.loads(row[3])
    settle(store, identity, request_id, db=db, input_tokens=amounts["tokens"], output_tokens=0,
           outcome="failed", output=None, workspace=None, provider_state=None, dispatched=True)
    return True


def release_finishing(store, db, run):
    held = _held(db, run["id"])
    if held is not None:
        for amounts in held.values():
            _change(run, amounts, -1)
        db.execute("DELETE FROM research_finishing WHERE run_id=?", (run["id"],))
    db.execute("UPDATE research_stages SET state='failed',updated_at=? WHERE run_id=? AND state='prepared'",
               (store.stamp(), run["id"]))


def purge(db, run_id):
    db.execute("DELETE FROM research_finishing WHERE run_id=?", (run_id,))
    db.execute("DELETE FROM research_stage_scope WHERE run_id=?", (run_id,))
    db.execute("UPDATE research_stages SET spec='{}',output=NULL WHERE run_id=?", (run_id,))

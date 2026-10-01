"""One provider connection, real chat, and a private persistent usage ledger."""
from contextlib import closing, contextmanager
from datetime import datetime
import json
import sqlite3
import uuid
from urllib.parse import urlsplit

import httpx

from backend import credentials

DEFAULT = {"base_url": "https://api.openai.com/v1", "protocol": "responses", "model": "",
           "input_usd_per_million": 0.0, "output_usd_per_million": 0.0,
           "pricing_verified": False, "max_output_tokens": 1024}
INSTRUCTIONS = "You are Maestro, a helpful personal assistant. Be clear and concise. Do not claim to have performed actions or accessed tools that are not available."


class ProviderFailure(ValueError):
    def __init__(self, message, may_be_billed=True):
        super().__init__(message)
        self.may_be_billed = may_be_billed


def validate_url(url):
    try:
        parts = urlsplit(url)
        parts.port  # Validate malformed and out-of-range ports before dispatch.
    except ValueError:
        raise ValueError("Enter a valid API base URL and port.") from None
    if parts.username or parts.password or parts.query or parts.fragment or not parts.hostname:
        raise ValueError("Use an API base URL without credentials, query parameters or fragments.")
    if parts.scheme != "https" and not (parts.scheme == "http" and parts.hostname in ("127.0.0.1", "localhost", "::1")):
        raise ValueError("Use HTTPS for remote providers or HTTP for a loopback model server.")
    return url.rstrip("/")


def network(config, key, method, path, payload=None):
    # No retries or redirects: never forward a credential to another endpoint.
    try:
        with httpx.Client(timeout=60, follow_redirects=False, trust_env=False) as client:
            response = client.request(method, config["base_url"] + path,
                                      headers={"Authorization": "Bearer " + key}, json=payload)
    except httpx.InvalidURL:
        raise ProviderFailure("Enter a valid API base URL.", False) from None
    except httpx.HTTPError:
        raise ValueError("Provider connection failed or timed out. Check the endpoint and your connection.") from None
    if response.status_code in (401, 403):
        raise ProviderFailure("Provider rejected access. Check the API key and model permissions.", False)
    if response.status_code == 429:
        raise ProviderFailure("Provider limit reached. Check quota, billing and rate limits.", False)
    if response.status_code >= 300:
        raise ProviderFailure(f"Provider returned HTTP {response.status_code}. Check the model, endpoint and API format.", response.status_code >= 500)
    try:
        data = response.json()
        if not isinstance(data, dict):
            raise ValueError()
        return data
    except (ValueError, TypeError):
        raise ValueError("Provider returned an invalid response.") from None


class Provider:
    def __init__(self, database, timezone):
        self.database, self.timezone = database, timezone

    def stamp(self):
        return datetime.now(self.timezone).isoformat()

    @contextmanager
    def transaction(self, with_db=False):
        with closing(sqlite3.connect(self.database, timeout=10)) as db, db:
            db.execute("PRAGMA secure_delete=ON")
            db.execute("CREATE TABLE IF NOT EXISTS provider_state (id INTEGER PRIMARY KEY CHECK(id=1), value TEXT NOT NULL)")
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT value FROM provider_state WHERE id=1").fetchone()
            state = json.loads(row[0]) if row else {"config": DEFAULT.copy(), "models": [], "tested_at": None, "ledger": []}
            yield (state, db) if with_db else state
            db.execute("INSERT INTO provider_state VALUES(1,?) ON CONFLICT(id) DO UPDATE SET value=excluded.value", (json.dumps(state),))

    def status(self):
        state = self.read_state()
        key, source = credentials.read(state["config"]["base_url"])
        return {"config": state["config"], "credentials_present": bool(key), "credential_source": source,
                "models": state["models"], "tested_at": state["tested_at"]}

    def read_state(self):
        with closing(sqlite3.connect(self.database)) as db:
            try:
                row = db.execute("SELECT value FROM provider_state WHERE id=1").fetchone()
                if row:
                    return json.loads(row[0])
            except sqlite3.OperationalError:
                pass
        return {"config": DEFAULT.copy(), "models": [], "tested_at": None, "ledger": []}

    def configure(self, config, key, persist):
        config["base_url"] = validate_url(config["base_url"])
        with self.transaction() as state:
            if key:
                credentials.save(config["base_url"], key, persist)
            state.update({"config": config, "models": [], "tested_at": None})
        return self.status()

    def test(self):
        status = self.status()
        config = status["config"]
        key, _ = credentials.read(config["base_url"])
        if not key:
            raise ValueError("Add an API key in Settings first.")
        data = network(config, key, "GET", "/models")
        if not isinstance(data.get("data"), list):
            raise ValueError("Provider does not expose an OpenAI-compatible model list. Enter a model ID manually.")
        models = sorted({item["id"] for item in data["data"] if isinstance(item, dict) and isinstance(item.get("id"), str)})
        with self.transaction() as state:
            if state["config"] != config:
                raise ValueError("Connection settings changed. Test the new connection again.")
            state.update({"models": models, "tested_at": self.stamp()})
        return self.status()

    def usage(self):
        entries = self.read_state()["ledger"]
        now = datetime.now(self.timezone)
        today = [x for x in entries if datetime.fromisoformat(x["at"]).astimezone(self.timezone).date() == now.date()]
        month = [x for x in entries if datetime.fromisoformat(x["at"]).astimezone(self.timezone).strftime("%Y-%m") == now.strftime("%Y-%m")]
        return {"today_usd": sum(x["cost"] for x in today), "month_usd": sum(x["cost"] for x in month),
                "input_tokens": sum(x.get("input_tokens", 0) for x in today), "output_tokens": sum(x.get("output_tokens", 0) for x in today),
                "calls": len(today), "reserved_usd": sum(x["cost"] for x in entries if x["status"] in ("reserved", "uncertain")),
                "uncertain": [{"id": x["id"], "at": x["at"], "reserved_usd": x["cost"]} for x in entries if x["status"] == "uncertain"]}

    def recover(self):
        with self.transaction() as state:
            for entry in state["ledger"]:
                if entry["status"] == "reserved":
                    entry["status"] = "uncertain"

    def reconcile(self, entry_id, cost):
        with self.transaction() as state:
            entry = next((x for x in state["ledger"] if x["id"] == entry_id and x["status"] == "uncertain"), None)
            if not entry:
                raise ValueError("No unresolved charge with that ID.")
            entry.update({"status": "reconciled", "cost": cost})

    def chat(self, text):
        # Reserve before dispatch under the same SQLite lock used for tasks and limits.
        with self.transaction() as state, closing(sqlite3.connect(self.database)) as db:
            config = state["config"].copy()
            key, _ = credentials.read(config["base_url"])
            if not key or not config["model"]:
                raise ValueError("Configure an API key and model in Settings before chatting.")
            if not config["pricing_verified"] or min(config["input_usd_per_million"], config["output_usd_per_million"]) < 0:
                raise ValueError("Verify the model's input/output prices in Settings first.")
            if any(x["status"] in ("reserved", "uncertain") for x in state["ledger"]):
                raise ValueError("A request is running or has unknown charges. Wait or reconcile its usage before sending again.")
            workspace = json.loads(db.execute("SELECT value FROM workspace WHERE id=1").fetchone()[0])
            history = [{"role": x["role"], "content": x["text"]} for x in workspace["messages"] if not x.get("demo", False)]
            history.append({"role": "user", "content": text})
            input_bound = len((INSTRUCTIONS + json.dumps(history)).encode("utf-8")) + 2048
            output_bound = config["max_output_tokens"]
            if input_bound + output_bound > workspace["limits"]["max_tokens"]:
                raise ValueError("This conversation exceeds your token limit. Start a new chat or adjust the limit.")
            reserve = (input_bound * config["input_usd_per_million"] + output_bound * config["output_usd_per_million"]) / 1000000
            now = datetime.now(self.timezone)
            daily = sum(x["cost"] for x in state["ledger"] if datetime.fromisoformat(x["at"]).astimezone(self.timezone).date() == now.date())
            monthly = sum(x["cost"] for x in state["ledger"] if datetime.fromisoformat(x["at"]).astimezone(self.timezone).strftime("%Y-%m") == now.strftime("%Y-%m"))
            limits = workspace["limits"]
            if limits["run_usd"] <= 0 or limits["daily_usd"] <= 0 or limits["monthly_usd"] <= 0 or reserve > limits["run_usd"] or daily + reserve > limits["daily_usd"] or monthly + reserve > limits["monthly_usd"]:
                raise ValueError("This request would exceed your spending limit. Adjust limits in Settings.")
            request_id = uuid.uuid4().hex
            state["ledger"].append({"id": request_id, "at": self.stamp(), "model": config["model"], "cost": reserve, "status": "reserved"})
        payload = {"model": config["model"], "store": False}
        if config["protocol"] == "responses":
            payload.update({"instructions": INSTRUCTIONS, "input": history, "max_output_tokens": output_bound})
            path = "/responses"
        else:
            cap_name = "max_completion_tokens" if config["base_url"] == "https://api.openai.com/v1" else "max_tokens"
            payload.update({"messages": [{"role": "system", "content": INSTRUCTIONS}, *history], cap_name: output_bound})
            path = "/chat/completions"
        try:
            data = network(config, key, "POST", path, payload)
            raw_usage = data.get("usage", {})
            fields = ("input_tokens", "output_tokens") if config["protocol"] == "responses" else ("prompt_tokens", "completion_tokens")
            if any(type(raw_usage.get(k)) is not int or raw_usage[k] < 0 for k in fields):
                raise ValueError("Provider did not return valid token usage; charges need reconciliation.")
            input_tokens, output_tokens = (raw_usage[k] for k in fields)
            cost = (input_tokens * config["input_usd_per_million"] + output_tokens * config["output_usd_per_million"]) / 1000000
            with self.transaction(with_db=True) as (state, db):
                entry = next(x for x in state["ledger"] if x["id"] == request_id)
                entry.update({"status": "settled", "cost": cost, "input_tokens": input_tokens, "output_tokens": output_tokens})
                if config["protocol"] == "responses":
                    reply_parts = []
                    for item in data.get("output", []) if isinstance(data.get("output"), list) else []:
                        if not isinstance(item, dict) or item.get("type") != "message" or not isinstance(item.get("content"), list):
                            continue
                        for part in item["content"]:
                            if isinstance(part, dict) and part.get("type") in ("output_text", "refusal"):
                                text_part = part.get("text", part.get("refusal", ""))
                                if isinstance(text_part, str):
                                    reply_parts.append(text_part)
                    reply = "".join(reply_parts)
                else:
                    choices = data.get("choices")
                    first = choices[0] if isinstance(choices, list) and choices and isinstance(choices[0], dict) else {}
                    message = first.get("message")
                    reply = message.get("content", "") if isinstance(message, dict) else ""
                if not isinstance(reply, str):
                    reply = ""
                # Persist the actual exchange and accounting atomically.
                workspace = json.loads(db.execute("SELECT value FROM workspace WHERE id=1").fetchone()[0])
                if reply:
                    workspace["messages"].extend([{"id": uuid.uuid4().hex, "role": "user", "text": text, "demo": False},
                        {"id": uuid.uuid4().hex, "role": "assistant", "text": reply, "demo": False, "model": config["model"], "cost": cost, "input_tokens": input_tokens, "output_tokens": output_tokens}])
                    db.execute("UPDATE workspace SET value=? WHERE id=1", (json.dumps(workspace),))
            if not reply:
                raise ValueError("Provider returned no text. Usage was recorded; try a larger output limit or another chat model.")
            if input_tokens > input_bound or output_tokens > output_bound or cost > reserve:
                with self.transaction() as state:
                    state["config"]["pricing_verified"] = False
            return reply
        except Exception as error:
            with self.transaction() as state:
                entry = next(x for x in state["ledger"] if x["id"] == request_id)
                if entry["status"] == "reserved":
                    if isinstance(error, ProviderFailure) and not error.may_be_billed:
                        entry.update({"status": "failed", "cost": 0})
                    else:
                        entry["status"] = "uncertain"
            if isinstance(error, ValueError):
                raise
            raise ValueError("Provider returned an unusable response. Check usage before retrying.") from None

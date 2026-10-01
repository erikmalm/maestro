"""One provider connection, real chat, and a private persistent usage ledger."""
from contextlib import closing, contextmanager
from datetime import datetime
import json
import sqlite3
import uuid
from urllib.parse import urlsplit

import httpx

from backend import credentials
from backend.web_search import WebSearch, TOOL, SEARCH_INSTRUCTIONS, fit_sources

DEFAULT = {"base_url": "https://api.openai.com/v1", "protocol": "responses", "model": "",
           "input_usd_per_million": 0.0, "output_usd_per_million": 0.0,
           "pricing_verified": False, "max_output_tokens": 1024,
           "ollama_context_tokens": 4096, "ollama_threads": 0, "ollama_keep_alive_minutes": 5}
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


def validate_ollama_url(url):
    url = validate_url(url)
    parts = urlsplit(url)
    if parts.hostname not in ("127.0.0.1", "localhost", "::1") or parts.path:
        raise ValueError("Local Ollama requires a loopback server root URL, for example http://127.0.0.1:11434.")
    return url


def is_local_model(model):
    details = model.get("details", {})
    name = model.get("name", "")
    capabilities = model.get("capabilities")
    return (not model.get("remote_host") and not model.get("remote_model")
            and not name.endswith((":cloud", "-cloud"))
            and isinstance(details, dict) and details.get("format") == "gguf"
            and (capabilities is None or (isinstance(capabilities, list) and "completion" in capabilities)))


def network(config, key, method, path, payload=None):
    # No retries or redirects: never forward a credential to another endpoint.
    local = config["protocol"] == "ollama"
    if local:
        validate_ollama_url(config["base_url"])
    try:
        timeout = httpx.Timeout(180, connect=5) if local else 60
        with httpx.Client(timeout=timeout, follow_redirects=False, trust_env=False) as client:
            response = client.request(method, config["base_url"] + path,
                                      headers={} if local else {"Authorization": "Bearer " + key}, json=payload)
    except httpx.InvalidURL:
        raise ProviderFailure("Enter a valid API base URL.", False) from None
    except httpx.HTTPError:
        if local:
            raise ProviderFailure("Ollama is unavailable or timed out. Start Ollama and check its local server URL.", False) from None
        raise ValueError("Provider connection failed or timed out. Check the endpoint and your connection.") from None
    if response.status_code in (401, 403):
        raise ProviderFailure("Provider rejected access. Check the API key and model permissions.", False)
    if response.status_code == 429:
        # Only classify documented codes; never display the provider's raw message or body.
        try:
            body = response.json()
            error = body.get("error", {}) if isinstance(body, dict) else {}
            code = error.get("code") if isinstance(error, dict) else None
            kind = error.get("type") if isinstance(error, dict) else None
        except ValueError:
            code = kind = None
        messages = {
            "credit_balance_exhausted": "Provider API credits are exhausted. Check API billing and credit balance.",
            "project_spend_limit_exceeded": "Provider project spending limit reached. Check the project's API limits.",
            "organization_spend_limit_exceeded": "Provider organization spending limit reached. Check the organization's API limits.",
            "organization_usage_limit_exceeded": "Provider organization API usage limit reached. Check the approved usage limit.",
            "insufficient_quota": "Provider API quota is unavailable. Check API billing, credits and project limits.",
            "rate_limit_exceeded": "Provider request rate limit reached. Wait before sending another message.",
            "slow_down": "Provider requested slower traffic. Wait before sending another message.",
        }
        message = messages.get(code) if isinstance(code, str) else None
        if not message and kind == "insufficient_quota":
            message = messages["insufficient_quota"]
        if not message and kind == "rate_limit_error":
            message = messages["rate_limit_exceeded"]
        raise ProviderFailure(message or "Provider limit reached. Check quota, billing and rate limits.", False)
    if response.status_code >= 300:
        raise ProviderFailure(f"Provider returned HTTP {response.status_code}. Check the model, endpoint and API format.", response.status_code >= 500)
    try:
        data = response.json()
        if not isinstance(data, dict):
            raise ValueError()
        return data
    except (ValueError, TypeError):
        raise ValueError("Provider returned an invalid response.") from None


def local_usage(data):
    if data.get("done") is not True:
        raise ValueError("Ollama did not complete the response. Try a shorter reply or check the model.")
    fields = ("prompt_eval_count", "eval_count")
    if any(type(data.get(field)) is not int or data[field] < 0 for field in fields):
        raise ValueError("Ollama did not return valid token usage. Check the local model before retrying.")
    return tuple(data[field] for field in fields)


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
            state["config"] = {**DEFAULT, **state["config"]}
            yield (state, db) if with_db else state
            db.execute("INSERT INTO provider_state VALUES(1,?) ON CONFLICT(id) DO UPDATE SET value=excluded.value", (json.dumps(state),))

    def status(self):
        state = self.read_state()
        required = state["config"]["protocol"] != "ollama"
        key, source = credentials.read(state["config"]["base_url"]) if required else (None, "not_required")
        return {"config": state["config"], "credentials_present": bool(key), "credential_source": source,
                "credentials_required": required, "models": state["models"], "tested_at": state["tested_at"]}

    def read_state(self):
        with closing(sqlite3.connect(self.database)) as db:
            try:
                row = db.execute("SELECT value FROM provider_state WHERE id=1").fetchone()
                if row:
                    state = json.loads(row[0])
                    state["config"] = {**DEFAULT, **state["config"]}
                    return state
            except sqlite3.OperationalError:
                pass
        return {"config": DEFAULT.copy(), "models": [], "tested_at": None, "ledger": []}

    def configure(self, config, key, persist):
        config["base_url"] = validate_url(config["base_url"])
        if config["protocol"] == "ollama":
            config["base_url"] = validate_ollama_url(config["base_url"])
            if key:
                raise ValueError("Local Ollama does not use an API key. Leave the key empty.")
            config.update(input_usd_per_million=0, output_usd_per_million=0, pricing_verified=True)
        with self.transaction() as state:
            # Model-list access depends on the endpoint/key, not the chat model or API format.
            connection_changed = bool(key) or config["base_url"] != state["config"]["base_url"] or ((config["protocol"] == "ollama") != (state["config"]["protocol"] == "ollama"))
            if key:
                credentials.save(config["base_url"], key, persist)
            state["config"] = config
            if connection_changed:
                state.update({"models": [], "tested_at": None})
        return self.status()

    def test(self):
        status = self.status()
        config = status["config"]
        if config["protocol"] == "ollama":
            data = network(config, None, "GET", "/api/tags")
            if not isinstance(data.get("models"), list):
                raise ValueError("Ollama did not return an installed model list.")
            models = sorted({item["name"] for item in data["models"] if isinstance(item, dict)
                             and isinstance(item.get("name"), str) and is_local_model(item)})
        else:
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
                "calls": sum(x.get("model_calls", 1) for x in today), "reserved_usd": sum(x["cost"] for x in entries if x["status"] in ("reserved", "uncertain")),
                "uncertain": [{"id": x["id"], "at": x["at"], "reserved_usd": x["cost"]} for x in entries if x["status"] == "uncertain"]}

    def recover(self):
        with self.transaction() as state:
            for entry in state["ledger"]:
                if entry["status"] == "reserved":
                    entry["status"] = "failed" if entry.get("protocol") == "ollama" else "uncertain"

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
            local = config["protocol"] == "ollama"
            auto_search = local and WebSearch(self.database, self.timezone).read_state()["config"]["enabled"]
            instructions = INSTRUCTIONS + (SEARCH_INSTRUCTIONS if auto_search else "")
            if auto_search and config["ollama_context_tokens"] < 8192:
                raise ValueError("Automatic web search needs at least 8192 context tokens. Update Local worker settings or disable search.")
            key, _ = (None, "not_required") if local else credentials.read(config["base_url"])
            if not config["model"]:
                raise ValueError("Choose a chat model in Settings before chatting.")
            if not local and not key:
                raise ValueError("Configure an API key and model in Settings before chatting.")
            if not config["pricing_verified"] or min(config["input_usd_per_million"], config["output_usd_per_million"]) < 0:
                raise ValueError("Verify the model's input/output prices in Settings first.")
            if any(x["status"] in ("reserved", "uncertain") for x in state["ledger"]):
                raise ValueError("A request is running or has unknown charges. Wait or reconcile its usage before sending again.")
            workspace = json.loads(db.execute("SELECT value FROM workspace WHERE id=1").fetchone()[0])
            history = [{"role": x["role"], "content": x["text"]} for x in workspace["messages"] if not x.get("demo", False)]
            history.append({"role": "user", "content": text})
            input_bound = len((instructions + json.dumps(history) + (json.dumps(TOOL) if auto_search else "")).encode("utf-8")) + 2048
            output_bound = config["max_output_tokens"]
            if input_bound + output_bound > workspace["limits"]["max_tokens"]:
                raise ValueError("This conversation exceeds your token limit. Start a new chat or adjust the limit.")
            if local and input_bound + output_bound > config["ollama_context_tokens"]:
                raise ValueError("This conversation exceeds the local worker's conservative context allowance. Start a new chat, lower the output limit or increase context size in Settings.")
            reserve = (input_bound * config["input_usd_per_million"] + output_bound * config["output_usd_per_million"]) / 1000000
            now = datetime.now(self.timezone)
            daily = sum(x["cost"] for x in state["ledger"] if datetime.fromisoformat(x["at"]).astimezone(self.timezone).date() == now.date())
            monthly = sum(x["cost"] for x in state["ledger"] if datetime.fromisoformat(x["at"]).astimezone(self.timezone).strftime("%Y-%m") == now.strftime("%Y-%m"))
            limits = workspace["limits"]
            if not local and (limits["run_usd"] <= 0 or limits["daily_usd"] <= 0 or limits["monthly_usd"] <= 0 or reserve > limits["run_usd"] or daily + reserve > limits["daily_usd"] or monthly + reserve > limits["monthly_usd"]):
                raise ValueError("This request would exceed your spending limit. Adjust limits in Settings.")
            request_id = uuid.uuid4().hex
            state["ledger"].append({"id": request_id, "at": self.stamp(), "model": config["model"], "protocol": config["protocol"], "cost": reserve, "status": "reserved"})
        payload = {"model": config["model"], "store": False}
        search_result = None
        if config["base_url"] == "https://api.openai.com/v1":
            payload["service_tier"] = "default"  # Match the standard price estimates, independent of project defaults.
        if local:
            options = {"num_predict": output_bound, "num_ctx": config["ollama_context_tokens"],
                       "num_thread": config["ollama_threads"]}
            keep_alive = config["ollama_keep_alive_minutes"]
            payload = {"model": config["model"], "messages": [{"role": "system", "content": instructions}, *history],
                       "stream": False, "options": options, "keep_alive": f"{keep_alive}m" if keep_alive else 0}
            if auto_search:
                payload["tools"] = [TOOL]
            path = "/api/chat"
        elif config["protocol"] == "responses":
            payload.update({"instructions": INSTRUCTIONS, "input": history, "max_output_tokens": output_bound})
            path = "/responses"
        else:
            cap_name = "max_completion_tokens" if config["base_url"] == "https://api.openai.com/v1" else "max_tokens"
            payload.update({"messages": [{"role": "system", "content": INSTRUCTIONS}, *history], cap_name: output_bound})
            path = "/chat/completions"
        try:
            if local:
                metadata = network(config, None, "POST", "/api/show", {"model": config["model"]})
                if (not is_local_model({**metadata, "name": config["model"]})
                        or not isinstance(metadata.get("capabilities"), list)
                        or "completion" not in metadata["capabilities"]):
                    raise ProviderFailure("Choose an installed local chat model. Ollama cloud models are disabled in local mode.", False)
                if auto_search and "tools" not in metadata["capabilities"]:
                    raise ProviderFailure("This local model does not support automatic search tools. Choose a tool-capable model or disable search.", False)
            data = network(config, key, "POST", path, payload)
            if auto_search:
                data, search_result = self.search_turn(config, payload, data, request_id, workspace["limits"])
            raw_usage = data if local else data.get("usage", {})
            fields = ("prompt_eval_count", "eval_count") if local else (("input_tokens", "output_tokens") if config["protocol"] == "responses" else ("prompt_tokens", "completion_tokens"))
            if local and data.get("done") is not True:
                raise ValueError("Ollama did not complete the response. Try a shorter reply or check the model.")
            if not isinstance(raw_usage, dict) or any(type(raw_usage.get(k)) is not int or raw_usage[k] < 0 for k in fields):
                if local:
                    raise ValueError("Ollama did not return valid token usage. Check the local model before retrying.")
                raise ValueError("Provider did not return valid token usage; charges need reconciliation.")
            input_tokens, output_tokens = (raw_usage[k] for k in fields)
            cost = (input_tokens * config["input_usd_per_million"] + output_tokens * config["output_usd_per_million"]) / 1000000
            with self.transaction(with_db=True) as (state, db):
                entry = next(x for x in state["ledger"] if x["id"] == request_id)
                entry.update({"status": "settled", "cost": cost, "input_tokens": input_tokens, "output_tokens": output_tokens})
                if local:
                    message = data.get("message")
                    reply = message.get("content", "") if isinstance(message, dict) else ""
                elif config["protocol"] == "responses":
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
                    if search_result:
                        workspace["messages"][-1]["web_search"] = search_result
                    db.execute("UPDATE workspace SET value=? WHERE id=1", (json.dumps(workspace),))
            if not reply:
                raise ValueError("Provider returned no text. Usage was recorded; try a larger output limit or another chat model.")
            if not local and (input_tokens > input_bound or output_tokens > output_bound or cost > reserve):
                with self.transaction() as state:
                    state["config"]["pricing_verified"] = False
            return reply
        except Exception as error:
            with self.transaction() as state:
                entry = next(x for x in state["ledger"] if x["id"] == request_id)
                if entry["status"] == "reserved":
                    if local or (isinstance(error, ProviderFailure) and not error.may_be_billed):
                        entry.update({"status": "failed", "cost": 0})
                    else:
                        entry["status"] = "uncertain"
            if isinstance(error, ValueError):
                raise
            raise ValueError("Provider returned an unusable response. Check usage before retrying.") from None

    def search_turn(self, config, payload, data, request_id, limits):
        first_input, first_output = local_usage(data)
        with self.transaction() as state:
            entry = next(x for x in state["ledger"] if x["id"] == request_id)
            entry.update(input_tokens=first_input, output_tokens=first_output, model_calls=1)
        message = data.get("message", {})
        calls = message.get("tool_calls", []) if isinstance(message, dict) else []
        if not calls:
            return data, None
        if not isinstance(calls, list) or len(calls) != 1 or not isinstance(calls[0], dict):
            raise ValueError("Only one web search is allowed per message. The model requested too many tools.")
        function = calls[0].get("function")
        args = function.get("arguments") if isinstance(function, dict) else None
        if (not isinstance(function, dict) or function.get("name") != "web_search"
                or not isinstance(args, dict) or set(args) != {"query"}
                or not isinstance(args["query"], str) or not 1 <= len(args["query"].strip()) <= 512):
            raise ValueError("The model returned an unsupported search tool or query. No tool was executed.")
        remaining = config["max_output_tokens"] - first_output
        if remaining <= 0:
            raise ValueError("The model used the reply token allowance before search. Increase the output limit.")
        assistant = {"role": "assistant", "content": message.get("content", "") if isinstance(message.get("content", ""), str) else "",
                     "tool_calls": [{"function": {"name": "web_search", "arguments": args}}]}
        final_messages = [*payload["messages"], assistant, {"role": "tool", "tool_name": "web_search", "content": ""}]
        base_bound = len(json.dumps(final_messages, ensure_ascii=False).encode("utf-8")) + 2048
        source_budget = min(4096, config["ollama_context_tokens"] - base_bound - remaining,
                            limits["max_tokens"] - first_input - first_output - base_bound - remaining)
        if source_budget < 512:
            raise ValueError("Search results would exceed the conversation or token allowance. Start a new chat or increase the local context/token limit.")
        result = WebSearch(self.database, self.timezone).search(args["query"])
        fitted = fit_sources(result["sources"], source_budget)
        final_messages[-1]["content"] = json.dumps(fitted, ensure_ascii=False)
        final_bound = len(json.dumps(final_messages, ensure_ascii=False).encode("utf-8")) + 2048
        with self.transaction(with_db=True) as (state, db):
            current_limits = json.loads(db.execute("SELECT value FROM workspace WHERE id=1").fetchone()[0])["limits"]
            if (final_bound + remaining > config["ollama_context_tokens"]
                    or first_input + first_output + final_bound + remaining > current_limits["max_tokens"]):
                raise ValueError("The search-assisted answer would exceed your token/context limit. No further model call was made.")
            next(x for x in state["ledger"] if x["id"] == request_id)["model_calls"] = 2
        final_payload = {**payload, "messages": final_messages, "options": {**payload["options"], "num_predict": remaining}}
        final_payload.pop("tools", None)  # No second search/tool round, regardless of model output.
        final = network(config, None, "POST", "/api/chat", final_payload)
        second_input, second_output = local_usage(final)
        final["prompt_eval_count"], final["eval_count"] = first_input + second_input, first_output + second_output
        with self.transaction() as state:
            next(x for x in state["ledger"] if x["id"] == request_id).update(input_tokens=final["prompt_eval_count"], output_tokens=final["eval_count"])
        if isinstance(final.get("message"), dict) and final["message"].get("tool_calls"):
            raise ValueError("The one-search-per-message limit was reached. No additional tool was executed; usage was recorded.")
        return final, {"query": result["query"], "at": result["at"],
                       "sources": [{"title": item["title"], "url": item["url"]} for item in fitted]}

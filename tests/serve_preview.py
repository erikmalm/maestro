"""Serve browser tests with a temporary workspace outside the checkout."""

import os
from pathlib import Path
import sys
import tempfile
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import uvicorn

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
with tempfile.TemporaryDirectory(prefix="maestro-browser-") as directory:
    assert Path(directory).resolve().parent == Path(tempfile.gettempdir()).resolve()
    os.environ["MAESTRO_DATA_DIR"] = directory
    os.environ["MAESTRO_PORT"] = "8777"
    os.environ.pop("OPENAI_API_KEY", None)  # Browser checks must never inherit a paid provider credential.
    from backend import app as backend, credentials

    # Synthetic HTTP provider, so browser tests exercise the full network path without a paid LLM.
    class FixtureProvider(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def reply(self, status, data):
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(data).encode())

        def authorized(self):
            if self.headers.get("Authorization") != "Bearer synthetic-browser-key":
                self.reply(401, {"error": "Invalid synthetic key"})
                return False
            return True

        def do_GET(self):
            if self.path == "/api/tags":
                if self.headers.get("Authorization"):
                    return self.reply(400, {"error": "Unexpected local credential"})
                return self.reply(200, {"models": [{"name": "synthetic-ollama:latest",
                    "details": {"format": "gguf"}, "capabilities": ["completion"]}]})
            if self.authorized():
                self.reply(200, {"data": [{"id": "synthetic-browser-model"}]})

        def do_POST(self):
            if self.path in ("/api/show", "/api/chat"):
                if self.headers.get("Authorization"):
                    return self.reply(400, {"error": "Unexpected local credential"})
                request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                if self.path == "/api/show":
                    return self.reply(200, {"details": {"format": "gguf"}, "capabilities": ["completion"]})
                history = [message for message in request["messages"] if message["role"] != "system"]
                reply = "Synthetic Ollama received: " + history[-1]["content"]
                if len(history) > 1:
                    reply += ". Earlier message: " + history[0]["content"]
                return self.reply(200, {"done": True, "message": {"role": "assistant", "content": reply},
                    "prompt_eval_count": 100, "eval_count": 20})
            if not self.authorized():
                return
            request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            history = request.get("input", [])
            reply = "Synthetic HTTP provider received: " + history[-1]["content"]
            if len(history) > 1:
                reply += ". Earlier message: " + history[0]["content"]
            self.reply(200, {"status": "completed", "output": [{"type": "message", "content": [{"type": "output_text", "text": reply}]}], "usage": {"input_tokens": 1000, "output_tokens": 200}})

    fake = ThreadingHTTPServer(("127.0.0.1", 0), FixtureProvider)
    threading.Thread(target=fake.serve_forever, daemon=True).start()
    # Isolate test credentials from any real key in this Windows account.
    credentials.read = lambda url: (credentials.session_keys.get(url), "session" if url in credentials.session_keys else "missing")
    credentials.save = lambda url, key, persist: credentials.session_keys.update({url: key})
    credentials.delete = lambda url: credentials.session_keys.pop(url, None)

    @backend.app.get("/api/provider-fixture")
    def fixture_url():
        return {"base_url": f"http://127.0.0.1:{fake.server_port}/v1", "ollama_url": f"http://127.0.0.1:{fake.server_port}"}
    # Keep the test-only fixture route before the production static-file catch-all.
    backend.app.router.routes.insert(0, backend.app.router.routes.pop())

    try:
        uvicorn.run(backend.app, host="127.0.0.1", port=8777, log_level="error", access_log=False)
    finally:
        fake.shutdown()
        fake.server_close()

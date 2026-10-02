"""Opt-in Podman integration: python tests/verify_container.py <built-image> [--tunnel].

Creates unique containers/volumes, uses synthetic Ollama, and removes only those
resources. Run on Windows with a running Podman machine; no model/key is needed.
"""
import http.cookiejar
import json
from pathlib import Path
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid

ROOT = Path(__file__).resolve().parent.parent
MODELS = ["synthetic-chat", "synthetic-orchestrator"]
FIXTURE = '''
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
class Ollama(BaseHTTPRequestHandler):
    def log_message(self, *args): pass
    def do_GET(self):
        self.reply({"models": [{"name": name, "details": {"format": "gguf"}, "capabilities": ["completion", "tools"]}
                               for name in ("synthetic-chat", "synthetic-orchestrator")]})
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        assert body["model"] in ("synthetic-chat", "synthetic-orchestrator")
        assert not self.headers.get("Authorization")
        if self.path == "/api/show":
            return self.reply({"details": {"format": "gguf"}, "capabilities": ["completion", "tools"]})
        assert self.path == "/api/chat"
        title = body["messages"][0]["content"].startswith("Create a short")
        self.reply({"done": True, "message": {"content": "Synthetic model choices" if title else "Reply from " + body["model"]},
                    "prompt_eval_count": 20 if title else 100, "eval_count": 5 if title else 20})
    def reply(self, body):
        encoded = json.dumps(body).encode()
        self.send_response(200); self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded))); self.end_headers(); self.wfile.write(encoded)
HTTPServer(("127.0.0.1", 11434), Ollama).serve_forever()
'''


def podman(*arguments, check=True, input=None):
    result = subprocess.run(["podman", *arguments], input=input, capture_output=True, text=True)
    if check and result.returncode:
        raise RuntimeError(f"Podman {arguments[0]} failed: {result.stderr[-1000:]}")
    return result


def verify(image, tunnel=False):
    name = "maestro-verify-" + uuid.uuid4().hex[:10]
    volume, restored, fixture_name = name + "-data", name + "-restored", name + "-ollama"
    restored_volume = restored + "-data"
    secrets = {name + "-provider-key": "synthetic-container-provider-key", name + "-search-key": "synthetic-container-search-key"}
    created_secrets = []
    endpoint = "https://synthetic.invalid/v1"
    for kind, resources in (("container", (name, restored, fixture_name)), ("volume", (volume, restored_volume))):
        assert all(podman(kind, "exists", resource, check=False).returncode == 1 for resource in resources), "Verification names already exist"
    with socket.socket() as available, socket.socket() as ollama_port:
        available.bind(("127.0.0.1", 0))
        ollama_port.bind(("127.0.0.1", 0))
        port = available.getsockname()[1]
        tunnel_port = ollama_port.getsockname()[1]
    base = f"http://127.0.0.1:{port}"
    browser = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()), urllib.request.ProxyHandler({}))
    fixture = None

    def launch(target=name, data=volume, action="start", check=True, mounted=False):
        options = ["-Tunnel", "-OllamaTunnelPort", str(tunnel_port)] if tunnel else []
        if mounted:
            options += ["-ProviderSecret", name + "-provider-key", "-SearchSecret", name + "-search-key", "-ApiKeyUrl", endpoint + "/"]
        result = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(ROOT / "scripts/container.ps1"),
                        "-Action", action, "-Name", target, "-Volume", data, "-Image", image, "-Port", str(port),
                        "-OllamaUrl", "http://127.0.0.1:11434", *options], capture_output=True, text=True)
        if check and result.returncode:
            raise RuntimeError(f"Container {action} failed: {result.stderr[-1000:]}")
        return result

    def api(path, payload=None, method=None, headers=None, expected=200):
        request = urllib.request.Request(base + path, data=json.dumps(payload).encode() if payload is not None else None,
                                         method=method, headers={"Content-Type": "application/json", **(headers or {})})
        try:
            response = browser.open(request, timeout=10)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            assert response.status == expected, f"{path}: expected HTTP {expected}, got {response.status}"
            body = response.read()
            assert all(key.encode() not in body for key in (*secrets.values(), "synthetic-replacement-key")), "API response exposed a synthetic key"
            return json.loads(body) if "application/json" in response.headers.get("Content-Type", "") else body

    def session():
        return {"X-Maestro-CSRF": api("/api/session")["csrf"]}

    try:
        launch()
        info = json.loads(podman("inspect", name).stdout)[0]
        host = info["HostConfig"]
        assert host["ReadonlyRootfs"] and host["Memory"] == 1073741824
        assert host.get("NanoCpus", 0) == 2000000000 or host["CpuQuota"] / host["CpuPeriod"] == 2
        assert info["Config"]["User"] == "1000:1000" and info["Config"].get("Healthcheck")
        private = podman("exec", name, "python", "-c", "import os; print(os.getuid(), oct(os.stat('/data').st_mode & 0o777))").stdout.strip()
        assert private == "1000 0o700"
        assert b"Maestro" in api("/") and b"<svg" in api("/favicon.svg")
        api("/api/workspace", expected=401)
        api("/health", headers={"Host": "synthetic.invalid"}, expected=400)
        api("/api/session", headers={"Origin": "https://synthetic.invalid"}, expected=403)
        headers = session()
        api("/api/tasks", {"title": "Blocked task"}, expected=403)
        task = api("/api/tasks", {"title": "Synthetic persisted task"}, headers=headers)["tasks"][0]
        config = {"base_url": "http://127.0.0.1:11434", "protocol": "ollama", "model": MODELS[0],
                  "orchestrator_model": MODELS[1], "max_output_tokens": 256, "ollama_keep_alive_minutes": 0}
        configured = api("/api/provider", {"config": config}, method="PUT", headers=headers)
        assert not configured["persist_supported"] and not configured["credentials_required"]
        fixture = subprocess.Popen(["podman", "run", "--rm", "--interactive", "--pull", "never", "--name", fixture_name,
                                    "--network", "container:" + name, image, "python", "-u", "-"],
                                   stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
        fixture.stdin.write(FIXTURE)
        fixture.stdin.close()
        for attempt in range(30):
            probe = podman("exec", name, "python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:11434/api/tags',timeout=1)", check=False)
            if probe.returncode == 0:
                break
            assert fixture.poll() is None, "Synthetic Ollama failed to start"
            time.sleep(0.2)
        else:
            raise AssertionError("Synthetic Ollama did not become ready")
        assert api("/api/provider/test", {}, headers=headers)["models"] == MODELS
        for choices in ({}, {"role": "orchestrator"}, {"role": "orchestrator", "model": MODELS[0]}):
            state = api("/api/chat", {"text": "Synthetic model choice", **choices}, headers=headers)
        replies = [message for message in state["messages"] if message["role"] == "assistant"]
        assert [(message["kind"], message["model"]) for message in replies] == [("chat", MODELS[0]), ("orchestrator", MODELS[1]), ("orchestrator", MODELS[0])]
        assert (state["usage"]["input_tokens"], state["usage"]["output_tokens"], state["usage"]["calls"], state["usage"]["today_usd"]) == (320, 65, 4, 0)
        second = podman("exec", name, "python", "-c", "from backend.storage import workspace_owner; workspace_owner('/data').__enter__()", check=False)
        assert second.returncode != 0 and "already running" in second.stderr
        podman("exec", name, "python", "-m", "backend.storage", "/data/workspace.sqlite3", "/data/verify-backup.sqlite3")
        assert podman("exec", name, "python", "-c", "import os; print(oct(os.stat('/data/verify-backup.sqlite3').st_mode & 0o777))").stdout.strip() == "0o600"
        podman("rm", "--force", fixture_name)
        fixture.wait(timeout=15)
        launch(action="stop")
        launch()
        headers = session()
        assert api("/api/workspace")["messages"] == state["messages"]
        launch(action="stop")
        podman("rm", name)
        launch()
        headers = session()
        retained = api("/api/workspace")
        assert retained["tasks"][0] == task and retained["messages"] == state["messages"]
        assert retained["provider"]["config"] == state["provider"]["config"] and retained["usage"] == state["usage"]
        launch(action="stop")
        podman("rm", name)
        podman("volume", "create", "--label", "io.maestro.managed=container.ps1", "--uid", "1000", "--gid", "1000", restored_volume)
        podman("run", "--rm", "--pull", "never", "--volume", volume + ":/source:ro", "--volume", restored_volume + ":/data:U",
               image, "python", "-m", "backend.storage", "/source/verify-backup.sqlite3", "/data/workspace.sqlite3")
        for secret_name, synthetic_key in secrets.items():
            podman("secret", "create", "--label", "io.maestro.managed=verify_container.py", secret_name, "-", input=synthetic_key)
            created_secrets.append(secret_name)
        launch(restored, restored_volume, mounted=True)
        headers = session()
        recovered = api("/api/workspace")
        assert recovered["tasks"][0] == task and recovered["messages"] == state["messages"] and recovered["usage"] == state["usage"]
        remote_config = {**recovered["provider"]["config"], "base_url": endpoint, "protocol": "responses", "orchestrator_model": ""}
        api("/api/provider", {"config": remote_config}, method="PUT", headers=headers)
        for path, config in (("/api/provider", remote_config), ("/api/web-search", recovered["web_search"]["config"])):
            status = api(path)
            assert status["credentials_present"] and status["credential_source"] == "mounted secret"
            assert status["managed_credentials"] and not status["persist_supported"]
            api(path, {"config": config, "api_key": "synthetic-replacement-key", "persist": False}, method="PUT", headers=headers, expected=409)
            api(path + "/key", method="DELETE", headers=headers, expected=409)
        podman("exec", restored, "python", "-c", "from pathlib import Path; db=Path('/data/workspace.sqlite3').read_bytes(); assert all(Path(p).read_bytes().strip() not in db for p in ('/run/secrets/maestro-provider-key','/run/secrets/maestro-search-key'))")
        print("Podman integration passed: isolation, model choices, persistence, recreation, backup/restore and mounted secrets.")
    finally:
        podman("rm", "--force", fixture_name, check=False)
        for container, data in ((restored, restored_volume), (name, volume)):
            stopped = launch(container, data, action="stop", check=False)
            if stopped.returncode:
                print(f"Cleanup stop failed: {stopped.stderr[-1000:]}", file=sys.stderr)
            podman("rm", "--force", container, check=False)
        if fixture:
            fixture.wait(timeout=15)
            fixture.stderr.close()
        for owned_volume in (restored_volume, volume):
            podman("volume", "rm", owned_volume, check=False)
        for owned_secret in created_secrets:
            podman("secret", "rm", owned_secret, check=False)


if __name__ == "__main__":
    if len(sys.argv) not in (2, 3) or len(sys.argv) == 3 and sys.argv[2] != "--tunnel":
        raise SystemExit("Usage: python tests/verify_container.py <built-image> [--tunnel]")
    verify(sys.argv[1], tunnel=len(sys.argv) == 3)

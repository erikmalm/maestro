"""Exercise native socket ownership and browser security with disposable servers."""

from contextlib import contextmanager
from http.cookiejar import CookieJar
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPCookieProcessor, ProxyHandler, Request, build_opener
import uuid


ROOT = Path(__file__).resolve().parent.parent

# The production server remains real. A test-only file signal requests graceful
# shutdown without depending on a Windows console being attached to the runner.
BOOTSTRAP = r"""
import os
from pathlib import Path
import runpy
import threading
import time
import httpx
import uvicorn
from backend import credentials

credentials.read = lambda url: (None, 'missing')
def blocked(*args, **kwargs):
    Path(os.environ['MAESTRO_TEST_NETWORK_MARKER']).write_text('unexpected outbound request')
    raise AssertionError('Native lifecycle tests must not call a provider or search service.')
async def blocked_async(*args, **kwargs):
    return blocked(*args, **kwargs)
httpx.HTTPTransport.handle_request = blocked
httpx.AsyncHTTPTransport.handle_async_request = blocked_async
original_run = uvicorn.Server.run
def controlled_run(server, *args, **kwargs):
    def watch():
        while not Path(os.environ['MAESTRO_TEST_STOP_FILE']).exists():
            time.sleep(0.02)
        server.should_exit = True
    threading.Thread(target=watch, daemon=True).start()
    return original_run(server, *args, **kwargs)
uvicorn.Server.run = controlled_run
runpy.run_module('backend.native_server', run_name='__main__')
"""


class NativeServerTests(unittest.TestCase):
    def environment(self, directory, **overrides):
        return {
            **os.environ,
            "MAESTRO_DATA_DIR": str(directory / "private workspace"),
            "MAESTRO_CONTEXT_ARCHIVE_DIR": "",
            "MAESTRO_LAUNCH_ID": uuid.uuid4().hex,
            "MAESTRO_PORT": "0",
            "MAESTRO_TIMEZONE": "Europe/Stockholm",
            "OPENAI_API_KEY": "",
            "MAESTRO_API_KEY_FILE": "",
            "MAESTRO_SEARCH_KEY_FILE": "",
            "MAESTRO_OLLAMA_URL": "",
            "MAESTRO_TEST_STOP_FILE": str(directory / "stop"),
            "MAESTRO_TEST_NETWORK_MARKER": str(directory / "outbound-request"),
            **overrides,
        }

    def run_rejected(self, directory, arguments, **environment):
        env = self.environment(directory, **environment)
        result = subprocess.run(
            [sys.executable, "-c", BOOTSTRAP, *arguments],
            cwd=ROOT, env=env, capture_output=True, text=True, timeout=15,
        )
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse((Path(env["MAESTRO_DATA_DIR"]) / "workspace.sqlite3").exists())
        self.assertFalse(Path(env["MAESTRO_TEST_NETWORK_MARKER"]).exists())
        return result, env

    @contextmanager
    def running(self, arguments=(), **environment):
        with tempfile.TemporaryDirectory(prefix="maestro native server ") as temporary:
            directory = Path(temporary)
            env = self.environment(directory, **environment)
            handshake = Path(env["MAESTRO_DATA_DIR"]) / f"server-address-{env['MAESTRO_LAUNCH_ID']}.json"
            process = subprocess.Popen(
                [sys.executable, "-c", BOOTSTRAP, *arguments],
                cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            )
            opener = build_opener(ProxyHandler({}), HTTPCookieProcessor(CookieJar()))
            ready = False
            try:
                deadline = time.monotonic() + 15
                while time.monotonic() < deadline and process.poll() is None:
                    try:
                        address = json.loads(handshake.read_text(encoding="utf-8"))
                        self.assertEqual(address["launch_id"], env["MAESTRO_LAUNCH_ID"])
                        self.assertIs(type(address["port"]), int)
                        self.assertGreater(address["port"], 0)
                        self.assertLessEqual(address["port"], 65535)
                        base = f"http://127.0.0.1:{address['port']}"
                        with opener.open(base + "/health", timeout=0.3) as response:
                            health = json.load(response)
                        self.assertEqual(health["application"], "maestro")
                        self.assertEqual(health["launch_id"], env["MAESTRO_LAUNCH_ID"])
                        ready = True
                        break
                    except (OSError, ValueError, URLError):
                        time.sleep(0.03)
                if not ready:
                    self.fail("Native server failed to publish a healthy loopback address.")
                yield base, opener, env
            finally:
                Path(env["MAESTRO_TEST_STOP_FILE"]).touch()
                try:
                    stdout, stderr = process.communicate(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    stdout, stderr = process.communicate(timeout=5)
                    self.fail("Native server did not shut down: " + stdout + stderr)
                if ready:
                    self.assertEqual(process.returncode, 0, stdout + stderr)
                    self.assertFalse(handshake.exists(), "Graceful shutdown left its address handshake.")
                else:
                    # Include startup diagnostics if readiness failed, without
                    # replacing a failure from the test body after readiness.
                    self.assertEqual(process.returncode, 0, stdout + stderr)
                self.assertFalse(Path(env["MAESTRO_TEST_NETWORK_MARKER"]).exists())

    def request(self, opener, url, *, method="GET", headers=None, body=None):
        request = Request(url, method=method, headers=headers or {}, data=body)
        with opener.open(request, timeout=5) as response:
            return json.load(response)

    def test_os_assigned_port_accepts_its_browser_origin_and_preserves_security(self):
        with self.running() as (base, opener, env):
            session = self.request(opener, base + "/api/session", headers={"Origin": base})
            workspace = self.request(opener, base + "/api/workspace", headers={"Origin": base})
            self.assertEqual(workspace["chats"], [])
            created = self.request(
                opener, base + "/api/chats", method="POST", body=b"",
                headers={"Origin": base, "X-Maestro-CSRF": session["csrf"]},
            )
            self.assertEqual(len(created["chats"]), 1)
            self.assertIsNotNone(created["active_chat_id"])

            with self.assertRaises(HTTPError) as wrong_origin:
                self.request(opener, base + "/api/workspace", headers={"Origin": "http://127.0.0.1:1"})
            self.assertEqual(wrong_origin.exception.code, 403)
            wrong_origin.exception.close()
            with self.assertRaises(HTTPError) as no_csrf:
                self.request(opener, base + "/api/chats", method="POST", body=b"", headers={"Origin": base})
            self.assertEqual(no_csrf.exception.code, 403)
            no_csrf.exception.close()

            anonymous = build_opener(ProxyHandler({}))
            with self.assertRaises(HTTPError) as no_session:
                self.request(anonymous, base + "/api/workspace", headers={"Origin": base})
            self.assertEqual(no_session.exception.code, 401)
            no_session.exception.close()
            self.assertTrue((Path(env["MAESTRO_DATA_DIR"]) / "workspace.sqlite3").is_file())

    def test_parallel_previews_receive_distinct_ports_and_workspace_identities(self):
        with self.running() as (first_base, first, _):
            with self.running() as (second_base, second, _):
                self.assertNotEqual(first_base, second_base)
                first_health = self.request(first, first_base + "/health")
                second_health = self.request(second, second_base + "/health")
                self.assertNotEqual(first_health["workspace_id"], second_health["workspace_id"])
                self.assertNotEqual(first_health["launch_id"], second_health["launch_id"])

    def test_parallel_previews_keep_shared_browser_sessions_and_csrf_isolated(self):
        jar = CookieJar()
        browser = build_opener(ProxyHandler({}), HTTPCookieProcessor(jar))
        with self.running() as (first_base, _, _):
            with self.running() as (second_base, _, _):
                vite_origin = "http://127.0.0.1:5173"
                vite_headers = {"Origin": vite_origin, "Host": "127.0.0.1:5173"}
                first_session = self.request(browser, first_base + "/api/session", headers=vite_headers)
                second_session = self.request(browser, second_base + "/api/session", headers={"Origin": second_base})
                # One real browser shares cookies across ports. Initializing the
                # second preview must leave the first authenticated.
                for base, session, title in (
                    (first_base, first_session, "First native preview task"),
                    (second_base, second_session, "Second native preview task"),
                ):
                    self.assertEqual(self.request(browser, base + "/api/workspace")["tasks"], [])
                    created = self.request(browser, base + "/api/tasks", method="POST",
                        headers={"Origin": base, "Content-Type": "application/json", "X-Maestro-CSRF": session["csrf"]},
                        body=json.dumps({"title": title}).encode())
                    self.assertEqual([task["title"] for task in created["tasks"]], [title])

                first_name = f"maestro_session_{urlsplit(first_base).port}"
                second_name = f"maestro_session_{urlsplit(second_base).port}"
                self.assertEqual({cookie.name for cookie in jar}, {first_name, second_name})
                self.request(browser, first_base + "/api/session", headers=vite_headers)
                self.assertEqual(len(self.request(browser, first_base + "/api/workspace", headers=vite_headers)["tasks"]), 1)
                self.assertEqual(len(self.request(browser, second_base + "/api/workspace")["tasks"]), 1)

                with self.assertRaises(HTTPError) as foreign_csrf:
                    self.request(browser, first_base + "/api/tasks", method="POST",
                        headers={"Origin": first_base, "Content-Type": "application/json", "X-Maestro-CSRF": second_session["csrf"]},
                        body=b'{"title":"Foreign CSRF must fail"}')
                self.assertEqual(foreign_csrf.exception.code, 403)
                foreign_csrf.exception.close()
                foreign_jar = CookieJar()
                foreign_jar.set_cookie(next(cookie for cookie in jar if cookie.name == second_name))
                foreign_browser = build_opener(ProxyHandler({}), HTTPCookieProcessor(foreign_jar))
                with self.assertRaises(HTTPError) as foreign_cookie:
                    self.request(foreign_browser, first_base + "/api/workspace")
                self.assertEqual(foreign_cookie.exception.code, 401)
                foreign_cookie.exception.close()

    def test_same_port_restart_rotates_session_without_invalidating_another_preview(self):
        jar = CookieJar()
        browser = build_opener(ProxyHandler({}), HTTPCookieProcessor(jar))
        with self.running() as (other_base, _, _):
            self.request(browser, other_base + "/api/session")
            with self.running() as (original_base, _, _):
                old_session = self.request(browser, original_base + "/api/session")
                port = urlsplit(original_base).port
                old_cookie_values = {cookie.name: cookie.value for cookie in jar}
            with self.running(("--port", str(port))) as (restarted_base, _, _):
                self.assertEqual(restarted_base, original_base)
                with self.assertRaises(HTTPError) as old_cookie:
                    self.request(browser, restarted_base + "/api/workspace")
                self.assertEqual(old_cookie.exception.code, 401)
                old_cookie.exception.close()
                fresh_session = self.request(browser, restarted_base + "/api/session")
                self.assertEqual(self.request(browser, restarted_base + "/api/workspace")["tasks"], [])
                self.assertEqual(self.request(browser, other_base + "/api/workspace")["tasks"], [])
                name = f"maestro_session_{port}"
                self.assertNotEqual(next(cookie.value for cookie in jar if cookie.name == name), old_cookie_values[name])
                with self.assertRaises(HTTPError) as old_csrf:
                    self.request(browser, restarted_base + "/api/tasks", method="POST",
                        headers={"Origin": restarted_base, "Content-Type": "application/json", "X-Maestro-CSRF": old_session["csrf"]},
                        body=b'{"title":"Old CSRF must fail"}')
                self.assertEqual(old_csrf.exception.code, 403)
                old_csrf.exception.close()
                created = self.request(browser, restarted_base + "/api/tasks", method="POST",
                    headers={"Origin": restarted_base, "Content-Type": "application/json", "X-Maestro-CSRF": fresh_session["csrf"]},
                    body=b'{"title":"Fresh restarted preview task"}')
                self.assertEqual(created["tasks"][0]["title"], "Fresh restarted preview task")

    def test_explicit_port_overrides_invalid_environment_default(self):
        with self.running(("--port", "0"), MAESTRO_PORT="invalid") as (base, opener, _):
            self.assertEqual(self.request(opener, base + "/health")["application"], "maestro")

    def test_occupied_port_refuses_before_initializing_workspace(self):
        with tempfile.TemporaryDirectory(prefix="maestro occupied port ") as temporary:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
                listener.bind(("127.0.0.1", 0))
                listener.listen()
                port = listener.getsockname()[1]
                self.run_rejected(Path(temporary), ("--port", str(port)))
                self.assertEqual(listener.getsockname()[1], port)

    def test_invalid_ports_and_launch_id_refuse_before_initializing_workspace(self):
        for port in ("-1", "65536", "invalid"):
            with self.subTest(port=port), tempfile.TemporaryDirectory(prefix="maestro invalid port ") as temporary:
                self.run_rejected(Path(temporary), ("--port", port))
        with tempfile.TemporaryDirectory(prefix="maestro invalid launch ") as temporary:
            self.run_rejected(Path(temporary), ("--port", "0"), MAESTRO_LAUNCH_ID="invalid")

    def test_existing_address_handshake_is_not_overwritten(self):
        with tempfile.TemporaryDirectory(prefix="maestro address collision ") as temporary:
            directory = Path(temporary)
            launch_id = uuid.uuid4().hex
            env = self.environment(directory, MAESTRO_LAUNCH_ID=launch_id)
            private = Path(env["MAESTRO_DATA_DIR"])
            private.mkdir()
            handshake = private / f"server-address-{launch_id}.json"
            handshake.write_text("existing record", encoding="utf-8")
            self.run_rejected(directory, ("--port", "0"), MAESTRO_LAUNCH_ID=launch_id)
            self.assertEqual(handshake.read_text(encoding="utf-8"), "existing record")


if __name__ == "__main__":
    unittest.main()

"""A loopback-only browser interface to the same persistent workflow."""

import json
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from urllib.parse import parse_qs, urlsplit

from .cancellation import ModelCancellation
from .config import RoutedAgent, load_settings
from .discovery import SelectionCancelled, SelectionRequired, select_backend
from .validation import LocalExecutor
from .workflow import Harness
from .workspace import git


class LocalApp:
    def __init__(self, args):
        self.args = args
        self.settings = load_settings(args.config)
        self.token = secrets.token_urlsafe(32)
        self.lock = threading.Lock()
        self.job = None
        self.worker = None
        self.cancellation = ModelCancellation()
        self.shutdown = threading.Event()
        self.selected_backend = args.backend

    def harness(self, agent=None):
        return Harness(
            self.args.repo,
            self.args.state_dir,
            agent,
            LocalExecutor(
                nix_flake=self.args.nix_flake,
                timeout=self.args.check_timeout,
                cancel=self.shutdown,
            ),
            unsigned_commits=self.args.unsigned_commits,
            progress=self.progress,
        )

    def progress(self, message):
        with self.lock:
            if self.job is not None:
                self.job["log"] = [*self.job["log"], message][-200:]

    def state(self, task_id=None):
        with self.lock:
            job = json.loads(json.dumps(self.job))
        harness = self.harness()
        try:
            harness.graph.checkpointer.setup()
            names = [
                row[0]
                for row in harness.connection.execute(
                    "SELECT DISTINCT thread_id FROM checkpoints ORDER BY thread_id"
                )
            ]
            tasks = []
            snapshots = {}
            for name in names:
                saved = harness.graph.get_state(harness._config(name)).values
                if not saved:
                    continue
                snapshots[name] = saved
                tasks.append({"task_id": name, "stage": saved["stage"]})
            task = None
            if task_id in snapshots:
                saved = snapshots[task_id]
                task = harness._view(saved, inspect_worktree=False)
                task["request"] = saved["request"]
                task["messages"] = saved["messages"]
        finally:
            harness.close()
        active = self.cancellation.active
        return {
            "repository": str(self.args.repo),
            "base": git(self.args.repo, "branch", "--show-current") or "HEAD",
            "tasks": tasks,
            "task": task,
            "job": job,
            "can_stop": active is not None and not active.is_set(),
            "stopping": active is not None and active.is_set(),
            "backend": self.selected_backend or self.settings.default_backend,
        }

    def start(self, command):
        allowed = {"start", "explore", "resume", "run", "respond", "restart", "abandon"}
        if command.get("command") not in allowed:
            raise ValueError("Unknown workflow command")
        if not isinstance(command.get("task_id"), str):
            raise ValueError("Choose a task name")
        for field in (
            "request",
            "base",
            "action",
            "phase",
            "text",
            "token",
            "confirmation",
            "backend",
        ):
            if command.get(field) is not None and not isinstance(command[field], str):
                raise ValueError(f"{field} must be text")
        ids = command.get("finding_ids", [])
        if not isinstance(ids, list) or not all(isinstance(item, str) for item in ids):
            raise ValueError("Finding IDs must be a list of names")
        backend = command.get("backend", self.selected_backend)
        if backend is not None and backend != "auto" and backend not in self.settings.backends:
            raise ValueError("Unknown backend")
        with self.lock:
            if self.job and self.job["running"]:
                raise ValueError("An operation is already running; stop it before sending another")
            self.job = {
                "id": secrets.token_hex(8),
                "task_id": command["task_id"],
                "running": True,
                "log": [],
                "result": None,
                "error": None,
                "selection": None,
            }
            self.worker = threading.Thread(
                target=self._run, args=(dict(command), backend), name="harness-web-task"
            )
            self.worker.start()
        return {"accepted": True}

    def _run(self, command, backend):
        harness = None
        try:
            agent = RoutedAgent(
                self.settings,
                backend=backend,
                model=self.args.model,
                timeout=self.args.agent_timeout,
                progress=self.progress,
                cancellation=self.cancellation,
            )
            action, task_id = command["command"], command["task_id"]
            if self.shutdown.is_set():
                raise SelectionCancelled
            if action != "abandon":
                selected = select_backend(agent, self.args.repo, interactive=False)
                self.selected_backend = selected
                self.progress(f"Selected backend: {selected}")
            if self.shutdown.is_set():
                raise SelectionCancelled
            harness = self.harness(agent)
            if action in {"start", "explore"}:
                if not command.get("base"):
                    raise ValueError("Choose a base branch or commit")
                result = harness.start(
                    task_id,
                    command.get("request", ""),
                    base=command["base"],
                    phase="explore" if action == "explore" else "interview",
                )
            elif action == "resume":
                result = harness.resume(task_id, retry_backend=True)
            elif action == "run":
                result = harness.run_phase(task_id, command.get("phase", ""))
            elif action == "respond":
                result = harness.respond(
                    task_id,
                    command.get("action", ""),
                    text=command.get("text", ""),
                    approval_token=command.get("token"),
                    finding_ids=command.get("finding_ids", []),
                )
            elif action == "restart":
                result = harness.restart(task_id)
            else:
                if command.get("confirmation") != task_id:
                    raise ValueError("Type the task name to confirm deletion")
                result = harness.abandon(task_id, confirmation_token=command.get("token"))
            with self.lock:
                self.job["result"] = result
                self.job["task_id"] = result["task_id"]
        except SelectionRequired as error:
            with self.lock:
                self.job["selection"] = error.view()
                self.job["pending_command"] = command
        except SelectionCancelled:
            with self.lock:
                self.job["error"] = "Backend selection stopped. No new task was started."
        except Exception as error:
            with self.lock:
                self.job["error"] = str(error)
        finally:
            if harness:
                harness.close()
            with self.lock:
                self.job["running"] = False

    def abandon_plan(self, task_id):
        if not task_id:
            raise ValueError("Choose a task to abandon")
        harness = self.harness()
        try:
            return harness.abandon_plan(task_id)
        finally:
            harness.close()

    def close(self):
        self.shutdown.set()
        self.cancellation.close()
        if self.worker:
            self.worker.join()


def create_server(app, port=8765):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def send(self, code, body, content_type="application/json"):
            data = json.dumps(body).encode() if content_type == "application/json" else body
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
            )
            self.end_headers()
            self.wfile.write(data)

        def authorized(self, api=False):
            host = f"127.0.0.1:{self.server.server_port}"
            if self.headers.get("Host") != host:
                self.send(403, {"error": "Use the printed loopback URL"})
                return False
            origin = self.headers.get("Origin")
            if origin is not None and origin != f"http://{host}":
                self.send(403, {"error": "Foreign origins are not allowed"})
                return False
            if api and not secrets.compare_digest(
                self.headers.get("X-Harness-Token", ""), app.token
            ):
                self.send(403, {"error": "Reload the harness page to connect"})
                return False
            return True

        def do_GET(self):
            path = urlsplit(self.path)
            if not self.authorized(api=path.path.startswith("/api/")):
                return
            try:
                query = parse_qs(path.query)
                task_id = query.get("task", [None])[0]
                if path.path == "/api/state":
                    self.send(200, app.state(task_id))
                elif path.path == "/api/abandon-plan":
                    self.send(200, app.abandon_plan(task_id))
                elif path.path in {"/", "/app.js", "/style.css"}:
                    name = "index.html" if path.path == "/" else path.path[1:]
                    content = files("hydra_harness").joinpath("web", name).read_text()
                    if name == "index.html":
                        content = content.replace("HARNESS_SESSION_TOKEN", app.token)
                    types = {
                        "index.html": "text/html; charset=utf-8",
                        "app.js": "text/javascript; charset=utf-8",
                        "style.css": "text/css; charset=utf-8",
                    }
                    self.send(200, content.encode(), types[name])
                else:
                    self.send(404, {"error": "Not found"})
            except (ValueError, RuntimeError, OSError) as error:
                self.send(400, {"error": str(error)})

        def do_POST(self):
            if not self.authorized(api=True):
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 1_000_000:
                    raise ValueError("Request must contain at most 1 MB of JSON")
                body = json.loads(self.rfile.read(length))
                if not isinstance(body, dict):
                    raise ValueError("Expected a JSON object")
                if self.path == "/api/command":
                    self.send(202, app.start(body))
                elif self.path == "/api/stop":
                    self.send(200, {"stopping": app.cancellation.stop()})
                else:
                    self.send(404, {"error": "Not found"})
            except (ValueError, RuntimeError, OSError) as error:
                self.send(400, {"error": str(error)})

    return ThreadingHTTPServer(("127.0.0.1", port), Handler)


def serve(args):
    app = LocalApp(args)
    server = create_server(app, args.port)
    print(f"Haskell Harness: http://127.0.0.1:{server.server_port}", flush=True)
    print(f"Project: {args.repo}\nKeep this process running while using the page.", flush=True)
    try:
        server.serve_forever(poll_interval=0.2)
    finally:
        app.close()
        server.server_close()

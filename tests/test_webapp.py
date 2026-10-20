"""The browser uses the existing gates and can stop and revise a real child process."""

import http.client
import json
import os
import threading
import time

import pytest

from hydra_harness.cli import parser
from hydra_harness.discovery import SelectionRequired
from hydra_harness.webapp import LocalApp, create_server

from .conftest import SPECIFICATION
from .helpers import executable


@pytest.fixture
def browser_app(repository, tmp_path):
    calls, ready = tmp_path / "calls.jsonl", tmp_path / "ready"
    script = executable(
        tmp_path,
        "import json, os, sys, time\nfrom pathlib import Path\n"
        "request = json.load(sys.stdin)\n"
        f"path = Path({str(calls)!r})\n"
        "first = not path.exists()\n"
        "with path.open('a') as out: out.write(json.dumps(request) + '\\n')\n"
        "if first:\n"
        f"    Path({str(ready)!r}).write_text(str(os.getpid()))\n"
        "    time.sleep(60)\n"
        f"print({json.dumps({'specification': SPECIFICATION, 'questions': []})!r})\n",
    )
    config = tmp_path / "backend.toml"
    config.write_text(
        'default_backend = "test"\n[backends.test]\ninput_format = "json"\n'
        f"command = [{json.dumps(script)}]\n"
    )
    args = parser().parse_args(
        [
            "--repo",
            str(repository),
            "--state-dir",
            str(tmp_path / "state"),
            "--config",
            str(config),
            "serve",
            "--port",
            "0",
        ]
    )
    app = LocalApp(args)
    server = create_server(app, 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def call(path, body=None, headers=None, authenticated=True):
        request_headers = {"X-Harness-Token": app.token} if authenticated else {}
        request_headers.update(headers or {})
        connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=10)
        try:
            connection.request(
                "GET" if body is None else "POST",
                path,
                body=None if body is None else json.dumps(body),
                headers=request_headers,
            )
            response = connection.getresponse()
            text = response.read().decode()
            return response.status, (
                json.loads(text)
                if response.getheader("Content-Type") == "application/json"
                else text
            )
        finally:
            connection.close()

    yield app, call, calls, ready
    app.close()
    server.shutdown()
    server.server_close()
    thread.join(timeout=5)


def wait_for(test):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        result = test()
        if result:
            return result
        time.sleep(0.02)
    raise AssertionError("Operation did not finish")


def test_browser_stop_edit_resume_and_approval_use_existing_workflow(browser_app):
    app, call, calls, ready = browser_app
    assert call("/api/state")[1]["tasks"] == []
    code, _ = call(
        "/api/command",
        {"command": "start", "task_id": "web", "request": "Original idea", "base": "main"},
    )
    assert code == 202
    wait_for(ready.exists)
    assert call("/api/state?task=web")[1]["can_stop"]
    assert call("/api/command", {"command": "resume", "task_id": "web"})[0] == 400
    assert call("/api/stop", {})[1]["stopping"]
    wait_for(lambda: not call("/api/state")[1]["job"]["running"])
    view = call("/api/state?task=web")[1]["task"]
    assert view["stage"] == "agent_paused"
    with pytest.raises(ProcessLookupError):
        os.kill(int(ready.read_text()), 0)
    assert app.state("web")["task"]["prompt"]["draft"] == "Original idea"
    call("/api/command", {"command": "resume", "task_id": "web"})
    wait_for(lambda: not call("/api/state")[1]["job"]["running"])
    assert len(calls.read_text().splitlines()) == 1
    call(
        "/api/command",
        {"command": "respond", "task_id": "web", "action": "revise", "text": "Corrected\nscope"},
    )
    wait_for(lambda: not call("/api/state")[1]["job"]["running"])
    state = call("/api/state?task=web")[1]
    assert state["task"]["stage"] == "specification_review"
    last = json.loads(calls.read_text().splitlines()[-1])
    assert last["context"]["messages"][-1]["text"] == "Corrected\nscope"
    call("/api/command", {"command": "respond", "task_id": "web", "action": "approve"})
    wait_for(lambda: not call("/api/state")[1]["job"]["running"])
    assert "token" in call("/api/state")[1]["job"]["error"]
    call(
        "/api/command",
        {
            "command": "respond",
            "task_id": "web",
            "action": "approve",
            "token": state["task"]["prompt"]["approval_token"],
        },
    )
    wait_for(lambda: not call("/api/state")[1]["job"]["running"])
    assert call("/api/state?task=web")[1]["task"]["stage"] == "specification_ready"
    assert len(calls.read_text().splitlines()) == 2


def test_browser_rejects_foreign_sites_and_unauthenticated_api_access(browser_app):
    _, call, _, _ = browser_app
    assert call("/api/state", authenticated=False)[0] == 403
    assert call("/api/stop", {}, authenticated=False)[0] == 403
    assert call("/", headers={"Host": "attacker.example"})[0] == 403
    assert call("/api/state", headers={"Origin": "https://attacker.example"})[0] == 403
    assert call("/")[0] == 200
    assert "HARNESS_SESSION_TOKEN" not in call("/")[1]
    assert call("/app.js")[0] == 200
    assert call("/../../README.md")[0] == 404
    assert call("/api/state?task=not-created-yet")[1]["task"] is None


def test_closing_server_stops_generation_and_persists_pause(browser_app):
    app, call, _, ready = browser_app
    call(
        "/api/command",
        {"command": "start", "task_id": "closing", "request": "Original", "base": "main"},
    )
    wait_for(ready.exists)
    app.close()
    assert not app.worker.is_alive()
    assert app.state("closing")["task"]["stage"] == "agent_paused"


def test_browser_asks_for_backend_without_losing_the_pending_request(browser_app, monkeypatch):
    _, call, calls, ready = browser_app

    def choose(agent, cwd, *, interactive):
        assert not interactive
        if agent.backend is None:
            raise SelectionRequired(
                [
                    {"name": "test", "available": True, "message": "Ready"},
                    {"name": "codex", "available": True, "message": "Ready"},
                ]
            )
        return agent.backend

    monkeypatch.setattr("hydra_harness.webapp.select_backend", choose)
    request = {"command": "start", "task_id": "choice", "request": "Keep my idea", "base": "main"}
    call("/api/command", request)
    wait_for(lambda: not call("/api/state")[1]["job"]["running"])
    state = call("/api/state")[1]
    assert state["job"]["selection"]["available_backends"] == ["test", "codex"]
    assert state["job"]["pending_command"] == request
    assert not calls.exists()
    call("/api/command", {**state["job"]["pending_command"], "backend": "test"})
    wait_for(ready.exists)
    call("/api/stop", {})
    wait_for(lambda: not call("/api/state")[1]["job"]["running"])
    assert call("/api/state?task=choice")[1]["task"]["request"] == "Keep my idea"


def test_browser_abandon_requires_task_name_and_current_confirmation_token(browser_app):
    _, call, _, ready = browser_app
    call(
        "/api/command",
        {"command": "start", "task_id": "delete", "request": "Discard this idea", "base": "main"},
    )
    wait_for(ready.exists)
    call("/api/stop", {})
    wait_for(lambda: not call("/api/state")[1]["job"]["running"])
    plan = call("/api/abandon-plan?task=delete")[1]
    request = {
        "command": "abandon",
        "task_id": "delete",
        "token": plan["confirmation_token"],
        "confirmation": "wrong-name",
    }
    call("/api/command", request)
    wait_for(lambda: not call("/api/state")[1]["job"]["running"])
    assert call("/api/state?task=delete")[1]["task"]["stage"] == "agent_paused"
    call("/api/command", {**request, "confirmation": "delete"})
    wait_for(lambda: not call("/api/state")[1]["job"]["running"])
    assert call("/api/state")[1]["tasks"] == []

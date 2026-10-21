"""The browser uses the existing gates and can stop and revise a real child process."""

import http.client
import json
import os
import threading
import time
from pathlib import Path

import pytest

from haskell_harness.__main__ import parser
from haskell_harness.discovery import SelectionRequired
from haskell_harness.webapp import LocalApp, create_server

from .conftest import IMPLEMENTED, SOURCE, SPECIFICATION, ScriptedAgent, ScriptedExecutor, git
from .helpers import executable
from .test_exploration import ANALYSIS


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


@pytest.mark.parametrize("kind", ["tests", "review"])
def test_browser_standalone_tasks_start_directly_and_finish_locally(browser_app, monkeypatch, kind):
    app, call, _, _ = browser_app
    agent, executor = ScriptedAgent(), ScriptedExecutor()
    monkeypatch.setattr("haskell_harness.webapp.RoutedAgent", lambda *a, **kw: agent)
    monkeypatch.setattr("haskell_harness.webapp.select_backend", lambda *a: "test")
    monkeypatch.setattr("haskell_harness.webapp.LocalExecutor", lambda **kw: executor)
    command = {"command": kind, "task_id": kind, "base": "main", "request": "Check increment"}
    if kind == "review":
        git(app.args.repo, "checkout", "-b", "feature")
        (app.args.repo / SOURCE).write_text(IMPLEMENTED)
        git(app.args.repo, "commit", "-am", "Change")
        command.update(head="feature", reviewers="haskell")
    else:
        command.update(packages=["example-app"], criteria=["Increment by one"], expectation="red")
    assert call("/api/command", command)[0] == 202
    wait_for(lambda: not call("/api/state")[1]["job"]["running"])
    state = call(f"/api/state?task={kind}")[1]
    assert state["job"]["error"] is None, state["job"]
    task = state["task"]
    assert task["task_kind"] == kind
    assert task["stage"] == ("standalone_review" if kind == "review" else "test_review")
    assert [role for role, _, _ in agent.calls] == [
        "haskell_reviewer" if kind == "review" else "test_author"
    ]
    call(
        "/api/command",
        {
            "command": "respond",
            "task_id": kind,
            "action": "done" if kind == "review" else "approve",
            "token": task["prompt"]["approval_token"],
        },
    )
    wait_for(lambda: not call("/api/state")[1]["job"]["running"])
    task = call(f"/api/state?task={kind}")[1]["task"]
    assert task["stage"] == f"{kind}_complete", task
    assert task["prompt"]["available_phases"] == []


@pytest.mark.parametrize(
    "field,value",
    [
        ("head", []),
        ("reviewers", {}),
        ("expectation", False),
        ("criteria", "not a list"),
        ("packages", [7]),
    ],
)
def test_standalone_form_rejects_invalid_field_types(browser_app, field, value):
    _, call, calls, _ = browser_app
    code, result = call("/api/command", {"command": "tests", "task_id": "bad", field: value})
    assert code == 400 and field in result["error"]
    assert not calls.exists()


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

    def choose(agent, cwd):
        if agent.backend is None:
            raise SelectionRequired(
                [
                    {"name": "test", "available": True, "message": "Ready"},
                    {"name": "codex", "available": True, "message": "Ready"},
                ]
            )
        return agent.backend

    monkeypatch.setattr("haskell_harness.webapp.select_backend", choose)
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


def test_browser_manages_project_context_without_a_model(browser_app):
    app, call, calls, _ = browser_app
    assert call("/api/project")[1]["context"]["history"] == []
    assert call("/api/context", {"sources": []})[0] == 400
    code, saved = call("/api/context", {"sources": ["CLAUDE.md"]})
    assert code == 200
    assert saved["documents"][0]["path"] == "CLAUDE.md"
    assert call("/api/context?snapshot=" + saved["id"])[1] == saved
    assert call("/api/project")[1]["context"]["latest"] == saved["id"]
    assert not calls.exists()
    assert call("/api/context", {"sources": ["../outside"]})[0] == 400
    assert app.state()["tasks"] == []


def test_context_selection_keeps_the_workflow_gate(browser_app):
    app, call, _, ready = browser_app
    saved = call("/api/context", {"sources": ["CLAUDE.md"]})[1]
    call(
        "/api/command",
        {"command": "start", "task_id": "context", "request": "Original", "base": "main"},
    )
    wait_for(ready.exists)
    assert call("/api/context", {"sources": ["CLAUDE.md"]})[0] == 400
    assert call("/api/context/use", {"task_id": "context", "snapshot": saved["id"]})[0] == 400
    call("/api/stop", {})
    wait_for(lambda: not app.state()["job"]["running"])
    assert call("/api/context/use", {"task_id": "context", "snapshot": saved["id"]})[0] == 400
    call(
        "/api/command",
        {"command": "respond", "task_id": "context", "action": "retry"},
    )
    wait_for(lambda: not app.state()["job"]["running"])
    token = app.state("context")["task"]["prompt"]["approval_token"]
    call(
        "/api/command",
        {"command": "respond", "task_id": "context", "action": "approve", "token": token},
    )
    wait_for(lambda: not app.state()["job"]["running"])
    code, view = call("/api/context/use", {"task_id": "context", "snapshot": saved["id"]})
    assert code == 200
    assert view["project_context_id"] == saved["id"]
    assert view["stage"] == "specification_ready"


def test_browser_backend_settings_are_explicit_and_do_not_invoke_models(browser_app):
    app, call, calls, _ = browser_app
    assert call("/api/backend", {"backend": "unknown"})[0] == 400
    assert call("/api/backend", {"backend": "test"})[0] == 200
    assert app.selected_backend == "test"
    assert not calls.exists()
    assert call("/api/backend", {"backend": "auto"})[0] == 200
    assert app.selected_backend == "auto"


def test_project_settings_resolve_backend_paths_against_the_project(browser_app):
    app, call, calls, _ = browser_app
    executable(app.args.repo, "raise AssertionError('Status must not execute a backend')\n")
    app.settings.backends["test"].command = ["./agent-double"]
    profiles = call("/api/project")[1]["backends"]
    assert next(item for item in profiles if item["name"] == "test")["available"]
    assert not calls.exists()


def test_project_controls_require_same_origin_and_session_token(browser_app):
    _, call, _, _ = browser_app
    assert call("/guide", authenticated=False)[0] == 200
    for path, body in (
        ("/api/project", None),
        ("/api/context", {"sources": ["CLAUDE.md"]}),
        ("/api/context/use", {"task_id": "example", "snapshot": "invalid"}),
        ("/api/backend", {"backend": "test"}),
    ):
        assert call(path, body, authenticated=False)[0] == 403
        assert call(path, body, headers={"Origin": "https://attacker.example"})[0] == 403


def test_browser_exploration_can_finish_and_reopen_without_coding(browser_app):
    app, call, calls, _ = browser_app
    executable(
        calls.parent,
        "import json, sys\nfrom pathlib import Path\nrequest = json.load(sys.stdin)\n"
        f"with Path({str(calls)!r}).open('a') as out: out.write(json.dumps(request) + '\\n')\n"
        f"print({json.dumps(ANALYSIS)!r})\n",
    )
    call(
        "/api/command",
        {"command": "explore", "task_id": "research", "request": "Compare options", "base": "main"},
    )
    wait_for(lambda: not app.state()["job"]["running"])
    original = call("/api/state?task=research")[1]["task"]
    assert original["stage"] == "exploration"
    call(
        "/api/command",
        {"command": "respond", "task_id": "research", "action": "question", "text": "done\nWhy?"},
    )
    wait_for(lambda: not app.state()["job"]["running"])
    assert app.state("research")["task"]["stage"] == "exploration"
    call("/api/command", {"command": "respond", "task_id": "research", "action": "done"})
    wait_for(lambda: not app.state()["job"]["running"])
    reopened = LocalApp(app.args)
    try:
        task = reopened.state("research")["task"]
        assert task["stage"] == "exploration_ready"
        assert task["prompt"]["available_phases"] == ["explore", "interview"]
        assert task["worktree"] == original["worktree"]
        assert any(item.get("text") == "done\nWhy?" for item in task["messages"])
        assert Path(task["prompt"]["report"]).is_file()
        assert [json.loads(line)["role"] for line in calls.read_text().splitlines()] == [
            "explorer",
            "explorer",
        ]
    finally:
        reopened.close()


def test_browser_resume_retries_a_provider_failure_once_in_the_same_task(browser_app):
    app, call, calls, _ = browser_app
    executable(
        calls.parent,
        "import sys\nfrom pathlib import Path\n"
        f"with Path({str(calls)!r}).open('a') as out: out.write('failure\\n')\n"
        "print('Usage limit reached', file=sys.stderr)\nsys.exit(1)\n",
    )
    call(
        "/api/command",
        {"command": "start", "task_id": "quota", "request": "Keep this", "base": "main"},
    )
    wait_for(lambda: not app.state()["job"]["running"])
    original = app.state("quota")["task"]
    assert original["stage"] == "backend_blocked"
    assert original["prompt"]["backend_failure"]["kind"] == "quota"
    assert calls.read_text() == "failure\n"
    executable(
        calls.parent,
        "import json, sys\nfrom pathlib import Path\njson.load(sys.stdin)\n"
        f"with Path({str(calls)!r}).open('a') as out: out.write('success\\n')\n"
        f"print({json.dumps({'specification': SPECIFICATION, 'questions': []})!r})\n",
    )
    call("/api/command", {"command": "resume", "task_id": "quota"})
    wait_for(lambda: not app.state()["job"]["running"])
    task = app.state("quota")["task"]
    assert task["stage"] == "specification_review"
    assert task["worktree"] == original["worktree"]
    assert task["request"] == "Keep this"
    assert calls.read_text() == "failure\nsuccess\n"


def test_browser_can_require_a_walkthrough_and_restart_without_losing_the_old_task(browser_app):
    app, call, _, ready = browser_app
    call(
        "/api/command",
        {
            "command": "start",
            "task_id": "tour",
            "request": "Original",
            "base": "main",
            "walkthrough": True,
        },
    )
    wait_for(ready.exists)
    call("/api/stop", {})
    wait_for(lambda: not app.state()["job"]["running"])
    original = app.state("tour")["task"]
    call("/api/command", {"command": "restart", "task_id": "tour"})
    wait_for(lambda: not app.state()["job"]["running"])
    fresh = app.state()["job"]["result"]
    assert fresh["task_id"] != original["task_id"]
    assert app.state("tour")["task"] == original
    harness = app.harness()
    try:
        assert harness._state(fresh["task_id"])["walkthrough_enabled"] is True
    finally:
        harness.close()

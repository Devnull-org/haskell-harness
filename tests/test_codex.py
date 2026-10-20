"""Codex uses saved login and can continue tasks started by another backend."""

import json
import os

import pytest

from hydra_harness.backends import CommandAgent
from hydra_harness.config import load_settings
from hydra_harness.contracts import Harness

from .conftest import SPECIFICATION, ScriptedAgent
from .helpers import executable


def codex_profile(script):
    backend = load_settings().backends["codex"]
    return backend.model_copy(update={"command": [script, *backend.command[1:]]})


@pytest.mark.parametrize(
    "role",
    [
        "interviewer",
        "test_author",
        "implementer",
        "refactorer",
        "haskell_reviewer",
        "security_reviewer",
        "walkthrough",
        "walkthrough_feedback",
    ],
)
def test_codex_reads_stdin_and_returns_a_validated_proposal(tmp_path, monkeypatch, role):
    capture = tmp_path / "request.json"
    credentials = ["OPENAI_API_KEY", "CODEX_API_KEY", "CODEX_ACCESS_TOKEN"]
    for name in credentials:
        monkeypatch.setenv(name, "test-only-secret")
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "existing-login"))
    reply = ScriptedAgent()(role, {}, tmp_path)
    script = executable(
        tmp_path,
        "import json, os, sys\nfrom pathlib import Path\n"
        f"assert not set({credentials!r}) & os.environ.keys()\n"
        f"assert os.environ['CODEX_HOME'] == {os.environ['CODEX_HOME']!r}\n"
        f"Path({str(capture)!r}).write_text(json.dumps({{\n"
        "'args': sys.argv[1:], 'prompt': sys.stdin.read(), 'cwd': os.getcwd()\n"
        "}))\n"
        "print('Progress goes to stderr', file=sys.stderr)\n"
        f"print({json.dumps(reply)!r})\n",
    )
    assert CommandAgent(codex_profile(script), model="selected-model")(role, {}, tmp_path) == reply
    request = json.loads(capture.read_text())
    args = request["args"]
    assert args[args.index("--ask-for-approval") + 1] == "never"
    assert args[args.index("--sandbox") + 1] == "read-only"
    assert args[args.index("--model") + 1] == "selected-model"
    assert args.index("exec") < args.index("-")
    assert "--ephemeral" in args
    assert "--ignore-user-config" in args
    assert "--ignore-rules" in args
    assert "--json" not in args
    assert "Return only one JSON object matching this schema:" in request["prompt"]
    assert request["cwd"] == str(tmp_path)
    for name in credentials:
        assert os.environ[name] == "test-only-secret"


@pytest.mark.parametrize("logged_in", [True, False])
def test_codex_doctor_only_checks_saved_login(tmp_path, logged_in):
    message = "Logged in using ChatGPT" if logged_in else "Not logged in"
    script = executable(
        tmp_path,
        "import sys\n"
        "assert sys.argv[1:] == ['login', 'status']\n"
        f"print({message!r}, file=sys.stderr)\n"
        f"sys.exit({0 if logged_in else 1})\n",
    )
    passed, detail = CommandAgent(codex_profile(script), name="codex").doctor(tmp_path)
    assert passed is logged_in
    assert message in detail


def test_codex_resumes_a_failed_interview_without_losing_answers(rig, repository, tmp_path):
    harness, agent, executor = rig
    agent.responses["interviewer"].append(
        {"specification": SPECIFICATION, "questions": ["Which inputs?"]}
    )
    harness.start("switch-backend", "Fix increment", mode="phased")

    def unavailable(role, context, cwd):
        raise RuntimeError("Provider usage limit")

    harness.agent = unavailable
    failed = harness.respond("switch-backend", "answer", text="Negative integers too.")
    assert failed["stage"] == "agent_error"
    harness.close()
    capture = tmp_path / "prompt.txt"
    script = executable(
        tmp_path,
        "import sys\nfrom pathlib import Path\n"
        f"Path({str(capture)!r}).write_text(sys.stdin.read())\n"
        f"print({json.dumps({'specification': SPECIFICATION, 'questions': []})!r})\n",
    )
    resumed = Harness(
        repository,
        tmp_path / "state",
        CommandAgent(codex_profile(script), name="codex"),
        executor,
    )
    try:
        assert resumed.resume("switch-backend")["stage"] == "agent_error"
        assert not capture.exists()
        view = resumed.respond("switch-backend", "retry")
        assert view["stage"] == "specification_review"
        assert view["worktree"] == failed["worktree"]
        assert view["base"] == failed["base"]
        assert "Negative integers too." in capture.read_text()
        assert set(view["prompt"]["actions"]) == {"question", "revise", "approve"}
    finally:
        resumed.close()

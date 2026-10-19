"""Exercise separate CLI processes against the same durable interview."""

import json
import os
import subprocess
import sys

import pytest

from hydra_harness.cli import doctor, main, parser

from .conftest import SPECIFICATION
from .helpers import executable


def test_cli_persists_a_conversation_across_processes(repository, tmp_path):
    envelope = {
        "type": "result",
        "is_error": False,
        "result": json.dumps({"specification": SPECIFICATION, "questions": []}),
    }
    backend = executable(tmp_path, f"print({json.dumps(envelope)!r})\n")
    config = tmp_path / "backend.toml"
    config.write_text(
        'default_backend = "test"\n[backends.test]\n'
        f'command = [{json.dumps(backend)}]\nresponse_fields = ["result"]\n'
    )
    repository = repository.rename(tmp_path / "checkout with spaces")
    args = [
        sys.executable,
        "-m",
        "hydra_harness.cli",
        "--repo",
        str(repository),
        "--state-dir",
        str(tmp_path / "state"),
        "--config",
        str(config),
        "--json",
    ]
    environment = dict(os.environ)

    def call(*command):
        result = subprocess.run(
            [*args, *command], text=True, capture_output=True, env=environment, check=True
        )
        return json.loads(result.stdout)

    view = call("start", "interview", "Fix increment", "--interview-only")
    assert view["stage"] == "specification_review"
    assert call("status", "interview")["prompt"] == view["prompt"]
    assert call("resume", "interview")["prompt"] == view["prompt"]
    done = call("respond", "interview", "approve", "--token", view["prompt"]["approval_token"])
    assert done["stage"] == "specification_ready"


def test_task_commands_require_an_explicit_checkout(capsys):
    with pytest.raises(SystemExit) as error:
        main(["start", "task", "Fix it"])
    assert error.value.code == 2
    assert "require --repo" in capsys.readouterr().err


def test_backend_listing_needs_no_checkout(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "unused"))
    assert main(["--json", "backends"]) == 0
    assert "claude-code" in json.loads(capsys.readouterr().out)["backends"]
    assert list(tmp_path.iterdir()) == []


def test_doctor_without_checkout_only_checks_tools_and_backends(tmp_path, monkeypatch, capsys):
    class Agent:
        def doctor(self, cwd):
            assert cwd == tmp_path
            return [(True, "Backend ready")]

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("hydra_harness.cli.shutil.which", lambda name: "/tool/" + name)
    assert doctor(parser().parse_args(["doctor"]), Agent()) == 0
    assert "No target checkout selected" in capsys.readouterr().out
    assert list(tmp_path.iterdir()) == []


def test_nonexistent_explicit_checkout_fails_before_backend_use(tmp_path, capsys):
    assert main(["--repo", str(tmp_path / "missing"), "start", "task", "Fix it"]) == 1
    assert "Target checkout does not exist" in capsys.readouterr().err

"""Exercise separate CLI processes against the same durable interview."""

import json
import os
import shutil
import subprocess
import sys

import pytest

from hydra_harness.cli import doctor, main, parser

from .conftest import SOURCE, SPECIFICATION, ScriptedAgent, git
from .helpers import executable


@pytest.mark.parametrize("start_options", [[], ["--interview-only"]])
def test_cli_persists_a_conversation_across_processes(repository, tmp_path, start_options):
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
    tools = tmp_path / "tools"
    tools.mkdir()
    (tools / "git").symlink_to(shutil.which("git"))
    environment["PATH"] = str(tools)

    def call(*command):
        result = subprocess.run(
            [*args, *command], text=True, capture_output=True, env=environment, check=True
        )
        return json.loads(result.stdout)

    view = call("start", "interview", "Fix increment", "--base", "main", *start_options)
    assert view["stage"] == "specification_review"
    assert call("status", "interview")["prompt"] == view["prompt"]
    assert call("resume", "interview")["prompt"] == view["prompt"]
    done = call("respond", "interview", "approve", "--token", view["prompt"]["approval_token"])
    assert done["stage"] == "specification_ready"
    assert done["prompt"]["available_phases"] == ["tests"]
    assert call("resume", "interview") == done
    restarted = call("restart", "interview", "--as", "fresh", "--base", "main")
    assert restarted["task_id"] == "fresh"
    assert restarted["restarted_from"] == "interview"
    assert restarted["stage"] == "specification_review"
    assert call("status", "interview") == done


@pytest.mark.parametrize("detached", [False, True])
def test_interactive_start_prompts_for_base(repository, tmp_path, monkeypatch, capsys, detached):
    if detached:
        git(repository, "checkout", "--detach")
    (repository / SOURCE).write_text("-- Pending developer work\n")
    agent = ScriptedAgent()
    monkeypatch.setattr("hydra_harness.cli.RoutedAgent", lambda *args, **kwargs: agent)
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    prompts = []
    answers = iter(["", "quit"])

    def answer(prompt):
        prompts.append(prompt)
        return next(answers)

    monkeypatch.setattr("builtins.input", answer)
    assert (
        main(
            [
                "--repo",
                str(repository),
                "--state-dir",
                str(tmp_path / "state"),
                "start",
                "task",
                "Fix it",
            ]
        )
        == 0
    )
    assert "Base branch or commit" in prompts[0]
    assert ("HEAD" if detached else "main") in prompts[0]
    assert "Base:" in capsys.readouterr().out
    assert agent.calls[0][2] != repository


@pytest.mark.parametrize("options", [[], ["--json"]])
def test_scripted_start_requires_explicit_base(repository, tmp_path, monkeypatch, capsys, options):
    monkeypatch.setattr("hydra_harness.cli.RoutedAgent", lambda *args, **kwargs: ScriptedAgent())
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    monkeypatch.setattr("builtins.input", lambda *args: pytest.fail("Unexpected prompt"))
    assert (
        main(
            [
                "--repo",
                str(repository),
                "--state-dir",
                str(tmp_path / "state"),
                *options,
                "start",
                "task",
                "Fix it",
            ]
        )
        == 1
    )
    assert "--base" in capsys.readouterr().err
    assert not (tmp_path / "state").exists()


def test_workflow_help_needs_no_repository_backend_or_nix(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PATH", "")
    assert main(["--config", str(tmp_path / "missing.toml"), "help"]) == 0
    guide = capsys.readouterr().out
    for phrase in (
        "flake.nix",
        "--repo",
        "--base",
        "doctor",
        "context",
        "start",
        "approve",
        "run my-feature tests",
        "run my-feature implement",
        "run my-feature refactor",
        "review-haskell",
        "review-security",
        "walkthrough",
        "handoff",
        "status",
        "resume",
    ):
        assert phrase in guide
    assert list(tmp_path.iterdir()) == []


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
    monkeypatch.setattr(
        "hydra_harness.cli.shutil.which", lambda name: "/tool/git" if name == "git" else None
    )
    assert doctor(parser().parse_args(["doctor"]), Agent()) == 0
    assert "No target checkout selected" in capsys.readouterr().out
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("target_flag", ["--repo", "--nix-flake"])
def test_doctor_requires_nix_for_target_validation(target_flag, tmp_path, monkeypatch, capsys):
    class Agent:
        def doctor(self, cwd):
            return [(True, "Backend ready")]

    (tmp_path / "flake.nix").write_text("{}")
    monkeypatch.setattr(
        "hydra_harness.cli.shutil.which", lambda name: "/tool/git" if name == "git" else None
    )
    args = parser().parse_args([target_flag, str(tmp_path), "doctor"])
    assert doctor(args, Agent()) == 1
    assert "nix: missing" in capsys.readouterr().out


def test_nonexistent_explicit_checkout_fails_before_backend_use(tmp_path, capsys):
    assert main(["--repo", str(tmp_path / "missing"), "start", "task", "Fix it"]) == 1
    assert "Target checkout does not exist" in capsys.readouterr().err


def test_context_commands_need_no_backend_configuration(repository, tmp_path, monkeypatch, capsys):
    def forbidden(*args):
        pytest.fail("Context commands must not load model configuration")

    monkeypatch.setattr("hydra_harness.cli.load_settings", forbidden)
    args = ["--repo", str(repository), "--state-dir", str(tmp_path / "saved"), "--json", "context"]
    assert main([*args, "refresh", "--source", SOURCE]) == 0
    snapshot = json.loads(capsys.readouterr().out)
    assert main([*args, "show", "--source", SOURCE]) == 0
    assert json.loads(capsys.readouterr().out)["content"] == (repository / SOURCE).read_text()
    assert main([*args, "history"]) == 0
    assert json.loads(capsys.readouterr().out)[0]["id"] == snapshot["id"]

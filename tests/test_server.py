"""The only entry point starts the local website."""

import subprocess
import sys

import pytest

from lambda_harness.__main__ import main, parser


def test_server_can_open_projects_or_an_explicit_checkout(repository):
    assert parser().parse_args([]).repo is None
    assert parser().parse_args(["--repo", str(repository)]).repo == repository


@pytest.mark.parametrize("command", ["start", "resume", "doctor", "backends", "serve", "context"])
def test_terminal_commands_are_not_exposed(repository, command):
    with pytest.raises(SystemExit) as error:
        parser().parse_args(["--repo", str(repository), command])
    assert error.value.code == 2


def test_help_does_not_load_a_backend_or_start_the_server(monkeypatch, capsys):
    monkeypatch.setattr(
        "lambda_harness.__main__.serve", lambda _: pytest.fail("Help must not start a server")
    )
    with pytest.raises(SystemExit) as error:
        main(["--help"])
    assert error.value.code == 0
    assert "local website" in capsys.readouterr().out


def test_invalid_project_fails_before_starting_the_server(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(
        "lambda_harness.__main__.serve", lambda _: pytest.fail("Invalid project was accepted")
    )
    assert main(["--repo", str(tmp_path / "missing")]) == 1
    assert "does not exist" in capsys.readouterr().err
    assert main(["--repo", str(tmp_path)]) == 1
    assert "git" in capsys.readouterr().err.lower()


def test_server_receives_resolved_options(repository, tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr("lambda_harness.__main__.serve", calls.append)
    assert main(["--repo", str(repository), "--port", "0", "--state-dir", str(tmp_path)]) == 0
    assert calls[0].repo == repository.resolve()
    assert calls[0].port == 0


def test_module_is_runnable_without_a_console_script():
    result = subprocess.run(
        [sys.executable, "-m", "lambda_harness", "--help"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert "local website" in result.stdout

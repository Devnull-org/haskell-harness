"""The only entry point starts the local website."""

import signal
import subprocess
import sys
from types import SimpleNamespace

import pytest

from lambda_harness.__main__ import main, parser


def test_server_can_open_projects_or_an_explicit_checkout(repository, tmp_path):
    options = ["--state-dir", str(tmp_path / "state")]
    assert parser().parse_args(options).repo is None
    assert parser().parse_args([*options, "--repo", str(repository)]).repo == repository


@pytest.mark.parametrize("with_project", [False, True])
def test_startup_requires_state_directory_before_touching_projects(
    repository, tmp_path, monkeypatch, capsys, with_project
):
    root = tmp_path / "unused-state"
    monkeypatch.setenv("XDG_STATE_HOME", str(root))
    monkeypatch.setattr(
        "lambda_harness.__main__.serve", lambda _: pytest.fail("Missing state directory accepted")
    )
    with pytest.raises(SystemExit) as error:
        main(["--repo", str(repository)] if with_project else [])
    assert error.value.code == 2
    assert "required: --state-dir" in capsys.readouterr().err
    assert not root.exists()


def test_backend_is_chosen_in_the_website_not_at_launch(tmp_path, monkeypatch, capsys):
    root = tmp_path / "state"
    monkeypatch.setattr(
        "lambda_harness.__main__.serve", lambda _: pytest.fail("Launch backend was accepted")
    )
    with pytest.raises(SystemExit) as error:
        main(["--state-dir", str(root), "--backend", "codex"])
    assert error.value.code == 2
    assert "unrecognized arguments: --backend codex" in capsys.readouterr().err
    assert not root.exists()


@pytest.mark.parametrize("command", ["start", "resume", "doctor", "backends", "serve", "context"])
def test_terminal_commands_are_not_exposed(repository, command):
    with pytest.raises(SystemExit) as error:
        parser().parse_args(
            ["--state-dir", str(repository.parent / "state"), "--repo", str(repository), command]
        )
    assert error.value.code == 2


def test_help_does_not_load_a_backend_or_start_the_server(monkeypatch, capsys):
    monkeypatch.setattr(
        "lambda_harness.__main__.serve", lambda _: pytest.fail("Help must not start a server")
    )
    with pytest.raises(SystemExit) as error:
        main(["--help"])
    assert error.value.code == 0
    output = capsys.readouterr().out
    assert "local website" in output
    assert "--state-dir PATH" in output
    assert "--backend" not in output


def test_invalid_project_fails_before_starting_the_server(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(
        "lambda_harness.__main__.serve", lambda _: pytest.fail("Invalid project was accepted")
    )
    options = ["--state-dir", str(tmp_path / "state")]
    assert main([*options, "--repo", str(tmp_path / "missing")]) == 1
    assert "does not exist" in capsys.readouterr().err
    assert main([*options, "--repo", str(tmp_path)]) == 1
    assert "git" in capsys.readouterr().err.lower()


def test_server_receives_resolved_options(repository, tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr("lambda_harness.__main__.serve", calls.append)
    assert main(["--repo", str(repository), "--port", "0", "--state-dir", str(tmp_path)]) == 0
    assert calls[0].repo == repository.resolve()
    assert calls[0].port == 0


def test_phone_access_is_opt_in_and_supports_another_https_port(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr("lambda_harness.__main__.serve", calls.append)
    assert parser().parse_args(["--state-dir", str(tmp_path)]).tailscale is False
    assert main(["--state-dir", str(tmp_path), "--tailscale", "--tailscale-port", "8443"]) == 0
    assert calls[0].tailscale is True and calls[0].tailscale_port == 8443
    assert main(["--state-dir", str(tmp_path), "--tailscale-port", "8443"]) == 1
    assert main(["--state-dir", str(tmp_path), "--tailscale", "--tailscale-port", "0"]) == 1
    assert len(calls) == 1


@pytest.mark.parametrize("startup_failure", [False, True])
def test_server_closes_phone_access_on_termination_or_startup_failure(
    monkeypatch, startup_failure, tmp_path, capsys
):
    from lambda_harness.webapp import serve

    events = []

    def start(port):
        events.append(("phone start", port))
        if startup_failure:
            raise RuntimeError("HTTPS setup required")

    def run(**kwargs):
        signal.raise_signal(signal.SIGTERM)

    phone = SimpleNamespace(
        start=start,
        close=lambda: events.append("phone closed"),
        origin="https://laptop.example.ts.net",
        login="owner@example.com",
    )
    server = SimpleNamespace(
        server_port=8765, serve_forever=run, server_close=lambda: events.append("server closed")
    )
    monkeypatch.setattr("lambda_harness.webapp.PhoneAccess.discover", lambda port: phone)
    monkeypatch.setattr(
        "lambda_harness.webapp.ProjectHub",
        lambda args: SimpleNamespace(close=lambda: events.append("hub closed")),
    )
    monkeypatch.setattr("lambda_harness.webapp.create_server", lambda *a, **kw: server)
    previous = signal.getsignal(signal.SIGTERM)
    with pytest.raises(RuntimeError if startup_failure else KeyboardInterrupt):
        serve(parser().parse_args(["--state-dir", str(tmp_path), "--tailscale"]))
    assert events == [("phone start", 8765), "phone closed", "hub closed", "server closed"]
    assert signal.getsignal(signal.SIGTERM) == previous
    assert f"Saved work: {tmp_path}" in capsys.readouterr().out


def test_module_is_runnable_without_a_console_script():
    result = subprocess.run(
        [sys.executable, "-m", "lambda_harness", "--help"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert "local website" in result.stdout

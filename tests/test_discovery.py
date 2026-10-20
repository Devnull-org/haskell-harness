"""Discover usable providers before sending them any project context."""

import json
from pathlib import Path

import pytest

from hydra_harness.backends import Backend
from hydra_harness.cli import doctor, parser
from hydra_harness.config import RoutedAgent, Settings
from hydra_harness.discovery import SelectionRequired, discover, select_backend

from .helpers import executable


def provider(tmp_path, name, *, failure=None, logged_in=True):
    directory = tmp_path / name
    directory.mkdir()
    calls = directory / "calls.jsonl"
    script = executable(
        directory,
        "import json, os, sys\nfrom pathlib import Path\n"
        "if '--login' in sys.argv:\n"
        f"    print({'Logged in' if logged_in else 'Not logged in'!r})\n"
        "    sys.exit(0)\n"
        "request = json.load(sys.stdin)\n"
        f"with Path({str(calls)!r}).open('a') as stream:\n"
        "    stream.write(json.dumps({'cwd': os.getcwd(), 'request': request}) + '\\n')\n"
        + (
            f"print({failure!r}, file=sys.stderr)\nsys.exit(1)\n"
            if failure
            else "print(json.dumps({'answer': 'ready'}))\n"
        ),
    )
    return Backend(
        command=[script],
        input_format="json",
        probe=["{executable}", "--login"],
        probe_reject_patterns=["not logged in"],
    ), calls


def test_discovery_checks_generation_and_isolates_project_contents(tmp_path):
    okay, okay_calls = provider(tmp_path, "okay")
    quota, quota_calls = provider(tmp_path, "quota", failure="You've hit your usage limit")
    signed_out, signed_out_calls = provider(tmp_path, "signed-out", logged_in=False)
    missing = Backend(command=[str(tmp_path / "missing")])
    project = tmp_path / "project"
    project.mkdir()
    (project / "private.txt").write_text("private project context")
    agent = RoutedAgent(
        Settings(
            backends={"okay": okay, "quota": quota, "signed-out": signed_out, "missing": missing}
        )
    )
    checks = {check["name"]: check for check in discover(agent, project)}
    assert checks["okay"]["available"]
    assert not any(checks[name]["available"] for name in ("quota", "signed-out", "missing"))
    assert checks["quota"]["kind"] == "quota"
    assert checks["signed-out"]["kind"] == "authentication"
    assert checks["missing"]["kind"] == "unavailable"
    assert not signed_out_calls.exists()
    for calls in (okay_calls, quota_calls):
        records = [json.loads(line) for line in calls.read_text().splitlines()]
        assert len(records) == 1
        assert records[0]["cwd"] != str(project)
        assert not Path(records[0]["cwd"]).exists()
        request = records[0]["request"]
        assert request["context"]["backend_check"] is True
        assert "private project context" not in json.dumps(request)
        assert str(project) not in json.dumps(request)


def test_one_usable_backend_is_selected_without_asking(tmp_path, monkeypatch):
    okay, _ = provider(tmp_path, "okay")
    quota, _ = provider(tmp_path, "quota", failure="insufficient_quota")
    agent = RoutedAgent(Settings(backends={"okay": okay, "quota": quota}))
    monkeypatch.setattr("builtins.input", lambda _: pytest.fail("Only one backend is usable"))
    assert select_backend(agent, tmp_path, interactive=True) == "okay"
    assert agent.resolve("implementer").name == "okay"


def test_multiple_usable_backends_require_a_choice(tmp_path, monkeypatch, capsys):
    first, _ = provider(tmp_path, "alpha")
    second, _ = provider(tmp_path, "beta")
    agent = RoutedAgent(Settings(backends={"alpha": first, "beta": second}))
    answers = iter(["", "99", "beta"])
    monkeypatch.setattr("builtins.input", lambda _: next(answers))
    assert select_backend(agent, tmp_path, interactive=True) == "beta"
    output = capsys.readouterr().out
    assert "1. alpha" in output and "2. beta" in output
    assert agent.resolve("interviewer").name == "beta"


def test_scripts_report_choices_instead_of_guessing(tmp_path):
    first, _ = provider(tmp_path, "alpha")
    second, _ = provider(tmp_path, "beta")
    agent = RoutedAgent(Settings(backends={"alpha": first, "beta": second}))
    with pytest.raises(SelectionRequired) as error:
        select_backend(agent, tmp_path, interactive=False)
    assert error.value.available == ["alpha", "beta"]
    assert agent.backend is None


def test_no_usable_backend_reports_every_failure(tmp_path):
    quota, _ = provider(tmp_path, "quota", failure="usage limit")
    signed_out, _ = provider(tmp_path, "signed-out", logged_in=False)
    agent = RoutedAgent(Settings(backends={"quota": quota, "signed-out": signed_out}))
    with pytest.raises(SelectionRequired) as error:
        select_backend(agent, tmp_path, interactive=True)
    assert error.value.available == []
    assert {check["kind"] for check in error.value.checks} == {"quota", "authentication"}


def test_explicit_backend_selection_skips_discovery(tmp_path, monkeypatch):
    backend, calls = provider(tmp_path, "chosen")
    agent = RoutedAgent(Settings(backends={"chosen": backend}), backend="chosen")
    monkeypatch.setattr("builtins.input", lambda _: pytest.fail("Explicit choice must be honored"))
    assert select_backend(agent, tmp_path, interactive=True) == "chosen"
    assert not calls.exists()


def test_relative_executable_is_resolved_before_entering_the_probe_directory(tmp_path):
    backend, calls = provider(tmp_path, "relative")
    backend.command = ["relative/agent-double"]
    agent = RoutedAgent(Settings(backends={"relative": backend}))
    assert discover(agent, tmp_path)[0]["available"]
    assert calls.exists()


def test_auto_is_reserved_for_selection():
    with pytest.raises(ValueError, match="reserved"):
        Settings(backends={"auto": Backend(command=["model"])})


def test_structured_signed_out_status_is_identified(tmp_path):
    script = executable(
        tmp_path, 'import sys\nprint(\'{"loggedIn":false,"authMethod":"none"}\')\nsys.exit(1)\n'
    )
    backend = Backend(command=[script], probe=["{executable}", "--login"])
    checks = discover(RoutedAgent(Settings(backends={"signed-out": backend})), tmp_path)
    assert checks[0]["kind"] == "authentication"
    assert not checks[0]["available"]


def test_auto_doctor_succeeds_with_one_login_without_generating(tmp_path, monkeypatch, capsys):
    okay, calls = provider(tmp_path, "okay")
    signed_out, _ = provider(tmp_path, "signed-out", logged_in=False)
    agent = RoutedAgent(Settings(backends={"okay": okay, "signed-out": signed_out}))
    monkeypatch.chdir(tmp_path)
    assert doctor(parser().parse_args(["doctor"]), agent) == 0
    assert not calls.exists()
    assert "Not logged in" in capsys.readouterr().out

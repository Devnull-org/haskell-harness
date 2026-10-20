"""Backend choice works across real CLI processes and saved failures."""

import json
import os
import subprocess
import sys

import pytest

from .conftest import SPECIFICATION
from .helpers import executable, terminal_process


def configured_cli(repository, tmp_path, *, multiple=False):
    calls = tmp_path / "calls.jsonl"
    profiles = ['default_backend = "auto"']
    for name in ("local", "codex", "claude-code"):
        directory = tmp_path / name
        directory.mkdir()
        failure = name == "local" or (name == "claude-code" and not multiple)
        script = executable(
            directory,
            "import json, sys\nfrom pathlib import Path\n"
            "request = json.load(sys.stdin)\n"
            f"with Path({str(calls)!r}).open('a') as stream:\n"
            f"    stream.write(json.dumps({{'backend': {name!r}, 'request': request}}) + '\\n')\n"
            + (
                "print('usage limit reached', file=sys.stderr)\nsys.exit(1)\n"
                if failure
                else "reply = {'answer': 'ready'} if request['context'].get('backend_check') "
                f"else {{'specification': {SPECIFICATION!r}, 'questions': ['Which inputs?']}}\n"
                "print(json.dumps(reply))\n"
            ),
        )
        profiles.append(
            f"[backends.{name}]\ncommand = [{json.dumps(script)}]\ninput_format = 'json'"
        )
    config = tmp_path / "backends.toml"
    config.write_text("\n".join(profiles))
    args = [
        sys.executable,
        "-m",
        "haskell_harness.cli",
        "--repo",
        str(repository),
        "--state-dir",
        str(tmp_path / "state"),
        "--config",
        str(config),
    ]
    return args, calls


def call(args, *command):
    return subprocess.run([*args, *command], text=True, capture_output=True, timeout=30)


@pytest.mark.parametrize("multiple", [False, True])
@pytest.mark.skipif(os.name != "posix", reason="Requires a POSIX terminal")
def test_resume_checks_backends_and_retries_the_saved_failure(repository, tmp_path, multiple):
    args, calls = configured_cli(repository, tmp_path, multiple=multiple)
    failed = call(
        args, "--backend", "local", "--json", "start", "feature", "Fix increment", "--base", "main"
    )
    assert failed.returncode == 1, failed.stderr
    before = json.loads(failed.stdout)
    assert before["stage"] == "backend_blocked"
    with terminal_process([*args, "resume", "feature"]) as (process, expect, send):
        if multiple:
            expect("Several backends are usable.")
            expect("Backend number or name")
            records = [json.loads(line) for line in calls.read_text().splitlines()]
            assert (
                len([r for r in records if not r["request"]["context"].get("backend_check")]) == 1
            )
            send("codex")
        expect("Selected backend for this session: codex")
        expect("Retrying saved failure from backend 'local' with selected backend 'codex'")
        expect("Which inputs?")
        expect(">")
        send("quit")
        assert process.wait(timeout=10) == 0
    records = [json.loads(line) for line in calls.read_text().splitlines()]
    probes = [r for r in records if r["request"]["context"].get("backend_check")]
    assert {r["backend"] for r in probes} == {"local", "codex", "claude-code"}
    actual = [r for r in records if not r["request"]["context"].get("backend_check")]
    assert [r["backend"] for r in actual] == ["local", "codex"]
    assert actual[0]["request"]["context"] == actual[1]["request"]["context"]
    status = call(args, "--json", "status", "feature")
    assert status.returncode == 0, status.stderr
    after = json.loads(status.stdout)
    assert after["stage"] == "interview"
    assert after["worktree"] == before["worktree"]
    assert after["base"] == before["base"]
    assert len(calls.read_text().splitlines()) == len(records), "status must not contact providers"
    resumed = call(args, "--backend", "codex", "--json", "resume", "feature")
    assert json.loads(resumed.stdout)["stage"] == "interview"
    assert len(calls.read_text().splitlines()) == len(records), "a human answer is still required"


def test_json_returns_backend_choices_before_creating_a_task(repository, tmp_path):
    args, calls = configured_cli(repository, tmp_path, multiple=True)
    result = call(args, "--json", "start", "feature", "Fix increment", "--base", "main")
    assert result.returncode == 2, result.stderr
    view = json.loads(result.stdout)
    assert view["stage"] == "backend_selection"
    assert view["available_backends"] == ["claude-code", "codex"]
    assert all(
        json.loads(line)["request"]["context"]["backend_check"]
        for line in calls.read_text().splitlines()
    )
    assert not list((tmp_path / "state").glob("*/feature"))


def test_canceling_selection_creates_no_task(repository, tmp_path):
    args, calls = configured_cli(repository, tmp_path, multiple=True)
    with terminal_process([*args, "start", "feature", "Fix increment", "--base", "main"]) as (
        process,
        expect,
        send,
    ):
        expect("Backend number or name")
        send("quit")
        assert process.wait(timeout=10) == 130
    assert not list((tmp_path / "state").glob("*/feature"))
    assert all(
        json.loads(line)["request"]["context"]["backend_check"]
        for line in calls.read_text().splitlines()
    )

"""Exercise separate CLI processes against the same durable interview."""

import io
import json
import os
import shutil
import subprocess
import sys
import time
from contextlib import contextmanager

import pytest

from hydra_harness.cli import conversation, doctor, main, parser

from .conftest import SOURCE, SPECIFICATION, ScriptedAgent, git
from .helpers import executable


@pytest.mark.parametrize("answer", ["Support negative integers too.", "quit should save progress"])
def test_terminal_accepts_plain_answers_and_preserves_them(rig, monkeypatch, answer):
    harness, agent, _ = rig
    agent.responses["interviewer"].append(
        {"specification": SPECIFICATION, "questions": ["Which inputs should be supported?"]}
    )
    view = harness.start("plain-answer", "Fix increment", mode="phased")
    lines = iter([answer, "quit"])
    monkeypatch.setattr("builtins.input", lambda prompt: next(lines))
    view = conversation(harness, view)
    assert view["stage"] == "specification_review"
    assert agent.calls[-1][1]["messages"][-1]["text"] == answer
    assert harness.inspect("plain-answer")["prompt"] == view["prompt"]


def test_terminal_plain_questions_and_conditional_approval_do_not_approve(rig, monkeypatch):
    harness, agent, _ = rig
    view = harness.start("discuss", "Fix increment", mode="phased")
    lines = iter(["Why is this enough?", "approve only after we discuss edge cases", "quit"])
    monkeypatch.setattr("builtins.input", lambda prompt: next(lines))
    view = conversation(harness, view)
    assert view["stage"] == "specification_review"
    assert [call[1].get("question") for call in agent.calls[1:]] == [
        "Why is this enough?",
        "approve only after we discuss edge cases",
    ]
    assert set(view["prompt"]["actions"]) == {"question", "revise", "approve"}


def test_terminal_blank_input_does_not_call_an_agent(rig, monkeypatch):
    harness, agent, _ = rig
    view = harness.start("blank", "Fix increment", mode="phased")
    lines = iter(["", "   ", "approve"])
    monkeypatch.setattr("builtins.input", lambda prompt: next(lines))
    view = conversation(harness, view)
    assert len(agent.calls) == 1
    assert view["stage"] == "specification_ready"


@pytest.mark.parametrize(
    "json_mode,tty,term,animated",
    [
        (True, True, "xterm", False),
        (False, False, "xterm", False),
        (False, True, "dumb", False),
        (False, True, "xterm", True),
    ],
)
def test_cli_spinner_uses_only_interactive_stderr(
    repository, tmp_path, monkeypatch, capsys, json_mode, tty, term, animated
):
    class Terminal(io.StringIO):
        def isatty(self):
            return tty

    stderr = Terminal()
    monkeypatch.setattr(sys, "stderr", stderr)
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    monkeypatch.setenv("TERM", term)
    reply = {"specification": SPECIFICATION, "questions": []}
    backend = executable(
        tmp_path, "import time\ntime.sleep(0.15)\n" + f"print({json.dumps(reply)!r})\n"
    )
    config = tmp_path / "backend.toml"
    config.write_text(
        f'default_backend = "test"\n[backends.test]\ncommand = [{json.dumps(backend)}]\n'
    )
    assert (
        main(
            [
                "--repo",
                str(repository),
                "--state-dir",
                str(tmp_path / "state"),
                "--config",
                str(config),
                *(["--json"] if json_mode else []),
                "start",
                "spinner",
                "Fix increment",
                "--base",
                "main",
            ]
        )
        == 0
    )
    output = capsys.readouterr().out
    assert "\x1b" not in output
    assert ("\r\x1b[2K" in stderr.getvalue()) == animated
    if json_mode:
        assert json.loads(output)["stage"] == "specification_review"


@pytest.mark.skipif(os.name != "posix", reason="Requires a POSIX terminal")
@pytest.mark.parametrize("invalid_first", [False, True])
def test_terminal_interview_reads_an_answer_and_resumes_in_a_new_process(
    repository, tmp_path, invalid_first
):
    import pty
    import select

    capture = tmp_path / "requests.jsonl"
    backend = executable(
        tmp_path,
        "import json, sys\nfrom pathlib import Path\n"
        "request = json.load(sys.stdin)\n"
        f"with Path({str(capture)!r}).open('a') as stream:\n"
        "    stream.write(json.dumps(request) + '\\n')\n"
        f"if {invalid_first!r} and not request['context']['messages'] "
        "and 'response_repair' not in request['context']:\n"
        "    print('invalid first reply')\n    sys.exit(0)\n"
        "questions = [] if request['context']['messages'] else ['Which inputs should work?']\n"
        f"print(json.dumps({{'specification': {SPECIFICATION!r}, 'questions': questions}}))\n",
    )
    config = tmp_path / "backend.toml"
    config.write_text(
        'default_backend = "test"\n[backends.test]\ninput_format = "json"\n'
        f"command = [{json.dumps(backend)}]\n"
    )
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
    ]

    @contextmanager
    def terminal(*command):
        master, slave = pty.openpty()
        try:
            process = subprocess.Popen(
                [*args, *command],
                stdin=slave,
                stdout=slave,
                stderr=slave,
                env=dict(os.environ, TERM="xterm"),
            )
        finally:
            os.close(slave)
        output = b""

        def expect(text):
            nonlocal output
            deadline = time.monotonic() + 30
            while text.encode() not in output:
                remaining = deadline - time.monotonic()
                assert remaining > 0, output.decode(errors="replace")
                ready, _, _ = select.select([master], [], [], remaining)
                assert ready, output.decode(errors="replace")
                try:
                    chunk = os.read(master, 65536)
                except OSError:
                    chunk = b""
                assert chunk, output.decode(errors="replace")
                output += chunk
            output = output.split(text.encode(), 1)[1]

        try:
            yield process, expect, lambda line: os.write(master, (line + "\n").encode())
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)
            os.close(master)

    with terminal("start", "terminal", "Fix increment", "--base", "main") as (
        process,
        expect,
        send,
    ):
        expect("interviewer working")
        expect("Which inputs should work?")
        expect("> ")
        send("Negative integers too.")
        expect("terminal: specification review")
        expect("> ")
        send("quit")
        assert process.wait(timeout=10) == 0
    requests = [json.loads(line) for line in capture.read_text().splitlines()]
    assert len(requests) == 2 + invalid_first
    assert requests[-1]["context"]["messages"][-1]["text"] == "Negative integers too."

    with terminal("resume", "terminal") as (process, expect, send):
        expect("terminal: specification review")
        expect("> ")
        send("approve")
        expect("terminal: specification ready")
        assert process.wait(timeout=10) == 0
    assert len(capture.read_text().splitlines()) == 2 + invalid_first


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

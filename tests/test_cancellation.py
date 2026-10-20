"""Stopping generation preserves the task and allows corrected instructions."""

import json
import os
import signal
import sys
import threading
import time
from pathlib import Path

import pytest

from haskell_harness.backends import Backend, CommandAgent
from haskell_harness.failures import AgentInterrupted
from haskell_harness.process import ProcessError, run_process
from haskell_harness.workflow import Harness

from .conftest import SOURCE, SPECIFICATION, git, reach_test_review
from .helpers import executable, terminal_process


def interrupted(role, context, cwd):
    raise AgentInterrupted()


def test_stopped_interview_can_be_revised_after_reopening_without_automatic_retry(
    rig, repository, tmp_path
):
    harness, agent, executor = rig
    harness.agent = interrupted
    view = harness.start("stopped", "Build the original idea.")
    assert view["stage"] == "agent_paused"
    assert view["prompt"]["actions"] == ["revise", "retry"]
    assert view["prompt"]["draft"] == "Build the original idea."
    assert not list((harness.state_dir / "stopped/proposals").glob("*.json"))
    reopened = Harness(repository, tmp_path / "state", agent, executor)
    try:
        assert reopened.resume("stopped", retry_backend=True) == view
        assert not agent.calls
        changed = reopened.respond("stopped", "revise", text="Use this narrower scope instead.")
        assert changed["stage"] == "specification_review"
        assert agent.calls[-1][1]["messages"][-1]["text"] == "Use this narrower scope instead."
        assert not executor.calls
        assert git(Path(view["worktree"]), "rev-parse", "HEAD") == git(
            repository, "rev-parse", "HEAD"
        )
    finally:
        reopened.close()


def test_stopped_test_review_question_can_be_edited_without_approving_tests(rig):
    harness, agent, executor = rig
    original = reach_test_review(harness)
    checks = len(executor.calls)
    harness.agent = interrupted
    stopped = harness.respond("increment", "question", text="Explain the old question.")
    assert stopped["stage"] == "agent_paused"
    assert stopped["prompt"]["draft"] == "Explain the old question."
    harness.agent = agent
    revised = harness.respond("increment", "revise", text="Explain the negative input case.")
    assert revised["stage"] == "test_review"
    assert revised["prompt"]["diff"] == original["prompt"]["diff"]
    assert agent.calls[-1][1]["question"] == "Explain the negative input case."
    assert not any(role == "implementer" for role, _, _ in agent.calls)
    assert len(executor.calls) == checks


def test_retry_after_stop_uses_same_instructions(rig):
    harness, agent, _ = rig
    harness.agent = interrupted
    harness.start("stopped", "Keep this request.")
    harness.agent = agent
    view = harness.respond("stopped", "retry")
    assert view["stage"] == "specification_review"
    assert agent.calls[-1][1]["request"] == "Keep this request."
    assert agent.calls[-1][1]["messages"] == []


def test_stopping_cannot_hide_a_direct_checkout_edit(rig):
    harness, _, _ = rig

    def edits_then_stops(role, context, cwd):
        (cwd / SOURCE).write_text("-- Unexpected mutation\n")
        raise AgentInterrupted()

    harness.agent = edits_then_stops
    view = harness.start("stopped", "Inspect the code")
    assert view["stage"] == "agent_error"
    assert "changed the checkout" in view["prompt"]["error"]
    assert view["prompt"]["actions"] == ["retry"]


def test_cancellation_stops_a_process_without_waiting_for_backend_timeout(tmp_path):
    ready = tmp_path / "ready"
    script = executable(
        tmp_path,
        "import os, time\nfrom pathlib import Path\n"
        f"Path({str(ready)!r}).write_text(str(os.getpid()))\n"
        "print('partial output', flush=True)\ntime.sleep(60)\n",
    )
    cancel = threading.Event()

    def stop_when_started():
        deadline = time.monotonic() + 5
        while not ready.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        cancel.set()

    stopper = threading.Thread(target=stop_when_started)
    stopper.start()
    started = time.monotonic()
    with pytest.raises(ProcessError) as failure:
        run_process([script], tmp_path, timeout=60, cancel=cancel)
    stopper.join(timeout=5)
    assert failure.value.kind == "cancelled"
    assert "partial output" in failure.value.stdout
    assert time.monotonic() - started < 5
    with pytest.raises(ProcessLookupError):
        os.kill(int(ready.read_text()), 0)


def test_cancelled_backend_never_returns_its_partial_proposal(tmp_path):
    from haskell_harness.cancellation import ModelCancellation

    script = executable(
        tmp_path,
        "import os, signal, sys, time\nsys.stdin.read()\n"
        f"print({json.dumps({'specification': SPECIFICATION, 'questions': []})!r}, flush=True)\n"
        "os.kill(os.getppid(), signal.SIGINT)\ntime.sleep(60)\n",
    )
    previous = signal.getsignal(signal.SIGINT)
    with ModelCancellation() as cancellation:
        agent = CommandAgent(Backend(command=[script]), cancellation=cancellation)
        with pytest.raises(AgentInterrupted):
            agent("interviewer", {"request": "Original", "messages": []}, tmp_path)
    assert signal.getsignal(signal.SIGINT) == previous


def test_terminal_sigint_stops_langgraph_worker_and_reopens_editable_instructions(
    repository, tmp_path
):
    calls = tmp_path / "calls.jsonl"
    ready = tmp_path / "ready"
    backend = executable(
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
    config = tmp_path / "config.toml"
    config.write_text(
        'default_backend = "test"\n[backends.test]\ninput_format = "json"\n'
        f"command = [{json.dumps(backend)}]\n"
    )
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
        "start",
        "stopped",
        "Original instructions",
        "--base",
        "main",
    ]
    with terminal_process(args) as (process, expect, send):
        expect("Press Ctrl+C to stop generation")
        deadline = time.monotonic() + 5
        while not ready.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert ready.exists()
        process.send_signal(signal.SIGINT)
        expect("stopped: agent paused")
        with pytest.raises(ProcessLookupError):
            os.kill(int(ready.read_text()), 0)
        expect(">")
        send("revise")
        expect("Original instructions")
        send("\x01Revised: ")
        expect("stopped: specification review")
        expect(">")
        send("quit")
        assert process.wait(timeout=10) == 0
    requests = [json.loads(line) for line in calls.read_text().splitlines()]
    assert len(requests) == 2
    assert requests[-1]["context"]["messages"][-1]["text"] == "Revised: Original instructions"

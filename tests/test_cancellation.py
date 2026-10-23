"""Stopping generation preserves the task and allows corrected instructions."""

import json
import os
import signal
import threading
import time
from pathlib import Path

import pytest

from lambda_harness.backends import Backend, CommandAgent
from lambda_harness.failures import AgentInterrupted
from lambda_harness.process import ProcessError, run_process
from lambda_harness.workflow import Harness

from .conftest import SOURCE, SPECIFICATION, git, reach_test_review
from .helpers import executable


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
    assert view["prompt"]["actions"] == ["retry", "question"]


@pytest.mark.parametrize("check_number", [1, 2, 3, 4])
def test_stopping_implementation_checks_never_starts_repair_or_commits(rig, check_number):
    from lambda_harness.validation import ValidationInterrupted

    from .conftest import IMPLEMENTED, approve_tests

    harness, agent, executor = rig
    tests = reach_test_review(harness)
    checks = 0

    def stop_check(argv, cwd, **kwargs):
        nonlocal checks
        checks += 1
        if checks == check_number:
            raise ValidationInterrupted(argv, ProcessError("Stopped", kind="cancelled"))
        return executor(argv, cwd, **kwargs)

    harness.execute = stop_check
    stopped = approve_tests(harness, tests)
    assert stopped["stage"] == "validation_blocked"
    assert stopped["prompt"]["validation"]["status"] == "stopped"
    assert checks == check_number
    assert [role for role, _, _ in agent.calls].count("implementer") == 1
    worktree = Path(stopped["worktree"])
    assert (worktree / SOURCE).read_text() == IMPLEMENTED
    assert SOURCE in git(worktree, "diff", "--name-only")
    assert (
        "validated_fingerprint" not in harness.graph.get_state(harness._config("increment")).values
    )


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
    from lambda_harness.cancellation import ModelCancellation

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


def test_backend_authentication_check_can_be_stopped(tmp_path):
    from lambda_harness.cancellation import ModelCancellation

    ready = tmp_path / "ready"
    script = executable(
        tmp_path,
        f"import time\nfrom pathlib import Path\nPath({str(ready)!r}).touch()\ntime.sleep(60)\n",
    )
    cancellation = ModelCancellation()

    def stop_when_started():
        deadline = time.monotonic() + 5
        while not ready.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        cancellation.stop()

    stopper = threading.Thread(target=stop_when_started)
    stopper.start()
    try:
        agent = CommandAgent(Backend(command=[script], probe=[script]), cancellation=cancellation)
        with pytest.raises(ProcessError, match="cancelled"):
            agent.doctor(tmp_path)
        assert cancellation.active is None
        assert cancellation.kind is None
    finally:
        stopper.join(timeout=5)

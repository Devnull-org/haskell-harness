"""Browser execution choices carry across gates and stop at the first error."""

import pytest

from lambda_harness.contracts import Harness
from lambda_harness.failures import BackendError

from .test_phases import approved_tests, refactored, roles, specification


def test_automatic_run_finishes_remaining_phases(rig):
    harness, agent, _ = rig
    approved_tests(harness)
    view = harness.run_phase("task", "implement", automatic=True)
    assert view["stage"] == "ready"
    assert view["mode"] == "full"
    assert view["outcome"]["title"] == "Task complete"
    assert roles(agent)[-4:] == [
        "implementer",
        "refactorer",
        "haskell_reviewer",
        "security_reviewer",
    ]


def test_automatic_run_preserves_approval_and_mode_across_restart(rig, repository, tmp_path):
    harness, agent, executor = rig
    specification(harness)
    view = harness.run_phase("task", "tests", automatic=True)
    assert view["stage"] == "test_review"
    assert "implementer" not in roles(agent)
    assert view["outcome"]["kind"] == "waiting"
    harness.close()
    resumed = Harness(repository, tmp_path / "state", agent, executor, pause_on_error=True)
    try:
        assert resumed.inspect("task")["mode"] == "full"
        view = resumed.respond("task", "approve", approval_token=view["prompt"]["approval_token"])
        assert view["stage"] == "ready"
    finally:
        resumed.close()


def test_can_switch_back_to_single_step_at_approval(rig):
    harness, agent, _ = rig
    specification(harness)
    view = harness.run_phase("task", "tests", automatic=True)
    view = harness.respond(
        "task", "approve", approval_token=view["prompt"]["approval_token"], automatic=False
    )
    assert view["stage"] == "tests_ready"
    assert view["mode"] == "phased"
    assert "implementer" not in roles(agent)


def test_browser_error_pauses_immediately_then_selected_repair_continues(rig):
    harness, agent, executor = rig
    harness.pause_on_error = True
    approved_tests(harness)
    executor.lint_error = True
    view = harness.run_phase("task", "implement", automatic=True)
    assert view["stage"] == "validation_blocked"
    assert roles(agent).count("implementer") == 1
    assert "refactorer" not in roles(agent)
    assert view["outcome"]["kind"] == "error"
    assert "lint" in view["outcome"]["detail"]
    assert "Not run:" in view["outcome"]["detail"]
    assert {"repair", "question", "recheck", "revise"} <= set(view["prompt"]["actions"])
    assert harness.resume("task") == view
    executor.lint_error = False
    view = harness.respond("task", "repair", automatic=True)
    assert view["stage"] == "ready"
    assert roles(agent).count("implementer") == 2


@pytest.mark.parametrize("first", ["review-security", "review-haskell"])
def test_automatic_reviews_run_each_missing_review_once(rig, first):
    harness, agent, _ = rig
    refactored(harness)
    view = harness.run_phase("task", first, automatic=True)
    assert view["stage"] == "ready"
    assert roles(agent).count("haskell_reviewer") == 1
    assert roles(agent).count("security_reviewer") == 1


def test_backend_failure_is_reported_from_saved_state(rig, monkeypatch):
    harness, agent, _ = rig
    approved_tests(harness)

    def fail(*args, **kwargs):
        raise BackendError("provider failed", backend="test", kind="quota")

    monkeypatch.setattr(harness, "_ask", fail)
    view = harness.run_phase("task", "implement", automatic=True)
    assert view["stage"] == "backend_blocked"
    assert view["outcome"]["kind"] == "error"
    assert "usage limit" in view["outcome"]["detail"]
    assert "another backend" in view["outcome"]["guidance"]
    assert view["prompt"]["actions"] == ["retry", "question"]
    assert harness.inspect("task")["outcome"] == view["outcome"]


def test_automatic_repair_does_not_approve_a_protected_patch(rig, repository):
    from pathlib import Path

    from .conftest import TEST, TEST_CONTENT
    from .test_change_permissions import permission_gate

    harness, agent, executor = rig
    view = permission_gate(rig, repository)
    view = harness.respond("task", "question", text="Explain the proposal", automatic=True)
    assert view["stage"] == "test_change_review"
    assert view["mode"] == "full"
    assert (Path(view["worktree"]) / TEST).read_text() == TEST_CONTENT
    assert "refactorer" not in roles(agent)

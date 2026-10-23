"""A bounded repair loop pauses visibly and can continue through ordinary conversation."""

from pathlib import Path

import pytest

from .conftest import IMPLEMENTED, REFACTORED, SOURCE, TEST, TEST_CONTENT
from .test_change_permissions import approve, permission_gate


def blocked_implementation(rig, repository):
    harness, _, executor = rig
    view = permission_gate(rig, repository)
    executor.lint_error = True
    view = approve(harness, view)
    assert view["stage"] == "validation_blocked"
    expected_attempts = 1 if harness.pause_on_error else harness.max_repair_rounds
    assert harness._state("task")["attempts"] == expected_attempts
    return view


def test_blocked_implementation_reports_failures_and_allows_a_question(rig, repository):
    harness, agent, executor = rig
    view = blocked_implementation(rig, repository)
    assert view["prompt"]["actions"][0] == "question"
    assert "Validation failed: lint" in view["prompt"]["answer"]
    assert "paused after 2 attempts" in view["prompt"]["answer"]
    assert view["prompt"]["failure_details"][0]["check"] == "lint"
    updates = [m for m in harness._state("task")["messages"] if m.get("kind") == "work_update"]
    assert any("Applied patch:" in m["answer"] for m in updates)
    assert any("continuing with these failures" in m["answer"] for m in updates)
    assert "paused after 2 attempts" in updates[-1]["answer"]
    calls = len(executor.calls)
    view = harness.respond("task", "question", text="Why did you stop?")
    assert view["stage"] == "validation_blocked"
    assert agent.calls[-1][1]["intent"] == "discuss_implementation"
    assert agent.calls[-1][1]["validation"]["commands"]["lint"]["exit_code"] == 1
    assert "applied_proposal" in agent.calls[-1][1]
    assert len(executor.calls) == calls
    assert harness._state("task")["attempts"] == harness.max_repair_rounds


@pytest.mark.parametrize("role", ["implementer", "refactorer"])
def test_saved_blocked_task_accepts_repair_through_ask_a_question(rig, repository, role):
    harness, agent, executor = rig
    blocked_implementation(rig, repository)
    state = harness._state("task")
    state["role"] = role
    # Older checkpoints offer only recheck/revise. They must work without a task reset.
    state["prompt"] = {"validation": state["validation"], "actions": ["recheck", "revise"]}
    harness.graph.update_state(harness._config("task"), state, as_node="validate")
    executor.lint_error = False
    agent.responses[role].append(
        {
            "answer": "Simplified the function to resolve the lint failure.",
            "proposal": {
                "summary": "Use successor",
                "changes": [{"path": SOURCE, "content": REFACTORED}],
                "commit_message": "fix: resolve lint",
                "complete": True,
            },
        }
    )
    view = harness.respond("task", "question", text="Fix that and continue.")
    assert view["stage"] == ("implementation_ready" if role == "implementer" else "refactor_ready")
    path = Path(view["worktree"])
    assert (path / SOURCE).read_text() == REFACTORED
    assert (path / TEST).read_text() == TEST_CONTENT
    assert "Validation passed:" in harness._state("task")["messages"][-1]["answer"]
    assert harness._state("task")["commits"]


def test_conversational_repair_still_requests_permission_for_protected_changes(rig, repository):
    harness, agent, executor = rig
    blocked_implementation(rig, repository)
    modified = TEST_CONTENT + "-- Added coverage\n"
    agent.responses["implementer"].append(
        {
            "answer": "This repair needs a test change; review the proposed diff.",
            "proposal": {
                "summary": "Update test coverage",
                "changes": [{"path": TEST, "content": modified}],
                "commit_message": "test: update coverage",
            },
        }
    )
    calls = len(executor.calls)
    view = harness.respond("task", "question", text="Fix the coverage issue.")
    assert view["stage"] == "test_change_review"
    assert (Path(view["worktree"]) / TEST).read_text() == TEST_CONTENT
    assert len(executor.calls) == calls
    assert view["prompt"]["blocked_paths"] == [TEST]


def test_question_does_not_enable_coding_before_baseline_passes(rig, repository):
    from .test_existing_tests import passing_base
    from .test_phases import specification

    harness, agent, executor = rig
    passing_base(repository)
    specification(harness)
    executor.lint_error = True
    view = harness.run_phase("task", "implement")
    assert "question" in view["prompt"]["actions"]
    agent.responses["implementer"].append(
        {
            "answer": "The baseline must pass before implementation can start.",
            "next_action": None,
        }
    )
    view = harness.respond("task", "question", text="Can we implement anyway?")
    assert view["stage"] == "validation_blocked"
    assert agent.calls[-1][1]["intent"] == "recover_operation"
    assert (Path(view["worktree"]) / SOURCE).read_text() == IMPLEMENTED

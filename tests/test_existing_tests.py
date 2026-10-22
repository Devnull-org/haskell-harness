"""Developers can reuse passing coverage without skipping validation or approvals."""

from pathlib import Path

import pytest

from lambda_harness.failures import BackendResponseError

from .conftest import BROKEN, IMPLEMENTED, REFACTORED, SOURCE, TEST, TEST_CONTENT, git
from .test_phases import specification


def passing_base(repository):
    (repository / SOURCE).write_text(IMPLEMENTED)
    (repository / TEST).write_text(TEST_CONTENT)
    git(repository, "add", ".")
    git(repository, "commit", "-m", "Existing behavior and coverage")


@pytest.mark.parametrize(
    "phase,role,stage",
    [
        ("implement", "implementer", "implementation_ready"),
        ("refactor", "refactorer", "refactor_ready"),
    ],
)
def test_approved_scope_can_use_existing_tests(rig, repository, phase, role, stage):
    harness, agent, executor = rig
    passing_base(repository)
    view = specification(harness)
    assert set(view["prompt"]["available_phases"]) == {
        "explore",
        "interview",
        "tests",
        "implement",
        "refactor",
    }
    agent.responses[role].append(
        {
            "summary": "Simplify using existing coverage",
            "changes": [{"path": SOURCE, "content": REFACTORED}],
            "commit_message": "refactor: simplify increment",
            "complete": True,
        }
    )
    view = harness.run_phase("task", phase)
    assert view["stage"] == stage
    assert [r for r, _, _ in agent.calls] == ["interviewer", role]
    context = agent.calls[-1][1]
    assert context["test_strategy"] == "existing"
    assert context["validation"]["status"] == "green"
    assert TEST in context["approved_tests"]
    assert (Path(view["worktree"]) / TEST).read_text() == TEST_CONTENT
    assert len(executor.calls) == 8  # Baseline and the changed code each get all four checks.
    assert len(harness._state("task")["commits"]) == 1


def test_failed_baseline_never_calls_the_implementer_or_accepts_red(rig):
    harness, agent, _ = rig
    specification(harness)
    view = harness.run_phase("task", "implement")
    assert view["stage"] == "validation_blocked"
    assert set(view["prompt"]["actions"]) == {"recheck", "choose_phase"}
    assert [r for r, _, _ in agent.calls] == ["interviewer"]
    assert not harness._state("task")["commits"]
    view = harness.respond("task", "choose_phase")
    assert view["stage"] == "specification_ready"
    assert not harness._state("task").get("approved_tests")


def test_existing_test_path_never_commits_red_intermediate_patches(rig, repository):
    harness, agent, _ = rig
    passing_base(repository)
    specification(harness)
    for _ in range(2):
        agent.responses["implementer"].append(
            {
                "summary": "Incomplete rewrite",
                "changes": [{"path": SOURCE, "content": BROKEN}],
                "commit_message": "refactor: work in progress",
                "complete": False,
            }
        )
    view = harness.run_phase("task", "implement")
    assert view["stage"] == "validation_blocked"
    assert view["prompt"]["validation"]["phase"] == "green"
    assert "accept_red" not in view["prompt"]["actions"]
    assert "choose_phase" not in view["prompt"]["actions"]
    assert not harness._state("task")["commits"]


def test_existing_tests_still_need_review_before_changes(rig, repository):
    harness, agent, _ = rig
    passing_base(repository)
    specification(harness)
    agent.responses["implementer"].append(
        {
            "summary": "Remove test",
            "changes": [{"path": TEST, "content": "-- gone\n"}],
            "commit_message": "Remove coverage",
            "complete": True,
        }
    )
    view = harness.run_phase("task", "implement")
    assert view["stage"] == "test_change_review"
    assert (Path(view["worktree"]) / TEST).read_text() == TEST_CONTENT


def test_no_test_patch_is_a_scope_decision_not_a_schema_failure(rig):
    harness, agent, executor = rig
    specification(harness)
    agent.responses["test_author"].append(
        {
            "summary": "This is an assessment; no file changes are requested.",
            "changes": [],
            "expected_failures": [],
            "coverage": {},
            "commit_message": "No commit",
        }
    )
    view = harness.run_phase("task", "tests")
    assert view["stage"] == "test_scope_review"
    assert "assessment" in view["prompt"]["summary"]
    assert set(view["prompt"]["actions"]) == {"question", "revise", "choose_phase"}
    assert not executor.calls
    assert [r for r, _, _ in agent.calls].count("test_author") == 1
    harness.respond("task", "question", text="What scope is missing?")
    assert harness.respond("task", "choose_phase")["stage"] == "specification_ready"
    assert "implementer" not in [r for r, _, _ in agent.calls]


def test_preexisting_failed_test_generation_can_return_to_phase_selection(rig):
    harness, agent, _ = rig
    specification(harness)

    def failed(*args):
        raise BackendResponseError("Empty test changes", "{}", "")

    harness.agent = failed
    assert harness.run_phase("task", "tests")["stage"] == "backend_blocked"
    harness.agent = agent
    view = harness.inspect("task")
    assert "choose_phase" in view["prompt"]["actions"]
    before = len(agent.calls)
    view = harness.respond("task", "choose_phase")
    assert view["stage"] == "specification_ready"
    assert "interview" in view["prompt"]["available_phases"]
    assert len(agent.calls) == before


def test_changing_phase_does_not_discard_worktree_edits(rig):
    harness, _, _ = rig
    specification(harness)

    def failed(*args):
        raise BackendResponseError("Empty test changes", "{}", "")

    harness.agent = failed
    view = harness.run_phase("task", "tests")
    path = Path(view["worktree"]) / TEST
    path.write_text("-- Unfinished developer work\n")
    with pytest.raises(ValueError, match="changed|clean"):
        harness.respond("task", "choose_phase")
    assert path.read_text() == "-- Unfinished developer work\n"

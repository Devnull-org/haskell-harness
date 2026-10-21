"""Standalone tasks do not inherit the feature workflow's prerequisites or next steps."""

from pathlib import Path

import pytest

from haskell_harness.workflow import Harness

from .conftest import BROKEN, FAILURE_ID, IMPLEMENTED, SOURCE, TEST, TEST_CONTENT, git


def test_review_uses_merge_base_and_pinned_head_without_running_code(rig, repository):
    harness, agent, executor = rig
    common = git(repository, "rev-parse", "HEAD")
    git(repository, "checkout", "-b", "feature")
    (repository / SOURCE).write_text(IMPLEMENTED)
    git(repository, "commit", "-am", "Implement increment")
    head = git(repository, "rev-parse", "HEAD")
    git(repository, "checkout", "main")
    (repository / "base-only.txt").write_text("Unrelated base branch change")
    git(repository, "add", ".")
    git(repository, "commit", "-m", "Advance base")
    (repository / SOURCE).write_text("Local unfinished work")

    view = harness.start_review("review", "Check correctness", base="main", head="feature")
    assert view["stage"] == "standalone_review", view
    assert [role for role, _, _ in agent.calls] == ["haskell_reviewer", "security_reviewer"]
    assert not executor.calls
    assert view["base"] == common
    assert view["revision"] == head
    for _, context, cwd in agent.calls:
        assert "base-only.txt" not in context["diff"]
        assert "+addOne x = x + 1" in context["diff"]
        assert context["specification"] is None
        assert context["task_kind"] == "review"
        assert git(cwd, "status", "--porcelain") == ""
    assert (repository / SOURCE).read_text() == "Local unfinished work"

    view = harness.respond("review", "question", text="Could this overflow?")
    assert view["stage"] == "standalone_review"
    assert "answer" in view["prompt"]
    view = harness.respond("review", "done")
    assert view["stage"] == "review_complete"
    assert view["prompt"]["reviews"]
    assert view["prompt"]["available_phases"] == []
    with pytest.raises(ValueError, match="not available"):
        harness.run_phase("review", "implement")
    assert "Check correctness" in (Path(view["report_directory"]) / "review.md").read_text()

    # Restart reviews the saved revision even if the source branch moves.
    git(repository, "branch", "-f", "feature", "main")
    view = harness.restart("review")
    assert view["revision"] == head
    assert view["base"] == common


@pytest.mark.parametrize("expectation", ["red", "green"])
def test_tests_only_requires_review_and_stops_after_approved_commit(rig, repository, expectation):
    harness, agent, executor = rig
    if expectation == "green":
        (repository / SOURCE).write_text(IMPLEMENTED)
        git(repository, "commit", "-am", "Existing behavior")
    agent.responses["test_author"].append(
        {
            "summary": "Test increment",
            "changes": [{"path": TEST, "content": TEST_CONTENT}],
            "expected_failures": [FAILURE_ID] if expectation == "red" else [],
            "coverage": {"A1": [FAILURE_ID]},
            "commit_message": "test: cover increment",
        }
    )
    view = harness.start_tests(
        "tests",
        "Test increment",
        base="main",
        packages=["example-app"],
        criteria=["addOne 0 returns 1"],
        expectation=expectation,
    )
    assert view["stage"] == "test_review", view
    assert view["prompt"]["validation"]["status"] == (
        "expected_red" if expectation == "red" else "green"
    )
    assert [role for role, _, _ in agent.calls] == ["test_author"]
    assert len(executor.calls) == 4
    assert (repository / TEST).read_text() == "-- Existing tests\n"
    original_head = view["revision"]
    with pytest.raises(ValueError, match="approval token"):
        harness.respond("tests", "approve")
    view = harness.respond("tests", "question", text="What edge cases are covered?")
    assert view["revision"] == original_head
    view = harness.respond("tests", "approve", approval_token=view["prompt"]["approval_token"])
    assert view["stage"] == "tests_complete"
    assert view["revision"] != original_head
    assert view["prompt"]["available_phases"] == []
    assert {role for role, _, _ in agent.calls} == {"test_author"}
    assert (Path(view["worktree"]) / SOURCE).read_text() == (
        BROKEN if expectation == "red" else IMPLEMENTED
    )
    with pytest.raises(ValueError, match="not available"):
        harness.run_phase("tests", "implement")
    # The task survives a new process, including the explicit expected test outcome.
    reopened = Harness(repository, harness.state_dir.parent, agent, executor)
    try:
        assert reopened.inspect("tests")["stage"] == "tests_complete"
    finally:
        reopened.close()


@pytest.mark.parametrize(
    "field,value", [("packages", []), ("criteria", []), ("expectation", "maybe")]
)
def test_invalid_test_scope_does_not_start_a_task(rig, field, value):
    harness, agent, executor = rig
    args = {"packages": ["example-app"], "criteria": ["Increment by one"], "expectation": "red"}
    args[field] = value
    with pytest.raises(ValueError):
        harness.start_tests("bad", "Test it", **args)
    assert not harness.graph.get_state(harness._config("bad")).values
    assert not agent.calls and not executor.calls


def test_review_rejects_empty_diff_without_creating_task(rig):
    harness, agent, _ = rig
    with pytest.raises(ValueError, match="changes"):
        harness.start_review("empty", "Review", base="main", head="main")
    assert not harness.graph.get_state(harness._config("empty")).values
    assert not agent.calls


def test_review_questions_cannot_silently_change_the_reviewed_revision(rig, repository):
    harness, agent, _ = rig
    git(repository, "checkout", "-b", "feature")
    (repository / SOURCE).write_text(IMPLEMENTED)
    git(repository, "commit", "-am", "Change")
    view = harness.start_review(
        "review", "Review", base="main", head="feature", reviewers="security"
    )
    (Path(view["worktree"]) / SOURCE).write_text("Unreviewed change")
    view = harness.respond("review", "question", text="Is this safe?")
    assert view["stage"] == "agent_error"
    assert "pinned revision" in view["prompt"]["error"]
    assert len(agent.calls) == 1


def test_passing_tests_require_execution_of_the_named_tests(rig, repository):
    harness, agent, _ = rig
    (repository / SOURCE).write_text(IMPLEMENTED)
    git(repository, "commit", "-am", "Existing behavior")
    agent.responses["test_author"].append(
        {
            "summary": "Test increment",
            "changes": [{"path": TEST, "content": TEST_CONTENT}],
            "expected_failures": [],
            "coverage": {"A1": ["Never.executed"]},
            "commit_message": "test: add coverage",
        }
    )
    view = harness.start_tests(
        "coverage",
        "Test increment",
        packages=["example-app"],
        criteria=["Increment by one"],
        expectation="green",
    )
    assert view["stage"] == "validation_blocked"
    assert "Never.executed" in view["prompt"]["validation"]["evidence_error"]
    assert "approve" not in view["prompt"]["actions"]


def test_standalone_test_author_cannot_change_production_code(rig, repository):
    harness, agent, executor = rig
    agent.responses["test_author"].append(
        {
            "summary": "Implement instead",
            "changes": [{"path": SOURCE, "content": IMPLEMENTED}],
            "expected_failures": [FAILURE_ID],
            "coverage": {"A1": [FAILURE_ID]},
            "commit_message": "fix: increment",
        }
    )
    view = harness.start_tests(
        "tests", "Test increment", packages=["example-app"], criteria=["Increment"]
    )
    assert view["stage"] == "agent_error"
    assert (Path(view["worktree"]) / SOURCE).read_text() == BROKEN
    assert not executor.calls

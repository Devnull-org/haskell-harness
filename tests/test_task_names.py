"""Readable task names remain stable across persistence and worktree operations."""

from pathlib import Path

import pytest

from lambda_harness.workflow import Harness

from .conftest import git


def test_spaced_names_survive_resume_restart_and_deletion(rig, repository, tmp_path):
    harness, agent, executor = rig
    name = "Review documentation"
    view = harness.start(name, "Check the documentation")
    assert view["stage"] == "specification_review", view
    assert view["task_id"] == name
    original = Path(view["worktree"])
    branch = git(original, "branch", "--show-current")
    assert " " not in branch
    git(repository, "check-ref-format", "--branch", branch)
    similar = harness.start("Review-documentation", "A different task")
    assert git(Path(similar["worktree"]), "branch", "--show-current") != branch
    harness.close()
    resumed = Harness(repository, tmp_path / "state", agent, executor)
    try:
        assert resumed.resume(name) == view
        restarted = resumed.restart(name)
        assert restarted["task_id"] == name + "-restart-1"
        assert restarted["stage"] == "specification_review"
        plan = resumed.abandon_plan(name)
        resumed.abandon(name, confirmation_token=plan["confirmation_token"])
        assert not original.parent.exists()
        assert not git(repository, "branch", "--list", branch)
        assert resumed.inspect(similar["task_id"])["worktree"] == similar["worktree"]
        assert Path(restarted["worktree"]).exists()
    finally:
        resumed.close()


@pytest.mark.parametrize(
    "name",
    [
        "",
        " ",
        " Review",
        "Review ",
        "Review\ncode",
        "Review\tcode",
        "../Review",
        "Review/code",
        "a" * 65,
    ],
)
def test_invalid_names_do_not_create_tasks_or_call_models(rig, repository, name):
    harness, agent, _ = rig
    before = git(repository, "worktree", "list", "--porcelain")
    with pytest.raises(ValueError, match="Task name"):
        harness.start(name, "Check documentation")
    assert not agent.calls
    assert git(repository, "worktree", "list", "--porcelain") == before

"""Starting over preserves every previous attempt and its approval boundaries."""

from pathlib import Path

import pytest

from haskell_harness.workflow import Harness

from .conftest import SOURCE, git


def test_restart_creates_an_independent_task_and_keeps_the_old_worktree(rig):
    harness, agent, _ = rig
    original = harness.start("task", "Fix addOne", base="main")
    (Path(original["worktree"]) / "notes.txt").write_text("Keep my notes")
    restarted = harness.restart("task")
    assert restarted["task_id"] == "task-restart-1"
    assert restarted["restarted_from"] == "task"
    assert restarted["worktree"] != original["worktree"]
    assert restarted["base"] == original["base"]
    assert restarted["stage"] == "specification_review"
    assert harness.inspect("task")["prompt"] == original["prompt"]
    assert (Path(original["worktree"]) / "notes.txt").read_text() == "Keep my notes"
    assert not (Path(restarted["worktree"]) / "notes.txt").exists()
    assert agent.calls[-1][1]["request"] == "Fix addOne"
    with pytest.raises(ValueError, match="approval"):
        harness.respond(
            restarted["task_id"], "approve", approval_token=original["prompt"]["approval_token"]
        )


def test_restart_reuses_pinned_commit_and_survives_process_restart(rig, repository, tmp_path):
    harness, agent, executor = rig
    original = harness.start("task", "Fix it", base="main")
    (repository / SOURCE).write_text("-- Advance the base\n")
    git(repository, "commit", "-am", "Advance base")
    restarted = harness.restart("task")
    assert restarted["base"] == original["base"]
    harness.close()
    resumed = Harness(repository, tmp_path / "state", agent, executor)
    try:
        assert resumed.resume(restarted["task_id"]) == restarted
        assert resumed.inspect("task") == original
    finally:
        resumed.close()


def test_restart_can_choose_a_name_request_and_new_base(rig, repository):
    harness, agent, _ = rig
    harness.start("task", "Old request", base="main")
    (repository / SOURCE).write_text("-- Advance the base\n")
    git(repository, "commit", "-am", "Advance base")
    restarted = harness.restart("task", new_task_id="second", request="New request", base="main")
    assert restarted["task_id"] == "second"
    assert restarted["base"] == git(repository, "rev-parse", "HEAD")
    assert agent.calls[-1][1]["request"] == "New request"
    assert agent.calls[-1][1]["messages"] == []


def test_restart_never_overwrites_an_existing_task(rig):
    harness, _, _ = rig
    original = harness.start("task", "Fix it")
    with pytest.raises(ValueError, match="already exists"):
        harness.restart("task", new_task_id="task")
    with pytest.raises(ValueError, match="base"):
        harness.restart("task", base="missing")
    assert harness.inspect("task") == original
    assert harness.restart("task")["task_id"] == "task-restart-1"
    assert harness.restart("task")["task_id"] == "task-restart-2"


def test_restart_preserves_completed_patches(rig):
    harness, _, _ = rig
    original = harness.start("task", "Fix it")
    harness.respond("task", "approve", approval_token=original["prompt"]["approval_token"])
    review = harness.run_phase("task", "tests")
    original = harness.respond("task", "approve", approval_token=review["prompt"]["approval_token"])
    restarted = harness.restart("task")
    assert harness.inspect("task") == original
    assert restarted["revision"] == restarted["base"] != original["revision"]
    assert harness._state(restarted["task_id"])["commits"] == []


def test_restart_handles_maximum_length_task_names(rig):
    harness, _, _ = rig
    task_id = "a" * 64
    harness.start(task_id, "Fix it")
    restarted = harness.restart(task_id)
    assert len(restarted["task_id"]) <= 64
    assert restarted["task_id"].endswith("-restart-1")

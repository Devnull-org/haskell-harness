"""Tasks read a pinned base without borrowing the developer's working files."""

from pathlib import Path

import pytest

from hydra_harness.workflow import Harness

from .conftest import BROKEN, SOURCE, TEST, git


def test_dirty_checkout_is_untouched_and_interview_reads_selected_base(rig, repository):
    harness, agent, executor = rig
    base = git(repository, "rev-parse", "HEAD")
    git(repository, "branch", "task-base")
    (repository / SOURCE).write_text("-- Committed work on another branch\n")
    git(repository, "commit", "-am", "Unrelated work")
    (repository / SOURCE).write_text("-- Staged work\n")
    git(repository, "add", SOURCE)
    (repository / SOURCE).write_text("-- Unstaged work\n")
    (repository / "notes.txt").write_text("Untracked work\n")
    before = (
        git(repository, "rev-parse", "HEAD"),
        git(repository, "diff", "--binary"),
        git(repository, "diff", "--cached", "--binary"),
        git(repository, "status", "--porcelain"),
    )

    view = harness.start("task", "Fix addOne", base="task-base")
    assert view["stage"] == "specification_review"
    assert view["base_ref"] == "task-base"
    assert view["base"] == view["revision"] == base
    worktree = Path(view["worktree"])
    assert worktree != repository
    assert (worktree / SOURCE).read_text() == BROKEN
    assert not (worktree / "notes.txt").exists()
    assert agent.calls[0][2] == worktree
    assert agent.calls[0][1]["diff"] == ""
    assert agent.calls[0][1]["base_revision"] == base
    assert executor.calls == []
    assert before == (
        git(repository, "rev-parse", "HEAD"),
        git(repository, "diff", "--binary"),
        git(repository, "diff", "--cached", "--binary"),
        git(repository, "status", "--porcelain"),
    )
    assert (repository / "notes.txt").read_text() == "Untracked work\n"


def test_base_is_pinned_across_source_changes_and_process_restarts(rig, repository, tmp_path):
    harness, agent, executor = rig
    base = git(repository, "rev-parse", "HEAD")
    view = harness.start("task", "Fix addOne", base="main")
    (repository / SOURCE).write_text("-- New committed work\n")
    git(repository, "commit", "-am", "Advance the base branch")
    (repository / TEST).write_text("-- Pending developer tests\n")
    harness.close()

    resumed = Harness(repository, tmp_path / "state", agent, executor)
    try:
        assert resumed.resume("task") == view
        approved = resumed.respond(
            "task", "approve", approval_token=view["prompt"]["approval_token"]
        )
        assert approved["stage"] == "specification_ready"
        tests = resumed.run_phase("task", "tests")
        assert tests["stage"] == "test_review"
        assert tests["base"] == tests["revision"] == base
        assert tests["worktree"] == view["worktree"]
        assert (Path(tests["worktree"]) / SOURCE).read_text() == BROKEN
        assert (repository / TEST).read_text() == "-- Pending developer tests\n"
    finally:
        resumed.close()


@pytest.mark.parametrize("ref", ["origin/main", "release", "commit"])
def test_remote_tracking_branch_tag_and_commit_can_be_bases(rig, repository, ref):
    harness, _, _ = rig
    base = git(repository, "rev-parse", "HEAD")
    git(repository, "update-ref", "refs/remotes/origin/main", base)
    git(repository, "tag", "-a", "release", "-m", "Fixture release")
    ref = base if ref == "commit" else ref
    assert harness.start("task", "Fix it", base=ref)["base"] == base


@pytest.mark.parametrize("base", ["missing-branch", "", "--all", "HEAD:hydra-node"])
def test_invalid_base_does_not_reserve_task_or_call_agents(rig, repository, base):
    harness, agent, _ = rig
    before = git(repository, "worktree", "list", "--porcelain")
    with pytest.raises(ValueError, match="base|Base"):
        harness.start("task", "Fix it", base=base)
    assert not (harness.state_dir / "task").exists()
    assert agent.calls == []
    assert git(repository, "worktree", "list", "--porcelain") == before
    assert harness.start("task", "Fix it", base="main")["stage"] == "specification_review"


def test_task_worktree_edits_still_invalidate_specification_approval(rig):
    harness, _, _ = rig
    view = harness.start("task", "Fix it", base="HEAD")
    (Path(view["worktree"]) / SOURCE).write_text("-- Edited after interview\n")
    with pytest.raises(ValueError, match="stale"):
        harness.respond("task", "approve", approval_token=view["prompt"]["approval_token"])


def test_distinct_tasks_get_distinct_worktrees_and_branches(rig):
    harness, _, _ = rig
    first = harness.start("one", "Fix it", base="main")
    second = harness.start("two", "Explain it", base="main")
    assert first["worktree"] != second["worktree"]
    assert git(Path(first["worktree"]), "branch", "--show-current") != git(
        Path(second["worktree"]), "branch", "--show-current"
    )


def test_interrupted_worktree_creation_reuses_saved_base(rig, repository, tmp_path, monkeypatch):
    from hydra_harness import workflow

    harness, agent, executor = rig
    original = workflow.git
    interrupted = False
    base = git(repository, "rev-parse", "HEAD")

    def create_then_interrupt(cwd, *args):
        nonlocal interrupted
        result = original(cwd, *args)
        if args[:2] == ("worktree", "add") and not interrupted:
            interrupted = True
            raise KeyboardInterrupt
        return result

    monkeypatch.setattr(workflow, "git", create_then_interrupt)
    with pytest.raises(KeyboardInterrupt):
        harness.start("task", "Fix it", base="main")
    harness.close()
    (repository / SOURCE).write_text("-- Advance branch after interruption\n")
    git(repository, "commit", "-am", "Advance main")
    resumed = Harness(repository, tmp_path / "state", agent, executor)
    try:
        assert resumed.inspect("task")["stage"] == "preparing"
        view = resumed.resume("task")
        assert view["stage"] == "specification_review"
        assert view["base"] == view["revision"] == base
        assert len(agent.calls) == 1
        assert git(repository, "worktree", "list", "--porcelain").count("worktree ") == 2
    finally:
        resumed.close()

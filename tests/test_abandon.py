"""Abandoning an idea removes only its local task resources."""

import copy
import json
import os
import sqlite3
import sys
from pathlib import Path

import pytest

from .conftest import SOURCE, TEST, git, reach_test_review
from .helpers import terminal_process


def abandon(harness, task_id):
    plan = harness.abandon_plan(task_id)
    return harness.abandon(task_id, confirmation_token=plan["confirmation_token"])


@pytest.mark.parametrize("dirty", [False, True])
def test_abandon_deletes_selected_task_and_allows_reusing_its_name(rig, repository, dirty):
    harness, agent, _ = rig
    context = harness.context.refresh(repository, sources=["CLAUDE.md"])
    original = git(repository, "rev-parse", "HEAD")
    (repository / SOURCE).write_text("-- Developer's unfinished work\n")
    view = harness.start("discard", "An idea to discard")
    keep = harness.start("keep", "An idea to keep")
    worktree = Path(view["worktree"])
    branch = git(worktree, "branch", "--show-current")
    if dirty:
        (worktree / TEST).write_text("-- Local changes\n")
        (worktree / "notes.txt").write_text("Untracked task notes")
    call_count = len(agent.calls)
    assert abandon(harness, "discard") == {"task_id": "discard", "stage": "abandoned"}
    assert not worktree.parent.exists()
    assert str(worktree) not in git(repository, "worktree", "list", "--porcelain")
    assert not git(repository, "branch", "--list", branch)
    assert not list(harness.graph.checkpointer.list(harness._config("discard")))
    assert harness.inspect("keep") == keep
    assert harness.context.get()["id"] == context["id"]
    assert len(agent.calls) == call_count
    assert git(repository, "rev-parse", "HEAD") == original
    assert (repository / SOURCE).read_text() == "-- Developer's unfinished work\n"
    with pytest.raises(ValueError, match="Unknown task"):
        harness.resume("discard")
    assert harness.start("discard", "A different idea")["stage"] == "specification_review"


def test_abandon_requires_current_confirmation(rig):
    harness, _, _ = rig
    view = harness.start("discard", "An idea")
    old = harness.abandon_plan("discard")
    harness.respond("discard", "question", text="Why?")
    with pytest.raises(ValueError, match="changed"):
        harness.abandon("discard", confirmation_token=old["confirmation_token"])
    assert Path(view["worktree"]).exists()
    with pytest.raises(ValueError, match="confirmation"):
        harness.abandon("discard", confirmation_token="")


def test_abandon_removes_unmerged_task_commits(rig, repository):
    harness, _, _ = rig
    view = reach_test_review(harness)
    harness.respond("increment", "approve", approval_token=view["prompt"]["approval_token"])
    branch = git(Path(view["worktree"]), "branch", "--show-current")
    assert git(repository, "rev-list", "--count", f"main..{branch}") == "3"
    abandon(harness, "increment")
    assert not git(repository, "branch", "--list", branch)
    assert git(repository, "branch", "--show-current") == "main"


def test_abandon_respects_repository_lock(rig):
    harness, _, _ = rig
    view = harness.start("discard", "An idea")
    plan = harness.abandon_plan("discard")
    with harness._lock(), pytest.raises(RuntimeError, match="Another harness process"):
        harness.abandon("discard", confirmation_token=plan["confirmation_token"])
    assert Path(view["worktree"]).exists()


@pytest.mark.parametrize("replacement", ["symlink", "branch"])
def test_abandon_refuses_resources_that_no_longer_belong_to_task(rig, repository, replacement):
    harness, _, _ = rig
    view = harness.start("discard", "An idea")
    worktree = Path(view["worktree"])
    if replacement == "branch":
        git(worktree, "checkout", "-b", "developer-work")
    else:
        git(repository, "worktree", "remove", str(worktree))
        worktree.symlink_to(repository, target_is_directory=True)
    with pytest.raises(ValueError, match="worktree|Worktree|symlink"):
        harness.abandon_plan("discard")
    assert (repository / SOURCE).exists()
    assert worktree.exists()


def test_abandon_refuses_saved_paths_outside_task_directory(rig, repository):
    harness, _, _ = rig
    view = harness.start("discard", "An idea")
    state = copy.deepcopy(harness._state("discard"))
    state["directory"] = str(repository)
    harness.graph.update_state(harness._config("discard"), state)
    with pytest.raises(ValueError, match="directory"):
        harness.abandon_plan("discard")
    assert Path(view["worktree"]).exists()
    assert (repository / SOURCE).exists()


@pytest.mark.parametrize("failure", ["branch", "database", "interrupt"])
def test_interrupted_abandon_can_be_finished_without_resuming_agents(rig, monkeypatch, failure):
    from haskell_harness import cleanup

    harness, agent, _ = rig
    harness.start("discard", "An idea")
    calls = len(agent.calls)
    with monkeypatch.context() as patch:
        if failure in {"branch", "interrupt"}:
            original = cleanup.git

            def fail(repository, *args):
                if args[:2] == ("branch", "-D"):
                    if failure == "interrupt":
                        raise KeyboardInterrupt("Simulated deletion failure")
                    raise OSError("Simulated deletion failure")
                return original(repository, *args)

            patch.setattr(cleanup, "git", fail)
        else:

            def fail(task_id):
                raise sqlite3.OperationalError("Simulated deletion failure")

            patch.setattr(harness.graph.checkpointer, "delete_thread", fail)
        with pytest.raises(
            (OSError, RuntimeError, KeyboardInterrupt), match="Simulated deletion failure"
        ):
            abandon(harness, "discard")
    assert harness.inspect("discard")["stage"] == "abandoning"
    with pytest.raises(ValueError, match="deletion is unfinished"):
        harness.resume("discard")
    assert abandon(harness, "discard")["stage"] == "abandoned"
    assert len(agent.calls) == calls


def test_abandon_rechecks_uncommitted_files_before_deletion(rig):
    harness, _, _ = rig
    view = harness.start("discard", "An idea")
    plan = harness.abandon_plan("discard")
    (Path(view["worktree"]) / SOURCE).write_text("-- Changed while confirmation was open\n")
    with pytest.raises(ValueError, match="changed"):
        harness.abandon("discard", confirmation_token=plan["confirmation_token"])
    assert Path(view["worktree"]).exists()


def test_abandon_cleans_up_after_worktree_was_already_removed(rig, repository):
    harness, _, _ = rig
    view = harness.start("discard", "An idea")
    branch = git(Path(view["worktree"]), "branch", "--show-current")
    git(repository, "worktree", "remove", view["worktree"])
    abandon(harness, "discard")
    assert not git(repository, "branch", "--list", branch)
    assert not Path(view["report_directory"]).exists()


def test_abandon_does_not_remove_a_moved_worktree(rig, repository, tmp_path):
    harness, _, _ = rig
    view = harness.start("discard", "An idea")
    moved = tmp_path / "developer-worktree"
    git(repository, "worktree", "move", view["worktree"], str(moved))
    with pytest.raises(ValueError, match="another worktree"):
        harness.abandon_plan("discard")
    assert (moved / SOURCE).exists()


@pytest.mark.skipif(os.name != "posix", reason="Requires a POSIX terminal")
def test_terminal_abandon_can_cancel_then_delete_without_a_backend(rig, tmp_path):
    harness, agent, _ = rig
    view = harness.start("discard", "An idea")
    config = tmp_path / "invalid-config.toml"
    config.write_text("This is not TOML and must not be needed to delete a task.")
    args = [
        sys.executable,
        "-m",
        "haskell_harness.cli",
        "--repo",
        str(harness.repository),
        "--state-dir",
        str(tmp_path / "state"),
        "--config",
        str(config),
        "abandon",
        "discard",
    ]
    with terminal_process(args) as (process, expect, send):
        expect("Type 'discard' to delete it")
        send("")
        expect("Cancelled. The task is still saved.")
        assert process.wait(timeout=10) == 0
    assert harness.inspect("discard") == view
    with terminal_process(args) as (process, expect, send):
        expect("Type 'discard' to delete it")
        send("discard")
        expect("The saved task has been deleted.")
        assert process.wait(timeout=10) == 0
    assert not Path(view["worktree"]).exists()
    assert len(agent.calls) == 1
    with pytest.raises(ValueError, match="Unknown task"):
        harness.inspect("discard")


@pytest.mark.parametrize("yes", [False, True])
def test_noninteractive_abandon_requires_explicit_confirmation(rig, tmp_path, capsys, yes):
    from haskell_harness.cli import main

    harness, _, _ = rig
    view = harness.start("discard", "An idea")
    result = main(
        [
            "--repo",
            str(harness.repository),
            "--state-dir",
            str(tmp_path / "state"),
            "--json",
            "abandon",
            "discard",
            *(["--yes"] if yes else []),
        ]
    )
    assert result == (0 if yes else 1)
    assert Path(view["worktree"]).exists() is not yes
    output = capsys.readouterr()
    if yes:
        assert json.loads(output.out)["stage"] == "abandoned"
    else:
        assert "requires --yes" in output.err


def test_conversation_can_abandon_and_multiline_text_cannot(rig, monkeypatch):
    from haskell_harness.cli import conversation

    harness, agent, _ = rig
    view = harness.start("discard", "An idea")
    lines = iter(["abandon\nDiscuss this first", "abandon", "", "abandon", "discard"])
    monkeypatch.setattr("builtins.input", lambda prompt: next(lines))
    assert conversation(harness, view)["stage"] == "abandoned"
    assert agent.calls[-1][1]["question"] == "abandon\nDiscuss this first"
    assert len(agent.calls) == 2
    assert not Path(view["worktree"]).exists()

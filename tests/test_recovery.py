"""Recovery and integrity boundaries beyond the main workflow's happy path."""

from pathlib import Path

import pytest

from haskell_harness.contracts import Harness
from haskell_harness.workspace import Workspace

from .conftest import (
    IMPLEMENTED,
    SOURCE,
    TEST,
    approve_tests,
    git,
    reach_test_review,
)


def test_red_nix_exception_needs_evidence_then_separate_test_approval(rig):
    harness, agent, executor = rig
    executor.nix_error = True
    view = reach_test_review(harness)
    assert view["stage"] == "red_failure_review"
    with pytest.raises(ValueError, match="requires text"):
        harness.respond("increment", "accept_red", approval_token=view["prompt"]["approval_token"])
    view = harness.respond(
        "increment",
        "accept_red",
        approval_token=view["prompt"]["approval_token"],
        text="Inspected Nix log: only the named assertion fails.",
    )
    assert view["stage"] == "test_review"
    assert "red_exception" in view["prompt"]["validation"]
    assert not any(role == "implementer" for role, _, _ in agent.calls)
    executor.nix_error = False
    assert approve_tests(harness, view)["stage"] == "ready"


def test_duplicate_task_does_not_overwrite_checkpoint(rig):
    harness, agent, _ = rig
    view = harness.start("same", "Fix it")
    with pytest.raises(ValueError, match="already exists"):
        harness.start("same", "Replace it")
    assert harness.inspect("same") == view
    assert len(agent.calls) == 1


def test_proposals_cannot_cross_symlinks(repository, tmp_path):
    workspace = Workspace(repository)
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    (repository / "escape").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        workspace.apply([{"path": "escape/payload", "content": "changed"}])
    assert not (outside / "payload").exists()


def test_test_author_cannot_change_production_source(rig):
    harness, agent, _ = rig
    agent.responses["test_author"].append(
        {
            "summary": "Invalid test patch",
            "changes": [{"path": SOURCE, "content": IMPLEMENTED}],
            "expected_failures": ["Hydra.Example.adds one"],
            "coverage": {"A1": ["Hydra.Example.adds one"]},
            "commit_message": "test: invalid",
        }
    )
    view = reach_test_review(harness)
    assert view["stage"] == "agent_error"
    assert "production" in view["prompt"]["error"]


def test_crash_after_commit_does_not_duplicate_patch(rig, repository, tmp_path, monkeypatch):
    harness, agent, executor = rig
    view = reach_test_review(harness)
    original = Workspace.commit
    interrupted = False

    def commit_then_interrupt(self, *args, **kwargs):
        nonlocal interrupted
        revision = original(self, *args, **kwargs)
        if not interrupted:
            interrupted = True
            raise KeyboardInterrupt
        return revision

    monkeypatch.setattr(Workspace, "commit", commit_then_interrupt)
    with pytest.raises(KeyboardInterrupt):
        approve_tests(harness, view)
    harness.close()
    resumed = Harness(repository, tmp_path / "state", agent, executor)
    try:
        view = resumed.resume("increment")
        assert view["stage"] == "ready"
        subjects = git(Path(view["worktree"]), "log", "--format=%s").splitlines()
        assert subjects.count("test: specify increment behavior") == 1
        assert [r for r, _, _ in agent.calls].count("test_author") == 1
    finally:
        resumed.close()


def test_external_test_change_cannot_be_approved_via_question(rig):
    harness, _, _ = rig
    view = reach_test_review(harness)
    (Path(view["worktree"]) / TEST).write_text("-- edited after validation\n")
    view = harness.respond("increment", "question", text="What is covered?")
    view = approve_tests(harness, view)
    assert view["stage"] == "agent_error"
    assert "changed since validation" in view["prompt"]["error"]


@pytest.mark.parametrize("path", [".GIT/config", "docs/CHANGELOG.md", "nested/AGENTS.md"])
def test_protected_paths_include_case_aliases_and_nested_instructions(repository, path):
    with pytest.raises(ValueError, match="path"):
        Workspace(repository).apply([{"path": path, "content": "unsafe"}])


def test_production_edits_cannot_enter_a_revised_test_patch(rig):
    harness, agent, _ = rig
    view = reach_test_review(harness)
    (Path(view["worktree"]) / SOURCE).write_text(IMPLEMENTED)
    view = harness.respond("increment", "revise", text="Add a boundary case")
    assert view["stage"] == "agent_error"
    assert "production edits" in view["prompt"]["error"]
    assert [r for r, _, _ in agent.calls].count("test_author") == 1


def test_a_failed_question_can_be_retried_without_losing_the_gate(rig):
    harness, agent, _ = rig
    view = reach_test_review(harness)
    original = harness.agent
    failed = False

    def flaky(role, context, cwd):
        nonlocal failed
        if context.get("intent") == "explain" and not failed:
            failed = True
            raise RuntimeError("Temporary backend failure")
        return original(role, context, cwd)

    harness.agent = flaky
    view = harness.respond("increment", "question", text="What is covered?")
    assert view["stage"] == "agent_error"
    view = harness.respond("increment", "retry")
    assert view["stage"] == "test_review"
    assert view["prompt"]["answer"]
    assert not any(role == "implementer" for role, _, _ in agent.calls)


def test_recovery_rejects_a_commit_amended_after_validation(repository):
    workspace = Workspace(repository)
    workspace.apply([{"path": SOURCE, "content": IMPLEMENTED}])
    expected = workspace.fingerprint()
    workspace.commit("fix: increment", expected=expected, step="one")
    (repository / SOURCE).write_text(IMPLEMENTED + "-- Changed after validation\n")
    git(repository, "add", SOURCE)
    git(repository, "commit", "--amend", "--no-edit")
    with pytest.raises(ValueError, match="differs from validated"):
        workspace.commit("fix: increment", expected=expected, step="one")


def test_switching_agents_preserves_the_pending_test_approval(rig, repository, tmp_path):
    from .conftest import ScriptedAgent

    harness, _, executor = rig
    pending = reach_test_review(harness)
    harness.close()
    replacement = ScriptedAgent()
    resumed = Harness(repository, tmp_path / "state", replacement, executor)
    try:
        restored = resumed.inspect("increment")
        assert restored["prompt"]["approval_token"] == pending["prompt"]["approval_token"]
        assert replacement.calls == []
        view = resumed.respond("increment", "question", text="Explain this assertion")
        assert view["stage"] == "test_review"
        assert not any(role == "implementer" for role, _, _ in replacement.calls)
        assert approve_tests(resumed, view)["stage"] == "ready"
        assert all(
            context.get("intent") == "explain"
            for role, context, _ in replacement.calls
            if role == "test_author"
        )
    finally:
        resumed.close()

"""One reviewed patch grants permission; later patches need their own decisions."""

import copy
from pathlib import Path

import pytest

from .conftest import IMPLEMENTED, REFACTORED, SOURCE, TEST, TEST_CONTENT
from .test_existing_tests import CABAL, CABAL_CONTENT, dependency_base, dependency_patch
from .test_phases import specification


def permission_gate(rig, repository, changes=None, reason=None):
    harness, agent, executor = rig
    dependency_base(repository)
    specification(harness)
    patch = dependency_patch(CABAL_CONTENT.replace("base", "base, servant"))
    if changes is not None:
        patch["changes"] = changes
    if reason:
        patch["permission_request"] = reason
    agent.responses["implementer"].append(patch)
    view = harness.run_phase("task", "implement")
    assert view["stage"] == "test_change_review"
    assert len(executor.calls) == 4  # Only the unchanged baseline has been checked.
    return view


def approve(harness, view):
    return harness.respond(
        "task", "approve_changes", approval_token=view["prompt"]["approval_token"]
    )


def test_approved_mixed_patch_applies_tests_cabal_and_source_then_validates(rig, repository):
    harness, agent, executor = rig
    changed_test = TEST_CONTENT + "-- Additional coverage\n"
    content = CABAL_CONTENT.replace("base", "base, servant")
    view = permission_gate(
        rig,
        repository,
        changes=[
            {"path": SOURCE, "content": REFACTORED},
            {"path": CABAL, "content": content},
            {"path": TEST, "content": changed_test},
        ],
        reason="Add test support and dependencies for the implementation.",
    )
    assert "test support" in view["prompt"]["permission_reason"]
    assert "+-- Additional coverage" in view["prompt"]["diff"]
    path = Path(view["worktree"])
    assert (path / TEST).read_text() == TEST_CONTENT
    assert (path / SOURCE).read_text() == IMPLEMENTED
    calls = len(agent.calls)
    view = approve(harness, view)
    assert view["stage"] == "implementation_ready"
    assert len(agent.calls) == calls  # Applies the reviewed patch, not a new model response.
    assert (path / CABAL).read_text() == content
    assert (path / TEST).read_text() == changed_test
    assert (path / SOURCE).read_text() == REFACTORED
    assert len(executor.calls) == 8
    receipt = next(
        m for m in harness._state("task")["messages"] if m.get("action") == "approve_changes"
    )
    assert receipt["action"] == "approve_changes"
    assert set(receipt["approved_paths"]) == {SOURCE, CABAL, TEST}


def test_model_can_request_permission_for_an_ordinary_source_patch(rig, repository):
    harness, _, _ = rig
    view = permission_gate(
        rig,
        repository,
        changes=[{"path": SOURCE, "content": REFACTORED}],
        reason="This changes the public behavior; please confirm the proposed approach.",
    )
    assert view["prompt"]["blocked_paths"] == []
    assert "public behavior" in view["prompt"]["permission_reason"]
    assert approve(harness, view)["stage"] == "implementation_ready"


def test_approved_build_configuration_changes_are_applied_and_rediscovered(rig, repository):
    harness, agent, executor = rig
    view = permission_gate(
        rig,
        repository,
        changes=[
            {"path": "cabal.project", "content": "packages: example-app\ntests: True\n-- setup\n"},
            {"path": SOURCE, "content": REFACTORED},
        ],
    )
    discovery_calls = len(agent.discovery_calls)
    assert approve(harness, view)["stage"] == "implementation_ready"
    assert len(agent.discovery_calls) > discovery_calls
    assert len(executor.calls) == 8


@pytest.mark.parametrize("action", ["deny_changes", "revise_changes"])
def test_denial_or_revision_returns_to_coder_without_applying_the_patch(rig, repository, action):
    harness, agent, executor = rig
    view = permission_gate(rig, repository)
    agent.responses["implementer"].append(
        {
            "summary": "Implement without the proposed dependency changes",
            "changes": [{"path": SOURCE, "content": REFACTORED}],
            "complete": True,
            "commit_message": "refactor: simplify",
        }
    )
    view = harness.respond(
        "task",
        action,
        text="Use existing dependencies.",
        approval_token=view["prompt"]["approval_token"],
    )
    assert view["stage"] == "implementation_ready"
    assert (Path(view["worktree"]) / CABAL).read_text() == CABAL_CONTENT
    assert agent.calls[-1][1]["messages"][-1]["action"] == action
    assert agent.calls[-1][1].get("intent") != "explain"
    assert len(executor.calls) == 8


def test_permission_is_invalid_after_worktree_changes(rig, repository):
    harness, _, executor = rig
    view = permission_gate(rig, repository)
    with pytest.raises(ValueError, match="token"):
        harness.respond("task", "approve_changes", approval_token="old")
    (Path(view["worktree"]) / SOURCE).write_text("-- external edit\n")
    with pytest.raises(ValueError, match="changed"):
        approve(harness, view)
    assert len(executor.calls) == 4


def test_later_test_changes_require_another_permission(rig, repository):
    harness, agent, _ = rig
    view = permission_gate(rig, repository)
    view = approve(harness, view)
    agent.responses["refactorer"].append(
        {
            "summary": "Adjust a test for refactoring",
            "changes": [{"path": TEST, "content": TEST_CONTENT + "-- refactored\n"}],
            "complete": True,
            "commit_message": "test: refactor",
        }
    )
    view = harness.run_phase("task", "refactor")
    assert view["stage"] == "test_change_review"
    assert (Path(view["worktree"]) / TEST).read_text() == TEST_CONTENT


def test_old_saved_gate_gets_permission_preview_and_can_be_approved(rig, repository):
    harness, _, _ = rig
    permission_gate(rig, repository)
    state = harness._state("task")
    state["prompt"].pop("diff")
    state["prompt"]["actions"] = ["question", "revise", "reject"]
    harness.graph.update_state(harness._config("task"), state, as_node="implement")
    view = harness.resume("task")
    assert "approve_changes" in view["prompt"]["actions"]
    assert "+  build-depends: base, servant" in view["prompt"]["diff"]
    assert approve(harness, view)["stage"] == "implementation_ready"


def test_approval_does_not_skip_failed_validation_or_commit_failed_code(rig, repository):
    harness, agent, executor = rig
    view = permission_gate(rig, repository)
    executor.force_red = True
    calls = len(agent.calls)
    view = approve(harness, view)
    assert view["stage"] == "validation_blocked"
    assert not harness._state("task")["commits"]
    assert len(agent.calls) > calls  # Coding agent gets validation evidence for repair.
    assert agent.calls[-1][1]["validation"]["status"] == "blocked"


@pytest.mark.parametrize("legacy", [False, True])
@pytest.mark.parametrize("role", ["implementer", "refactorer"])
def test_question_can_revise_pending_patch_before_approval(rig, repository, legacy, role):
    harness, agent, executor = rig
    view = permission_gate(rig, repository)
    state = harness._state("task")
    state["role"] = role
    if legacy:
        state["prompt"].pop("diff")
        state["prompt"]["actions"] = ["question", "revise", "reject"]
    harness.graph.update_state(harness._config("task"), state, as_node="implement")
    original = copy.deepcopy(state["proposed_test_change"])
    revised = copy.deepcopy(original)
    comment = "-- Increment the input using the successor operation.\n"
    revised["changes"] = [
        {"path": SOURCE, "content": REFACTORED + comment},
        *[change for change in revised["changes"] if change["path"] != SOURCE],
    ]
    agent.responses[role].append({"answer": "Added the code comment.", "proposal": revised})
    updated = harness.respond("task", "question", text="Add a comment explaining the function.")
    assert updated["stage"] == "test_change_review"
    assert updated["prompt"]["proposal"] == revised
    assert "+" + comment in updated["prompt"]["diff"]
    assert "+  build-depends: base, servant" in updated["prompt"]["diff"]
    assert updated["prompt"]["blocked_paths"] == [CABAL]
    assert updated["prompt"]["answer"] == "Added the code comment."
    assert agent.calls[-1][0] == role
    assert agent.calls[-1][1]["intent"] == "discuss_patch"
    assert agent.calls[-1][1]["current_proposal"] == original
    assert (Path(updated["worktree"]) / SOURCE).read_text() == IMPLEMENTED
    assert len(executor.calls) == 4
    with pytest.raises(ValueError, match="token"):
        approve(harness, view)

    # A subsequent explanation must preserve the revised proposal, including on resume.
    updated = harness.respond("task", "question", text="Why use successor here?")
    assert agent.calls[-1][1]["current_proposal"] == revised
    assert updated["prompt"]["proposal"] == revised
    assert harness.resume("task")["prompt"]["diff"] == updated["prompt"]["diff"]
    assert len(executor.calls) == 4
    result = approve(harness, updated)
    assert result["stage"] == (
        "implementation_ready" if role == "implementer" else "refactor_ready"
    )
    assert (Path(result["worktree"]) / SOURCE).read_text() == REFACTORED + comment
    assert len(executor.calls) == 8


def test_question_without_revision_preserves_the_original_patch(rig, repository):
    harness, agent, executor = rig
    view = permission_gate(rig, repository)
    original = copy.deepcopy(harness._state("task")["proposed_test_change"])
    updated = harness.respond("task", "question", text="Can you explain the dependency change?")
    assert agent.calls[-1][1]["current_proposal"] == original
    assert updated["prompt"]["diff"] == view["prompt"]["diff"]
    assert updated["prompt"]["proposal"] == original
    assert harness._state("task")["proposed_test_change"] == original
    assert len(executor.calls) == 4


def test_empty_revision_is_repaired_before_updating_the_patch(rig, repository):
    harness, agent, _ = rig
    permission_gate(rig, repository)
    original = copy.deepcopy(harness._state("task")["proposed_test_change"])
    revised = copy.deepcopy(original)
    revised["changes"][0]["content"] += "-- Dependency needed by the implementation.\n"
    agent.responses["implementer"].extend(
        [
            {"answer": "Added comments.", "proposal": {**original, "changes": []}},
            {"answer": "Here is the revised patch.", "proposal": revised},
        ]
    )
    view = harness.respond("task", "question", text="Add comments.")
    assert "response_repair" in agent.calls[-1][1]
    assert "revised file changes" in agent.calls[-1][1]["response_repair"]["validation_error"]
    assert view["prompt"]["answer"] == "Here is the revised patch."
    assert view["prompt"]["proposal"] == revised

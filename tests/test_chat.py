"""General chat stays conversational, persistent, and read-only."""

from pathlib import Path

import pytest

from haskell_harness.failures import AgentInterrupted, BackendError
from haskell_harness.workflow import Harness

from .conftest import SOURCE, git

REPLY = {"answer": "A pure function returns a result without performing IO.", "references": []}


def start_chat(harness, agent):
    agent.responses["chat"].append(REPLY)
    return harness.start("conversation", "Explain pure functions.", phase="chat")


def test_chat_answers_directly_without_feature_gates_or_code_changes(rig, repository):
    harness, agent, executor = rig
    view = start_chat(harness, agent)
    assert view["stage"] == "chat", view
    assert view["task_kind"] == "chat"
    assert view["prompt"]["actions"] == ["answer"]
    assert "specification" not in view["prompt"]
    assert harness._state("conversation")["messages"][-1]["answer"] == REPLY["answer"]
    assert [role for role, _, _ in agent.calls] == ["chat"]
    assert agent.calls[0][1]["task_kind"] == "chat"
    assert not executor.calls
    assert git(Path(view["worktree"]), "status", "--porcelain") == ""
    assert git(Path(view["worktree"]), "rev-parse", "HEAD") == git(repository, "rev-parse", "HEAD")
    assert REPLY["answer"] in (Path(view["report_directory"]) / "chat.md").read_text()
    for action in ("approve", "done"):
        with pytest.raises(ValueError, match="not available"):
            harness.respond("conversation", action)
    with pytest.raises(ValueError, match="pending"):
        harness.run_phase("conversation", "implement")


def test_chat_keeps_context_and_can_continue_after_reopening(rig, repository, tmp_path):
    harness, agent, executor = rig
    snapshot = harness.context.refresh(repository, sources=["CLAUDE.md"])
    view = start_chat(harness, agent)
    reopened = Harness(repository, tmp_path / "state", agent, executor)
    try:
        assert reopened.resume("conversation") == view
        assert len(agent.calls) == 1
        agent.responses["chat"].append({"answer": "addOne is pure.", "references": [f"{SOURCE}:3"]})
        view = reopened.respond("conversation", "answer", text="Show me an example here.")
        context = agent.calls[-1][1]
        assert context["messages"][0]["answer"] == REPLY["answer"]
        assert context["messages"][-1]["text"] == "Show me an example here."
        assert context["project_context"]
        assert view["project_context_id"] == snapshot["id"]
        assert view["stage"] == "chat"
        assert view["prompt"]["references"] == [f"{SOURCE}:3"]
        transcript = (Path(view["report_directory"]) / "chat.md").read_text()
        assert REPLY["answer"] in transcript and "Show me an example here." in transcript
        assert "addOne is pure." in transcript
        assert not executor.calls
    finally:
        reopened.close()


@pytest.mark.parametrize("failure", [AgentInterrupted(), BackendError("Usage limit", kind="quota")])
def test_chat_failure_keeps_the_question_and_previous_answer(rig, failure):
    harness, agent, executor = rig
    view = start_chat(harness, agent)
    report = Path(view["report_directory"]) / "chat.md"
    previous = report.read_text()

    def unavailable(*args):
        raise failure

    harness.agent = unavailable
    view = harness.respond("conversation", "answer", text="What about IO?")
    assert view["stage"] in {"agent_paused", "backend_blocked"}
    assert report.read_text() == previous
    assert harness._state("conversation")["messages"][-1]["text"] == "What about IO?"
    harness.agent = agent
    agent.responses["chat"].append({"answer": "IO represents effects.", "references": []})
    if isinstance(failure, AgentInterrupted):
        view = harness.respond("conversation", "revise", text="Actually, explain laziness.")
        assert agent.calls[-1][1]["messages"][-1]["text"] == "Actually, explain laziness."
        assert (
            agent.calls[-1][1]["messages"][-1]["replaces_interrupted_instructions"]
            == "What about IO?"
        )
    else:
        view = harness.respond("conversation", "retry")
        assert agent.calls[-1][1]["messages"][-1]["text"] == "What about IO?"
    assert view["stage"] == "chat"
    assert not executor.calls


def test_chat_restart_preserves_original_conversation(rig):
    harness, agent, _ = rig
    original = start_chat(harness, agent)
    agent.responses["chat"].append(REPLY)
    fresh = harness.restart("conversation")
    assert fresh["stage"] == "chat" and fresh["task_kind"] == "chat"
    assert fresh["worktree"] != original["worktree"]
    assert harness.inspect("conversation") == original


def test_chat_direct_edits_are_rejected(rig, repository):
    harness, _, executor = rig
    original = (repository / SOURCE).read_text()

    def edits(role, context, cwd):
        (cwd / SOURCE).write_text("-- Unrequested edit\n")
        return REPLY

    harness.agent = edits
    view = harness.start("conversation", "Explain this code.", phase="chat")
    assert view["stage"] == "agent_error"
    assert "changed the checkout" in view["prompt"]["error"]
    assert (repository / SOURCE).read_text() == original
    assert not (Path(view["report_directory"]) / "chat.md").exists()
    assert not executor.calls

"""General chat stays conversational, persistent, and read-only."""

from pathlib import Path

import pytest

from lambda_harness.failures import AgentInterrupted, BackendError
from lambda_harness.workflow import Harness

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
        return {**REPLY, "memory_note": "This must not be saved"}

    harness.agent = edits
    view = harness.start("conversation", "Explain this code.", phase="chat")
    assert view["stage"] == "agent_error"
    assert "changed the checkout" in view["prompt"]["error"]
    assert (repository / SOURCE).read_text() == original
    assert not (Path(view["report_directory"]) / "chat.md").exists()
    assert harness.context.latest_id() is None
    assert not executor.calls


def test_chat_saves_requested_note_to_current_memory_and_keeps_other_tasks_pinned(rig):
    harness, agent, executor = rig
    first = harness.context.save(
        harness.repository, notes="Original rule", sources=["CLAUDE.md"], expected_current=None
    )
    start_chat(harness, agent)
    agent.responses["chat"].append(REPLY)
    other = harness.start("other", "Discuss the project", phase="chat")
    current = harness.context.save(
        harness.repository, notes="Newer rule", sources=["CLAUDE.md"], expected_current=first["id"]
    )
    note = "Every job must be safe to retry."
    agent.responses["chat"].append({**REPLY, "memory_note": note})
    view = harness.respond("conversation", "answer", text="Save this to project memory: " + note)
    saved = harness.context.get()
    assert saved["notes"] == "Newer rule\n\n" + note
    assert saved["documents"] == current["documents"]
    assert view["project_context_id"] == saved["id"]
    assert harness.inspect("other")["project_context_id"] == other["project_context_id"]
    receipt = harness._state("conversation")["messages"][-1]["memory_save"]
    assert receipt == {"status": "saved", "note": note, "snapshot": saved["id"]}
    transcript = (Path(view["report_directory"]) / "chat.md").read_text()
    assert (
        "Saved to project memory" in transcript and note in transcript and saved["id"] in transcript
    )
    agent.responses["chat"].append(REPLY)
    harness.respond("conversation", "answer", text="What do you remember?")
    assert agent.calls[-1][1]["project_context"]["notes"] == saved["notes"]
    assert not executor.calls


def test_first_chat_message_can_save_memory_and_normal_messages_do_not(rig):
    harness, agent, _ = rig
    agent.responses["chat"].append({**REPLY, "memory_note": "Keep the core pure."})
    view = harness.start(
        "remember", "Save this to project memory: keep the core pure.", phase="chat"
    )
    assert view["stage"] == "chat"
    saved = harness.context.get()
    assert saved["notes"] == "Keep the core pure."
    agent.responses["chat"].append({**REPLY, "memory_note": None})
    harness.respond("remember", "answer", text="Can we discuss that choice?")
    assert harness.context.get() == saved
    assert len(harness.context.history()) == 1


def test_chat_memory_limit_reports_failure_without_claiming_success_or_crashing(rig):
    harness, agent, _ = rig
    current = harness.context.save(
        harness.repository, notes="x" * 32000, sources=[], expected_current=None
    )
    agent.responses["chat"].append(
        {"answer": "Saved!", "references": [], "memory_note": "New rule"}
    )
    view = harness.start("full", "Save this to project memory: New rule", phase="chat")
    assert view["stage"] == "chat" and view["prompt"]["actions"] == ["answer"]
    assert harness.context.get() == current
    message = harness._state("full")["messages"][-1]
    assert message["memory_save"]["status"] == "error"
    assert message["memory_save"]["note"] == "New rule"
    assert "32,000" in message["memory_save"]["error"]
    assert "Saved!" not in message["answer"]
    assert "not saved" in message["answer"]


def test_chat_memory_save_is_not_repeated_when_recovering_after_a_crash(rig, monkeypatch):
    harness, agent, _ = rig
    original = harness.context.append_note

    def saved_then_crashed(*args, **kwargs):
        original(*args, **kwargs)
        raise SystemExit("Simulated process crash before checkpoint")

    monkeypatch.setattr(harness.context, "append_note", saved_then_crashed)
    agent.responses["chat"].append({**REPLY, "memory_note": "Retry safely."})
    with pytest.raises(SystemExit):
        harness.start("crash", "Save this to project memory: retry safely.", phase="chat")
    saved = harness.context.get()
    monkeypatch.setattr(harness.context, "append_note", original)
    view = harness.resume("crash")
    assert view["stage"] == "chat"
    assert harness.context.get() == saved
    assert len(harness.context.history()) == 1
    assert len(agent.calls) == 1
    assert len(harness._state("crash")["messages"]) == 1


def test_retry_after_report_failure_reuses_the_saved_note_and_chat_reply(rig, monkeypatch):
    harness, agent, _ = rig
    gate = harness._gate
    failed = False

    def fail_first_chat_report(state, stage, prompt, actions):
        nonlocal failed
        if stage == "chat" and not failed:
            failed = True
            raise OSError("Report write interrupted")
        return gate(state, stage, prompt, actions)

    monkeypatch.setattr(harness, "_gate", fail_first_chat_report)
    agent.responses["chat"].append({**REPLY, "memory_note": "Keep retrying safely."})
    view = harness.start(
        "retry", "Save this to project memory: keep retrying safely.", phase="chat"
    )
    assert view["stage"] == "agent_error"
    saved = harness.context.get()
    view = harness.respond("retry", "retry")
    assert view["stage"] == "chat"
    assert len(harness._state("retry")["messages"]) == 1
    assert harness.context.get() == saved
    assert len(harness.context.history()) == 1
    assert len(agent.calls) == 1

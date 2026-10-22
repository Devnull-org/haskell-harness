"""Interview turns remain ordered, including tasks saved before responses were recorded."""

import copy
import json
from pathlib import Path

from lambda_harness.workflow import Harness

from .conftest import SPECIFICATION


def interview(agent, question):
    agent.responses["interviewer"].append(
        {"specification": copy.deepcopy(SPECIFICATION), "questions": [question]}
    )


def test_interview_keeps_every_response_between_developer_replies(rig, repository):
    harness, agent, executor = rig
    interview(agent, "Which inputs?")
    harness.start("task", "Fix addOne")
    interview(agent, "What about negative inputs?")
    harness.respond("task", "answer", text="Integers.")
    harness.respond("task", "answer", text="Include negatives.")
    messages = harness._state("task")["messages"]
    assert [m.get("role", "user") for m in messages] == [
        "interviewer",
        "user",
        "interviewer",
        "user",
        "interviewer",
    ]
    assert messages[0]["questions"] == ["Which inputs?"]
    assert messages[2]["questions"] == ["What about negative inputs?"]
    assert messages[4]["specification"] == SPECIFICATION
    assert messages[4]["questions"] == []
    reopened = Harness(repository, harness.state_dir.parent, agent, executor)
    try:
        reopened.resume("task")
        assert reopened._state("task")["messages"] == messages
        assert len(agent.calls) == 3
    finally:
        reopened.close()


def legacy_interview(harness, agent):
    interview(agent, "First saved question")
    harness.start("legacy", "Fix addOne")
    interview(agent, "Second saved question")
    harness.respond("legacy", "answer", text="First developer reply")
    state = copy.deepcopy(harness._state("legacy"))
    state["messages"] = [m for m in state["messages"] if m.get("role") != "interviewer"]
    for path in (Path(state["directory"]) / "steps").glob("*-interview.json"):
        saved = json.loads(path.read_text())
        saved["messages"] = [m for m in saved["messages"] if m.get("role") != "interviewer"]
        path.write_text(json.dumps(saved))
    harness.graph.update_state(harness._config("legacy"), state)
    harness.graph.invoke(None, harness._config("legacy"))
    return copy.deepcopy(harness._state("legacy"))


def test_legacy_interview_responses_are_recovered_without_rewriting_checkpoints(rig):
    from lambda_harness.conversation import conversation_messages

    harness, agent, _ = rig
    state = legacy_interview(harness, agent)
    before = copy.deepcopy(state)
    restored = conversation_messages(state)
    assert [m.get("text") or m["questions"][0] for m in restored] == [
        "First saved question",
        "First developer reply",
        "Second saved question",
    ]
    assert harness._state("legacy") == before
    assert conversation_messages({**state, "messages": restored}) == restored
    interview(agent, "Third saved question")
    harness.respond("legacy", "answer", text="Second developer reply")
    updated = harness._state("legacy")["messages"]
    assert updated[:3] == restored
    assert updated[-2]["text"] == "Second developer reply"
    assert updated[-1]["questions"] == ["Third saved question"]
    assert conversation_messages(harness._state("legacy")) == updated


def test_history_recovery_ignores_corrupt_or_unrelated_journal_entries(rig):
    from lambda_harness.conversation import conversation_messages

    harness, agent, _ = rig
    state = legacy_interview(harness, agent)
    journals = sorted((Path(state["directory"]) / "steps").glob("*-interview.json"))
    journals[0].write_text("incomplete JSON")
    saved = json.loads(journals[1].read_text())
    saved["messages"][0]["text"] = "A different conversation"
    journals[1].write_text(json.dumps(saved))
    assert conversation_messages(state) == state["messages"]

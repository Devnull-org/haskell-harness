"""Agents receive their own conversation and explicit workflow handoffs."""

import copy
import json

import pytest

from lambda_harness.failures import AgentInterrupted
from lambda_harness.workflow import Harness

from .conftest import IMPLEMENTED, SOURCE, SPECIFICATION, TEST, approve_tests, git


def test_tdd_agents_start_without_other_agents_conversations(rig, repository):
    harness, agent, _ = rig
    snapshot = harness.context.refresh(repository, sources=["CLAUDE.md"])
    harness.start("increment", "Fix addOne", mode="full")
    agent.responses["interviewer"].append({"answer": "INTERVIEW_PRIVATE_EXPLANATION"})
    view = harness.respond("increment", "question", text="INTERVIEW_PRIVATE_QUESTION")
    view = harness.respond("increment", "approve", approval_token=view["prompt"]["approval_token"])
    agent.responses["test_author"].append({"answer": "TEST_AUTHOR_PRIVATE_EXPLANATION"})
    view = harness.respond("increment", "question", text="TEST_AUTHOR_PRIVATE_QUESTION")
    view = approve_tests(harness, view)
    assert view["stage"] == "ready"

    for role, context, _ in agent.calls:
        if role not in {"interviewer", "test_author"}:
            assert "PRIVATE" not in json.dumps(context)
            assert context["messages"] == []
            assert TEST in context["approved_tests"]
            assert context["validation"]["commands"]
        if role != "interviewer":
            assert "INTERVIEW_PRIVATE" not in json.dumps(context)
        assert context["project_context"]["id"] == snapshot["id"]
        if role != "interviewer":
            assert context["specification"] == SPECIFICATION
        for patch in context["patches"]:
            if patch["role"] != role:
                assert "summary" not in patch
    # Prompt filtering must not remove the developer's visible history.
    transcript = json.dumps(harness._state("increment")["messages"])
    assert "INTERVIEW_PRIVATE_EXPLANATION" in transcript
    assert "TEST_AUTHOR_PRIVATE_EXPLANATION" in transcript


def test_own_questions_survive_reopening_without_leaking_to_next_agent(rig, repository):
    harness, agent, executor = rig
    harness.start("task", "Fix addOne")
    agent.responses["interviewer"].append({"answer": "OWN_INTERVIEW_ANSWER"})
    harness.respond("task", "question", text="OWN_INTERVIEW_QUESTION")
    reopened = Harness(repository, harness.state_dir.parent, agent, executor)
    try:
        view = reopened.respond("task", "question", text="Explain that answer.")
        assert agent.calls[-1][1]["messages"][-1]["answer"] == "OWN_INTERVIEW_ANSWER"
        reopened.respond("task", "approve", approval_token=view["prompt"]["approval_token"])
        reopened.run_phase("task", "tests")
        assert "OWN_INTERVIEW" not in json.dumps(agent.calls[-1][1])
    finally:
        reopened.close()


def test_reviewers_never_receive_peer_findings_or_peer_followup_answers(rig, repository):
    harness, agent, _ = rig
    git(repository, "checkout", "-b", "feature")
    (repository / SOURCE).write_text(IMPLEMENTED)
    git(repository, "commit", "-am", "Implement increment")
    roles = ["haskell_reviewer", "security_reviewer", "documentation_reviewer"]
    for role in roles:
        agent.responses[role].append({"findings": [], "summary": f"PRIVATE_REPORT_{role}"})
    harness.start_review("review", "Check changes", base="main", head="feature", reviewers="all")
    for role in roles:
        agent.responses[role].append({"answer": f"PRIVATE_ANSWER_{role}"})
    harness.respond("review", "question", text="Explain the evidence.")
    harness.respond("review", "question", text="What else did you check?")
    for role, context, _ in agent.calls[3:]:
        serialized = json.dumps(context)
        assert f"PRIVATE_REPORT_{role}" in serialized
        for peer in set(roles) - {role}:
            assert f"PRIVATE_REPORT_{peer}" not in serialized
            assert f"PRIVATE_ANSWER_{peer}" not in serialized
    for role, context, _ in agent.calls[-3:]:
        assert f"PRIVATE_ANSWER_{role}" in json.dumps(context)
    transcript = json.dumps(harness._state("review")["messages"])
    assert all(f"PRIVATE_ANSWER_{role}" in transcript for role in roles)

    harness.respond("review", "revise", text="Focus on boundary cases.")
    for _, context, _ in agent.calls[-3:]:
        assert "PRIVATE_" not in json.dumps(context)
        assert context["messages"][-1]["text"] == "Focus on boundary cases."


@pytest.mark.parametrize(
    "role", ["test_author", "implementer", "refactorer", "haskell_reviewer", "security_reviewer"]
)
def test_only_coding_agents_receive_explicitly_selected_repairs(rig, role):
    harness, _, _ = rig
    harness.start("task", "Fix addOne")
    state = copy.deepcopy(harness._state("task"))
    selected = [{"id": "SEC-1", "title": "SELECTED_REPAIR"}]
    state["selected_findings"] = selected
    state["exploration"] = {"analysis": "EXPLORER_PRIVATE_ANALYSIS"}
    context = harness._context(state, role=role)
    assert "EXPLORER_PRIVATE_ANALYSIS" not in json.dumps(context)
    if role.endswith("reviewer"):
        assert "SELECTED_REPAIR" not in json.dumps(context)
    else:
        assert context["selected_findings"] == selected


def test_legacy_mixed_answers_are_not_replayed_to_reviewers(rig):
    harness, _, _ = rig
    harness.start("task", "Fix addOne")
    state = copy.deepcopy(harness._state("task"))
    state["messages"] = [
        {"question": "Earlier question", "answer": "UNATTRIBUTED_COMBINED_ANSWER"},
        {"stage": "interview", "action": "answer", "text": "INTERVIEW_ONLY"},
    ]
    before = copy.deepcopy(state)
    context = harness._context(state, role="security_reviewer", intent="explain")
    assert context["messages"] == []
    assert state == before


def test_feature_review_question_receives_only_its_own_review_packet(rig):
    harness, agent, _ = rig
    harness.start("task", "Fix addOne")
    state = copy.deepcopy(harness._state("task"))
    state.update(
        stage="review_findings",
        haskell_reviewer={"findings": [], "summary": "CODE_REVIEW_PRIVATE"},
        security_reviewer={"findings": [], "summary": "SECURITY_REVIEW_PRIVATE"},
        prompt={
            "findings": ["CODE_REVIEW_PRIVATE", "SECURITY_REVIEW_PRIVATE"],
            "actions": ["question"],
        },
        response={"action": "question", "text": "Explain SEC-1"},
    )
    harness._reply(state)
    role, context, _ = agent.calls[-1]
    assert role == "security_reviewer"
    assert context["review_packet"] == state["security_reviewer"]
    assert "CODE_REVIEW_PRIVATE" not in json.dumps(context)


def test_interrupted_implementation_revisions_stay_with_the_implementer(rig):
    harness, agent, _ = rig
    view = harness.start("increment", "Fix addOne", mode="full")
    view = harness.respond("increment", "approve", approval_token=view["prompt"]["approval_token"])

    def stop_implementation(role, context, cwd):
        if role == "implementer":
            raise AgentInterrupted()
        return agent(role, context, cwd)

    harness.agent = stop_implementation
    stopped = approve_tests(harness, view)
    assert stopped["stage"] == "agent_paused"
    harness.agent = agent
    view = harness.respond("increment", "revise", text="IMPLEMENTER_ONLY_INSTRUCTION")
    assert view["stage"] == "ready"
    implementation = next(context for role, context, _ in agent.calls if role == "implementer")
    assert implementation["messages"][-1]["text"] == "IMPLEMENTER_ONLY_INSTRUCTION"
    for role, context, _ in agent.calls:
        if role == "refactorer" or role.endswith("reviewer"):
            assert "IMPLEMENTER_ONLY_INSTRUCTION" not in json.dumps(context)


def test_reviewers_keep_tool_evidence_without_peer_posted_findings(rig):
    harness, _, _ = rig
    harness.start("task", "Fix addOne")
    state = copy.deepcopy(harness._state("task"))
    tool_result = {"stage": "review_action", "action": "check_links", "summary": "LINK_EVIDENCE"}
    state["messages"] = [
        tool_result,
        {
            "stage": "review_action",
            "action": "post_review_comment",
            "role": "security_reviewer",
            "finding_id": "SEC-1",
        },
    ]
    for intent in ("review", "explain"):
        context = harness._context(state, "documentation_reviewer", intent=intent)
        assert context["messages"] == [tool_result]
        security = harness._context(state, "security_reviewer", intent=intent)
        assert security["messages"] == state["messages"]

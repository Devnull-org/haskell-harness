"""Exploration delivers analysis and can end without starting a coding workflow."""

import copy
import json
import subprocess
import sys
from pathlib import Path

import pytest

from hydra_harness.cli import conversation
from hydra_harness.failures import BackendError
from hydra_harness.workflow import Harness

from .conftest import SOURCE, SPECIFICATION, git
from .helpers import executable, terminal_process

ANALYSIS = {
    "summary": "A pure policy and an explicit effect/result boundary are viable alternatives.",
    "analysis": (
        "The current function returns its input unchanged.\n\n"
        "A pure policy can reject an input before the state transition. An external policy "
        "needs an effect and a matching result input; performing IO inside the pure "
        "transition is not an option. Approval must still match the current state."
    ),
    "references": [f"{SOURCE}:3 — the current pure function"],
    "open_questions": ["Would a pure policy have enough information?"],
}


def research(harness, agent):
    agent.responses["explorer"].append(ANALYSIS)
    return harness.start(
        "research", "Explore the alternatives without implementing.", phase="explore"
    )


def test_exploration_delivers_analysis_and_saves_it_without_interview_or_code(rig, repository):
    harness, agent, executor = rig
    view = research(harness, agent)
    assert view["stage"] == "exploration"
    assert view["prompt"]["exploration"]["analysis"] == ANALYSIS["analysis"]
    assert set(view["prompt"]["actions"]) == {"question", "done"}
    assert "specification" not in view["prompt"]
    assert [call[0] for call in agent.calls] == ["explorer"]
    assert not executor.calls
    assert git(Path(view["worktree"]), "status", "--porcelain") == ""
    assert git(Path(view["worktree"]), "rev-parse", "HEAD") == git(repository, "rev-parse", "HEAD")
    report = Path(view["prompt"]["report"])
    assert ANALYSIS["analysis"] in report.read_text()
    assert SOURCE in report.read_text()
    assert json.loads(report.with_suffix(".json").read_text())["analysis"] == ANALYSIS["analysis"]
    with pytest.raises(ValueError, match="not available"):
        harness.respond("research", "approve", approval_token=view["prompt"]["approval_token"])
    completed = harness.respond("research", "done")
    assert completed["stage"] == "exploration_ready"
    assert completed["prompt"]["available_phases"] == ["explore", "interview"]
    assert harness.resume("research") == completed
    assert len(agent.calls) == 1
    with pytest.raises(ValueError, match="not available"):
        harness.run_phase("research", "tests")


def test_existing_approved_interview_can_run_exploration_with_its_scope_and_context(
    rig, repository
):
    harness, agent, executor = rig
    snapshot = harness.context.refresh(repository, sources=["CLAUDE.md"])
    view = harness.start("research", "Read this issue and explore our options.")
    view = harness.respond("research", "revise", text="Only explore validation before application.")
    approved = harness.respond(
        "research", "approve", approval_token=view["prompt"]["approval_token"]
    )
    # Reproduce a saved task from before exploration was added.
    state = copy.deepcopy(harness._state("research"))
    state.pop("initial_phase", None)
    state["prompt"]["available_phases"] = ["tests"]
    harness.graph.update_state(harness._config("research"), state, as_node="finish")
    assert harness.inspect("research")["prompt"]["available_phases"] == ["explore", "tests"]
    agent.responses["explorer"].append(ANALYSIS)
    view = harness.run_phase("research", "explore")
    assert view["stage"] == "exploration"
    assert view["worktree"] == approved["worktree"]
    assert view["project_context_id"] == snapshot["id"]
    role, context, cwd = agent.calls[-1]
    assert role == "explorer"
    assert context["specification"] == SPECIFICATION
    assert context["messages"][0]["text"] == "Only explore validation before application."
    assert context["project_context"]
    assert cwd == Path(approved["worktree"])
    assert not executor.calls


def test_exploration_questions_and_results_survive_new_process_and_done(rig, repository, tmp_path):
    harness, agent, executor = rig
    view = research(harness, agent)
    reopened = Harness(repository, tmp_path / "state", agent, executor)
    try:
        assert reopened.resume("research") == view
        reply = {**ANALYSIS, "analysis": "The pure policy uses candidate data and configuration."}
        agent.responses["explorer"].append(reply)
        follow_up = reopened.respond("research", "question", text="Focus only on a pure policy.")
        context = agent.calls[-1][1]
        assert context["exploration"]["analysis"] == ANALYSIS["analysis"]
        assert context["messages"][-1]["text"] == "Focus only on a pure policy."
        assert follow_up["prompt"]["exploration"]["analysis"] == reply["analysis"]
        assert reply["analysis"] in Path(follow_up["prompt"]["report"]).read_text()
        assert len(agent.calls) == 2
        completed = reopened.respond("research", "done")
        assert completed["stage"] == "exploration_ready"
        assert len(agent.calls) == 2
        assert not executor.calls
    finally:
        reopened.close()


def test_coding_after_research_requires_new_interview_and_test_approval(rig):
    harness, agent, executor = rig
    research(harness, agent)
    harness.respond("research", "done")
    view = harness.run_phase("research", "interview")
    assert view["stage"] == "specification_review"
    assert agent.calls[-1][1]["intent"] == "implementation_interview"
    assert agent.calls[-1][1]["exploration"]["analysis"] == ANALYSIS["analysis"]
    assert not executor.calls
    with pytest.raises(ValueError, match="pending"):
        harness.run_phase("research", "tests")
    harness.respond("research", "approve", approval_token=view["prompt"]["approval_token"])
    assert harness.run_phase("research", "tests")["stage"] == "test_review"
    assert "implementer" not in [call[0] for call in agent.calls]


def test_restart_repeats_exploration_without_changing_previous_discussion(rig):
    harness, agent, _ = rig
    original = research(harness, agent)
    agent.responses["explorer"].append(ANALYSIS)
    fresh = harness.restart("research")
    assert fresh["stage"] == "exploration"
    assert fresh["worktree"] != original["worktree"]
    assert harness.inspect("research") == original
    assert [call[0] for call in agent.calls] == ["explorer", "explorer"]


@pytest.mark.parametrize("text", ["done after answering this", "done\nExplain the trade-off."])
def test_finishing_requires_done_on_its_own(rig, monkeypatch, text):
    harness, agent, _ = rig
    view = research(harness, agent)
    agent.responses["explorer"].append(ANALYSIS)
    inputs = iter([text, "quit"])
    monkeypatch.setattr("builtins.input", lambda prompt: next(inputs))
    view = conversation(harness, view)
    assert view["stage"] == "exploration"
    assert agent.calls[-1][1]["messages"][-1]["text"] == text


def test_exploration_quota_failure_preserves_question_and_previous_report(rig):
    harness, agent, executor = rig
    view = research(harness, agent)
    report = Path(view["prompt"]["report"])
    before = report.read_text()
    calls = []

    def unavailable(role, context, cwd):
        calls.append(role)
        raise BackendError("Usage limit reached", kind="quota")

    harness.agent = unavailable
    blocked = harness.respond("research", "question", text="What about state changes?")
    assert blocked["stage"] == "backend_blocked"
    assert blocked["prompt"]["backend_failure"]["kind"] == "quota"
    assert calls == ["explorer"]
    assert report.read_text() == before
    harness.agent = agent
    agent.responses["explorer"].append(ANALYSIS)
    resumed = harness.resume("research", retry_backend=True)
    assert resumed["stage"] == "exploration"
    assert agent.calls[-1][1]["messages"][-1]["text"] == "What about state changes?"
    assert agent.calls[-1][1]["exploration"]["analysis"] == ANALYSIS["analysis"]
    assert not executor.calls


def test_explorer_cannot_return_only_a_specification(rig):
    harness, agent, executor = rig
    agent.responses["explorer"].extend(
        [{"specification": SPECIFICATION, "questions": []}, ANALYSIS]
    )
    view = harness.start("research", "Explore the options", phase="explore")
    assert view["stage"] == "exploration"
    assert view["prompt"]["exploration"]["analysis"] == ANALYSIS["analysis"]
    repair = agent.calls[-1][1]["response_repair"]
    assert "analysis" in repair["validation_error"]
    assert len(agent.calls) == 2
    assert not executor.calls


def test_explorer_direct_edits_are_rejected_before_saving_analysis(rig, repository):
    harness, _, executor = rig
    original = (repository / SOURCE).read_text()

    def edits(role, context, cwd):
        (cwd / SOURCE).write_text("-- An unauthorized change\n")
        return ANALYSIS

    harness.agent = edits
    view = harness.start("research", "Explore the options", phase="explore")
    assert view["stage"] == "agent_error"
    assert "changed the checkout" in view["prompt"]["error"]
    assert not (Path(view["report_directory"]) / "exploration.md").exists()
    assert (repository / SOURCE).read_text() == original
    assert not executor.calls


@pytest.mark.parametrize("existing_interview", [False, True])
def test_terminal_delivers_analysis_then_resumes_follow_up_in_another_process(
    repository, tmp_path, existing_interview
):
    capture = tmp_path / "requests.jsonl"
    backend = executable(
        tmp_path,
        "import json, sys\nfrom pathlib import Path\n"
        "request = json.load(sys.stdin)\n"
        f"with Path({str(capture)!r}).open('a') as stream:\n"
        "    stream.write(json.dumps(request) + '\\n')\n"
        "if request['role'] == 'explorer':\n"
        f"    reply = {ANALYSIS!r}\n"
        "    if request['context']['messages']:\n"
        "        reply['analysis'] = 'Follow-up analysis: approval must match the current state.'\n"
        "else:\n"
        f"    reply = {{'specification': {SPECIFICATION!r}, 'questions': []}}\n"
        "print(json.dumps(reply))\n",
    )
    config = tmp_path / "backend.toml"
    config.write_text(
        'default_backend = "test"\n[backends.test]\ninput_format = "json"\n'
        f"command = [{json.dumps(backend)}]\n"
    )
    args = [
        sys.executable,
        "-m",
        "hydra_harness.cli",
        "--repo",
        str(repository),
        "--state-dir",
        str(tmp_path / "state"),
        "--config",
        str(config),
    ]
    if existing_interview:
        with terminal_process(
            [*args, "start", "research", "Explore the options", "--base", "main"]
        ) as (process, expect, send):
            expect("research: specification review")
            expect(">")
            send("approve")
            expect("research: specification ready")
            assert process.wait(timeout=10) == 0
        command = ["run", "research", "explore"]
    else:
        command = ["explore", "research", "--base", "main"]

    with terminal_process([*args, *command]) as (process, expect, send):
        if not existing_interview:
            expect("What do you want to explore?")
            expect(">")
            send("Explore the options")
        expect(ANALYSIS["summary"])
        expect(ANALYSIS["analysis"].split("\n")[0])
        expect("Saved analysis:")
        expect(">")
        send("quit")
        assert process.wait(timeout=10) == 0
    requests = [json.loads(line) for line in capture.read_text().splitlines()]
    assert [item["role"] for item in requests] == (
        ["interviewer", "explorer"] if existing_interview else ["explorer"]
    )
    assert "analysis" in requests[-1]["schema"]["required"]
    assert "Do not produce another specification" in requests[-1]["prompt"]

    with terminal_process([*args, "resume", "research"]) as (process, expect, send):
        expect(ANALYSIS["analysis"].split("\n")[0])
        expect(">")
        send("What if state changes?")
        expect("Follow-up analysis: approval must match the current state.")
        expect(">")
        send("done")
        expect("research: exploration ready")
        expect("Saved analysis:")
        assert process.wait(timeout=10) == 0
    requests = [json.loads(line) for line in capture.read_text().splitlines()]
    assert len(requests) == (3 if existing_interview else 2)
    context = requests[-1]["context"]
    assert context["messages"][-1]["text"] == "What if state changes?"
    assert context["exploration"]["analysis"] == ANALYSIS["analysis"]
    status = subprocess.run(
        [*args, "--json", "status", "research"], check=True, capture_output=True, text=True
    )
    view = json.loads(status.stdout)
    assert view["prompt"]["available_phases"] == ["explore", "interview"]
    assert "Follow-up analysis" in Path(view["prompt"]["report"]).read_text()
    assert git(Path(view["worktree"]), "rev-parse", "HEAD") == git(repository, "rev-parse", "HEAD")
    assert git(Path(view["worktree"]), "status", "--porcelain") == ""

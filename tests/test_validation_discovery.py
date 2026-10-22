"""Validation uses inspected project conventions before executing any check."""

import copy
from pathlib import Path

import pytest

from lambda_harness.backends import prompt_for
from lambda_harness.validation import ValidationConfig, classify_validation
from lambda_harness.validation import test_evidence as evidence
from lambda_harness.validation_discovery import project_inventory, source_hashes
from lambda_harness.workspace import Workspace

from .conftest import ScriptedAgent, command_result, git
from .test_existing_tests import passing_base
from .test_phases import specification


def discovered(*, detailed=False, questions=()):
    plan = ScriptedAgent()("validation_discovery", {}, Path("."))
    plan["summary"] = "Cabal runs the suite; lint is a Nix check, with no shell HLint."
    plan["checks"]["lint"] = ["nix", "build", ".#checks.aarch64-darwin.treefmt"]
    if not detailed:
        plan["checks"]["junit_glob"] = None
    plan["questions"] = list(questions)
    return plan


def test_discovery_runs_before_baseline_and_supplies_knowledge_to_coder(rig, repository):
    harness, agent, executor = rig
    passing_base(repository)
    specification(harness)
    agent.responses["validation_discovery"].append(discovered())
    calls = []

    def run(argv, cwd, **kwargs):
        assert agent.discovery_calls
        calls.append(argv)
        result = command_result(argv)
        if argv[:2] == ["cabal", "test"]:
            assert kwargs["report_glob"] is None
            result["stdout"] = (
                "All 1214 tests passed (417.78s)\n1 of 1 test suites (1 of 1 test cases) passed.\n"
            )
        return result

    harness.execute = run
    view = harness.run_phase("task", "implement")
    assert view["stage"] == "implementation_ready"
    assert ["hlint", "."] not in calls
    assert len(agent.discovery_calls) == 1
    assert len(calls) == 8
    context = agent.calls[-1][1]
    assert context["validation_knowledge"]["suites"][0]["package"] == "example-app"
    assert context["validation_config"]["junit_glob"] is None
    assert (Path(view["report_directory"]) / "validation-setup.json").exists()
    assert not executor.calls


def test_unknown_setup_asks_before_running_or_writing_tests(rig):
    harness, agent, executor = rig
    specification(harness)
    plan = discovered(questions=["Does the integration suite need a local service?"])
    plan["checks"] = None
    agent.responses["validation_discovery"].append(plan)
    view = harness.run_phase("task", "tests")
    assert view["stage"] == "validation_setup"
    assert not executor.calls
    assert [role for role, _, _ in agent.calls] == ["interviewer"]
    assert "local service" in view["prompt"]["questions"][0]
    view = harness.respond("task", "answer", text="The fixture starts its own service.")
    assert view["stage"] == "test_review"
    assert agent.discovery_calls[-1][1]["messages"][-1]["text"].startswith("The fixture")


def test_tdd_does_not_silently_accept_exit_codes_without_failure_identities(rig):
    harness, agent, executor = rig
    specification(harness)
    agent.responses["validation_discovery"].append(discovered())
    view = harness.run_phase("task", "tests")
    assert view["stage"] == "validation_setup"
    assert "named test results" in view["prompt"]["questions"][0]
    assert not executor.calls
    assert not any(role == "test_author" for role, _, _ in agent.calls)


def test_discovery_is_read_only_and_has_its_own_role_context(rig):
    harness, agent, _ = rig
    specification(harness)
    harness.run_phase("task", "tests")
    context = agent.discovery_calls[0][1]
    assert context["messages"] == []
    assert context["validation_overrides"] == {}
    assert context["validation_config"] == {}
    assert "example-app/example-app.cabal" in context["inventory"]["files"]
    prompt, _ = prompt_for("validation_discovery", context)
    assert "Do not probe commands" in prompt
    assert "read-only file inspection" in prompt


def test_changed_runner_configuration_is_inspected_again_before_recheck(rig):
    harness, agent, _ = rig
    specification(harness)
    view = harness.run_phase("task", "tests")
    source = Path(view["worktree"]) / "example-app/example-app.cabal"
    source.write_text(source.read_text() + "-- New test runner settings\n")
    harness.respond("task", "recheck")
    assert len(agent.discovery_calls) == 2


def test_old_paused_tasks_rediscover_implicit_defaults_on_recheck(rig):
    harness, agent, executor = rig
    specification(harness)
    harness.run_phase("task", "implement")
    state = copy.deepcopy(harness._state("task"))
    state.pop("validation_knowledge")
    state.pop("validation_overrides")
    state["validation_config"] = ValidationConfig(
        lint=["hlint", "."], junit_glob="test-reports/**/*.xml"
    ).model_dump()
    harness.graph.update_state(harness._config("task"), state)
    executor.calls.clear()
    plan = discovered(questions=["Which local integration prerequisites apply?"])
    agent.responses["validation_discovery"].append(plan)
    view = harness.respond("task", "recheck")
    assert view["stage"] == "validation_setup"
    assert not executor.calls
    assert agent.discovery_calls[-1][1]["validation_overrides"] == {}


def test_inventory_does_not_follow_symlinks_out_of_repository(repository, tmp_path):
    outside = tmp_path / "private"
    outside.write_text("DO NOT READ")
    (repository / "AGENTS.md").symlink_to(outside)
    git(repository, "add", "AGENTS.md")
    workspace = Workspace(repository)
    inventory = project_inventory(workspace)
    assert "DO NOT READ" not in str(inventory)
    with pytest.raises(ValueError, match="symlink"):
        source_hashes(workspace, [{"path": "AGENTS.md"}])


@pytest.mark.parametrize(
    "stdout,code",
    [
        ("", 0),
        ("No tests to run", 0),
        ("0 of 0 test suites (0 of 0 test cases) passed.", 0),
        ("0 of 1 test suites (0 of 1 test cases) passed.", 1),
        ("1 of 1 test suites (1 of 1 test cases) passed.", 1),
    ],
)
def test_cabal_green_requires_positive_complete_successful_run(stdout, code):
    result = command_result(["cabal", "test", "example-app"], exit_code=code)
    result["stdout"] = stdout
    with pytest.raises(ValueError):
        evidence({"test:example-app": result}, allow_cabal=True)


def test_cabal_passing_summary_is_never_accepted_as_red_evidence():
    checks = {key: command_result([key]) for key in ["lint", "build", "nix", "test:example-app"]}
    checks["test:example-app"]["stdout"] = "1 of 1 test suites (1 of 1 test cases) passed.\n"
    assert classify_validation(checks, [], phase="green", allow_cabal=True) == "green"
    report = evidence(checks, allow_cabal=True)
    assert report["suites_executed"] == 1
    assert report["executed"] == 0  # Cabal's suite count is not an assertion count.
    assert (
        classify_validation(checks, ["missing assertion"], phase="red", allow_cabal=True)
        == "blocked"
    )

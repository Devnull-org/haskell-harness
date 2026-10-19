"""Expected red tests never excuse build errors or unrelated failures."""

import pytest

from hydra_harness.contracts import classify_validation

from .conftest import FAILURE_ID, command_result, junit


def results(*, failing=True):
    return {
        "lint": command_result(["just", "lint"]),
        "nix": command_result(["just", "check"]),
        "test:hydra-node": command_result(
            ["just", "test", "hydra-node"],
            exit_code=int(failing),
            junit_xml=[junit(failing=failing)],
        ),
    }


def test_only_named_failing_tests_establish_red():
    assert classify_validation(results(), [FAILURE_ID], phase="red") == "expected_red"


def test_passing_tests_do_not_demonstrate_missing_behavior():
    assert classify_validation(results(failing=False), [FAILURE_ID], phase="red") == "blocked"


@pytest.mark.parametrize("command", ["lint", "test:hydra-node"])
def test_compiler_errors_cannot_be_reported_as_expected_red(command):
    checks = results()
    checks[command].update(exit_code=1, stderr="GHC: error [-Werror]", junit_xml=[])
    assert classify_validation(checks, [FAILURE_ID], phase="red") == "blocked"


def test_unrelated_failing_test_blocks_progress():
    checks = results()
    checks["test:hydra-node"]["junit_xml"] = [junit(name="unrelated regression")]
    assert classify_validation(checks, [FAILURE_ID], phase="red") == "blocked"


def test_test_runner_errors_are_not_assertion_failures():
    checks = results()
    checks["test:hydra-node"]["junit_xml"] = [junit(errors=True)]
    assert classify_validation(checks, [FAILURE_ID], phase="red") == "blocked"


@pytest.mark.parametrize("xml", ["<invalid", '<testsuite tests="0"/>'])
def test_missing_or_malformed_test_evidence_blocks_progress(xml):
    checks = results()
    checks["test:hydra-node"].update(exit_code=0, junit_xml=[xml])
    assert classify_validation(checks, [FAILURE_ID], phase="green") == "blocked"


def test_nix_failure_requires_explicit_red_failure_review():
    checks = results()
    checks["nix"].update(exit_code=1, stderr="A flake check failed")
    assert classify_validation(checks, [FAILURE_ID], phase="red") == "needs_red_review"


def test_green_requires_every_command_to_pass():
    assert classify_validation(results(failing=False), [FAILURE_ID], phase="green") == "green"
    checks = results(failing=False)
    checks["nix"]["exit_code"] = 1
    assert classify_validation(checks, [FAILURE_ID], phase="green") == "blocked"


def test_failed_assertion_is_failure_even_when_runner_exits_zero():
    checks = results()
    checks["test:hydra-node"]["exit_code"] = 0
    assert classify_validation(checks, [FAILURE_ID], phase="green") == "blocked"


@pytest.mark.parametrize("missing", ["lint", "nix", "test:hydra-node"])
def test_missing_required_check_is_not_success(missing):
    checks = results(failing=False)
    del checks[missing]
    assert classify_validation(checks, [FAILURE_ID], phase="green") == "blocked"

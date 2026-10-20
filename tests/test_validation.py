"""Expected red tests never excuse build errors or unrelated failures."""

import pytest

from haskell_harness.contracts import classify_validation
from haskell_harness.validation import LocalExecutor

from .conftest import FAILURE_ID, command_result, junit


@pytest.mark.parametrize("override", [False, True])
def test_nix_checks_use_task_worktree_unless_overridden(tmp_path, monkeypatch, override):
    worktree = tmp_path / "task worktree"
    dependency = tmp_path / "dependencies"
    calls = []

    def run(argv, cwd, **kwargs):
        calls.append((argv, cwd))
        return 0, "", ""

    monkeypatch.setattr("haskell_harness.validation.run_process", run)
    executor = LocalExecutor(nix_flake=dependency if override else None)
    executor(["just", "check"], worktree)
    assert calls == [
        (
            [
                "nix",
                "develop",
                str(dependency if override else worktree),
                "--command",
                "just",
                "check",
            ],
            worktree,
        )
    ]


def results(*, failing=True):
    return {
        "lint": command_result(["just", "lint"]),
        "build": command_result(
            ["cabal", "build", "all", "--enable-tests", "--ghc-options=-Werror"]
        ),
        "nix": command_result(["just", "check"]),
        "test:example-app": command_result(
            ["just", "test", "example-app"],
            exit_code=int(failing),
            junit_xml=[junit(failing=failing)],
        ),
    }


def test_only_named_failing_tests_establish_red():
    assert classify_validation(results(), [FAILURE_ID], phase="red") == "expected_red"


def test_passing_tests_do_not_demonstrate_missing_behavior():
    assert classify_validation(results(failing=False), [FAILURE_ID], phase="red") == "blocked"


@pytest.mark.parametrize("command", ["lint", "build", "test:example-app"])
def test_compiler_errors_cannot_be_reported_as_expected_red(command):
    checks = results()
    checks[command].update(exit_code=1, stderr="GHC: error [-Werror]", junit_xml=[])
    assert classify_validation(checks, [FAILURE_ID], phase="red") == "blocked"


def test_unrelated_failing_test_blocks_progress():
    checks = results()
    checks["test:example-app"]["junit_xml"] = [junit(name="unrelated regression")]
    assert classify_validation(checks, [FAILURE_ID], phase="red") == "blocked"


def test_test_runner_errors_are_not_assertion_failures():
    checks = results()
    checks["test:example-app"]["junit_xml"] = [junit(errors=True)]
    assert classify_validation(checks, [FAILURE_ID], phase="red") == "blocked"


@pytest.mark.parametrize("xml", ["<invalid", '<testsuite tests="0"/>'])
def test_missing_or_malformed_test_evidence_blocks_progress(xml):
    checks = results()
    checks["test:example-app"].update(exit_code=0, junit_xml=[xml])
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
    checks["test:example-app"]["exit_code"] = 0
    assert classify_validation(checks, [FAILURE_ID], phase="green") == "blocked"


@pytest.mark.parametrize("missing", ["lint", "build", "nix", "test:example-app"])
def test_missing_required_check_is_not_success(missing):
    checks = results(failing=False)
    del checks[missing]
    assert classify_validation(checks, [FAILURE_ID], phase="green") == "blocked"


@pytest.mark.parametrize("attribute", ['errors="1"', 'failures="1"'])
def test_suite_failures_without_case_evidence_block_green(attribute):
    checks = results(failing=False)
    xml = checks["test:example-app"]["junit_xml"][0]
    checks["test:example-app"]["junit_xml"] = [
        xml.replace("<testsuite ", f"<testsuite {attribute} ")
    ]
    assert classify_validation(checks, [FAILURE_ID], phase="green") == "blocked"

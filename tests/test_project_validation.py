"""Projects choose their checks; saved tasks retain the commands they started with."""

import os
import time

import pytest

from haskell_harness.config import load_settings
from haskell_harness.validation import LocalExecutor, ValidationConfig, classify_validation
from haskell_harness.workflow import Harness

from .conftest import FAILURE_ID, ScriptedAgent, command_result, junit


def test_default_checks_use_cabal_hlint_and_nix_without_project_recipes():
    commands = dict(ValidationConfig().commands(["example-app"]))
    assert commands == {
        "lint": ["hlint", "."],
        "build": ["cabal", "build", "all", "--enable-tests", "--ghc-options=-Werror"],
        "nix": ["nix", "flake", "check"],
        "test:example-app": ["cabal", "test", "example-app", "--test-show-details=direct"],
    }


def test_explicit_user_configuration_can_supply_project_commands(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text(
        '[validation]\nlint = ["just", "lint"]\n'
        'build = ["just", "build-strict"]\nnix = ["just", "check"]\n'
        'test = ["just", "test", "{package}"]\n'
        'junit_glob = "reports/{package}/**/*.xml"\n'
    )
    settings = load_settings(path)
    assert dict(settings.validation.commands(["example-app"]))["test:example-app"] == [
        "just",
        "test",
        "example-app",
    ]
    assert settings.validation.report_pattern("example-app") == "reports/example-app/**/*.xml"


@pytest.mark.parametrize(
    "fields",
    [
        {"lint": []},
        {"build": []},
        {"test": ["cabal", "test"]},
        {"test": ["cabal", "test", "{unknown}"]},
        {"junit_glob": "../outside/*.xml"},
        {"junit_glob": "/tmp/*.xml"},
    ],
)
def test_invalid_validation_configuration_fails_early(fields):
    with pytest.raises(ValueError):
        ValidationConfig(**fields)


@pytest.mark.parametrize("build", [None, 1])
def test_red_tests_do_not_excuse_missing_or_failed_compilation(build):
    results = {
        "lint": command_result(["hlint", "."]),
        "nix": command_result(["nix", "flake", "check"]),
        "test:example-app": command_result(
            ["cabal", "test", "example-app"], exit_code=1, junit_xml=[junit()]
        ),
    }
    if build is not None:
        results["build"] = command_result(["cabal", "build"], exit_code=build)
    assert classify_validation(results, [FAILURE_ID], phase="red") == "blocked"


def test_executor_reads_only_fresh_reports_inside_worktree(tmp_path, monkeypatch):
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    reports = checkout / "reports"
    reports.mkdir()
    stale = reports / "old.xml"
    stale.write_text(junit(name="stale"))
    os.utime(stale, ns=(1, 1))
    outside = tmp_path / "outside"
    outside.mkdir()
    (reports / "linked").symlink_to(outside, target_is_directory=True)

    def run(argv, cwd, **kwargs):
        (reports / "new.xml").write_text(junit())
        (outside / "escape.xml").write_text(junit(name="outside"))
        now = time.time_ns() + 1_000_000
        os.utime(reports / "new.xml", ns=(now, now))
        os.utime(outside / "escape.xml", ns=(now, now))
        return 1, "", ""

    monkeypatch.setattr("haskell_harness.validation.run_process", run)
    result = LocalExecutor()(
        ["cabal", "test", "example-app"], checkout, report_glob="reports/**/*.xml"
    )
    assert result["junit_xml"] == [junit()]


def test_validation_commands_are_pinned_across_resume(repository, tmp_path):
    configuration = ValidationConfig(
        lint=["team-lint"],
        build=["team-build"],
        nix=["team-check"],
        test=["team-test", "{package}"],
        junit_glob="reports/{package}/*.xml",
    )
    calls = []

    def execute(argv, cwd, *, report_glob=None):
        calls.append((argv, report_glob))
        return command_result(
            argv,
            exit_code=1 if report_glob else 0,
            junit_xml=[junit()] if report_glob else [],
        )

    harness = Harness(
        repository, tmp_path / "state", ScriptedAgent(), execute, validation=configuration
    )
    view = harness.start("configured", "Fix increment")
    harness.respond("configured", "approve", approval_token=view["prompt"]["approval_token"])
    harness.close()
    harness = Harness(repository, tmp_path / "state", ScriptedAgent(), execute)
    try:
        view = harness.run_phase("configured", "tests")
        assert view["stage"] == "test_review"
        package = harness._state("configured")["specification"]["packages"][0]
        assert calls == [
            (["team-lint"], None),
            (["team-build"], None),
            (["team-check"], None),
            (["team-test", package], f"reports/{package}/*.xml"),
        ]
    finally:
        harness.close()

"""Small local repositories and scripted agents for workflow acceptance tests."""

import copy
import subprocess
from collections import defaultdict, deque
from pathlib import Path

import pytest

from lambda_harness.contracts import Harness

SOURCE = "example-app/src/Example/Arithmetic.hs"
TEST = "example-app/test/Example/ArithmeticSpec.hs"
BROKEN = "module Example.Arithmetic where\naddOne :: Int -> Int\naddOne x = x\n"
IMPLEMENTED = "module Example.Arithmetic where\naddOne :: Int -> Int\naddOne x = x + 1\n"
REFACTORED = "module Example.Arithmetic where\naddOne :: Int -> Int\naddOne = succ\n"
TEST_CONTENT = '-- Acceptance A1: increment the input.\nit "adds one" $ addOne 0 `shouldBe` 1\n'
FAILURE_ID = "Example.Arithmetic.adds one"
SPECIFICATION = {
    "problem": "addOne returns its input unchanged",
    "expected_behavior": "Return the input incremented by one",
    "acceptance_criteria": [{"id": "A1", "description": "addOne 0 returns 1"}],
    "out_of_scope": ["Changing unrelated arithmetic"],
    "constraints": ["Follow repository conventions", "Compile with -Werror"],
    "packages": ["example-app"],
    "open_questions": [],
    "assumptions": [],
}


def git(repository: Path, *args: str) -> str:
    return subprocess.check_output(
        ["git", "-C", str(repository), *args], text=True, stderr=subprocess.PIPE
    ).strip()


def command_result(argv, *, exit_code=0, junit_xml=None, stderr=""):
    return {
        "argv": list(argv),
        "exit_code": exit_code,
        "stdout": "command completed",
        "stderr": stderr,
        "junit_xml": junit_xml or [],
    }


def junit(*, failing=True, name="adds one", errors=False):
    failure = '<error message="test crashed" />' if errors else '<failure message="expected 1" />'
    content = failure if failing else ""
    return (
        '<testsuite name="Example.Arithmetic" tests="1">'
        f'<testcase classname="Example.Arithmetic" name="{name}">{content}</testcase>'
        "</testsuite>"
    )


class ScriptedAgent:
    def __init__(self):
        self.responses = defaultdict(deque)
        self.calls = []
        self.discovery_calls = []

    def __call__(self, role, context, cwd):
        if role == "validation_discovery":
            self.discovery_calls.append((role, copy.deepcopy(context), Path(cwd)))
            if self.responses[role]:
                return copy.deepcopy(self.responses[role].popleft())
            return {
                "summary": "Read the fixture's test setup.",
                "suites": [
                    {
                        "package": "example-app",
                        "suites": ["tests"],
                        "framework": "fixture runner",
                        "execution": "Local tests through Cabal.",
                    }
                ],
                "sources": [{"path": "example-app/example-app.cabal", "reason": "Package tests"}],
                "checks": {
                    "lint": ["hlint", "."],
                    "build": ["cabal", "build", "all", "--enable-tests", "--ghc-options=-Werror"],
                    "nix": ["nix", "flake", "check"],
                    "test": ["cabal", "test", "{package}", "--test-show-details=direct"],
                    "junit_glob": "test-reports/**/*.xml",
                },
                "questions": [],
            }
        self.calls.append((role, copy.deepcopy(context), Path(cwd)))
        if self.responses[role]:
            return copy.deepcopy(self.responses[role].popleft())
        if context.get("intent") == "explain":
            return {"answer": "The test establishes acceptance criterion A1."}
        if context.get("intent") == "discuss_patch":
            return {"answer": "The patch implements acceptance criterion A1.", "proposal": None}
        defaults = {
            "interviewer": {"specification": SPECIFICATION, "questions": []},
            "test_author": {
                "summary": "Exercise A1 before implementing addOne",
                "changes": [{"path": TEST, "content": TEST_CONTENT}],
                "expected_failures": [FAILURE_ID],
                "coverage": {"A1": [FAILURE_ID]},
                "commit_message": "test: specify increment behavior",
            },
            "implementer": {
                "summary": "Increment the input",
                "changes": [{"path": SOURCE, "content": IMPLEMENTED}],
                "commit_message": "fix: increment the input",
                "complete": True,
            },
            "refactorer": {
                "summary": "Use the existing successor operation",
                "changes": [{"path": SOURCE, "content": REFACTORED}],
                "commit_message": "refactor: simplify increment",
                "complete": True,
            },
            "haskell_reviewer": {"findings": [], "summary": "Reviewed the implementation"},
            "security_reviewer": {"findings": [], "summary": "Reviewed safety and liveness"},
            "documentation_reviewer": {
                "findings": [],
                "summary": "Reviewed documentation accuracy",
            },
            "walkthrough": {
                "questions": [
                    {"question": "What behavior changed?", "reference": SOURCE},
                    {"question": "Which test establishes A1?", "reference": TEST},
                    {"question": "Why use succ?", "reference": SOURCE},
                ]
            },
            "walkthrough_feedback": {
                "feedback": "Your answer identifies the behavior and its test.",
                "follow_up": None,
            },
        }
        return copy.deepcopy(defaults[role])


class ScriptedExecutor:
    def __init__(self):
        self.calls = []
        self.lint_error = False
        self.nix_error = False
        self.force_red = False

    def __call__(self, argv, cwd, *, report_glob=None):
        source = (cwd / SOURCE).read_text()
        self.calls.append((list(argv), Path(cwd), source))
        if argv == ["hlint", "."] and self.lint_error:
            return command_result(argv, exit_code=1, stderr="GHC: unused binding [-Werror]")
        if argv == ["nix", "flake", "check"] and self.nix_error:
            return command_result(argv, exit_code=1, stderr="nix check failed: see log")
        if report_glob:
            red = source == BROKEN or self.force_red
            return command_result(argv, exit_code=int(red), junit_xml=[junit(failing=red)])
        return command_result(argv)


@pytest.fixture
def repository(tmp_path):
    repo = tmp_path / "project"
    repo.mkdir()
    git(repo, "init", "-b", "main")
    git(repo, "config", "user.name", "Harness test")
    git(repo, "config", "user.email", "harness@example.invalid")
    git(repo, "config", "commit.gpgsign", "false")
    git(repo, "config", "core.hooksPath", "/dev/null")
    (repo / SOURCE).parent.mkdir(parents=True)
    (repo / SOURCE).write_text(BROKEN)
    (repo / TEST).parent.mkdir(parents=True)
    (repo / TEST).write_text("-- Existing tests\n")
    (repo / "cabal.project").write_text("packages: example-app\ntests: True\n")
    (repo / "example-app/example-app.cabal").write_text("name: example-app\n")
    (repo / "CLAUDE.md").write_text("Use plain English comments. Never edit CHANGELOG.md.\n")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "Initial fixture")
    return repo


@pytest.fixture
def rig(repository, tmp_path):
    agent = ScriptedAgent()
    executor = ScriptedExecutor()
    harness = Harness(repository, tmp_path / "state", agent, executor, max_repair_rounds=2)
    yield harness, agent, executor
    harness.close()


def reach_test_review(harness, *, walkthrough=False):
    view = harness.start("increment", "Fix addOne", mode="full", walkthrough=walkthrough)
    assert view.get("stage") == "specification_review", view
    return harness.respond("increment", "approve", approval_token=view["prompt"]["approval_token"])


def approve_tests(harness, view):
    assert view.get("stage") == "test_review", view
    return harness.respond("increment", "approve", approval_token=view["prompt"]["approval_token"])


@pytest.fixture(autouse=True)
def isolated_backend_configuration(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "user-config"))

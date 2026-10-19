"""Interfaces exercised by the acceptance tests."""

from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol, TypedDict


class CommandResult(TypedDict):
    argv: list[str]
    exit_code: int
    stdout: str
    stderr: str
    junit_xml: list[str]


class Agent(Protocol):
    def __call__(self, role: str, context: dict[str, Any], cwd: Path) -> dict[str, Any]: ...


Executor = Callable[[list[str], Path], CommandResult]


class View(TypedDict, total=False):
    task_id: str
    stage: str
    prompt: dict[str, Any]
    worktree: str
    revision: str
    report_directory: str


class Harness:
    """Public interface; behavior awaits approval of the acceptance tests."""

    def __init__(
        self,
        repository: Path,
        state_dir: Path,
        agent: Agent,
        execute: Executor,
        max_repair_rounds: int = 3,
    ) -> None:
        self.repository = repository
        self.state_dir = state_dir
        self.agent = agent
        self.execute = execute
        self.max_repair_rounds = max_repair_rounds

    def start(
        self,
        task_id: str,
        request: str,
        *,
        mode: str = "full",
        walkthrough: bool = False,
    ) -> View:
        return {"task_id": task_id, "stage": "not_implemented"}

    def respond(self, task_id: str, action: str, **fields: Any) -> View:
        return {"task_id": task_id, "stage": "not_implemented"}

    def inspect(self, task_id: str) -> View:
        return {"task_id": task_id, "stage": "not_implemented"}

    def close(self) -> None:
        pass


def classify_validation(
    results: dict[str, CommandResult], expected_failures: list[str], *, phase: str
) -> str:
    """Return green, expected_red, needs_red_review, or blocked."""
    return "not_implemented"


class CursorAgent:
    def __init__(self, executable: str = "cursor-agent", timeout: float = 600) -> None:
        self.executable = executable
        self.timeout = timeout

    def __call__(self, role: str, context: dict[str, Any], cwd: Path) -> dict[str, Any]:
        return {}

"""Serializable boundaries between the workflow, agents, and command runner."""

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
    project_context_id: str | None

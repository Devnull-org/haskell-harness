"""Validate agent handoffs before they can affect workflow state."""

import copy
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError


class Record(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Criterion(Record):
    id: str = Field(min_length=1)
    description: str = Field(min_length=1)


class Specification(Record):
    problem: str = Field(min_length=1)
    expected_behavior: str = Field(min_length=1)
    acceptance_criteria: list[Criterion] = Field(min_length=1)
    out_of_scope: list[str]
    constraints: list[str]
    packages: list[str] = Field(min_length=1)
    open_questions: list[str]
    assumptions: list[str]


class Interview(Record):
    specification: Specification
    questions: list[str]


class Exploration(Record):
    summary: str = Field(min_length=1, description="A direct answer to the research question.")
    analysis: str = Field(
        min_length=1,
        description="Completed analysis in Markdown: findings, concrete options, and trade-offs.",
    )
    references: list[str] = Field(
        description="Repository paths and line numbers supporting findings."
    )
    open_questions: list[str] = Field(
        description="Remaining uncertainties, without requiring a choice."
    )


class Change(Record):
    path: str = Field(min_length=1, max_length=1024)
    content: str | None = Field(description="Complete UTF-8 file content; null deletes the file.")


class Patch(Record):
    summary: str = Field(min_length=1)
    changes: list[Change]
    commit_message: str = Field(min_length=1, max_length=2000)
    complete: bool = True


class Tests(Record):
    summary: str = Field(min_length=1)
    changes: list[Change] = Field(min_length=1)
    expected_failures: list[str] = Field(min_length=1)
    coverage: dict[str, list[str]]
    commit_message: str = Field(min_length=1, max_length=2000)


class Finding(Record):
    id: str = Field(min_length=1)
    severity: Literal["critical", "high", "medium", "low", "info"]
    location: str = Field(min_length=1)
    title: str = Field(min_length=1)
    conditions: str = Field(min_length=1)
    sequence: list[str]
    impact: str = Field(min_length=1)
    evidence: str = Field(min_length=1)
    confidence: Literal["confirmed", "suspected"]
    recommendation: str = Field(min_length=1)
    needs_test: bool


class PassingTests(Tests):
    expected_failures: list[str] = Field(max_length=0)


class Review(Record):
    findings: list[Finding]
    summary: str = Field(min_length=1)


class Question(Record):
    question: str = Field(min_length=1)
    reference: str = Field(min_length=1)


class Walkthrough(Record):
    questions: list[Question] = Field(min_length=3, max_length=5)


class Feedback(Record):
    feedback: str = Field(min_length=1)
    follow_up: str | None


class Explanation(Record):
    answer: str = Field(min_length=1)


class ChatReply(Explanation):
    memory_note: str | None = Field(
        default=None,
        min_length=1,
        max_length=32_000,
        description="A standalone note to append to project memory, only when the latest user "
        "message explicitly requests saving or remembering it. Otherwise null. "
        "The harness performs the save and reports its result; do not claim it has saved yet.",
    )
    references: list[str] = Field(
        description="Repository references when relevant, otherwise empty."
    )


SCHEMAS = {
    "chat": ChatReply,
    "interviewer": Interview,
    "explorer": Exploration,
    "test_author": Tests,
    "implementer": Patch,
    "refactorer": Patch,
    "haskell_reviewer": Review,
    "security_reviewer": Review,
    "walkthrough": Walkthrough,
    "walkthrough_feedback": Feedback,
}


def schema_for(role, context):
    if context.get("intent") == "explain":
        return Explanation
    if (
        role == "test_author"
        and context.get("task_kind") == "tests"
        and context.get("test_expectation") == "green"
    ):
        return PassingTests
    return SCHEMAS[role]


def validate_response(role, context, value, *, notify=None):
    """Discard only empty unexpected fields; keep all other validation strict."""
    schema = schema_for(role, context)
    try:
        validated = schema.model_validate(value)
    except ValidationError as error:
        errors = error.errors()
        if not all(
            item["type"] == "extra_forbidden"
            and (
                item["input"] is None
                or isinstance(item["input"], (str, list, dict))
                and len(item["input"]) == 0
            )
            for item in errors
        ):
            raise
        cleaned = copy.deepcopy(value)
        paths = []
        for item in errors:
            parent = cleaned
            for part in item["loc"][:-1]:
                parent = parent[part]
            del parent[item["loc"][-1]]
            paths.append(".".join(map(str, item["loc"])))
        validated = schema.model_validate(cleaned)
        if notify:
            notify("Ignored empty extra response fields: " + ", ".join(paths))
    return validated.model_dump(exclude_unset=True)

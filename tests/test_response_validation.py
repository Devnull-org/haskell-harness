"""Harmless empty additions do not discard an otherwise valid agent handoff."""

import copy
import json

import pytest
from pydantic import ValidationError

from hydra_harness.backends import Backend, BackendResponseError, CommandAgent, decode_response
from hydra_harness.schemas import validate_response

from .conftest import SPECIFICATION
from .helpers import executable


@pytest.mark.parametrize("empty", [None, "", [], {}])
def test_empty_extra_fields_are_removed_without_changing_known_fields(empty):
    reply = {
        "specification": copy.deepcopy(SPECIFICATION),
        "questions": ["Should this handle negative integers?"],
    }
    expected = copy.deepcopy(reply)
    reply["specification"]["out_of_scope_note"] = empty
    reply["specification"]["acceptance_criteria"][0]["note"] = empty
    reply["unused"] = empty
    original = copy.deepcopy(reply)
    notices = []
    assert validate_response("interviewer", {}, reply, notify=notices.append) == expected
    assert reply == original
    assert len(notices) == 1
    assert "specification.out_of_scope_note" in notices[0]
    assert "specification.acceptance_criteria.0.note" in notices[0]
    assert "unused" in notices[0]


@pytest.mark.parametrize("value", [False, 0, "Keep this requirement", " ", ["item"], {"x": 1}])
def test_nonempty_or_meaningful_extra_fields_are_not_discarded(value):
    reply = {"specification": {**SPECIFICATION, "out_of_scope_note": value}, "questions": []}
    with pytest.raises(ValidationError, match="extra_forbidden"):
        validate_response("interviewer", {}, reply)


@pytest.mark.parametrize("problem", ["missing", "type", "empty_required"])
def test_empty_extras_do_not_mask_other_schema_errors(problem):
    spec = {**SPECIFICATION, "out_of_scope_note": []}
    if problem == "missing":
        del spec["out_of_scope"]
    elif problem == "type":
        spec["packages"] = "hydra-node"
    else:
        spec["acceptance_criteria"] = []
    notices = []
    with pytest.raises(ValidationError):
        validate_response(
            "interviewer", {}, {"specification": spec, "questions": []}, notify=notices.append
        )
    assert notices == []


def test_narrated_backend_reply_with_an_empty_extra_field_is_recovered(tmp_path):
    reply = {
        "specification": {**SPECIFICATION, "out_of_scope_note": []},
        "questions": ["Which inputs?"],
    }
    text = "I checked the code.```json\n" + json.dumps(reply) + "\n```"
    script = executable(tmp_path, f"print({json.dumps({'result': text})!r})\n")
    notices = []
    result = CommandAgent(
        Backend(command=[script], response_fields=["result"]), progress=notices.append
    )("interviewer", {}, tmp_path)
    assert result == {"specification": SPECIFICATION, "questions": ["Which inputs?"]}
    assert any("specification.out_of_scope_note" in notice for notice in notices)


def test_embedded_agents_get_the_same_validation_and_keep_the_interview_gate(rig):
    harness, agent, _ = rig
    agent.responses["interviewer"].append(
        {
            "specification": {**SPECIFICATION, "out_of_scope_note": []},
            "questions": ["Which inputs?"],
        }
    )
    view = harness.start("extra-empty-field", "Fix increment", mode="phased")
    assert view["stage"] == "interview"
    assert view["prompt"]["questions"] == ["Which inputs?"]
    assert view["prompt"]["actions"] == ["answer"]


def test_saved_response_can_restore_the_interview_without_repeating_the_answer(rig):
    harness, agent, _ = rig
    agent.responses["interviewer"].append(
        {"specification": SPECIFICATION, "questions": ["Which inputs?"]}
    )
    harness.start("recover", "Fix increment", mode="phased")
    reply = {
        "specification": {**SPECIFICATION, "out_of_scope_note": []},
        "questions": ["Should overflow be checked?"],
    }
    stdout = json.dumps({"result": json.dumps(reply)})

    def old_backend(role, context, cwd):
        raise BackendResponseError("Extra inputs are not permitted", stdout, "")

    harness.agent = old_backend
    view = harness.respond("recover", "answer", text="Negative integers too.")
    assert view["stage"] == "agent_error"
    saved = harness.state_dir / "recover/backend-responses/0003-interviewer/stdout.txt"

    def replay(role, context, cwd):
        assert context["messages"] == [
            {"stage": "interview", "action": "answer", "text": "Negative integers too."}
        ]
        result = decode_response(decode_response(saved.read_text())["result"])
        return validate_response(role, context, result)

    harness.agent = replay
    view = harness.respond("recover", "retry")
    assert view["stage"] == "interview"
    assert view["prompt"]["questions"] == ["Should overflow be checked?"]
    assert view["prompt"]["actions"] == ["answer"]
    assert len(agent.calls) == 1
    assert saved.read_text() == stdout

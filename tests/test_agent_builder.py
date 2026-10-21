"""Agent definitions are reviewed, reusable, and pinned for ongoing work."""

import copy
import json

import pytest

from lambda_harness.agent_library import AgentLibrary
from lambda_harness.backends import prompt_for
from lambda_harness.failures import AgentInterrupted

from .conftest import git

DEFINITION = {
    "name": "Rust reviewer",
    "avatar": "crab",
    "role": "reviewer",
    "languages": ["Rust"],
    "expertise": ["Ownership and borrowing", "Error handling", "Concurrency"],
    "instructions": (
        "Review ownership and lifetimes. Explain tradeoffs and follow repository "
        "conventions. Do not introduce unsafe code without a clear reason."
    ),
}
DRAFT = {
    "answer": (
        "I suggest ownership, error handling, and concurrency checks. "
        "What kind of Rust project is this?"
    ),
    "definition": DEFINITION,
}


def agent_repo(tmp_path):
    root = tmp_path / "catalog"
    root.mkdir()
    git(root, "init", "-b", "main")
    return root


@pytest.fixture(autouse=True)
def catalog_for_builder(request, tmp_path):
    if "rig" in request.fixturenames:
        harness, _, _ = request.getfixturevalue("rig")
        harness.agents.close()
        harness.agents = AgentLibrary(tmp_path / "agent-settings", agent_repo(tmp_path))


def test_builder_discusses_practices_and_only_saves_after_explicit_review(rig):
    harness, agent, executor = rig
    agent.responses["agent_builder"].append(DRAFT)
    view = harness.start_agent_builder(
        "builder", "Help me create a Rust reviewer; I do not know the best practices."
    )
    assert view["stage"] == "agent_builder"
    assert view["prompt"]["definition"] == DEFINITION
    assert view["prompt"]["actions"] == ["answer", "save_agent"]
    assert harness.agents.list() == []
    assert agent.calls[-1][0] == "agent_builder"
    assert "project_context" not in agent.calls[-1][1]
    with pytest.raises(ValueError, match="token"):
        harness.respond("builder", "save_agent")
    view = harness.respond("builder", "save_agent", approval_token=view["prompt"]["approval_token"])
    assert view["stage"] == "agent_ready"
    assert len(agent.calls) == 1 and not executor.calls
    saved = harness.agents.list()[0]
    assert saved["definition"] == DEFINITION
    assert harness.agents.selected(harness.repository) == {}
    assert view["saved_agent"]["version"] == saved["version"]


def test_builder_can_stop_revise_and_resume_without_losing_the_draft(rig):
    harness, agent, _ = rig
    agent.responses["agent_builder"].append(DRAFT)
    harness.start_agent_builder("builder", "A Rust reviewer")

    def stopped(*args):
        raise AgentInterrupted()

    harness.agent = stopped
    view = harness.respond("builder", "answer", text="Focus on networking")
    assert view["stage"] == "agent_paused"
    harness.agent = agent
    refined = {**DEFINITION, "instructions": "Focus on async cancellation and resource cleanup."}
    agent.responses["agent_builder"].append(
        {"answer": "I suggest checking cancellation safety.", "definition": refined}
    )
    view = harness.respond("builder", "revise", text="Focus on async cancellation instead")
    assert view["prompt"]["definition"] == refined
    assert harness.agents.list() == []
    assert agent.calls[-1][1]["messages"][-1]["text"] == "Focus on async cancellation instead"


def test_definitions_live_in_git_and_assignments_are_project_specific(
    tmp_path,
):
    with AgentLibrary(tmp_path / "settings", agent_repo(tmp_path)) as library:
        first = library.save("rust-reviewer", DEFINITION, expected_version=None)
        library.assign(tmp_path / "one", "reviewer", first["id"])
        assert library.selected(tmp_path / "one")["haskell_reviewer"] == first
        assert library.selected(tmp_path / "two") == {}
        changed = {**DEFINITION, "instructions": "Review cancellation and cleanup."}
        second = library.save(first["id"], changed, expected_version=first["version"])
        assert first["definition"] == DEFINITION
        assert json.loads((tmp_path / "catalog/agents/rust-reviewer.json").read_text()) == changed
        assert library.selected(tmp_path / "one")["haskell_reviewer"] == second
        with pytest.raises(ValueError, match="changed"):
            library.save(first["id"], DEFINITION, expected_version=first["version"])
        library.assign(tmp_path / "one", "reviewer", None)
        assert library.selected(tmp_path / "one") == {}
    with AgentLibrary(tmp_path / "settings", tmp_path / "catalog") as reopened:
        assert reopened.list() == [second]


def test_assigned_expertise_reaches_agent_and_existing_tasks_keep_their_profile(rig):
    harness, agent, _ = rig
    definition = {**DEFINITION, "role": "chat"}
    saved = harness.agents.save("rust-chat", definition, expected_version=None)
    harness.agents.assign(harness.repository, "chat", saved["id"])
    agent.responses["chat"].append({"answer": "Let us discuss Rust.", "references": []})
    harness.start("chat", "Discuss ownership", phase="chat")
    context = agent.calls[-1][1]
    assert context["agent_profile"] == saved
    prompt, _ = prompt_for("chat", context)
    assert definition["instructions"] in prompt
    updated = harness.agents.save(
        saved["id"],
        {**definition, "instructions": "Different instructions"},
        expected_version=saved["version"],
    )
    agent.responses["chat"].append({"answer": "Still the original definition.", "references": []})
    harness.respond("chat", "answer", text="Continue")
    assert agent.calls[-1][1]["agent_profile"] == saved
    agent.responses["chat"].append({"answer": "New definition.", "references": []})
    harness.start("new", "Discuss ownership", phase="chat")
    assert agent.calls[-1][1]["agent_profile"] == updated


def test_custom_reviewer_uses_generic_role_contract_without_haskell_expertise(tmp_path):
    with AgentLibrary(tmp_path / "settings", agent_repo(tmp_path)) as library:
        saved = library.save("rust", DEFINITION, expected_version=None)
    prompt, _ = prompt_for("haskell_reviewer", {"agent_profile": saved})
    assert "Rust" in prompt
    assert "Assess Haskell" not in prompt
    assert "Do not edit code" in prompt


def test_language_expertise_does_not_enable_an_unsupported_build_toolchain(tmp_path):
    with AgentLibrary(tmp_path / "settings", agent_repo(tmp_path)) as library:
        saved = library.save("rust", {**DEFINITION, "role": "implementer"}, expected_version=None)
        with pytest.raises(ValueError, match="toolchain"):
            library.assign(tmp_path / "project", "implementer", saved["id"])
        assert library.get(saved["id"]) == saved
        assert library.selected(tmp_path / "project") == {}


def test_revising_a_definition_requires_a_fresh_review(rig):
    harness, agent, _ = rig
    original = harness.agents.save("rust", DEFINITION, expected_version=None)
    revised = copy.deepcopy(DEFINITION)
    revised["instructions"] = "Review resource lifetimes and async cancellation."
    agent.responses["agent_builder"].append(
        {"answer": "Here is the revised agent.", "definition": revised}
    )
    view = harness.start_agent_builder("edit", "Improve async guidance", agent_id=original["id"])
    assert agent.calls[-1][1]["definition"] == DEFINITION
    assert harness.agents.get(original["id"]) == original
    harness.respond("edit", "save_agent", approval_token=view["prompt"]["approval_token"])
    assert harness.agents.get(original["id"])["definition"] == revised
    assert original["definition"] == DEFINITION


def test_agents_are_shared_by_git_clone_and_avatars_are_in_the_definition(tmp_path):
    root = agent_repo(tmp_path)
    with AgentLibrary(tmp_path / "one", root) as library:
        saved = library.save("rust", DEFINITION, expected_version=None)
    assert git(root, "status", "--porcelain") == "?? agents/"
    git(root, "add", "agents")
    git(
        root,
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@example.invalid",
        "-c",
        "commit.gpgsign=false",
        "-c",
        "core.hooksPath=/dev/null",
        "commit",
        "-m",
        "Add agent",
    )
    git(tmp_path, "clone", str(root), str(tmp_path / "clone"))
    with AgentLibrary(tmp_path / "two", tmp_path / "clone") as cloned:
        assert cloned.get("rust") == saved
        assert cloned.list()[0]["definition"]["avatar"] == "crab"


def test_bundled_catalog_is_read_only_and_cannot_accept_paths_or_symlinks(tmp_path):
    with AgentLibrary(tmp_path / "settings") as bundled:
        assert len(bundled.list()) >= 9
        assert not bundled.writable
        with pytest.raises(ValueError, match="repository"):
            bundled.save("rust", DEFINITION, expected_version=None)
    root = agent_repo(tmp_path)
    with AgentLibrary(tmp_path / "settings", root) as library:
        with pytest.raises(ValueError, match="ID"):
            library.save("../escape", DEFINITION, expected_version=None)
        (root / "agents").mkdir()
        secret = tmp_path / "outside.json"
        secret.write_text(json.dumps(DEFINITION))
        (root / "agents/rust.json").symlink_to(secret)
        with pytest.raises(ValueError, match="symbolic"):
            library.save("rust", DEFINITION, expected_version=None)
        assert json.loads(secret.read_text()) == DEFINITION


def test_catalog_switch_cannot_redirect_a_draft_save(rig, tmp_path):
    harness, agent, _ = rig
    agent.responses["agent_builder"].append(DRAFT)
    view = harness.start_agent_builder("draft", "A Rust reviewer")
    harness.agents.close()
    other = tmp_path / "other"
    other.mkdir()
    git(other, "init")
    harness.agents = AgentLibrary(tmp_path / "agent-settings", other)
    view = harness.respond("draft", "save_agent", approval_token=view["prompt"]["approval_token"])
    assert view["stage"] == "agent_error"
    assert "repository changed" in view["prompt"]["error"]
    assert harness.agents.list() == []


def test_missing_assignment_blocks_new_tasks_without_reserving_the_task_name(rig):
    harness, agent, _ = rig
    saved = harness.agents.save("rust", DEFINITION, expected_version=None)
    harness.agents.assign(harness.repository, "reviewer", saved["id"])
    (harness.agents.source / "rust.json").unlink()
    with pytest.raises(ValueError, match="removed"):
        harness.start("chat", "Hello", phase="chat")
    assert not (harness.state_dir / "chat").exists()
    harness.agents.assign(harness.repository, "reviewer", None)
    agent.responses["chat"].append({"answer": "Hello", "references": []})
    assert harness.start("chat", "Hello", phase="chat")["stage"] == "chat"

"""Shared protocol context stays traceable, durable, and separate from live edits."""

import pytest

from hydra_harness.project_context import ContextStore
from hydra_harness.workspace import Workspace

from .conftest import SOURCE, git


def test_context_survives_restart_and_keeps_immutable_history(repository, tmp_path):
    with ContextStore(tmp_path / "context") as store:
        first = store.refresh(repository, sources=[SOURCE])
        assert first["revision"] == git(repository, "rev-parse", "HEAD")
    (repository / SOURCE).write_text("module Hydra.Example where\n-- A new property\n")
    git(repository, "commit", "-am", "Change documented behavior")
    with ContextStore(tmp_path / "context") as store:
        assert store.latest_id() == first["id"]
        second = store.refresh(repository, sources=[SOURCE])
        assert second["id"] != first["id"]
        assert len(store.history()) == 2
        assert store.get(first["id"]) == first
        assert "new property" in store.get()["documents"][0]["content"]


def test_refresh_reads_committed_files_and_flags_dirty_sources(repository, tmp_path):
    (repository / SOURCE).write_text("-- Uncommitted and not authoritative\n")
    with ContextStore(tmp_path / "context") as store:
        snapshot = store.refresh(repository, sources=[SOURCE])
        assert "Uncommitted" not in snapshot["documents"][0]["content"]
        context = store.for_agent(snapshot["id"], Workspace(repository))
        assert context["changed_sources"] == [SOURCE]
        assert "excerpt" not in context["documents"][0]
        assert "Uncommitted" not in str(context)


@pytest.mark.parametrize("path", ["../outside", "/tmp/outside", ".git/config", "missing.md"])
def test_invalid_or_missing_sources_do_not_replace_context(repository, tmp_path, path):
    with ContextStore(tmp_path / "context") as store:
        first = store.refresh(repository, sources=[SOURCE])
        with pytest.raises((ValueError, RuntimeError)):
            store.refresh(repository, sources=[path])
        assert store.latest_id() == first["id"]


def test_committed_symlinks_are_not_imported(repository, tmp_path):
    (repository / "linked.hs").symlink_to(SOURCE)
    git(repository, "add", "linked.hs")
    git(repository, "commit", "-m", "Link source")
    with ContextStore(tmp_path / "context") as store:
        with pytest.raises(ValueError, match="regular"):
            store.refresh(repository, sources=["linked.hs"])
        assert store.latest_id() is None


def test_new_tasks_share_context_but_refresh_does_not_rewrite_existing_tasks(rig):
    harness, agent, _ = rig
    first = harness.context.refresh(harness.repository, sources=[SOURCE])
    harness.start("one", "Fix increment")
    assert agent.calls[-1][1]["project_context"]["id"] == first["id"]
    harness.start("two", "Explain increment")
    assert agent.calls[-1][1]["project_context"]["id"] == first["id"]
    second = harness.context.refresh(harness.repository, sources=[SOURCE, "cabal.project"])
    harness.respond("one", "question", text="Explain the property")
    assert agent.calls[-1][1]["project_context"]["id"] == first["id"]
    harness.start("three", "Describe the implementation")
    assert agent.calls[-1][1]["project_context"]["id"] == second["id"]


def test_context_can_be_selected_for_an_existing_task_only_between_phases(rig):
    harness, agent, _ = rig
    view = harness.start("task", "Fix increment")
    snapshot = harness.context.refresh(harness.repository, sources=[SOURCE])
    with pytest.raises(ValueError, match="pending"):
        harness.use_context("task")
    harness.respond("task", "approve", approval_token=view["prompt"]["approval_token"])
    view = harness.use_context("task")
    assert view["project_context_id"] == snapshot["id"]
    assert harness.run_phase("task", "tests")["stage"] == "test_review"
    assert agent.calls[-1][1]["project_context"]["id"] == snapshot["id"]


def test_hydra_profile_records_protocol_and_actual_state_constructors(repository, tmp_path):
    sources = {
        "docs/docs/protocol-overview.md": "# Head protocol\nAn agreed snapshot records the UTxO.\n",
        "hydra-node/src/Hydra/HeadLogic/State.hs": (
            "data HeadState tx\n  = Idle tx\n  | Open tx\n  deriving stock (Show)\n"
        ),
        "hydra-node/src/Hydra/HeadLogic.hs": (
            "update :: Input -> State -> Outcome\nupdate = undefined\n"
        ),
    }
    for name, content in sources.items():
        path = repository / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    git(repository, "add", ".")
    git(repository, "commit", "-m", "Add protocol sources")
    with ContextStore(tmp_path / "context") as store:
        snapshot = store.refresh(repository, profile="hydra")
        assert {d["path"] for d in snapshot["documents"]} == set(sources)
        assert "HeadState" in snapshot["state_declaration"]
        assert "Open tx" in snapshot["state_declaration"]
        assert snapshot["missing_sources"]
        context = store.for_agent(snapshot["id"], Workspace(repository))
        assert context["changed_sources"] == []
        assert "agreed snapshot" in str(context)
        assert any("update" in d["symbols"] for d in context["documents"])


def test_context_prompt_excerpts_are_bounded_but_full_sources_are_saved(repository, tmp_path):
    content = "-- Source detail\n" * 10_000
    (repository / SOURCE).write_text(content)
    git(repository, "commit", "-am", "Large source")
    with ContextStore(tmp_path / "context") as store:
        snapshot = store.refresh(repository, sources=[SOURCE])
        assert store.get()["documents"][0]["content"] == content
        document = store.for_agent(snapshot["id"], Workspace(repository))["documents"][0]
        assert document["truncated"]
        assert len(document["excerpt"]) <= 4_000

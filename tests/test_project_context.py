"""Shared project context stays traceable, durable, and separate from live edits."""

import pytest

from lambda_harness.project_context import ContextStore
from lambda_harness.workspace import Workspace

from .conftest import SOURCE, git


def test_context_survives_restart_and_keeps_immutable_history(repository, tmp_path):
    with ContextStore(tmp_path / "context") as store:
        first = store.refresh(repository, sources=[SOURCE])
        assert first["revision"] == git(repository, "rev-parse", "HEAD")
    (repository / SOURCE).write_text("module Example.Arithmetic where\n-- A new property\n")
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


def test_explicit_sources_record_project_architecture_and_state_types(repository, tmp_path):
    sources = {
        "docs/architecture.md": "# Queue service\nEvery accepted job can be retried.\n",
        "src/Example/State.hs": "data JobState = Pending | Running | Finished\n",
        "src/Example/Worker.hs": "advance :: JobState -> JobState\nadvance = id\n",
    }
    for name, content in sources.items():
        path = repository / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    git(repository, "add", ".")
    git(repository, "commit", "-m", "Add project sources")
    with ContextStore(tmp_path / "context") as store:
        snapshot = store.refresh(repository, sources=list(sources))
        assert {d["path"] for d in snapshot["documents"]} == set(sources)
        context = store.for_agent(snapshot["id"], Workspace(repository))
        assert context["changed_sources"] == []
        assert "accepted job" in str(context)
        assert any("JobState" in d["symbols"] for d in context["documents"])
        assert any("advance" in d["symbols"] for d in context["documents"])


def test_context_requires_explicit_source_selection(repository, tmp_path):
    with ContextStore(tmp_path / "context") as store:
        with pytest.raises(ValueError, match="at least one source"):
            store.refresh(repository)
        assert store.latest_id() is None


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


def test_memory_notes_can_be_created_edited_and_cleared_without_sources(repository, tmp_path):
    with ContextStore(tmp_path / "context") as store:
        first = store.save(
            repository, notes="# Invariants\nJobs are retryable.", sources=[], expected_current=None
        )
        assert first["notes"].startswith("# Invariants")
        assert store.for_agent(first["id"], Workspace(repository))["notes"] == first["notes"]
        second = store.save(
            repository, notes="Jobs are idempotent.", sources=[], expected_current=first["id"]
        )
        assert store.get(first["id"])["notes"] == first["notes"]
        assert store.get()["notes"] == second["notes"]
        empty = store.save(repository, notes="", sources=[], expected_current=second["id"])
        assert empty["notes"] == "" and empty["documents"] == []
        assert len(store.history()) == 3
    with ContextStore(tmp_path / "context") as reopened:
        assert reopened.get() == empty


def test_editing_memory_retains_sources_until_explicitly_refreshed(repository, tmp_path):
    with ContextStore(tmp_path / "context") as store:
        first = store.refresh(repository, sources=[SOURCE, "CLAUDE.md"])
        (repository / SOURCE).write_text("-- Updated reference\n")
        git(repository, "commit", "-am", "Change reference")
        saved = store.save(
            repository,
            notes="Explain the state machine.",
            sources=[SOURCE],
            expected_current=first["id"],
        )
        assert saved["documents"] == first["documents"][:1]
        assert saved["revision"] == first["revision"]
        assert store.for_agent(saved["id"], Workspace(repository))["changed_sources"] == [SOURCE]
        refreshed = store.save(
            repository,
            notes=saved["notes"],
            sources=[SOURCE],
            expected_current=saved["id"],
            refresh_sources=True,
        )
        assert "Updated reference" in refreshed["documents"][0]["content"]
        assert refreshed["notes"] == saved["notes"]
        assert store.for_agent(refreshed["id"], Workspace(repository))["changed_sources"] == []
        assert store.get(first["id"]) == first


def test_adding_sources_and_legacy_refresh_preserve_notes(repository, tmp_path):
    with ContextStore(tmp_path / "context") as store:
        first = store.save(
            repository, notes="Architecture decisions", sources=[], expected_current=None
        )
        saved = store.save(
            repository, notes=first["notes"], sources=[SOURCE], expected_current=first["id"]
        )
        assert saved["documents"][0]["path"] == SOURCE
        refreshed = store.refresh(repository, sources=["CLAUDE.md"])
        assert refreshed["notes"] == first["notes"]


def test_memory_rejects_stale_edits_and_restores_without_destroying_versions(repository, tmp_path):
    with ContextStore(tmp_path / "context") as first, ContextStore(tmp_path / "context") as second:
        original = first.save(repository, notes="Original", sources=[], expected_current=None)
        changed = second.save(
            repository, notes="Changed", sources=[], expected_current=original["id"]
        )
        with pytest.raises(ValueError, match="changed"):
            first.save(repository, notes="Stale", sources=[], expected_current=original["id"])
        with pytest.raises(ValueError, match="changed"):
            first.restore(original["id"], expected_current=original["id"])
        assert first.latest_id() == changed["id"]
        assert first.restore(original["id"], expected_current=changed["id"]) == original
        assert first.get(changed["id"])["notes"] == "Changed"
        assert len(first.history()) == 2


@pytest.mark.parametrize(
    "notes,sources",
    [
        (None, []),
        ("x" * 32001, []),
        ("bad\x00note", []),
        ("valid", ["../outside"]),
        ("valid", [123]),
    ],
    ids=["not-text", "too-long", "nul", "outside", "non-string-path"],
)
def test_invalid_memory_edits_do_not_replace_existing_version(repository, tmp_path, notes, sources):
    with ContextStore(tmp_path / "context") as store:
        first = store.refresh(repository, sources=[SOURCE])
        with pytest.raises(ValueError):
            store.save(repository, notes=notes, sources=sources, expected_current=first["id"])
        assert store.latest_id() == first["id"]


def test_chat_can_explicitly_use_updated_memory_between_messages(rig):
    harness, agent, executor = rig
    first = harness.context.save(
        harness.repository, notes="Old invariant", sources=[], expected_current=None
    )
    agent.responses["chat"].append({"answer": "Understood", "references": []})
    harness.start("chat", "Discuss invariants", phase="chat")
    new = harness.context.save(
        harness.repository, notes="New invariant", sources=[], expected_current=first["id"]
    )
    assert harness.inspect("chat")["project_context_id"] == first["id"]
    view = harness.use_context("chat", new["id"])
    assert view["stage"] == "chat" and view["prompt"]["actions"] == ["answer"]
    agent.responses["chat"].append({"answer": "Using the new invariant", "references": []})
    harness.respond("chat", "answer", text="What changed?")
    assert agent.calls[-1][1]["project_context"]["notes"] == "New invariant"
    assert len(agent.calls) == 2 and not executor.calls


def test_source_import_cannot_overwrite_a_concurrent_save(repository, tmp_path, monkeypatch):
    with ContextStore(tmp_path / "context") as first, ContextStore(tmp_path / "context") as second:
        original = first.save(repository, notes="Before", sources=[], expected_current=None)
        read = first._read_documents

        def read_while_another_session_saves(*args):
            second.save(
                repository, notes="Concurrent edit", sources=[], expected_current=original["id"]
            )
            return read(*args)

        monkeypatch.setattr(first, "_read_documents", read_while_another_session_saves)
        with pytest.raises(ValueError, match="changed"):
            first.save(
                repository, notes="Slow import", sources=[SOURCE], expected_current=original["id"]
            )
        assert first.get()["notes"] == "Concurrent edit"
        assert len(first.history()) == 2


def test_legacy_snapshots_remain_readable_without_rewriting_them(repository, tmp_path):
    import json

    with ContextStore(tmp_path / "context") as store:
        saved = store.refresh(repository, sources=[SOURCE])
        legacy = {key: value for key, value in saved.items() if key != "notes"}
        legacy["version"] = 2
        with store.connection:
            store.connection.execute(
                "UPDATE snapshots SET data = ? WHERE id = ?", (json.dumps(legacy), saved["id"])
            )
        assert store.get() == legacy
        assert store.for_agent(saved["id"], Workspace(repository))["notes"] == ""
        updated = store.save(
            repository, notes="New notes", sources=[SOURCE], expected_current=saved["id"]
        )
        assert updated["notes"] == "New notes"
        assert store.get(saved["id"]) == legacy

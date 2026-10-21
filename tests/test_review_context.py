"""Large reviews retain complete patches without exceeding the model prompt budget."""

import json
from pathlib import Path

import pytest

from lambda_harness import review_context
from lambda_harness.backends import prompt_for
from lambda_harness.workspace import Workspace

from .conftest import git


def large_change(repository):
    (repository / "old name.md").write_text("A document to rename.\n" * 20)
    (repository / "removed.md").write_text("Obsolete instructions.\n")
    git(repository, "add", ".")
    git(repository, "commit", "-m", "Original documentation")
    git(repository, "branch", "review-base")
    git(repository, "checkout", "-b", "large-docs")
    git(repository, "mv", "old name.md", "new\tname.md")
    (repository / "removed.md").unlink()
    (repository / "diagram.svg").write_text("<svg>" + 'x"\\é' * 150_000 + "END_OF_DIAGRAM</svg>\n")
    (repository / "guide[1]*.md").write_text("# Setup\nUse the documented flags.\n")
    (repository / "picture.bin").write_bytes(b"\x00\xff\x01")
    git(repository, "add", ".")
    git(repository, "commit", "-m", "Large documentation change")


def test_large_review_retains_all_changes_and_full_patches(rig, repository):
    harness, scripted, executor = rig
    large_change(repository)
    seen = []

    def check_prompt(role, context, cwd):
        prompt, _ = prompt_for(role, context)
        assert len(prompt.encode()) < 250_000
        seen.append(context)
        return scripted(role, context, cwd)

    harness.agent = check_prompt
    view = harness.start_review(
        "large",
        "Check instructions and diagrams",
        base="review-base",
        head="large-docs",
        reviewers="all",
    )
    assert view["stage"] == "standalone_review", view["prompt"]
    assert len(seen) == 3 and not executor.calls
    changes = seen[0]["review_changes"]
    index = json.loads(Path(changes["index"]).read_text())
    assert changes["index_complete"]
    assert changes["file_count"] == len(index["files"]) == len(changes["files"])
    assert index["base_revision"] == view["base"]
    assert index["head_revision"] == view["revision"]
    paths = {name for entry in index["files"] for name in entry["paths"]}
    assert paths == {
        "diagram.svg",
        "old name.md",
        "new\tname.md",
        "removed.md",
        "guide[1]*.md",
        "picture.bin",
    }
    patches = {entry["paths"][-1]: Path(entry["patch"]).read_text() for entry in index["files"]}
    assert "END_OF_DIAGRAM" in patches["diagram.svg"]
    assert "rename from" in patches["new\tname.md"]
    assert "-Obsolete instructions." in patches["removed.md"]
    assert "Use the documented flags." in patches["guide[1]*.md"]
    assert "END_OF_DIAGRAM" not in patches["guide[1]*.md"]
    assert "Binary files" in patches["picture.bin"]
    assert "END_OF_DIAGRAM" in Path(changes["full_diff"]).read_text()
    assert "END_OF_DIAGRAM" not in seen[0]["diff"]
    assert "Use the documented flags." in seen[0]["diff"]
    before = Workspace(view["worktree"]).fingerprint()
    view = harness.respond("large", "question", text="What about the diagram?")
    assert view["stage"] == "standalone_review"
    assert seen[-1]["review_changes"] == changes
    assert Workspace(view["worktree"]).fingerprint() == before
    assert git(Path(view["worktree"]), "status", "--porcelain") == ""


def test_small_review_keeps_complete_diff_inline(rig, repository):
    harness, agent, _ = rig
    git(repository, "checkout", "-b", "small-docs")
    (repository / "README.md").write_text("# Setup\nRead the guide.\n")
    git(repository, "add", ".")
    git(repository, "commit", "-m", "Write docs")
    view = harness.start_review("small", "Check setup", base="main", head="small-docs")
    assert view["stage"] == "standalone_review"
    assert "+# Setup" in agent.calls[0][1]["diff"]
    assert "review_changes" not in agent.calls[0][1]


def test_existing_size_failure_can_retry_in_the_same_worktree(rig, repository, monkeypatch):
    harness, scripted, _ = rig
    large_change(repository)

    def check_prompt(role, context, cwd):
        prompt_for(role, context)
        return scripted(role, context, cwd)

    harness.agent = check_prompt
    with monkeypatch.context() as original:
        original.setattr(review_context, "INLINE_DIFF_BYTES", 10_000_000)
        failed = harness.start_review(
            "retry-large",
            "Review docs",
            base="review-base",
            head="large-docs",
            reviewers="documentation",
        )
    assert failed["stage"] == "agent_error"
    assert "context is too large" in failed["prompt"]["error"]
    assert not scripted.calls
    view = harness.respond("retry-large", "retry")
    assert view["stage"] == "standalone_review", view["prompt"]
    for key in ("task_id", "base", "revision", "worktree", "project_context_id", "agent_profiles"):
        assert view[key] == failed[key]
    assert len(scripted.calls) == 1


def test_large_file_index_is_bounded_but_complete_index_is_readable(rig, repository, monkeypatch):
    harness, agent, _ = rig
    large_change(repository)
    monkeypatch.setattr(review_context, "INLINE_INDEX_BYTES", 400)
    view = harness.start_review(
        "many-files",
        "Review docs",
        base="review-base",
        head="large-docs",
        reviewers="documentation",
    )
    assert view["stage"] == "standalone_review"
    changes = agent.calls[0][1]["review_changes"]
    assert not changes["index_complete"]
    assert changes["file_count"] > len(changes["files"])
    assert len(json.dumps(changes["files"]).encode()) <= 402
    assert len(json.loads(Path(changes["index"]).read_text())["files"]) == changes["file_count"]


def test_review_artifacts_cannot_overwrite_files_through_a_symlink(repository, tmp_path):
    large_change(repository)
    reports = tmp_path / "reports"
    outside = tmp_path / "outside"
    reports.mkdir()
    outside.mkdir()
    (reports / "review-input").symlink_to(outside, target_is_directory=True)
    source = {
        "merge_base": git(repository, "rev-parse", "review-base"),
        "head": git(repository, "rev-parse", "HEAD"),
    }
    with pytest.raises(ValueError, match="symbolic links"):
        review_context.review_diff(Workspace(repository), source, reports)
    assert list(outside.iterdir()) == []

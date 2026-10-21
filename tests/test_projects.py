"""Project registration never deletes a checkout or its saved work."""

import copy

import pytest

from lambda_harness.projects import ProjectRegistry
from lambda_harness.webapp import ProjectHub

from .conftest import git


def test_registry_deduplicates_paths_and_remembers_removal(repository, tmp_path):
    root = tmp_path / "state"
    registry = ProjectRegistry(root)
    first = registry.add(str(repository))
    alias = tmp_path / "alias"
    alias.symlink_to(repository, target_is_directory=True)
    assert registry.add(str(alias)) == first
    assert registry.list() == [first]
    marker = repository / "keep-me"
    marker.write_text("source")
    saved = root / first["id"] / "saved-memory"
    saved.parent.mkdir()
    saved.write_text("memory")
    registry.remove(first["id"])
    assert ProjectRegistry(root).list() == []
    assert marker.read_text() == "source" and saved.read_text() == "memory"
    assert registry.add(str(repository)) == first


def test_registry_rejects_missing_non_git_and_nested_paths(repository, tmp_path):
    registry = ProjectRegistry(tmp_path / "state")
    nested = repository / "nested"
    nested.mkdir()
    for path in [None, "", str(tmp_path / "missing"), str(tmp_path), str(nested)]:
        with pytest.raises((ValueError, RuntimeError)):
            registry.add(path)
    assert registry.list() == []


def test_hub_keeps_project_preferences_and_launch_flake_isolated(repository, tmp_path):
    from lambda_harness.__main__ import parser

    second_path = tmp_path / "other" / repository.name
    second_path.parent.mkdir()
    git(tmp_path, "clone", str(repository), str(second_path))
    args = parser().parse_args(
        [
            "--repo",
            str(repository),
            "--state-dir",
            str(tmp_path / "state"),
            "--nix-flake",
            str(repository),
        ]
    )
    hub = ProjectHub(args)
    first = hub.project()
    second = hub.add({"repository": str(second_path)})
    other = hub.project(second["id"])
    assert first.identity["name"] == other.identity["name"]
    assert first.identity["id"] != other.identity["id"]
    assert other.args.nix_flake is None
    first.project_change("backend", {"backend": "codex"})
    assert other.project()["backend"] == "auto"
    with pytest.raises(ValueError, match="confirm"):
        hub.remove({"project_id": first.identity["id"]})
    first.job = {"running": True}
    with pytest.raises(ValueError, match="running"):
        hub.remove({"project_id": first.identity["id"], "confirmation": first.identity["id"]})
    first.job = None
    hub.remove({"project_id": first.identity["id"], "confirmation": first.identity["id"]})
    assert hub.project() is None
    with pytest.raises(ValueError, match="registered"):
        hub.project(first.identity["id"])
    hub.close()
    args = copy.copy(args)
    args.repo = None
    reopened = ProjectHub(args)
    assert len(reopened.projects()["projects"]) == 1
    assert reopened.project() is None
    reopened.add({"repository": str(repository)})
    assert reopened.project(first.identity["id"]).project()["backend"] == "codex"
    reopened.close()

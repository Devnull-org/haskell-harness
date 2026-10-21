"""The product rename keeps saved work and user configuration discoverable."""

from lambda_harness.__main__ import main
from lambda_harness.config import load_settings
from lambda_harness.paths import default_state_root
from lambda_harness.project_context import state_directory


def test_new_projects_use_the_new_state_directory(repository, tmp_path, monkeypatch):
    root = tmp_path / "state"
    monkeypatch.setenv("XDG_STATE_HOME", str(root))
    assert default_state_root(repository) == root / "lambda-harness"
    assert not root.exists()


def test_website_can_start_without_a_project(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    calls = []
    monkeypatch.setattr("lambda_harness.__main__.serve", calls.append)
    assert main([]) == 0
    assert calls[0].repo is None
    assert calls[0].state_dir == tmp_path / "lambda-harness"
    assert main(["--nix-flake", str(tmp_path)]) == 1


def test_existing_project_state_is_reused_without_moving_worktrees(
    repository, tmp_path, monkeypatch
):
    root = tmp_path / "state"
    monkeypatch.setenv("XDG_STATE_HOME", str(root))
    legacy = root / "haskell-harness"
    directory = state_directory(repository, legacy)
    directory.mkdir(parents=True)
    (directory / "checkpoints.sqlite").write_bytes(b"existing checkpoint")
    # A new directory for a different project must not hide this project's history.
    (root / "lambda-harness" / "another-project").mkdir(parents=True)
    assert default_state_root(repository) == legacy
    calls = []
    monkeypatch.setattr("lambda_harness.__main__.serve", calls.append)
    assert main(["--repo", str(repository)]) == 0
    assert calls[0].state_dir == legacy
    assert (directory / "checkpoints.sqlite").read_bytes() == b"existing checkpoint"


def test_new_project_state_takes_precedence_without_merging_old_state(
    repository, tmp_path, monkeypatch
):
    root = tmp_path / "state"
    monkeypatch.setenv("XDG_STATE_HOME", str(root))
    for name in ("haskell-harness", "lambda-harness"):
        directory = state_directory(repository, root / name)
        directory.mkdir(parents=True)
        (directory / "project-context.sqlite").touch()
    assert default_state_root(repository) == root / "lambda-harness"


def test_explicit_state_directory_wins(repository, tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr("lambda_harness.__main__.serve", calls.append)
    explicit = tmp_path / "chosen"
    assert main(["--repo", str(repository), "--state-dir", str(explicit)]) == 0
    assert calls[0].state_dir == explicit


def test_old_configuration_is_used_only_without_a_new_configuration(tmp_path, monkeypatch):
    root = tmp_path / "configuration"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(root))
    old = root / "haskell-harness" / "config.toml"
    new = root / "lambda-harness" / "config.toml"
    old.parent.mkdir(parents=True)
    old.write_text('default_backend = "codex"\n')
    assert load_settings().default_backend == "codex"
    assert not new.exists()
    new.parent.mkdir()
    new.write_text('default_backend = "claude-code"\n')
    assert load_settings().default_backend == "claude-code"
    assert load_settings(old).default_backend == "codex"

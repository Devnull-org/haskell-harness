"""Storage is chosen explicitly; user configuration keeps its existing lookup rules."""

from lambda_harness.__main__ import main
from lambda_harness.config import load_settings
from lambda_harness.project_context import state_directory


def test_website_can_start_without_a_project(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr("lambda_harness.__main__.serve", calls.append)
    options = ["--state-dir", str(tmp_path / "chosen")]
    assert main(options) == 0
    assert calls[0].repo is None
    assert calls[0].state_dir == tmp_path / "chosen"
    assert main([*options, "--nix-flake", str(tmp_path)]) == 1


def test_explicit_existing_state_is_reused_without_moving_worktrees(
    repository, tmp_path, monkeypatch
):
    root = tmp_path / "state"
    monkeypatch.setenv("XDG_STATE_HOME", str(root))
    legacy = root / "haskell-harness"
    directory = state_directory(repository, legacy)
    directory.mkdir(parents=True)
    (directory / "checkpoints.sqlite").write_bytes(b"existing checkpoint")
    # An alternative state root must not override the developer's explicit choice.
    (root / "lambda-harness" / "another-project").mkdir(parents=True)
    calls = []
    monkeypatch.setattr("lambda_harness.__main__.serve", calls.append)
    assert main(["--repo", str(repository), "--state-dir", str(legacy)]) == 0
    assert calls[0].state_dir == legacy
    assert (directory / "checkpoints.sqlite").read_bytes() == b"existing checkpoint"


def test_state_directory_is_resolved_before_startup(repository, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    calls = []
    monkeypatch.setattr("lambda_harness.__main__.serve", calls.append)
    explicit = tmp_path / "chosen"
    assert main(["--repo", str(repository), "--state-dir", "chosen"]) == 0
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

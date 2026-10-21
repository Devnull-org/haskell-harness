"""Default locations, retaining old installations without moving registered worktrees."""

import os
from pathlib import Path

from .workspace import digest

APP_DIRECTORY = "lambda-harness"
# Compatibility only: changing existing worktree paths would break Git registrations.
LEGACY_DIRECTORY = "haskell-harness"


def default_state_root(repository):
    root = Path(os.environ.get("XDG_STATE_HOME", str(Path.home() / ".local/state")))
    project = digest(str(Path(repository).expanduser().resolve()))[:16]
    for name in (APP_DIRECTORY, LEGACY_DIRECTORY):
        directory = root / name / project
        if any(
            (directory / file).is_file()
            for file in ("checkpoints.sqlite", "project-context.sqlite")
        ):
            return root / name
    return root / APP_DIRECTORY


def default_config_path():
    root = Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config")))
    preferred = root / APP_DIRECTORY / "config.toml"
    legacy = root / LEGACY_DIRECTORY / "config.toml"
    return legacy if not preferred.exists() and legacy.exists() else preferred

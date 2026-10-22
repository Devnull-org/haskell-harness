"""Find user configuration, including settings saved before the product rename."""

import os
from pathlib import Path

APP_DIRECTORY = "lambda-harness"
LEGACY_DIRECTORY = "haskell-harness"


def default_config_path():
    root = Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config")))
    preferred = root / APP_DIRECTORY / "config.toml"
    legacy = root / LEGACY_DIRECTORY / "config.toml"
    return legacy if not preferred.exists() and legacy.exists() else preferred

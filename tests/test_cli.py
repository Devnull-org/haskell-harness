"""Exercise separate CLI processes against the same durable interview."""

import json
import os
import subprocess
import sys

from .conftest import SPECIFICATION
from .test_cursor import executable


def test_cli_persists_a_conversation_across_processes(repository, tmp_path):
    envelope = {
        "type": "result",
        "is_error": False,
        "result": json.dumps({"specification": SPECIFICATION, "questions": []}),
    }
    cursor = executable(tmp_path, f"print({json.dumps(envelope)!r})\n")
    args = [
        sys.executable,
        "-m",
        "hydra_harness.cli",
        "--repo",
        str(repository),
        "--state-dir",
        str(tmp_path / "state"),
        "--cursor",
        cursor,
        "--json",
    ]
    environment = dict(os.environ)

    def call(*command):
        result = subprocess.run(
            [*args, *command], text=True, capture_output=True, env=environment, check=True
        )
        return json.loads(result.stdout)

    view = call("start", "interview", "Fix increment", "--interview-only")
    assert view["stage"] == "specification_review"
    assert call("status", "interview")["prompt"] == view["prompt"]
    assert call("resume", "interview")["prompt"] == view["prompt"]
    done = call("respond", "interview", "approve", "--token", view["prompt"]["approval_token"])
    assert done["stage"] == "specification_ready"

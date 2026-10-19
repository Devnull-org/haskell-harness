"""Small executable doubles for process-boundary tests."""

import sys


def executable(tmp_path, body):
    script = tmp_path / "agent-double"
    script.write_text(f"#!{sys.executable}\n" + body)
    script.chmod(0o700)
    return str(script)

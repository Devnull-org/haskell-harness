"""Exercise the process boundary without contacting Cursor or using credentials."""

import json
import os
import sys
from pathlib import Path

import pytest

from hydra_harness.contracts import CursorAgent

from .conftest import SPECIFICATION


def executable(tmp_path, body):
    script = tmp_path / "cursor-double"
    script.write_text(f"#!{sys.executable}\n" + body)
    script.chmod(0o700)
    return str(script)


def test_cursor_uses_saved_login_and_read_only_mode(tmp_path, monkeypatch):
    capture = tmp_path / "invocation.json"
    reply = {"specification": SPECIFICATION, "questions": []}
    envelope = {
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "result": json.dumps(reply),
    }
    script = executable(
        tmp_path,
        "import json, os, sys\n"
        "from pathlib import Path\n"
        f"Path({str(capture)!r}).write_text(json.dumps({{\n"
        "  'argv': sys.argv[1:], 'prompt': sys.stdin.read(),\n"
        "  'has_api_key': 'CURSOR_API_KEY' in os.environ, 'cwd': os.getcwd()\n"
        "}))\n"
        f"print({json.dumps(envelope)!r})\n",
    )
    monkeypatch.setenv("CURSOR_API_KEY", "test-only-value-that-must-not-be-forwarded")
    result = CursorAgent(script)("interviewer", {"request": "Fix addOne"}, tmp_path)
    assert result == reply
    invocation = json.loads(capture.read_text())
    assert invocation["has_api_key"] is False
    assert invocation["cwd"] == str(tmp_path)
    assert "--print" in invocation["argv"]
    assert invocation["argv"][invocation["argv"].index("--mode") + 1] == "ask"
    assert invocation["argv"][invocation["argv"].index("--output-format") + 1] == "json"
    assert "--force" not in invocation["argv"]
    assert "Fix addOne" in invocation["prompt"]


@pytest.mark.parametrize(
    "body, message",
    [
        ("import sys\nprint('Not logged in', file=sys.stderr)\nsys.exit(1)\n", "logged in"),
        ("print('not json')\n", "JSON|json"),
        ('print(\'{"type": "result", "is_error": true}\')\n', "error|failed"),
        (
            "import json\nprint(json.dumps({'type': 'result', 'is_error': False, "
            "'result': '{\"unexpected\": 1}'}))\n",
            "schema|valid|specification",
        ),
    ],
)
def test_cursor_errors_do_not_advance_the_workflow(tmp_path, body, message):
    runner = CursorAgent(executable(tmp_path, body))
    with pytest.raises((RuntimeError, ValueError), match=message):
        runner("interviewer", {"request": "Fix addOne"}, tmp_path)


def test_cursor_timeout_stops_the_process(tmp_path):
    marker = tmp_path / "finished"
    script = executable(
        tmp_path,
        "import time\nfrom pathlib import Path\ntime.sleep(2)\n"
        f"Path({str(marker)!r}).write_text('unexpected completion')\n",
    )
    with pytest.raises(RuntimeError, match="timed out|timeout"):
        CursorAgent(script, timeout=0.05)("interviewer", {}, tmp_path)
    assert not marker.exists()


def test_missing_cursor_reports_an_actionable_error(tmp_path):
    missing = str(Path(tmp_path) / "missing-cursor")
    assert not os.path.exists(missing)
    with pytest.raises(RuntimeError, match="Cursor|cursor|executable"):
        CursorAgent(missing)("interviewer", {}, tmp_path)

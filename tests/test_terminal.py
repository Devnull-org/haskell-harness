"""Backend activity stays visible and leaves the next terminal prompt clean."""

import io
import json
import os
import sys
import time

import pytest

from hydra_harness.backends import Backend, CommandAgent
from hydra_harness.terminal import spinner

from .helpers import executable, terminal_process


@pytest.mark.skipif(os.name != "posix", reason="Requires a POSIX terminal")
@pytest.mark.parametrize(
    "keys,expected",
    [
        ("First\x1b\r\x1b\r  α = 1\nLast", "First\n\n  α = 1\nLast"),
        (
            "\x1b[200~First\r\n\r\n  α = 1\r\napprove\r\n\x1b[201~",
            "First\n\n  α = 1\napprove\n",
        ),
    ],
    ids=["typed-newlines", "pasted-paragraphs"],
)
def test_message_editor_preserves_lines_and_waits_for_enter(tmp_path, keys, expected):
    capture = tmp_path / "message.json"
    program = (
        "import json\nfrom pathlib import Path\n"
        "from hydra_harness.terminal import read_message\n"
        "message = read_message()\n"
        f"Path({str(capture)!r}).write_text(json.dumps(message))\n"
        "print('Message submitted', flush=True)\n"
    )
    with terminal_process([sys.executable, "-c", program]) as (process, expect, send):
        expect(">")
        send(keys, enter=False)
        expect("Last" if keys.startswith("First") else "approve")
        assert not capture.exists()
        send("")
        expect("Message submitted")
        assert process.wait(timeout=10) == 0
    assert json.loads(capture.read_text()) == expected


@pytest.mark.skipif(os.name != "posix", reason="Requires a POSIX terminal")
@pytest.mark.parametrize("key,error", [("\x03", "KeyboardInterrupt"), ("\x04", "EOFError")])
def test_message_editor_interrupt_does_not_submit(tmp_path, key, error):
    program = (
        "from hydra_harness.terminal import read_message\n"
        "try:\n    read_message()\n"
        f"except {error}:\n    print('Cancelled', flush=True)\n"
        "else:\n    raise AssertionError('Input was submitted')\n"
    )
    with terminal_process([sys.executable, "-c", program]) as (process, expect, send):
        expect(">")
        send(key, enter=False)
        expect("Cancelled")
        assert process.wait(timeout=10) == 0


@pytest.mark.parametrize("failure", [None, "exit", "invalid", "timeout"])
def test_backend_spinner_animates_and_clears_on_completion_or_failure(tmp_path, failure):
    stream = io.StringIO()
    result = {"answer": "The code handles this case."}
    script = executable(
        tmp_path,
        "import sys, time\nsys.stdin.read()\ntime.sleep(0.1)\n"
        + {
            None: f"print({json.dumps(result)!r})\n",
            "exit": "sys.exit(1)\n",
            "invalid": "print('invalid response')\n",
            "timeout": "time.sleep(5)\n",
        }[failure],
    )
    agent = CommandAgent(
        Backend(command=[script]),
        timeout=0.2 if failure == "timeout" else 5,
        activity=lambda message: spinner(message, stream=stream, interval=0.01),
    )
    if failure:
        with pytest.raises((ValueError, RuntimeError)):
            agent("interviewer", {"intent": "explain"}, tmp_path)
    else:
        assert agent("interviewer", {"intent": "explain"}, tmp_path) == result
    output = stream.getvalue()
    assert "interviewer working" in output
    assert "0s elapsed" in output
    assert output.count("working") >= 2
    assert output.endswith("\r\x1b[2K")
    time.sleep(0.03)
    assert stream.getvalue() == output


def test_spinner_clears_and_stops_on_keyboard_interrupt():
    stream = io.StringIO()
    with pytest.raises(KeyboardInterrupt), spinner("Inspecting code", stream=stream, interval=0.01):
        raise KeyboardInterrupt
    output = stream.getvalue()
    assert "Inspecting code" in output
    assert output.endswith("\r\x1b[2K")
    time.sleep(0.03)
    assert stream.getvalue() == output

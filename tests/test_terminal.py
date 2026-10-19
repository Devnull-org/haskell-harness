"""Backend activity stays visible and leaves the next terminal prompt clean."""

import io
import json
import time

import pytest

from hydra_harness.backends import Backend, CommandAgent
from hydra_harness.terminal import spinner

from .helpers import executable


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

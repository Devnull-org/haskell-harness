"""Small executable doubles for process-boundary tests."""

import os
import subprocess
import sys
import time
from contextlib import contextmanager


def executable(tmp_path, body):
    script = tmp_path / "agent-double"
    script.write_text(f"#!{sys.executable}\n" + body)
    script.chmod(0o700)
    return str(script)


@contextmanager
def terminal_process(args):
    """Run a real terminal conversation and wait for its visible prompts."""
    import pty
    import select
    import termios

    master, slave = pty.openpty()
    termios.tcsetwinsize(slave, (24, 100))
    try:
        process = subprocess.Popen(
            args,
            stdin=slave,
            stdout=slave,
            stderr=slave,
            env=dict(os.environ, TERM="xterm", PROMPT_TOOLKIT_NO_CPR="1"),
        )
    finally:
        os.close(slave)
    output = b""

    def expect(text):
        nonlocal output
        deadline = time.monotonic() + 30
        while text.encode() not in output:
            remaining = deadline - time.monotonic()
            assert remaining > 0, output.decode(errors="replace")
            ready, _, _ = select.select([master], [], [], remaining)
            assert ready, output.decode(errors="replace")
            try:
                chunk = os.read(master, 65536)
            except OSError:
                chunk = b""
            assert chunk, output.decode(errors="replace")
            output += chunk
        output = output.split(text.encode(), 1)[1]

    def send(text, *, enter=True):
        os.write(master, (text + ("\r" if enter else "")).encode())

    try:
        yield process, expect, send
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
        os.close(master)

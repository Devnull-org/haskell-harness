"""Terminal message editing and progress indicators."""

import itertools
import shutil
import sys
import threading
import time
from contextlib import contextmanager

from prompt_toolkit import prompt
from prompt_toolkit.key_binding import KeyBindings


def read_message(message="> ", *, default=""):
    """Keep typed and pasted lines in one editable message until Enter submits it."""
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        return input(message)

    bindings = KeyBindings()

    @bindings.add("enter")
    def submit(event):
        event.current_buffer.validate_and_handle()

    @bindings.add("escape", "enter")
    @bindings.add("c-j")
    def newline(event):
        event.current_buffer.insert_text("\n")

    print("Enter sends. Alt+Enter or Ctrl+J adds a newline. Pasted lines stay together.")
    return prompt(
        message, default=default, multiline=True, prompt_continuation="  ", key_bindings=bindings
    )


@contextmanager
def spinner(message, *, stream=None, interval=0.1):
    """Animate until the operation ends, then clear the line before the next prompt."""
    stream = sys.stderr if stream is None else stream
    stopped = threading.Event()
    frames = itertools.cycle("|/-\\")
    started = time.monotonic()
    message = " ".join(message.splitlines())

    def draw():
        width = max(1, shutil.get_terminal_size().columns - 1)
        elapsed = f" ({int(time.monotonic() - started)}s elapsed)"
        label = message[: max(0, width - len(elapsed) - 2)]
        line = f"{next(frames)} {label}{elapsed}"
        stream.write("\r\x1b[2K" + line[:width])
        stream.flush()

    def animate():
        while not stopped.wait(interval):
            draw()

    draw()
    worker = threading.Thread(target=animate, name="harness-spinner", daemon=True)
    worker.start()
    try:
        yield
    finally:
        stopped.set()
        worker.join()
        stream.write("\r\x1b[2K")
        stream.flush()

"""Stop external work without interrupting LangGraph's checkpoint bookkeeping."""

import signal
import threading
from contextlib import contextmanager


class ModelCancellation:
    def __init__(self):
        self.active = None
        self.kind = None
        self.previous_handler = None
        self.closing = threading.Event()

    def stop(self):
        """Return whether a running request was asked to stop."""
        active = self.active
        if active is None:
            return False
        active.set()
        return True

    def close(self):
        self.closing.set()
        self.stop()

    @contextmanager
    def request(self, *, kind="model"):
        if self.active is not None:
            raise RuntimeError("A model request is already running")
        event = threading.Event()
        self.kind = kind
        self.active = event
        if self.closing.is_set():
            event.set()
        try:
            yield event
        finally:
            self.active = None
            self.kind = None

    def __enter__(self):
        self.previous_handler = signal.getsignal(signal.SIGINT)

        def interrupt(signum, frame):
            if not self.stop():
                if callable(self.previous_handler):
                    self.previous_handler(signum, frame)
                elif self.previous_handler != signal.SIG_IGN:
                    signal.default_int_handler(signum, frame)

        signal.signal(signal.SIGINT, interrupt)
        return self

    def __exit__(self, *args):
        signal.signal(signal.SIGINT, self.previous_handler)

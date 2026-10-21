"""Run local commands and stop their process groups on cancellation or timeout."""

import os
import signal
import subprocess
import time


class ProcessError(RuntimeError):
    """Preserve partial output when a process cannot start or times out."""

    def __init__(self, message, *, kind, stdout="", stderr=""):
        super().__init__(message)
        self.kind, self.stdout, self.stderr = kind, stdout, stderr


def run_process(
    argv,
    cwd,
    *,
    timeout=600,
    stdin=None,
    env=None,
    progress=None,
    progress_interval=15,
    cancel=None,
):
    if progress_interval <= 0:
        raise ValueError("Progress interval must be positive")
    if cancel is not None and cancel.is_set():
        raise ProcessError("Process cancelled", kind="cancelled")
    try:
        process = subprocess.Popen(
            argv,
            cwd=cwd,
            env=env,
            stdin=subprocess.PIPE if stdin is not None else subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            start_new_session=True,
        )
    except OSError as error:
        raise ProcessError(
            f"Cannot run executable {argv[0]}: {error}", kind="unavailable", stderr=str(error)
        ) from error
    started = time.monotonic()
    last_progress = 0
    try:
        while True:
            if cancel is not None and cancel.is_set():
                raise ProcessError("Process cancelled", kind="cancelled")
            remaining = timeout - (time.monotonic() - started)
            if remaining <= 0:
                raise subprocess.TimeoutExpired(argv, timeout)
            try:
                wait = min(remaining, progress_interval) if progress else remaining
                if cancel is not None:
                    wait = min(wait, 0.1)
                stdout, stderr = process.communicate(stdin, timeout=wait)
                if cancel is not None and cancel.is_set():
                    raise ProcessError("Process cancelled", kind="cancelled")
                break
            except subprocess.TimeoutExpired:
                stdin = None
                elapsed = time.monotonic() - started
                if elapsed >= timeout:
                    raise
                if progress and elapsed - last_progress >= progress_interval:
                    progress(elapsed)
                    last_progress = elapsed
    except BaseException as error:
        stdout, stderr = "", ""
        try:
            os.killpg(process.pid, signal.SIGTERM)
            stdout, stderr = process.communicate(timeout=3)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            stdout, stderr = process.communicate()
        except ProcessLookupError:
            stdout, stderr = process.communicate()
        if isinstance(error, subprocess.TimeoutExpired):
            raise ProcessError(
                f"{argv[0]} timed out after {timeout} seconds",
                kind="timeout",
                stdout=stdout,
                stderr=stderr,
            ) from error
        if isinstance(error, ProcessError):
            error.stdout, error.stderr = stdout, stderr
        raise
    return process.returncode, stdout, stderr

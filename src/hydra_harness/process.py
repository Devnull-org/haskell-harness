"""Run local commands without a shell and stop their process groups on timeout."""

import os
import signal
import subprocess
import time


def run_process(
    argv, cwd, *, timeout=600, stdin=None, env=None, progress=None, progress_interval=15
):
    if progress_interval <= 0:
        raise ValueError("Progress interval must be positive")
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
        raise RuntimeError(f"Cannot run executable {argv[0]}: {error}") from error
    started = time.monotonic()
    try:
        while True:
            remaining = timeout - (time.monotonic() - started)
            if remaining <= 0:
                raise subprocess.TimeoutExpired(argv, timeout)
            try:
                stdout, stderr = process.communicate(
                    stdin, timeout=min(remaining, progress_interval) if progress else remaining
                )
                break
            except subprocess.TimeoutExpired:
                stdin = None
                elapsed = time.monotonic() - started
                if elapsed >= timeout:
                    raise
                if progress:
                    progress(elapsed)
    except BaseException as error:
        try:
            os.killpg(process.pid, signal.SIGTERM)
            process.communicate(timeout=3)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.communicate()
        except ProcessLookupError:
            process.wait()
        if isinstance(error, subprocess.TimeoutExpired):
            raise RuntimeError(f"{argv[0]} timed out after {timeout} seconds") from error
        raise
    return process.returncode, stdout, stderr

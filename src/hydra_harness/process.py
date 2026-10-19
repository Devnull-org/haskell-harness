"""Run local commands without a shell and stop their process groups on timeout."""

import os
import signal
import subprocess


def run_process(argv, cwd, *, timeout=600, stdin=None, env=None):
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
    try:
        stdout, stderr = process.communicate(stdin, timeout=timeout)
    except (subprocess.TimeoutExpired, KeyboardInterrupt) as error:
        try:
            os.killpg(process.pid, signal.SIGTERM)
            process.communicate(timeout=3)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.communicate()
        except ProcessLookupError:
            process.wait()
        if isinstance(error, KeyboardInterrupt):
            raise
        raise RuntimeError(f"{argv[0]} timed out after {timeout} seconds") from error
    return process.returncode, stdout, stderr

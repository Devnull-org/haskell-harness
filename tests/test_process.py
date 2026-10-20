"""Long-running backends report elapsed time without losing their response."""

from haskell_harness.process import run_process

from .helpers import executable


def test_process_reports_progress_and_preserves_piped_input_and_output(tmp_path):
    script = executable(
        tmp_path,
        "import sys, time\ntext = sys.stdin.read()\n"
        "print('working', file=sys.stderr, flush=True)\ntime.sleep(0.2)\nprint(text)\n",
    )
    elapsed = []
    code, stdout, stderr = run_process(
        [script],
        tmp_path,
        stdin="request",
        timeout=3,
        progress=elapsed.append,
        progress_interval=0.03,
    )
    assert code == 0
    assert stdout == "request\n"
    assert stderr == "working\n"
    assert elapsed and elapsed == sorted(elapsed)

"""Tests for the shell-free process runner.

These run *real* subprocesses (``python``/``sleep`` only) so they exercise the
actual argv/exit-code/timeout/signal behavior (spec §13, §16.1) without needing
Wine. All commands are self-contained and harmless.
"""

from __future__ import annotations

import json
import signal
import sys
import threading
import time
from contextlib import contextmanager

import pytest

from lsw.errors import OperationTimeoutError
from lsw.process import ProcessRunner


def test_exit_code_passthrough():
    result = ProcessRunner().run([sys.executable, "-c", "import sys; sys.exit(7)"], capture=True)
    assert result.exit_code == 7


def test_capture_stdout_and_stderr_separately():
    code = "import sys; print('out'); print('err', file=sys.stderr); sys.exit(3)"
    result = ProcessRunner().run([sys.executable, "-c", code], capture=True)
    assert result.exit_code == 3
    assert result.stdout == b"out\n"
    assert result.stderr == b"err\n"


def test_argv_is_an_array_not_a_shell_string():
    probe = "import sys, json; print(json.dumps(sys.argv[1:]))"
    args = ["a b", 'c"d', "中文", r"C:\path\with\spaces"]
    result = ProcessRunner().run([sys.executable, "-c", probe, *args], capture=True)
    assert json.loads(result.stdout.decode("utf-8")) == args


def test_timeout_raises_operation_timeout():
    runner = ProcessRunner(poll_interval=0.01)
    started = time.monotonic()
    with pytest.raises(OperationTimeoutError):
        runner.run(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            timeout=0.3,
            capture=True,
        )
    assert time.monotonic() - started < 10


def test_signal_death_maps_to_128_plus_signum():
    code = "import os, signal; os.kill(os.getpid(), signal.SIGTERM)"
    result = ProcessRunner().run([sys.executable, "-c", code], capture=True)
    assert result.exit_code == 128 + signal.SIGTERM


def test_empty_argv_rejected():
    with pytest.raises(ValueError):
        ProcessRunner().run([])


def test_nonpositive_timeout_rejected():
    with pytest.raises(ValueError):
        ProcessRunner().run([sys.executable], timeout=0, capture=True)


def test_capture_and_interactive_conflict():
    with pytest.raises(ValueError):
        ProcessRunner().run([sys.executable], capture=True, interactive=True)


# --------------------------------------------------------- pump teardown

# The regression tests below exercise the pump-thread lifecycle. They spawn a
# child that exits while a *descendant* keeps the captured pipe open (the shape
# of Wine leaving its wineserver attached), which is exactly the situation that
# used to make run() close a stream under a still-reading pump thread:
#
# * old _wait built the CommandResult the instant the child was reaped, before
#   the pumps had drained  -> captured output came back empty/truncated;
# * old teardown joined pumps for a fixed poll_interval*4, then closed the
#   streams regardless -> a mid-drain pump raised
#   "ValueError: I/O operation on closed file" in its daemon thread.


@contextmanager
def _captured_thread_exceptions():
    """Yield a list recording unhandled exceptions from daemon threads.

    pytest surfaces these as PytestUnhandledThreadExceptionWarning; recording
    them here lets us assert deterministically that a pump thread died cleanly.
    """
    errors: list[BaseException] = []
    previous = threading.excepthook

    def _record(args: threading.ExceptHookArgs) -> None:
        errors.append(args.exc_value)

    threading.excepthook = _record
    try:
        yield errors
    finally:
        threading.excepthook = previous


def _child_holding_pipe_open(tmp_path, hold_seconds: float) -> str:
    """Child prints ``hello``, spawns a grandchild that keeps stdout open for
    ``hold_seconds``, then exits — EOF on the captured pipe is delayed past the
    child's death (mirrors Wine's wineserver inheriting the pipe)."""
    grandchild = tmp_path / "holder.py"
    grandchild.write_text(
        f"import sys, time\ntime.sleep({hold_seconds})\nsys.stdout.write('more')\n"
    )
    child = tmp_path / "child.py"
    child.write_text(
        "import subprocess, sys\n"
        f"subprocess.Popen([sys.executable, {str(grandchild)!r}])\n"
        "print('hello')\n"
    )
    return str(child)


def _child_with_continuous_writer(tmp_path) -> str:
    """Child writes a burst then exits while a grandchild keeps streaming to the
    same pipe for ~0.6s, so the pump thread is *actively draining* — not blocked
    waiting on EOF — at the moment teardown used to close its stream."""
    writer = tmp_path / "writer.py"
    writer.write_text(
        "import sys, time\n"
        "end = time.time() + 0.6\n"
        "block = 'more\\n' * 2000\n"
        "while time.time() < end:\n"
        "    sys.stdout.write(block); sys.stdout.flush()\n"
    )
    child = tmp_path / "child.py"
    child.write_text(
        "import subprocess, sys\n"
        f"subprocess.Popen([sys.executable, {str(writer)!r}])\n"
        "sys.stdout.write('x' * 200000)\n"
        "sys.stdout.flush()\n"
    )
    return str(child)


def test_capture_returns_complete_output_when_pipe_held_past_child_exit(tmp_path):
    # Regression: run() used to build the result the moment the child was
    # reaped, before the pumps had drained — captured output came back empty
    # whenever a descendant kept the pipe open.
    child = _child_holding_pipe_open(tmp_path, hold_seconds=0.6)
    result = ProcessRunner().run([sys.executable, child], capture=True, timeout=30)
    assert result.stdout == b"hello\n"


def test_capture_teardown_never_closes_stream_under_a_live_pump(tmp_path):
    # Regression: teardown used to close the streams after a fixed short join;
    # with the pipe still flowing, a mid-drain pump raised "I/O operation on
    # closed file" in its daemon thread. The pump must terminate before its
    # stream is closed, and the result must still carry the full output.
    child = _child_with_continuous_writer(tmp_path)
    with _captured_thread_exceptions() as errors:
        result = ProcessRunner().run([sys.executable, child], capture=True, timeout=30)
        time.sleep(0.2)  # give a buggy pump thread time to surface its exception
        assert errors == [], f"unhandled thread exceptions: {errors!r}"
    assert result.exit_code == 0


def test_capture_bounded_shutdown_when_pipe_never_reaches_eof(tmp_path):
    # A descendant holding the pipe open must not hang run(); the pump is
    # signalled to stop deterministically instead of waiting on a pipe that will
    # not reach EOF. run() returns promptly and still captures the child's line.
    child = _child_holding_pipe_open(tmp_path, hold_seconds=5)
    started = time.monotonic()
    result = ProcessRunner().run([sys.executable, child], capture=True, timeout=30)
    assert time.monotonic() - started < 2.0
    assert result.stdout == b"hello\n"


def test_capture_timeout_tears_down_pumps_cleanly():
    # Timeout path: the child is killed, then the pumps must still be joined and
    # their streams closed without any thread surfacing an exception.
    with _captured_thread_exceptions() as errors:
        runner = ProcessRunner(poll_interval=0.01)
        with pytest.raises(OperationTimeoutError):
            runner.run(
                [sys.executable, "-c", "import time; time.sleep(30)"],
                timeout=0.3,
                capture=True,
            )
        time.sleep(0.1)
        assert errors == []


def test_capture_signal_death_tears_down_pumps_cleanly():
    # Child-signal path: a child killed by SIGTERM still gets deterministic pump
    # teardown (exit code 143, no thread exceptions).
    with _captured_thread_exceptions() as errors:
        code = "import os, signal; os.kill(os.getpid(), signal.SIGTERM)"
        result = ProcessRunner().run([sys.executable, "-c", code], capture=True)
        assert result.exit_code == 128 + signal.SIGTERM
        time.sleep(0.1)
        assert errors == []


def test_capture_ctrl_c_path_tears_down_pumps_cleanly(monkeypatch):
    # Ctrl-C path: make the wait loop raise KeyboardInterrupt once (as the
    # terminal would deliver SIGINT). _wait forwards SIGINT, escalates to a kill
    # because the child ignores it, and returns 130 — with clean pump teardown.
    real_sleep = time.sleep
    fired = False

    def interrupt_once(seconds: float) -> None:
        nonlocal fired
        if not fired:
            fired = True
            raise KeyboardInterrupt
        real_sleep(seconds)

    monkeypatch.setattr("time.sleep", interrupt_once)
    with _captured_thread_exceptions() as errors:
        code = (
            "import signal; signal.signal(signal.SIGINT, signal.SIG_IGN); "
            "import time; time.sleep(30)"
        )
        result = ProcessRunner(signal_grace_seconds=0.3).run(
            [sys.executable, "-c", code], capture=True, timeout=30
        )
        assert result.exit_code == 130
        time.sleep(0.1)
        assert errors == []


def test_passthrough_timeout_raises_and_tears_down():
    # Non-capture (passthrough) path: the child is killed on timeout and no
    # capture threads ever spin up, so there is nothing left to join or leak.
    runner = ProcessRunner(poll_interval=0.01)
    started = time.monotonic()
    with pytest.raises(OperationTimeoutError):
        runner.run(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            timeout=0.3,
            capture=False,
        )
    assert time.monotonic() - started < 10

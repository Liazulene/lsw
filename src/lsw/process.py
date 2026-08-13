"""Shell-free subprocess execution (spec §13).

One rule: **never build a shell command string**. Everything goes through an
``argv`` array and :func:`subprocess.Popen` directly, so spaces, backslashes,
quotes and Unicode in arguments survive intact.

Three modes:

* *capture* — internal probes (``wine --version``, ``wineboot --init``,
  ``wineserver -k/-w``; never ``wineserver -p`` — that only sets the
  persistence delay, it is not a status query): bounded stdout/stderr, a finite
  timeout, and a dedicated process group so a timed-out probe can be torn down
  without touching anything else.
* *interactive* — programs the user runs bare (``lsw`` → ``cmd.exe``): stdio
  is inherited so the program talks to the terminal directly, the child stays
  in the terminal's foreground process group (so Ctrl-C reaches it), and there
  is no timeout.
* *non-interactive passthrough* — ``lsw -d NAME -- PROG ARGS``: stdio is also
  inherited, but the child runs in its own session so signals are forwarded
  deliberately instead of being silently delivered to the wrong group.

Exit codes are passed through untouched; a child killed by a signal maps to
the ``128 + signum`` convention (e.g. SIGTERM → 143). Ctrl-C during a wait
returns 130.

Capture teardown is deterministic: the child is reaped on every exit path
(normal, timeout, Ctrl-C), then the pump threads are joined to completion and
only then are their streams closed — so a read never races a close and captured
output is complete. A pump thread blocked on a pipe a descendant kept open is
stopped via a signal rather than by closing the stream underneath it.
"""

from __future__ import annotations

import os
import select
import signal
import subprocess
import threading
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol

from .errors import OperationTimeoutError
from .models import CommandResult


class CommandRunner(Protocol):
    """Structural interface a fake process runner can satisfy in tests."""

    def run(
        self,
        argv: Sequence[str],
        *,
        env: Mapping[str, str] | None = None,
        cwd: str | None = None,
        timeout: float | None = None,
        capture: bool = False,
        interactive: bool = False,
    ) -> CommandResult: ...


#: Pump threads poll their pipe this often (ms) so a stop signal is observed
#: promptly even while a descendant of the child keeps the pipe open past the
#: child's death (e.g. Wine's wineserver). Short enough to be deterministic,
#: long enough not to spin.
_PUMP_POLL_MS = 50


def _pump(stream: object, limit: int, sink: list[bytes], stop: threading.Event) -> None:
    """Read *stream* into ``sink[0]`` up to *limit* bytes, then keep draining.

    The drain past the limit prevents a deadlock (the child would block writing
    to a full pipe) while bounding how much output we keep in memory.

    The fd is made non-blocking and polled with a short timeout so this thread
    can observe *stop* even when a descendant of the child keeps the pipe open
    past the child's death and EOF never arrives. Once the child has been
    reaped, every byte it wrote is already sitting in the pipe, so the final
    drain-before-stop loses nothing. ``sink[0]`` is always written before the
    thread returns — on EOF, on a stop signal, or if the parent closes the
    stream (treated as an orderly shutdown, never an error to surface).
    """
    fd = stream.fileno()  # type: ignore[attr-defined]
    os.set_blocking(fd, False)
    poll = select.poll()
    poll.register(fd, select.POLLIN)
    kept = bytearray()
    while True:
        # Drain everything available right now, then decide. Checking stop only
        # after a full drain guarantees a stop signalled while this thread is
        # paused never discards the child's buffered tail.
        while True:
            try:
                chunk = os.read(fd, 65536)
            except BlockingIOError:
                break
            except OSError:
                sink[0] = bytes(kept)  # parent closed the stream: shutdown
                return
            if not chunk:
                sink[0] = bytes(kept)  # EOF
                return
            if len(kept) < limit:
                kept.extend(chunk[: limit - len(kept)])
        if stop.is_set():
            break
        poll.poll(_PUMP_POLL_MS)
    sink[0] = bytes(kept)


def _normalize_exit_code(code: int) -> int:
    """Map a signal-killed exit code to the shell convention (128 + signum)."""
    return code if code >= 0 else 128 + (-code)


@dataclass
class ProcessRunner:
    max_capture_bytes: int = 1 << 20  # 1 MiB per stream
    poll_interval: float = 0.05
    signal_grace_seconds: float = 2.0

    def run(
        self,
        argv: Sequence[str],
        *,
        env: Mapping[str, str] | None = None,
        cwd: str | None = None,
        timeout: float | None = None,
        capture: bool = False,
        interactive: bool = False,
    ) -> CommandResult:
        if not argv:
            raise ValueError("argv must not be empty")
        if timeout is not None and timeout <= 0:
            raise ValueError("timeout must be positive")
        if capture and interactive:
            raise ValueError("capture and interactive are mutually exclusive")

        # Interactive programs stay in the terminal's foreground process group
        # so terminal-delivered signals reach them; everything else gets its
        # own session so we can tear the whole group down on timeout/signal.
        start_new_session = capture or not interactive
        proc = subprocess.Popen(
            list(argv),
            stdin=subprocess.DEVNULL if capture else None,
            stdout=subprocess.PIPE if capture else None,
            stderr=subprocess.PIPE if capture else None,
            env=dict(env) if env is not None else None,
            cwd=cwd,
            start_new_session=start_new_session,
        )

        started = time.monotonic()
        out_sink: list[bytes] = [b""]
        err_sink: list[bytes] = [b""]
        threads: list[threading.Thread] = []
        stops: list[threading.Event] = []
        if capture:
            for stream, sink in ((proc.stdout, out_sink), (proc.stderr, err_sink)):
                stop = threading.Event()
                stops.append(stop)
                threads.append(
                    threading.Thread(
                        target=_pump,
                        args=(stream, self.max_capture_bytes, sink, stop),
                        daemon=True,
                    )
                )
            for thread in threads:
                thread.start()
        code: int | None = None
        try:
            code = self._wait(proc, argv, timeout, started)
        finally:
            if capture:
                self._teardown_capture(proc, threads, stops)
        assert code is not None  # _wait returns a code or raises
        return self._result(argv, code, capture, out_sink, err_sink, started, timed_out=False)

    # ---------------------------------------------------------------- waiting

    def _wait(
        self,
        proc: subprocess.Popen[bytes],
        argv: Sequence[str],
        timeout: float | None,
        started: float,
    ) -> int:
        """Wait for *proc* and return its exit code.

        The child is reaped on every exit path: normal exit, timeout
        (kill-then-wait, raising :class:`OperationTimeoutError`), and Ctrl-C
        (forward SIGINT, then kill-then-wait, returning 130). Reaping closes
        the child's pipe write ends, which is what lets the pump threads in
        :meth:`run` reach EOF and finish.
        """
        deadline = None if timeout is None else started + timeout
        try:
            while True:
                code = proc.poll()
                if code is not None:
                    return code
                if deadline is not None and time.monotonic() >= deadline:
                    self._kill(proc)
                    proc.wait()
                    raise OperationTimeoutError(f"命令超时（>{timeout:.0f}s）：{' '.join(argv)}")
                time.sleep(self.poll_interval)
        except KeyboardInterrupt:
            self._forward_sigint(proc)
            try:
                code = proc.wait(timeout=self.signal_grace_seconds)
            except KeyboardInterrupt:
                self._kill(proc)
                code = 130
            except subprocess.TimeoutExpired:
                self._kill(proc)
                code = 130
            return code

    def _result(
        self,
        argv: Sequence[str],
        code: int,
        capture: bool,
        out_sink: list[bytes],
        err_sink: list[bytes],
        started: float,
        *,
        timed_out: bool,
    ) -> CommandResult:
        return CommandResult(
            argv=tuple(argv),
            exit_code=_normalize_exit_code(code),
            stdout=b"".join(out_sink) if capture else b"",
            stderr=b"".join(err_sink) if capture else b"",
            duration=time.monotonic() - started,
            timed_out=timed_out,
        )

    # ------------------------------------------------------------ teardown

    def _teardown_capture(
        self,
        proc: subprocess.Popen[bytes],
        threads: list[threading.Thread],
        stops: list[threading.Event],
    ) -> None:
        """Terminate the pump threads, then close their streams — in that order.

        The child is reaped on every exit path from ``_wait``, so the pipe write
        ends are closed by the child's death and each pump would drain to EOF on
        its own. We still signal *stop* first: a descendant that keeps a pipe
        open past the child's death (Wine's wineserver) must never make us wait
        on a pipe that will not reach EOF. Because ``_pump`` reads non-blocking,
        every thread exits within one poll tick after its stop event is set.

        Streams are closed only after every pump thread has terminated, so a
        read can never race a close — this is what eliminated the "I/O operation
        on closed file" ValueError that surfaced as an unhandled thread
        exception in the real-Wine integration tests.
        """
        for stop in stops:
            stop.set()
        if proc.poll() is None:
            # Unexpected exit path (an exception outside _wait's own handling):
            # don't leak the child process.
            self._kill(proc)
        for thread in threads:
            thread.join()
        for stream in (proc.stdout, proc.stderr):
            if stream is not None:
                stream.close()

    def _group_is_ours(self, proc: subprocess.Popen[bytes]) -> bool | None:
        """True when the child shares our process group (interactive mode).

        Killing that group would kill us too, so such children are handled by
        signaling only the direct child. ``None`` means the child is already gone.
        """
        try:
            return os.getpgid(proc.pid) == os.getpgrp()
        except ProcessLookupError:
            return None

    def _forward_sigint(self, proc: subprocess.Popen[bytes]) -> None:
        if proc.poll() is not None:
            return
        ours = self._group_is_ours(proc)
        if ours is None:
            return
        try:
            if ours:
                proc.send_signal(signal.SIGINT)
            else:
                os.killpg(proc.pid, signal.SIGINT)
        except (ProcessLookupError, PermissionError):
            pass

    def _kill(self, proc: subprocess.Popen[bytes]) -> None:
        """SIGTERM (then SIGKILL after a grace period) the child's group."""
        ours = self._group_is_ours(proc)
        if ours is None:
            return
        try:
            if ours:
                proc.terminate()
            else:
                os.killpg(proc.pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            pass
        try:
            proc.wait(timeout=self.signal_grace_seconds)
        except subprocess.TimeoutExpired:
            try:
                if ours:
                    proc.kill()
                else:
                    os.killpg(proc.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                proc.kill()
            proc.wait()

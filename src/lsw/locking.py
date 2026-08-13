"""Process-local advisory file locks.

Uses ``flock(2)`` (Linux), so locks are tied to an open file description and
are released automatically when the process exits — even on crash. The lock
file itself is created and left in place; its *content* is meaningless.
"""

from __future__ import annotations

import contextlib
import errno
import fcntl
import os
from collections.abc import Iterator
from pathlib import Path


class LockUnavailable(Exception):
    """Raised when a non-blocking lock could not be acquired."""


@contextlib.contextmanager
def exclusive_lock(path: Path, *, blocking: bool = True) -> Iterator[None]:
    """Hold an exclusive ``flock`` on *path* for the duration of the block.

    With ``blocking=False``, raises :class:`LockUnavailable` if the lock is
    already held elsewhere. The lock file is created (with parents) on demand.
    """
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        flags = fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB)
        try:
            fcntl.flock(fd, flags)
        except OSError as exc:
            if exc.errno in (errno.EACCES, errno.EAGAIN):
                raise LockUnavailable(f"无法获取锁：{path}") from exc
            raise
        try:
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)

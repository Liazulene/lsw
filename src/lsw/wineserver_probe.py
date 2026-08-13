"""Side-effect-free, per-prefix detection of a running wineserver.

``wineserver`` has no status/query operation: ``-p`` / ``--persistent`` only
sets the persistence delay (with no argument the server stays running
indefinitely), so a prefix's running state cannot be probed by invoking
``wineserver`` at all — doing so would mutate server state.

Instead we read ``/proc`` — pure filesystem access, no subprocess, no signals —
and look for a ``wineserver`` process whose ``WINEPREFIX`` environment variable
resolves to the target prefix. Filtering on the exact WINEPREFIX keeps this
per-prefix isolated: unlike name-wide scans (``pgrep wine`` / ``pkill`` /
``killall``), it can never mix or touch another instance's processes.
"""

from __future__ import annotations

import os
from pathlib import Path

# The wineserver may be spawned through Wine's preloader (it execs into the
# real binary), so accept the preloader name too.
_WINESERVER_COMMANDS = ("wineserver", "wineserver64", "wineserver-preloader")


def _parse_environ(data: bytes) -> dict[str, str]:
    env: dict[str, str] = {}
    for chunk in data.split(b"\0"):
        if not chunk:
            continue
        key, sep, value = chunk.partition(b"=")
        if sep:
            env[key.decode("utf-8", "replace")] = value.decode("utf-8", "replace")
    return env


def _read_cmdline(pid_dir: Path) -> list[str] | None:
    try:
        raw = (pid_dir / "cmdline").read_bytes()
    except OSError:
        return None
    return [arg.decode("utf-8", "replace") for arg in raw.split(b"\0") if arg]


def _read_environ(pid_dir: Path) -> dict[str, str] | None:
    try:
        raw = (pid_dir / "environ").read_bytes()
    except OSError:
        return None
    return _parse_environ(raw)


def wineserver_running(prefix: Path, *, proc_root: Path | None = None) -> bool | None:
    """Return whether a wineserver for *prefix* is currently alive.

    Returns ``True`` when a ``wineserver`` process whose ``WINEPREFIX`` env
    resolves to *prefix* exists, ``False`` when none does, and ``None`` when the
    check is inconclusive (e.g. /proc is unreadable). Callers must map ``None``
    to UNKNOWN, never to STOPPED.

    Side-effect-free: only reads files under *proc_root* (default ``/proc``).
    Per-prefix: a process counts only when its exact WINEPREFIX matches.
    """
    root = proc_root if proc_root is not None else Path("/proc")
    wanted = prefix.resolve(strict=False)
    try:
        entries = list(root.iterdir())
    except OSError:
        return None
    inconclusive = False
    for entry in entries:
        if not entry.name.isdigit():
            continue
        argv = _read_cmdline(entry)
        if not argv:
            continue
        if os.path.basename(argv[0]) not in _WINESERVER_COMMANDS:
            continue
        env = _read_environ(entry)
        if env is None:
            # A wineserver we cannot inspect: we cannot rule out that it
            # belongs to our prefix, so the check is inconclusive — never a
            # confident "stopped" (spec §9.4: probe failure != Stopped).
            inconclusive = True
            continue
        value = env.get("WINEPREFIX")
        if value is None:
            inconclusive = True
            continue
        try:
            if Path(value).resolve(strict=False) == wanted:
                return True
        except OSError:
            inconclusive = True
            continue
    return None if inconclusive else False

"""Unit tests for the side-effect-free wineserver presence probe.

The probe must never invoke any subprocess and must be scoped to the exact
WINEPREFIX of the instance, so a wineserver for another prefix is never
confused with this one (per-prefix isolation).
"""

from __future__ import annotations

from pathlib import Path

from lsw.wineserver_probe import wineserver_running


def _proc(root: Path) -> Path:
    root.mkdir(parents=True)
    return root


def _add_wineserver(proc: Path, pid: int, winprefix: str) -> None:
    pdir = proc / str(pid)
    pdir.mkdir(parents=True)
    (pdir / "cmdline").write_bytes(b"wineserver\0")
    (pdir / "environ").write_bytes(b"WINEPREFIX=" + winprefix.encode() + b"\0")


def _add_wine_client(proc: Path, pid: int, winprefix: str) -> None:
    """A wine client process, not a wineserver — must not count as running."""
    pdir = proc / str(pid)
    pdir.mkdir(parents=True)
    (pdir / "cmdline").write_bytes(b"wine\0")
    (pdir / "environ").write_bytes(b"WINEPREFIX=" + winprefix.encode() + b"\0")


def test_true_when_wineserver_for_prefix_present(tmp_path):
    proc = _proc(tmp_path / "proc")
    _add_wineserver(proc, 1001, "/data/lsw/instances/X/prefix")
    assert wineserver_running(Path("/data/lsw/instances/X/prefix"), proc_root=proc) is True


def test_false_when_no_wineserver(tmp_path):
    proc = _proc(tmp_path / "proc")
    _add_wineserver(proc, 1001, "/data/lsw/instances/OTHER/prefix")
    assert wineserver_running(Path("/data/lsw/instances/X/prefix"), proc_root=proc) is False


def test_false_when_only_wine_client_process(tmp_path):
    proc = _proc(tmp_path / "proc")
    _add_wine_client(proc, 1001, "/data/lsw/instances/X/prefix")
    assert wineserver_running(Path("/data/lsw/instances/X/prefix"), proc_root=proc) is False


def test_none_when_proc_root_missing(tmp_path):
    assert wineserver_running(Path("/pf"), proc_root=tmp_path / "no-proc") is None


def test_empty_proc_root_means_stopped(tmp_path):
    proc = _proc(tmp_path / "proc")
    assert wineserver_running(Path("/pf"), proc_root=proc) is False


def test_unreadable_wineserver_is_inconclusive_not_stopped(tmp_path):
    # spec §9.4: probe failure must not auto-equal Stopped.
    proc = _proc(tmp_path / "proc")
    pdir = proc / "1001"
    pdir.mkdir(parents=True)
    (pdir / "cmdline").write_bytes(b"wineserver\0")
    # no environ file → unreadable → inconclusive, never a confident "stopped"
    assert wineserver_running(Path("/pf"), proc_root=proc) is None

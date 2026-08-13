"""Contract tests for the Wine backend (M4) via a fake process runner.

These pin down the exact commands LSW issues against Wine (spec §16.2) without
needing Wine installed: WINEPREFIX correctness, init command order, argv
arrays (never shell strings), terminate scoped to the instance prefix, the
dosdevices policy, and conversion of Wine failures/timeouts into domain errors.
"""

from __future__ import annotations

import os
import shutil
import uuid
from collections.abc import Callable
from dataclasses import replace
from datetime import datetime, timezone

import pytest

from lsw.backends import WineBackend, create_backend
from lsw.errors import BackendError, DependencyMissingError, OperationTimeoutError
from lsw.models import (
    BackendKind,
    CommandResult,
    FilesystemPolicy,
    InstallState,
    Instance,
    InstanceState,
    RunOptions,
)


def _fake_resolver(command: str) -> str:
    return "/fake/" + command


def _inst(name: str) -> Instance:
    return Instance(
        id=uuid.uuid4(),
        name=name,
        version=1,
        backend=BackendKind.WINE,
        install_state=InstallState.INSTALLED,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )


def _prefix(isolated_roots, name: str = "Windows-11") -> str:
    return str(isolated_roots.data_home / "instances" / name / "prefix")


class FakeProc:
    """A minimal fake ``/proc`` tree for the wineserver presence probe."""

    def __init__(self, root) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def add_wineserver(self, pid: int, winprefix: str) -> None:
        pdir = self.root / str(pid)
        pdir.mkdir(parents=True, exist_ok=True)
        (pdir / "cmdline").write_bytes(b"wineserver\0")
        (pdir / "environ").write_bytes(b"WINEPREFIX=" + str(winprefix).encode() + b"\0")

    def remove(self, pid: int) -> None:
        shutil.rmtree(self.root / str(pid), ignore_errors=True)


class FakeRunner:
    """Records every invocation; behaves like the Wine tools' subprocesses.

    Any ``wineserver -p`` invocation raises AssertionError: wineserver has no
    status/query operation, and status() must never invoke it.
    """

    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.version_exit = 0
        self.wineboot_exit = 0
        self.wineserver_wait_exit = 0
        self.run_exit = 0
        self.raise_on: list[str] = []  # argv[0] basenames that raise a timeout
        self.on_wineserver_k: Callable[[], None] | None = None

    def run(
        self,
        argv,
        *,
        env=None,
        cwd=None,
        timeout=None,
        capture=False,
        interactive=False,
    ):
        env = dict(env or {})
        self.calls.append(
            {
                "argv": list(argv),
                "env": env,
                "cwd": cwd,
                "timeout": timeout,
                "capture": capture,
                "interactive": interactive,
            }
        )
        name = os.path.basename(argv[0])
        if name in self.raise_on:
            raise OperationTimeoutError(f"模拟超时：{' '.join(argv)}")
        if name == "wine" and "--version" in argv:
            code = self.version_exit
            return CommandResult(
                tuple(argv), code, stdout=b"wine-9.0\n" if code == 0 else b"", stderr=b""
            )
        if name == "wineboot":
            code = self.wineboot_exit
            return CommandResult(
                tuple(argv), code, stdout=b"", stderr=b"wineboot boom" if code else b""
            )
        if name == "wineserver":
            if "-p" in argv:
                raise AssertionError("status 不得调用 wineserver -p（无状态查询操作）")
            if "-k" in argv:
                if self.on_wineserver_k is not None:
                    self.on_wineserver_k()
                return CommandResult(tuple(argv), 0)
            if "-w" in argv:
                return CommandResult(tuple(argv), self.wineserver_wait_exit)
        return CommandResult(tuple(argv), self.run_exit)


def _backend(isolated_roots, runner=None, **kwargs) -> WineBackend:
    return WineBackend(
        roots=isolated_roots,
        executable_resolver=_fake_resolver,
        runner=runner or FakeRunner(),
        **kwargs,
    )


# --------------------------------------------------------------------- probe


def test_probe_reports_wine_kind(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda command: None)
    info = WineBackend().probe()
    assert info.backend is BackendKind.WINE


def test_probe_reports_unavailable_when_binaries_missing(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda command: None)
    info = WineBackend().probe()
    assert info.available is False
    assert any("wineboot" in diagnostic for diagnostic in info.diagnostics)


def test_probe_returns_version_from_wine_version():
    runner = FakeRunner()
    backend = _backend(None, runner=runner)
    info = backend.probe()
    assert info.available is True
    assert info.version == "wine-9.0"
    assert info.executable == "/fake/wine"
    assert runner.calls[0]["argv"] == ["/fake/wine", "--version"]
    assert runner.calls[0]["capture"] is True
    # the version probe must never touch a caller-set prefix
    assert "WINEPREFIX" not in runner.calls[0]["env"]


def test_probe_unavailable_when_version_command_fails():
    runner = FakeRunner()
    runner.version_exit = 1
    backend = _backend(None, runner=runner)
    info = backend.probe()
    assert info.available is False
    assert "wine --version" in info.diagnostics[0]


def test_probe_reports_non_linux_as_unavailable(monkeypatch):
    monkeypatch.setattr("sys.platform", "darwin")
    info = WineBackend().probe()
    assert info.available is False
    assert "Linux" in info.diagnostics[0]


# ---------------------------------------------------------------- initialize


def test_initialize_runs_wineboot_then_wineserver_wait(isolated_roots):
    runner = FakeRunner()
    backend = _backend(isolated_roots, runner=runner)
    backend.initialize(_inst("Windows-11"))
    assert [c["argv"] for c in runner.calls] == [
        ["/fake/wine", "--version"],
        ["/fake/wineboot", "--init"],
        ["/fake/wineserver", "-w"],
    ]
    boot = runner.calls[1]
    assert boot["capture"] is True
    assert boot["timeout"] == 60.0
    assert boot["env"]["WINEPREFIX"] == _prefix(isolated_roots)
    assert boot["env"]["WINEARCH"] == "win64"


def test_initialize_requires_data_roots():
    runner = FakeRunner()
    backend = _backend(None, runner=runner)
    with pytest.raises(BackendError):
        backend.initialize(_inst("Windows-11"))


def test_initialize_failure_raises_backend_error(isolated_roots):
    runner = FakeRunner()
    runner.wineboot_exit = 1
    backend = _backend(isolated_roots, runner=runner)
    with pytest.raises(BackendError) as exc:
        backend.initialize(_inst("Windows-11"))
    assert "wineboot" in str(exc.value)


def test_initialize_timeout_raises_operation_timeout(isolated_roots):
    runner = FakeRunner()
    runner.raise_on = ["wineboot"]
    backend = _backend(isolated_roots, runner=runner)
    with pytest.raises(OperationTimeoutError):
        backend.initialize(_inst("Windows-11"))


# ------------------------------------------------------------- dosdevices


def test_initialize_applies_dosdevices_policy(isolated_roots):
    prefix = isolated_roots.data_home / "instances" / "Windows-11" / "prefix"
    dosdevices = prefix / "dosdevices"
    dosdevices.mkdir(parents=True)
    (dosdevices / "c:").symlink_to("../drive_c")
    (dosdevices / "z:").symlink_to("/")
    runner = FakeRunner()
    backend = _backend(isolated_roots, runner=runner)
    backend.initialize(_inst("Windows-11"))
    assert (dosdevices / "c:").is_symlink()
    assert not (dosdevices / "z:").exists()  # map_root defaults to False


def test_dosdevices_recreates_escaping_c_drive(isolated_roots):
    prefix = isolated_roots.data_home / "instances" / "Windows-11" / "prefix"
    dosdevices = prefix / "dosdevices"
    dosdevices.mkdir(parents=True)
    (dosdevices / "c:").symlink_to("/etc")
    runner = FakeRunner()
    backend = _backend(isolated_roots, runner=runner)
    backend.initialize(_inst("Windows-11"))
    assert (dosdevices / "c:").resolve() == (prefix / "drive_c")
    assert (prefix / "drive_c").is_dir()


def test_dosdevices_keeps_z_when_map_root_enabled(isolated_roots):
    prefix = isolated_roots.data_home / "instances" / "Windows-11" / "prefix"
    dosdevices = prefix / "dosdevices"
    dosdevices.mkdir(parents=True)
    (dosdevices / "z:").symlink_to("/")
    runner = FakeRunner()
    backend = _backend(isolated_roots, runner=runner)
    inst = replace(_inst("Windows-11"), filesystem_policy=FilesystemPolicy(map_root=True))
    backend.initialize(inst)
    assert (dosdevices / "z:").is_symlink()


# ------------------------------------------------------------------- status


def test_status_running_when_wineserver_for_prefix_alive(isolated_roots, tmp_path):
    proc = FakeProc(tmp_path / "proc")
    proc.add_wineserver(1001, _prefix(isolated_roots))
    runner = FakeRunner()
    backend = _backend(isolated_roots, runner=runner, proc_root=proc.root)
    assert backend.status(_inst("Windows-11")) is InstanceState.RUNNING
    # status must never invoke any wineserver subprocess
    assert not any(os.path.basename(c["argv"][0]) == "wineserver" for c in runner.calls)


def test_status_stopped_when_no_wineserver_for_prefix(isolated_roots, tmp_path):
    proc = FakeProc(tmp_path / "proc")
    runner = FakeRunner()
    backend = _backend(isolated_roots, runner=runner, proc_root=proc.root)
    assert backend.status(_inst("Windows-11")) is InstanceState.STOPPED
    assert not any(os.path.basename(c["argv"][0]) == "wineserver" for c in runner.calls)


def test_status_ignores_wineserver_of_other_prefix(isolated_roots, tmp_path):
    proc = FakeProc(tmp_path / "proc")
    proc.add_wineserver(1001, "/elsewhere/.wine")
    backend = _backend(isolated_roots, runner=FakeRunner(), proc_root=proc.root)
    assert backend.status(_inst("Windows-11")) is InstanceState.STOPPED


def test_status_unknown_when_proc_unreadable(isolated_roots, tmp_path):
    backend = _backend(isolated_roots, runner=FakeRunner(), proc_root=tmp_path / "no-proc")
    assert backend.status(_inst("Windows-11")) is InstanceState.UNKNOWN


def test_status_never_invokes_wineserver_p(isolated_roots, tmp_path):
    proc = FakeProc(tmp_path / "proc")
    proc.add_wineserver(1001, _prefix(isolated_roots))
    runner = FakeRunner()
    backend = _backend(isolated_roots, runner=runner, proc_root=proc.root)
    backend.status(_inst("Windows-11"))
    assert not any("-p" in c["argv"] for c in runner.calls)


# ---------------------------------------------------------------------- run


def test_run_passes_argv_array_and_prefix(isolated_roots):
    runner = FakeRunner()
    runner.run_exit = 7
    backend = _backend(isolated_roots, runner=runner)
    code = backend.run(
        _inst("Windows-11"),
        ("cmd.exe", "/c", "exit 7"),
        RunOptions(argv=("cmd.exe", "/c", "exit 7")),
    )
    assert code == 7
    last = runner.calls[-1]
    assert last["argv"] == ["/fake/wine", "cmd.exe", "/c", "exit 7"]
    assert last["env"]["WINEPREFIX"] == _prefix(isolated_roots)
    assert last["capture"] is False
    assert last["interactive"] is False


def test_run_overrides_caller_preset_wineprefix(isolated_roots, monkeypatch):
    monkeypatch.setenv("WINEPREFIX", "/home/user/.wine")
    runner = FakeRunner()
    backend = _backend(isolated_roots, runner=runner)
    backend.run(_inst("Windows-11"), ("cmd.exe",), RunOptions(argv=("cmd.exe",)))
    assert runner.calls[-1]["env"]["WINEPREFIX"] == _prefix(isolated_roots)


def test_run_forwards_interactive_flag(isolated_roots):
    runner = FakeRunner()
    backend = _backend(isolated_roots, runner=runner)
    backend.run(
        _inst("Windows-11"),
        ("cmd.exe",),
        RunOptions(argv=("cmd.exe",), interactive=True),
    )
    assert runner.calls[-1]["interactive"] is True


# ---------------------------------------------------------------- terminate


def test_terminate_running_sends_scoped_wineserver_k(isolated_roots, tmp_path):
    proc = FakeProc(tmp_path / "proc")
    proc.add_wineserver(1001, _prefix(isolated_roots))
    runner = FakeRunner()
    runner.on_wineserver_k = lambda: proc.remove(1001)
    backend = _backend(isolated_roots, runner=runner, proc_root=proc.root)
    result = backend.terminate(_inst("Windows-11"), 15.0)
    assert result.previous_state is InstanceState.RUNNING
    kill = [c for c in runner.calls if c["argv"] == ["/fake/wineserver", "-k"]]
    assert len(kill) == 1
    assert kill[0]["env"]["WINEPREFIX"] == _prefix(isolated_roots)
    # terminate must poll with the /proc probe, never with `wineserver -p`
    assert not any("-p" in c["argv"] for c in runner.calls)


def test_terminate_stopped_is_idempotent(isolated_roots, tmp_path):
    proc = FakeProc(tmp_path / "proc")
    runner = FakeRunner()
    backend = _backend(isolated_roots, runner=runner, proc_root=proc.root)
    result = backend.terminate(_inst("Windows-11"), 15.0)
    assert result.previous_state is InstanceState.STOPPED
    assert not any(c["argv"] == ["/fake/wineserver", "-k"] for c in runner.calls)


def test_terminate_timeout_raises_operation_timeout(isolated_roots, tmp_path):
    proc = FakeProc(tmp_path / "proc")
    proc.add_wineserver(1001, _prefix(isolated_roots))  # never stops
    runner = FakeRunner()
    backend = _backend(isolated_roots, runner=runner, proc_root=proc.root)
    with pytest.raises(OperationTimeoutError):
        backend.terminate(_inst("Windows-11"), 0.5)


# --------------------------------------------------------------- shutdown


def test_shutdown_all_terminates_registered_instances(isolated_roots, tmp_path):
    proc = FakeProc(tmp_path / "proc")
    proc.add_wineserver(1001, _prefix(isolated_roots, "A"))
    proc.add_wineserver(1002, _prefix(isolated_roots, "B"))
    runner = FakeRunner()
    runner.on_wineserver_k = lambda: (proc.remove(1001), proc.remove(1002))
    backend = _backend(isolated_roots, runner=runner, proc_root=proc.root)
    result = backend.shutdown_all([_inst("A"), _inst("B")], 15.0)
    assert result.terminated == ("A", "B")
    assert result.failed == ()
    assert not any("-p" in c["argv"] for c in runner.calls)


def test_shutdown_all_aggregates_failures_not_aborts(isolated_roots, tmp_path):
    proc = FakeProc(tmp_path / "proc")
    proc.add_wineserver(1001, _prefix(isolated_roots, "A"))
    proc.add_wineserver(1002, _prefix(isolated_roots, "B"))
    runner = FakeRunner()
    runner.raise_on = ["wineserver"]  # every -k raises
    backend = _backend(isolated_roots, runner=runner, proc_root=proc.root)
    result = backend.shutdown_all([_inst("A"), _inst("B")], 15.0)
    assert result.terminated == ()
    assert [name for name, _ in result.failed] == ["A", "B"]


# ------------------------------------------------------------ dependencies


def test_operational_methods_raise_dependency_when_unavailable():
    backend = WineBackend(wine_binary="lsw-no-such-wine-binary")
    with pytest.raises(DependencyMissingError):
        backend.initialize(_inst("Windows-11"))
    with pytest.raises(DependencyMissingError):
        backend.status(_inst("Windows-11"))
    with pytest.raises(DependencyMissingError):
        backend.run(_inst("Windows-11"), ("cmd.exe",), RunOptions(argv=("cmd.exe",)))
    with pytest.raises(DependencyMissingError):
        backend.terminate(_inst("Windows-11"), 15.0)
    # shutdown_all aggregates the dependency error per instance
    result = backend.shutdown_all([_inst("Windows-11")], 15.0)
    assert result.terminated == ()
    assert [name for name, _ in result.failed] == ["Windows-11"]


# ------------------------------------------------------------------- wiring


def test_create_backend_wine_from_config():
    backend = create_backend("wine", {"wine_binary": "lsw-no-such-wine-binary"})
    assert isinstance(backend, WineBackend)

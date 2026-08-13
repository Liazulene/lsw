"""Application services (use cases).

This layer sits between the CLI and the repository/backend. Each method is one
use case from the milestone M3 list (install / list / set-default / run /
terminate / shutdown / unregister / set-version / status). The CLI stays
dependent only on these methods plus the value objects they return, so it can
be exercised end-to-end with a fake backend and no Wine.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import config
from .backends import create_backend
from .backends.base import RuntimeBackend
from .errors import (
    BackendError,
    ConfirmationRequiredError,
    DependencyMissingError,
    InstanceBusyError,
    OperationError,
    OperationTimeoutError,
)
from .logging import EventLog
from .models import (
    BackendInfo,
    InstallState,
    Instance,
    InstanceState,
    RunOptions,
    ShutdownResult,
    TerminateResult,
    redact_argument,
)
from .paths import DataRoots
from .repository import CorruptInstance, InstanceRepository

_MISSING_RUNTIME = (
    "未找到 Wine runtime。提示：请先通过系统包管理器安装 Wine，然后运行 `lsw --status` 检查。"
)


@dataclass(frozen=True)
class ListedInstance:
    """One healthy instance with its probed runtime state."""

    instance: Instance
    runtime_state: InstanceState
    is_default: bool


@dataclass(frozen=True)
class ListResult:
    instances: tuple[ListedInstance, ...] = ()
    corrupt: tuple[CorruptInstance, ...] = ()
    default: str | None = None


@dataclass(frozen=True)
class StatusInfo:
    default_instance: str | None
    instance_count: int
    corrupt_count: int
    backend: BackendInfo
    data_root: Path


@dataclass
class Services:
    repo: InstanceRepository
    backend: RuntimeBackend
    roots: DataRoots
    default_install_name: str = "Windows-11"
    default_program: str = "cmd.exe"
    default_arch: str = "win64"
    terminate_timeout: float = 15.0
    shutdown_timeout: float = 15.0
    log: EventLog | None = None

    def _log(self, instance_name: str | None, event: str, **fields: Any) -> None:
        if self.log is not None:
            self.log.log(instance_name, event, **fields)

    def install(self, name: str) -> Instance:
        """Install (initialize) an instance, or fail without half-installs."""
        info = self.backend.probe()
        if not info.available:
            raise DependencyMissingError(_MISSING_RUNTIME)
        instance = self.repo.create(name, arch=self.default_arch)
        self._log(instance.name, "install", state="pending")
        try:
            self.backend.initialize(instance)
        except OperationTimeoutError:
            self.repo.update_install_state(instance.name, InstallState.FAILED)
            self._log(instance.name, "install", level="ERROR", state="failed", reason="timeout")
            raise
        except Exception as exc:  # noqa: BLE001 - any initialize failure is a failed install
            self.repo.update_install_state(instance.name, InstallState.FAILED)
            root = self.repo.instance_root(instance.name)
            self._log(instance.name, "install", level="ERROR", state="failed", reason=str(exc))
            raise OperationError(
                f"初始化实例 {instance.name} 失败：{exc}（已保留失败状态目录 {root} 用于诊断）"
            ) from exc
        result = self.repo.update_install_state(instance.name, InstallState.INSTALLED)
        self._log(instance.name, "install", state="installed")
        return result

    def list_instances(self) -> ListResult:
        listing = self.repo.list()
        default = self.repo.get_default()
        rows = tuple(
            ListedInstance(
                instance=instance,
                runtime_state=self._runtime_state(instance),
                is_default=instance.name == default,
            )
            for instance in listing.instances
        )
        return ListResult(instances=rows, corrupt=listing.corrupt, default=default)

    def _runtime_state(self, instance: Instance) -> InstanceState:
        # Install state drives the display for non-installed instances; a
        # failed probe must never be conflated with Stopped.
        if instance.install_state is InstallState.PENDING:
            return InstanceState.INSTALLING
        if instance.install_state is InstallState.FAILED:
            return InstanceState.BROKEN
        try:
            return self.backend.status(instance)
        except (BackendError, DependencyMissingError):
            return InstanceState.UNKNOWN

    def set_default(self, name: str) -> None:
        self.repo.set_default(name)  # only fully installed instances qualify

    def run(self, instance_name: str, argv: Sequence[str], options: RunOptions) -> int:
        instance = self.repo.get(instance_name)
        redacted_argv = tuple(redact_argument(arg) for arg in argv)
        code = self.backend.run(instance, argv, options)
        self._log(instance.name, "run", argv=redacted_argv, exit_code=code)
        return code

    def terminate(self, name: str, timeout: float) -> TerminateResult:
        instance = self.repo.get(name)
        result = self.backend.terminate(instance, timeout)
        self._log(instance.name, "terminate", previous_state=result.previous_state.value)
        return result

    def shutdown(self, timeout: float) -> ShutdownResult:
        listing = self.repo.list()
        result = self.backend.shutdown_all(listing.instances, timeout)
        self._log(
            None,
            "shutdown",
            terminated=",".join(result.terminated),
            failed=",".join(name for name, _ in result.failed),
        )
        return result

    def ensure_not_running(self, name: str) -> None:
        instance = self.repo.get(name)
        if self.backend.status(instance) is InstanceState.RUNNING:
            raise InstanceBusyError(f"实例 {name} 正在运行，请先运行 `lsw --terminate {name}`。")

    def unregister(self, name: str, *, confirmed: bool) -> None:
        """Delete an instance; refuses while running, requires confirmation."""
        instance = self.repo.get(name)
        if self.backend.status(instance) is InstanceState.RUNNING:
            raise InstanceBusyError(f"实例 {name} 正在运行，请先运行 `lsw --terminate {name}`。")
        if not confirmed:
            raise ConfirmationRequiredError(
                f"删除实例 {name} 需要确认：请使用 --yes，或在交互终端输入实例名。"
            )
        if self.repo.get_default() == name:
            self.repo.clear_default()
        self.repo.delete(name)
        self._log(name, "unregister", state="deleted")

    def set_version(self, name: str, version: int) -> None:
        instance = self.repo.get(name)
        if version != 1:
            raise OperationError(f"尚不支持版本 {version}；当前仅支持版本 1。")
        if instance.version != 1:
            raise OperationError(f"实例 {name} 版本异常（{instance.version}）。")

    def status(self) -> StatusInfo:
        listing = self.repo.list()
        return StatusInfo(
            default_instance=self.repo.get_default(),
            instance_count=len(listing.instances),
            corrupt_count=len(listing.corrupt),
            backend=self.backend.probe(),
            data_root=self.roots.data_home,
        )


def build_services(roots: DataRoots, *, backend: RuntimeBackend | None = None) -> Services:
    """Wire repository, backend and config defaults into :class:`Services`."""
    table = config.load_config(path=roots.config_home / "config.toml")
    backend_config = table["backend"]
    runtime_backend = backend or create_backend(
        str(backend_config["type"]), backend_config, roots=roots
    )
    return Services(
        repo=InstanceRepository(roots),
        backend=runtime_backend,
        roots=roots,
        default_install_name=str(table["default_install_name"]),
        default_program=str(table["execution"]["default_program"]),
        default_arch=str(backend_config["arch"]),
        terminate_timeout=float(backend_config["shutdown_timeout_seconds"]),
        shutdown_timeout=float(backend_config["shutdown_timeout_seconds"]),
        log=EventLog(roots.state_home / "logs"),
    )

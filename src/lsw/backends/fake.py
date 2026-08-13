"""Fake backend: a configurable, record-keeping stand-in used by tests.

It lets the full application and CLI lifecycle run without Wine: availability,
Wine version, initialize failures, runtime states, program exit codes,
terminate failures and partial shutdown failures are all configurable, and
every call is recorded in :attr:`FakeBackend.calls` for assertions.
"""

from __future__ import annotations

import platform
from collections.abc import Sequence
from dataclasses import dataclass, field

from ..errors import BackendError, DependencyMissingError
from ..models import (
    BackendInfo,
    BackendKind,
    Instance,
    InstanceState,
    RunOptions,
    ShutdownResult,
    TerminateResult,
)

_MISSING_RUNTIME = "未找到 Wine runtime（FakeBackend 模拟不可用）。"


@dataclass
class FakeBackend:
    available: bool = True
    wine_version: str | None = "fake-wine-9.0"
    initialize_error: str | None = None
    status_override: InstanceState | None = None
    run_exit_code: int = 0
    run_error: str | None = None
    terminate_error: str | None = None
    shutdown_failures: set[str] = field(default_factory=set)
    running: set[str] = field(default_factory=set)
    calls: list[str] = field(default_factory=list)
    #: Full ``(instance name, argv, options)`` records for each run call, so
    #: tests can assert on fields the string log does not capture (e.g.
    #: ``RunOptions.interactive``).
    run_calls: list[tuple[str, tuple[str, ...], RunOptions]] = field(default_factory=list)

    def _record(self, entry: str) -> None:
        self.calls.append(entry)

    def _require_available(self) -> None:
        if not self.available:
            raise DependencyMissingError(_MISSING_RUNTIME)

    def probe(self) -> BackendInfo:
        self._record("probe")
        return BackendInfo(
            backend=BackendKind.FAKE,
            available=self.available,
            executable="fake" if self.available else None,
            version=self.wine_version,
            architecture=platform.machine(),
            diagnostics=() if self.available else ("fake backend 模拟不可用",),
        )

    def initialize(self, instance: Instance) -> None:
        self._record(f"initialize:{instance.name}")
        self._require_available()
        if self.initialize_error:
            raise BackendError(self.initialize_error)

    def status(self, instance: Instance) -> InstanceState:
        self._record(f"status:{instance.name}")
        self._require_available()
        if self.status_override is not None:
            return self.status_override
        return InstanceState.RUNNING if instance.name in self.running else InstanceState.STOPPED

    def run(self, instance: Instance, argv: Sequence[str], options: RunOptions) -> int:
        self._record(f"run:{instance.name}:{' '.join(argv)}")
        self.run_calls.append((instance.name, tuple(argv), options))
        self._require_available()
        if self.run_error:
            raise BackendError(self.run_error)
        self.running.add(instance.name)
        return self.run_exit_code

    def terminate(self, instance: Instance, timeout: float) -> TerminateResult:
        self._record(f"terminate:{instance.name}")
        self._require_available()
        if self.terminate_error:
            raise BackendError(self.terminate_error)
        previous = InstanceState.RUNNING if instance.name in self.running else InstanceState.STOPPED
        self.running.discard(instance.name)
        return TerminateResult(instance_name=instance.name, previous_state=previous)

    def shutdown_all(self, instances: Sequence[Instance], timeout: float) -> ShutdownResult:
        names = ",".join(instance.name for instance in instances)
        self._record(f"shutdown_all:{names}")
        self._require_available()
        terminated: list[str] = []
        failed: list[tuple[str, str]] = []
        for instance in instances:
            if instance.name in self.shutdown_failures:
                failed.append((instance.name, "模拟关闭失败"))
            else:
                self.running.discard(instance.name)
                terminated.append(instance.name)
        return ShutdownResult(terminated=tuple(terminated), failed=tuple(failed))

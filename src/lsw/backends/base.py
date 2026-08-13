"""Runtime backend protocol.

A backend knows how to probe the host, initialize a prefix, query runtime
status, run Windows programs, terminate instances and shut everything down.
The protocol keeps the application services and CLI independent of any
particular runtime; the ``fake`` backend makes the full lifecycle testable
without Wine, while ``wine`` (probe-only in M3) is wired in M4.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from ..models import (
    BackendInfo,
    Instance,
    InstanceState,
    RunOptions,
    ShutdownResult,
    TerminateResult,
)


@runtime_checkable
class RuntimeBackend(Protocol):
    def probe(self) -> BackendInfo: ...

    def initialize(self, instance: Instance) -> None: ...

    def status(self, instance: Instance) -> InstanceState: ...

    def run(self, instance: Instance, argv: Sequence[str], options: RunOptions) -> int: ...

    def terminate(self, instance: Instance, timeout: float) -> TerminateResult: ...

    def shutdown_all(self, instances: Sequence[Instance], timeout: float) -> ShutdownResult: ...

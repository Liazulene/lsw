"""Backend registry and selection.

``create_backend`` resolves the configured backend kind (from the
``[backend] type`` config key) to a :class:`RuntimeBackend` implementation.
``fake`` exists so the full lifecycle runs without Wine; ``wine`` implements
the real runtime (M4). ``roots`` is threaded through so the Wine backend can
resolve each instance's absolute WINEPREFIX inside the LSW data root.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..errors import ConfigurationError
from ..paths import DataRoots
from .base import RuntimeBackend
from .fake import FakeBackend
from .wine import WineBackend

__all__ = ["FakeBackend", "RuntimeBackend", "WineBackend", "create_backend"]


def create_backend(
    kind: str, table: Mapping[str, Any], *, roots: DataRoots | None = None
) -> RuntimeBackend:
    """Instantiate a backend from a config ``[backend]`` table."""
    if kind == "fake":
        return FakeBackend()
    if kind == "wine":
        return WineBackend(
            wine_binary=str(table.get("wine_binary", "wine")),
            wineboot_binary=str(table.get("wineboot_binary", "wineboot")),
            wineserver_binary=str(table.get("wineserver_binary", "wineserver")),
            winepath_binary=str(table.get("winepath_binary", "winepath")),
            startup_timeout_seconds=float(table.get("startup_timeout_seconds", 60)),
            shutdown_timeout_seconds=float(table.get("shutdown_timeout_seconds", 15)),
            roots=roots,
        )
    raise ConfigurationError(f"未知 backend 类型 {kind!r}。")

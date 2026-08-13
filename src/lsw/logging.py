"""Minimal structured event logging with secret redaction (M5).

Events are appended as single redacted lines to ``<state>/logs/<instance>/``
(see spec §3.2 layout). Logging is strictly best-effort: an I/O failure or an
unsanitizable instance name drops the event — it must never fail the operation
that produced it. Sensitive environment values and ``key=value`` text are
masked before they reach the file (see :mod:`lsw.models` redaction helpers).
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .models import is_sensitive_key, redact_text

_REDACTED = "<redacted>"


class EventLog:
    """Appends redacted per-instance event lines under ``root``."""

    def __init__(self, root: Path) -> None:
        self._root = root

    def log(
        self,
        instance_name: str | None,
        event: str,
        *,
        level: str = "INFO",
        **fields: Any,
    ) -> None:
        """Append one event line; failures are dropped, never raised."""
        try:
            self._append(instance_name, event, level, fields)
        except (OSError, ValueError):
            pass  # logging must never break the user operation

    def _append(
        self,
        instance_name: str | None,
        event: str,
        level: str,
        fields: dict[str, Any],
    ) -> None:
        name = "global" if instance_name is None else instance_name
        if not _is_safe_segment(name):
            raise ValueError(f"不安全的日志实例名：{name!r}")
        directory = self._root / name
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        date = datetime.now(timezone.utc).date().isoformat()
        line = self._format(level, event, name, fields)
        with open(directory / f"{date}.log", "a", encoding="utf-8") as handle:
            handle.write(line + "\n")

    def _format(self, level: str, event: str, name: str, fields: Mapping[str, Any]) -> str:
        parts = [
            datetime.now(timezone.utc).isoformat(timespec="seconds"),
            level,
            event,
            f"instance={name}",
        ]
        for key, value in fields.items():
            parts.append(self._field(key, value))
        return " ".join(parts)

    @staticmethod
    def _field(key: str, value: Any) -> str:
        text = _render_value(value)
        if is_sensitive_key(key):
            text = _REDACTED
        return f"{key}={redact_text(text)}"


def _is_safe_segment(name: str) -> bool:
    return bool(name) and "/" not in name and "\\" not in name and name not in {".", ".."}


def _render_value(value: Any) -> str:
    """Render *value* for a log line, masking sensitive environment values."""
    if isinstance(value, Mapping):
        rendered: list[str] = []
        for key, val in value.items():
            val_text = str(val)
            if is_sensitive_key(str(key)):
                val_text = _REDACTED
            rendered.append(f"{key}={val_text}")
        return ";".join(rendered)
    if isinstance(value, (list, tuple)):
        return ",".join(str(item) for item in value)
    return str(value)

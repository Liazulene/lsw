"""Global configuration.

The config file at ``<config root>/config.toml`` is optional: when it is
absent, safe built-in defaults are used and nothing is written. Unknown keys
produce a warning instead of being silently dropped; an unsupported
``schema_version`` is rejected outright.

The default-instance reference (``default_instance``) is also managed here:
it lives in the same config file and is updated atomically while preserving
any other keys the user already has. Full merge/priority machinery
(CLI > env > instance > global > defaults) lands in a later milestone.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

import tomli_w

from .errors import ConfigurationError
from .paths import config_file as resolve_config_file
from .validation import validate_instance_name

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib

SUPPORTED_SCHEMA_VERSION = 1

DEFAULT_CONFIG: dict[str, Any] = {
    "schema_version": 1,
    "default_install_name": "Windows-11",
    "backend": {
        "type": "wine",
        "wine_binary": "wine",
        "wineboot_binary": "wineboot",
        "wineserver_binary": "wineserver",
        "winepath_binary": "winepath",
        "arch": "win64",
        "startup_timeout_seconds": 60,
        "shutdown_timeout_seconds": 15,
    },
    "execution": {
        "default_program": "cmd.exe",
        "inherit_environment": True,
        "working_directory": "inherit",
    },
    "filesystem": {"map_home": False, "map_root": False, "map_current_directory": True},
    "logging": {"level": "INFO", "retention_days": 14},
}

_KNOWN_KEYS: dict[str, frozenset[str]] = {
    "": frozenset(
        {
            "schema_version",
            "default_instance",
            "default_install_name",
            "backend",
            "execution",
            "filesystem",
            "logging",
        }
    ),
    "backend": frozenset(
        {
            "type",
            "wine_binary",
            "wineboot_binary",
            "wineserver_binary",
            "winepath_binary",
            "arch",
            "startup_timeout_seconds",
            "shutdown_timeout_seconds",
        }
    ),
    "execution": frozenset({"default_program", "inherit_environment", "working_directory"}),
    "filesystem": frozenset({"map_home", "map_root", "map_current_directory"}),
    "logging": frozenset({"level", "retention_days"}),
}


def _default_warn(message: str) -> None:
    print(f"lsw: 警告: {message}", file=sys.stderr)


def _deep_merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _unknown_keys(table: Mapping[str, Any], prefix: str = "") -> list[str]:
    known = _KNOWN_KEYS.get(prefix, frozenset())
    found: list[str] = []
    for key, value in table.items():
        if key in known:
            child = f"{prefix}.{key}" if prefix else key
            if isinstance(value, dict) and child in _KNOWN_KEYS:
                found.extend(_unknown_keys(value, child))
        else:
            found.append(f"{prefix}.{key}" if prefix else key)
    return found


def _load_raw_table(path: Path) -> dict[str, Any]:
    """Read and schema-validate the raw config table (``{}`` when absent)."""
    if not path.is_file():
        return {}
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        raise ConfigurationError(f"无法读取配置文件 {path}：{exc}") from exc
    try:
        with os.fdopen(fd, "rb") as handle:
            table = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ConfigurationError(f"无法读取配置文件 {path}：{exc}") from exc
    if not isinstance(table, dict):
        raise ConfigurationError(f"配置文件 {path} 顶层必须是 TOML 表。")
    version = table.get("schema_version")
    if version is not None and version != SUPPORTED_SCHEMA_VERSION:
        raise ConfigurationError(
            f"不支持的配置 schema_version {version!r}（当前仅支持 {SUPPORTED_SCHEMA_VERSION}）。"
        )
    return table


def _write_raw_table(path: Path, table: dict[str, Any]) -> None:
    """Atomically write *table* to *path* (tmp + fsync + rename)."""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "wb") as handle:
            tomli_w.dump(table, handle)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    finally:
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass


def load_config(
    path: Path | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    warn: Callable[[str], None] = _default_warn,
) -> dict[str, Any]:
    """Load the global config table, merging file values over built-in defaults.

    Returns the built-in defaults unchanged when *path* (default: the resolved
    ``config.toml``) does not exist. Raises :class:`ConfigurationError` for
    malformed TOML or an unsupported ``schema_version``.
    """
    target = path if path is not None else resolve_config_file(environ)
    file_table = _load_raw_table(target)
    for key in _unknown_keys(file_table):
        warn(f"配置文件 {target} 中存在未知键：{key}")
    return _deep_merge(dict(DEFAULT_CONFIG), file_table)


def _resolve_target(path: Path | None, environ: Mapping[str, str] | None) -> Path:
    return path if path is not None else resolve_config_file(environ)


def get_default_instance(
    *, path: Path | None = None, environ: Mapping[str, str] | None = None
) -> str | None:
    """Return the configured default instance name, or ``None``."""
    table = _load_raw_table(_resolve_target(path, environ))
    value = table.get("default_instance")
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise ConfigurationError("配置中的 default_instance 必须是非空字符串。")
    return value


def set_default_instance(
    name: str, *, path: Path | None = None, environ: Mapping[str, str] | None = None
) -> None:
    """Atomically set the default instance, preserving other config keys."""
    validated = validate_instance_name(name)
    target = _resolve_target(path, environ)
    table = _load_raw_table(target)
    if table.get("default_instance") == validated:
        return
    table["default_instance"] = validated
    _write_raw_table(target, table)


def clear_default_instance(
    *, path: Path | None = None, environ: Mapping[str, str] | None = None
) -> None:
    """Remove the ``default_instance`` key (a no-op when absent)."""
    target = _resolve_target(path, environ)
    table = _load_raw_table(target)
    if "default_instance" not in table:
        return
    del table["default_instance"]
    _write_raw_table(target, table)

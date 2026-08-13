"""Instance repository: directory layout, TOML metadata, atomic writes, locks.

Owns the ``<data>/lsw/instances/`` tree:

    instances/
    └── <name>/
        ├── instance.toml   # metadata (the only authoritative state)
        ├── prefix/         # the Wine prefix
        └── locks/

Every mutation takes a per-instance exclusive lock; metadata is written to a
temp file, fsynced, then atomically replaced. Instance names are validated
before touching the filesystem, and paths are resolved and re-checked to stay
inside ``instances/`` (no symlink escape).
"""

from __future__ import annotations

import os
import shutil
import sys
import uuid
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import tomli_w

from . import paths
from .config import (
    clear_default_instance as clear_default_instance,
)
from .config import (
    get_default_instance as get_default_instance,
)
from .config import (
    set_default_instance as set_default_instance,
)
from .errors import (
    ConfigurationError,
    InstanceAlreadyExistsError,
    InstanceBusyError,
    InstanceCorruptError,
    InstanceNotFoundError,
    InvalidInstanceNameError,
    OperationError,
    UnsafePathError,
)
from .locking import LockUnavailable, exclusive_lock
from .models import BackendKind, FilesystemPolicy, InstallState, Instance, RuntimeConfig
from .validation import validate_instance_name

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib

METADATA_SCHEMA_VERSION = 1
_PREFIX_DIRECTORY_DEFAULT = "prefix"


def _utcnow() -> datetime:
    # Truncate microseconds so values survive an RFC 3339 seconds-precision round trip.
    return datetime.now(timezone.utc).replace(microsecond=0)


def _format_dt(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _parse_dt(text: str) -> datetime:
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"非法时间戳 {text!r}") from exc
    if dt.tzinfo is None:
        raise ValueError(f"时间戳缺少时区：{text!r}")
    return dt.astimezone(timezone.utc)


def _instance_to_toml(inst: Instance) -> dict[str, Any]:
    runtime: dict[str, Any] = {"arch": inst.runtime_config.arch}
    if inst.runtime_config.initialized_with_wine_version is not None:
        runtime["initialized_with_wine_version"] = inst.runtime_config.initialized_with_wine_version
    table: dict[str, Any] = {
        "schema_version": METADATA_SCHEMA_VERSION,
        "id": str(inst.id),
        "name": inst.name,
        "version": inst.version,
        "backend": inst.backend.value,
        "state": inst.install_state.value,
        "created_at": _format_dt(inst.created_at),
        "updated_at": _format_dt(inst.updated_at),
        "prefix_directory": inst.prefix_directory,
        "runtime": runtime,
        "filesystem": {
            "map_home": inst.filesystem_policy.map_home,
            "map_root": inst.filesystem_policy.map_root,
            "map_current_directory": inst.filesystem_policy.map_current_directory,
        },
    }
    if inst.labels:
        table["labels"] = dict(inst.labels)
    return table


def _as_bool(value: Any, default: bool) -> bool:
    """Strict TOML boolean: only real ``true``/``false`` are accepted.

    Metadata is untrusted input; coercing e.g. the string ``"false"`` with
    ``bool(...)`` would yield ``True`` and silently enable root mapping.
    """
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    raise ValueError(f"filesystem 布尔值必须是 true/false，得到 {value!r}")


def _instance_from_toml(table: dict[str, Any], expected_name: str) -> Instance:
    try:
        if table.get("schema_version") != METADATA_SCHEMA_VERSION:
            raise ValueError(f"不支持的 schema_version {table.get('schema_version')!r}")
        raw_id = table["id"]
        raw_name = table["name"]
        version = int(table["version"])
        backend = BackendKind(str(table["backend"]))
        state = InstallState(str(table["state"]))
        created = _parse_dt(str(table["created_at"]))
        updated = _parse_dt(str(table["updated_at"]))
        prefix_dir = str(table.get("prefix_directory", _PREFIX_DIRECTORY_DEFAULT))
        instance_id = uuid.UUID(str(raw_id))
    except (KeyError, ValueError, TypeError) as exc:
        raise InstanceCorruptError(f"实例 {expected_name} 元数据字段缺失或非法：{exc}") from exc

    if raw_name != expected_name:
        raise InstanceCorruptError(
            f"实例 {expected_name} 元数据中的 name {raw_name!r} 与目录名不一致。"
        )
    try:
        validate_instance_name(raw_name)
    except InvalidInstanceNameError as exc:
        raise InstanceCorruptError(f"实例 {expected_name} 元数据 name 非法：{exc}") from exc

    runtime_raw = table.get("runtime") or {}
    fs_raw = table.get("filesystem") or {}
    labels_raw = table.get("labels") or {}
    try:
        arch = str(runtime_raw.get("arch", "win64"))
        if arch not in ("win64", "win32"):
            raise ValueError(f"不支持的 arch {arch!r}（仅支持 win64/win32）")
        wine_raw = runtime_raw.get("initialized_with_wine_version")
        wine_version = str(wine_raw) if wine_raw is not None else None
        fs = FilesystemPolicy(
            map_home=_as_bool(fs_raw.get("map_home"), False),
            map_root=_as_bool(fs_raw.get("map_root"), False),
            map_current_directory=_as_bool(fs_raw.get("map_current_directory"), True),
        )
        labels = {str(key): str(value) for key, value in labels_raw.items()}
    except (ValueError, TypeError) as exc:
        raise InstanceCorruptError(
            f"实例 {expected_name} 元数据 runtime/filesystem 非法：{exc}"
        ) from exc

    return Instance(
        id=instance_id,
        name=raw_name,
        version=version,
        backend=backend,
        install_state=state,
        created_at=created,
        updated_at=updated,
        prefix_directory=prefix_dir,
        runtime_config=RuntimeConfig(arch=arch, initialized_with_wine_version=wine_version),
        filesystem_policy=fs,
        labels=labels,
    )


def _read_metadata(inst_dir: Path, expected_name: str) -> Instance:
    target = inst_dir / "instance.toml"
    if target.is_symlink():
        # Metadata is untrusted: never follow a swapped-in symlink out of the
        # instance directory.
        raise InstanceCorruptError(f"实例 {expected_name} 元数据文件是符号链接，已拒绝。")
    if not target.is_file():
        raise InstanceNotFoundError(f"实例 {expected_name} 缺少元数据文件。")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(target, flags)
    except OSError as exc:
        raise InstanceCorruptError(f"实例 {expected_name} 元数据无法打开：{exc}") from exc
    try:
        with os.fdopen(fd, "rb") as handle:
            table = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise InstanceCorruptError(f"实例 {expected_name} 元数据无法解析：{exc}") from exc
    try:
        return _instance_from_toml(table, expected_name)
    except InstanceCorruptError:
        raise
    except Exception as exc:  # noqa: BLE001 - any stray validation error is a corrupt instance
        raise InstanceCorruptError(f"实例 {expected_name} 元数据非法：{exc}") from exc


def _atomic_write_text(target: Path, text: str) -> None:
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = target.with_name(f".{target.name}.tmp.{os.getpid()}")
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, target)
    finally:
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass


def _toml_dumps(table: dict[str, Any]) -> str:
    from io import BytesIO

    stream = BytesIO()
    tomli_w.dump(table, stream)
    return stream.getvalue().decode("utf-8")


def _write_metadata(inst_dir: Path, inst: Instance) -> None:
    _atomic_write_text(inst_dir / "instance.toml", _toml_dumps(_instance_to_toml(inst)))


_ALLOWED_TRANSITIONS: dict[InstallState, set[InstallState]] = {
    InstallState.PENDING: {InstallState.PENDING, InstallState.INSTALLED, InstallState.FAILED},
    InstallState.FAILED: {InstallState.FAILED, InstallState.PENDING, InstallState.INSTALLED},
    InstallState.INSTALLED: {InstallState.INSTALLED},
}


def _validate_transition(current: InstallState, new: InstallState) -> None:
    if new not in _ALLOWED_TRANSITIONS[current]:
        raise ConfigurationError(f"不允许从 {current.value} 转换到 {new.value}。")


@dataclass(frozen=True)
class CorruptInstance:
    """An instance directory that could not be read as valid metadata."""

    name: str
    reason: str


@dataclass(frozen=True)
class InstanceListing:
    """Result of listing: healthy instances plus broken entries (never fatal)."""

    instances: tuple[Instance, ...] = ()
    corrupt: tuple[CorruptInstance, ...] = ()


class InstanceRepository:
    """Filesystem-backed repository for instance metadata and directories."""

    def __init__(self, roots: paths.DataRoots) -> None:
        self._roots = roots
        self._instances_dir = roots.data_home / "instances"

    @property
    def instances_dir(self) -> Path:
        return self._instances_dir

    # ------------------------------------------------------------------ paths

    def _name_lock_path(self, name: str) -> Path:
        return self._instances_dir / ".locks" / f"{name}.lock"

    def _instance_dir(self, name: str) -> Path:
        return self._instances_dir / name

    def _require_instances_dir_safe(self) -> None:
        if self._instances_dir.is_symlink():
            raise UnsafePathError("instances 目录不应是符号链接。")

    def _ensure_safe_instance_dir(self, name: str) -> Path:
        validated = validate_instance_name(name)
        self._require_instances_dir_safe()
        inst_dir = self._instance_dir(validated)
        if inst_dir.is_symlink():
            raise UnsafePathError(f"实例目录 {inst_dir} 是符号链接，已拒绝。")
        return inst_dir

    def instance_root(self, name: str) -> Path:
        """Absolute root directory for *name* (validated, not created)."""
        return self._instance_dir(validate_instance_name(name))

    def prefix_path(self, name: str) -> Path:
        """Absolute prefix directory for *name*, derived from its metadata."""
        validated = validate_instance_name(name)
        inst = self.get(validated)
        return self._instance_dir(validated) / inst.prefix_directory

    # ---------------------------------------------------------------- writes

    def create(
        self,
        name: str,
        *,
        backend: BackendKind = BackendKind.WINE,
        arch: str = "win64",
        filesystem_policy: FilesystemPolicy | None = None,
        labels: dict[str, str] | None = None,
    ) -> Instance:
        """Create a new *pending* instance, atomically and exclusively.

        Raises :class:`InstanceAlreadyExistsError` if it already exists, and
        :class:`InstanceBusyError` if another operation holds the instance lock.
        """
        validated = validate_instance_name(name)
        self._require_instances_dir_safe()
        lock_path = self._name_lock_path(validated)
        try:
            with exclusive_lock(lock_path, blocking=False):
                inst_dir = self._instance_dir(validated)
                if inst_dir.is_symlink():
                    raise UnsafePathError(f"实例目录 {inst_dir} 是符号链接，已拒绝。")
                if inst_dir.exists():
                    raise InstanceAlreadyExistsError(f"实例 {validated} 已存在。")
                inst_dir.mkdir(parents=True, exist_ok=False, mode=0o700)
                (inst_dir / "prefix").mkdir(exist_ok=True, mode=0o700)
                (inst_dir / "locks").mkdir(exist_ok=True, mode=0o700)
                now = _utcnow()
                inst = Instance(
                    id=uuid.uuid4(),
                    name=validated,
                    version=1,
                    backend=backend,
                    install_state=InstallState.PENDING,
                    created_at=now,
                    updated_at=now,
                    runtime_config=RuntimeConfig(arch=arch),
                    filesystem_policy=filesystem_policy or FilesystemPolicy(),
                    labels=dict(labels) if labels else {},
                )
                _write_metadata(inst_dir, inst)
        except LockUnavailable as exc:
            raise InstanceBusyError(f"实例 {validated} 正被其他操作占用。") from exc
        return inst

    def update_install_state(self, name: str, state: InstallState) -> Instance:
        """Transition the persisted install state (guarded), atomically."""
        validated = validate_instance_name(name)
        lock_path = self._name_lock_path(validated)
        try:
            with exclusive_lock(lock_path, blocking=False):
                inst = self.get(validated)
                _validate_transition(inst.install_state, state)
                updated = replace(inst, install_state=state, updated_at=_utcnow())
                _write_metadata(self._instance_dir(validated), updated)
        except LockUnavailable as exc:
            raise InstanceBusyError(f"实例 {validated} 正被其他操作占用。") from exc
        return updated

    def delete(self, name: str) -> None:
        """Delete an instance directory after verifying path containment.

        The directory is first atomically renamed to a tombstone under the same
        data root (``<data>/lsw/tombstones/<name>.<uuid>``), then removed. If
        the final removal fails the instance is no longer listed but the
        recoverable path is reported. Refuses to follow a symlinked instance
        directory or anything that resolves outside ``instances/``.
        """
        validated = validate_instance_name(name)
        lock_path = self._name_lock_path(validated)
        try:
            with exclusive_lock(lock_path, blocking=False):
                inst_dir = self._ensure_safe_instance_dir(validated)
                if not inst_dir.exists():
                    raise InstanceNotFoundError(f"实例 {validated} 不存在。")
                if inst_dir.is_symlink():
                    # Narrow the TOCTOU window: never rename a symlink in.
                    raise UnsafePathError(f"实例目录 {inst_dir} 是符号链接，已拒绝删除。")
                real = inst_dir.resolve(strict=False)
                root = self._instances_dir.resolve(strict=False)
                if not real.is_relative_to(root):
                    raise UnsafePathError(f"实例目录 {inst_dir} 逃逸出数据根目录，已拒绝删除。")
                tombstone_dir = self._roots.data_home / "tombstones"
                tombstone_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
                tombstone = tombstone_dir / f"{validated}.{uuid.uuid4().hex}"
                try:
                    os.rename(inst_dir, tombstone)
                except OSError as exc:
                    raise OperationError(f"无法将实例 {validated} 移入 tombstone：{exc}") from exc
                try:
                    self._remove_tombstone(tombstone)
                except OSError as exc:
                    raise OperationError(
                        f"实例 {validated} 已移至 {tombstone}，但清理失败：{exc}"
                        f"（可手动删除该目录）。"
                    ) from exc
                # 锁文件有意保留：删除它会让同名实例的下一把锁使用新 inode，
                # 与仍在持有的 flock 并发，破坏“同名实例互斥”（spec §3.5）。
        except LockUnavailable as exc:
            raise InstanceBusyError(f"实例 {validated} 正被其他操作占用。") from exc

    @staticmethod
    def _remove_tombstone(tombstone: Path) -> None:
        """Remove a renamed instance tree without ever following a symlink."""
        if tombstone.is_symlink():
            tombstone.unlink()
            return
        shutil.rmtree(tombstone)

    # ----------------------------------------------------------------- reads

    def exists(self, name: str) -> bool:
        try:
            validated = validate_instance_name(name)
        except InvalidInstanceNameError:
            return False
        inst_dir = self._instance_dir(validated)
        return inst_dir.is_dir() and not inst_dir.is_symlink()

    def get(self, name: str) -> Instance:
        """Read one instance; raises NotFound/Corrupt/UnsafePath errors."""
        inst_dir = self._ensure_safe_instance_dir(name)
        if not inst_dir.exists():
            raise InstanceNotFoundError(f"实例 {name} 不存在。")
        return _read_metadata(inst_dir, name)

    def list(self) -> InstanceListing:
        """List all instances; a broken entry never fails the whole listing."""
        if not self._instances_dir.is_dir():
            return InstanceListing()
        self._require_instances_dir_safe()
        instances: list[Instance] = []
        corrupt: list[CorruptInstance] = []
        for entry in sorted(self._instances_dir.iterdir(), key=lambda p: p.name):
            if entry.name.startswith(".") or not entry.is_dir():
                continue
            if entry.is_symlink():
                corrupt.append(CorruptInstance(name=entry.name, reason="实例目录是符号链接。"))
                continue
            try:
                instances.append(_read_metadata(entry, entry.name))
            except InstanceNotFoundError as exc:
                corrupt.append(CorruptInstance(name=entry.name, reason=str(exc)))
            except InstanceCorruptError as exc:
                corrupt.append(CorruptInstance(name=entry.name, reason=str(exc)))
        return InstanceListing(instances=tuple(instances), corrupt=tuple(corrupt))

    # --------------------------------------------------------- default instance

    def _config_path(self) -> Path:
        return self._roots.config_home / "config.toml"

    def get_default(self) -> str | None:
        """Return the default instance name, or ``None``."""
        return get_default_instance(path=self._config_path())

    def set_default(self, name: str) -> None:
        """Make *name* the default; only fully installed instances qualify."""
        validated = validate_instance_name(name)
        inst = self.get(validated)
        if inst.install_state is not InstallState.INSTALLED:
            raise ConfigurationError(f"实例 {validated} 尚未完成安装，不能设为默认。")
        set_default_instance(validated, path=self._config_path())

    def clear_default(self) -> None:
        clear_default_instance(path=self._config_path())

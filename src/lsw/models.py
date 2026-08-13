"""Core domain models.

These are pure data objects: no I/O, no Wine knowledge. Persistence, the
backend interface and the CLI live in other layers and depend only on these
types.
"""

from __future__ import annotations

import enum
import re
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime

_REDACTED = "<redacted>"

_SENSITIVE_TOKENS = (
    "password",
    "passwd",
    "secret",
    "token",
    "apikey",
    "api_key",
    "credential",
    "authorization",
)


def _compact_key(key: str) -> str:
    """Lowercase *key* with every non-alphanumeric character stripped."""
    return re.sub(r"[^a-z0-9]", "", str(key).lower())


def is_sensitive_key(key: str) -> bool:
    """Whether a variable/config *name* smells sensitive (conservative).

    Substring matching on compacted keys means ``API_TOKEN``, ``api-key`` and
    ``api_key`` all count; over-redaction is preferred to a single leak.
    """
    compact = _compact_key(key)
    return any(token.replace("_", "") in compact for token in _SENSITIVE_TOKENS)


def redact_argument(arg: str) -> str:
    """Mask the value of ``--name=value`` arguments whose name smells sensitive.

    Only the ``--key=value`` form is handled; this is deliberately
    conservative so ordinary program arguments are never altered.
    """
    if "=" in arg:
        name, value = arg.split("=", 1)
        if value and any(token in name.lower() for token in _SENSITIVE_TOKENS):
            return f"{name}={_REDACTED}"
    return arg


def redact_env(env: Mapping[str, str]) -> dict[str, str]:
    """Return a copy of *env* with sensitive values masked, safe for logging."""
    return {key: (_REDACTED if is_sensitive_key(key) else value) for key, value in env.items()}


_SENSITIVE_PAIR_RE = re.compile(
    r"(?i)(?P<keyquote>[\"']?)(?P<key>[A-Za-z_][A-Za-z0-9_. -]{0,63})"
    r"(?P<sep>[\"']?\s*[:=]\s*)"
    r"(?P<valuequote>[\"']?)(?P<value>[^,;\s][^\"',;]*)[\"']?"
)


def redact_text(text: str) -> str:
    """Mask ``key=value`` / ``key: value`` pairs whose key smells sensitive.

    Handles quoted keys and values (``"api_key": "xyz"``) as well as bare
    ones. Conservative: an ordinary word followed by ``:``/``=`` is only
    masked when the key itself looks sensitive, so normal prose and paths
    survive intact.
    """

    def _mask(match: re.Match[str]) -> str:
        if is_sensitive_key(match.group("key")):
            return f"{match.group('keyquote')}{match.group('key')}{match.group('sep')}{_REDACTED}"
        return match.group(0)

    return _SENSITIVE_PAIR_RE.sub(_mask, text)


class BackendKind(str, enum.Enum):
    """Identifies a runtime backend implementation."""

    WINE = "wine"
    FAKE = "fake"


class InstallState(str, enum.Enum):
    """Persisted installation state of an instance.

    This is *not* the instantaneous Running/Stopped status, which is probed
    from the backend and lives in :class:`InstanceState`.
    """

    PENDING = "pending"
    INSTALLED = "installed"
    FAILED = "failed"


class InstanceState(str, enum.Enum):
    """Instantaneous runtime status of an instance, probed from the backend."""

    RUNNING = "Running"
    STOPPED = "Stopped"
    INSTALLING = "Installing"
    BROKEN = "Broken"
    UNKNOWN = "Unknown"


@dataclass(frozen=True)
class FilesystemPolicy:
    """File-mapping policy applied to an instance's prefix."""

    map_home: bool = False
    map_root: bool = False
    map_current_directory: bool = True


@dataclass(frozen=True)
class RuntimeConfig:
    """Backend runtime settings captured at install time."""

    arch: str = "win64"
    initialized_with_wine_version: str | None = None


_PREFIX_DIRECTORY = "prefix"


def _validate_prefix_directory(value: str) -> None:
    """A prefix directory must be a single, non-escaping relative segment."""
    if not isinstance(value, str) or not value:
        raise ValueError("prefix_directory must be a non-empty relative path")
    if value in {".", ".."} or "/" in value or "\\" in value:
        raise ValueError("prefix_directory must be a single relative path segment")


@dataclass(frozen=True)
class Instance:
    """A registered Windows userspace instance (a named Wine prefix)."""

    id: uuid.UUID
    name: str
    version: int
    backend: BackendKind
    install_state: InstallState
    created_at: datetime
    updated_at: datetime
    prefix_directory: str = _PREFIX_DIRECTORY
    runtime_config: RuntimeConfig = field(default_factory=RuntimeConfig)
    filesystem_policy: FilesystemPolicy = field(default_factory=FilesystemPolicy)
    labels: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.version < 1:
            raise ValueError("version must be >= 1")
        if self.id.version != 4:
            raise ValueError("id must be a v4 UUID")
        _validate_prefix_directory(self.prefix_directory)


@dataclass(frozen=True)
class BackendInfo:
    """Probe result describing a backend's availability on this host."""

    backend: BackendKind
    available: bool
    executable: str | None = None
    version: str | None = None
    architecture: str | None = None
    diagnostics: tuple[str, ...] = ()


@dataclass(frozen=True)
class RunOptions:
    """How to launch a Windows program inside an instance."""

    argv: tuple[str, ...]
    cwd: str | None = None
    environment: Mapping[str, str] = field(default_factory=dict)
    interactive: bool = False
    timeout: float | None = None
    capture: bool = False

    def __post_init__(self) -> None:
        if not self.argv:
            raise ValueError("argv must not be empty")
        if self.timeout is not None and self.timeout <= 0:
            raise ValueError("timeout must be positive")


@dataclass(frozen=True)
class TerminateResult:
    """Outcome of terminating one instance."""

    instance_name: str
    previous_state: InstanceState


@dataclass(frozen=True)
class ShutdownResult:
    """Aggregate outcome of shutting down multiple instances."""

    terminated: tuple[str, ...] = ()
    failed: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class CommandResult:
    """Result of a backend/internal command invocation."""

    argv: tuple[str, ...]
    exit_code: int
    stdout: bytes = b""
    stderr: bytes = b""
    duration: float = 0.0
    timed_out: bool = False

    @property
    def sanitized_argv(self) -> tuple[str, ...]:
        """argv with sensitive option values redacted, safe for logging."""
        return tuple(redact_argument(arg) for arg in self.argv)

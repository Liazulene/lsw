"""Wine backend (milestone M4: real Wine integration).

Every Wine subprocess is launched with an ``argv`` array through a single
shell-free runner (no ``shell=True``, no command strings) and a **curated**
environment: only an allowlisted subset of the inherited user environment is
forwarded (PATH, HOME, locale, display/session, D-Bus, WSL interop, temp,
identity, timezone — see :data:`_ENV_ALWAYS_PRESERVE`), so tokens, keys,
secrets, credentials and agent sockets can never leak into a Wine process by
accident. ``WINEPREFIX``/``WINEARCH`` are always derived from the instance and
can never be clobbered by a caller, so a process can never cross into another
instance or the host's default ``~/.wine``.

Lifecycle implemented here:

* ``probe`` — resolve ``wine``/``wineboot``/``wineserver`` (injectable
  resolver), then ``wine --version`` with a bounded timeout;
* ``initialize`` — ``wineboot --init`` then ``wineserver -w`` under the
  instance prefix with bounded timeouts, then the dosdevices policy;
* ``status`` — side-effect-free /proc probe keyed on the instance's WINEPREFIX
  (never invokes wineserver; there is no status/query operation);
* ``run`` — re-enforce the dosdevices policy (removing any host-mount drive
  letter recreated since install), then ``[wine, *argv]`` with stdio passthrough
  and exit-code passthrough;
* ``terminate`` — ``wineserver -k`` scoped to the prefix, then poll to timeout;
* ``shutdown_all`` — terminate each registered instance and aggregate results.

``wineserver`` has no status/query operation (``-p`` / ``--persistent`` only
sets the persistence delay, so it is never used as a probe). Running state is
instead detected by reading ``/proc`` for a ``wineserver`` process carrying the
instance's exact WINEPREFIX (see ``wineserver_probe``); the real-Wine contract
is pinned by the Wine-marked integration tests, which run only on hosts that
actually have Wine and skip explicitly everywhere else.
"""

from __future__ import annotations

import os
import platform
import re
import shutil
import sys
import time
from collections.abc import Callable, Sequence
from pathlib import Path

from ..errors import BackendError, DependencyMissingError, OperationTimeoutError, UnsafePathError
from ..models import (
    BackendInfo,
    BackendKind,
    CommandResult,
    Instance,
    InstanceState,
    RunOptions,
    ShutdownResult,
    TerminateResult,
    redact_text,
)
from ..paths import DataRoots
from ..process import CommandRunner, ProcessRunner
from ..wineserver_probe import wineserver_running

_MISSING_RUNTIME = (
    "未找到 Wine runtime。提示：请先通过系统包管理器安装 Wine，然后运行 `lsw --status` 检查。"
)

#: Variables LSW always derives from the instance. Callers may never override
#: them through ``RunOptions.environment`` — they define the instance boundary,
#: and clobbering either would break per-instance isolation (spec §10.3).
_PROTECTED_WINE_VARS = frozenset({"WINEPREFIX", "WINEARCH"})

#: Host mount roots that are never mapped into an instance by default — WSL's
#: ``/mnt/c``, ``/mnt/d``, ``/mnt/e``, ... Only a future explicit opt-in feature
#: may change this; M5 ships no such opt-in.
_HOST_MOUNT_BLOCKLIST = ("/mnt",)

#: Inherited environment variables always forwarded to Wine.
#:
#: Deliberately minimal (M5.1 env policy, spec §10.3): only the categories a
#: Wine/desktop/WSL session actually needs are forwarded — executable/search
#: path, home, locale, display and session, D-Bus, WSL interop, temp dirs,
#: identity and timezone. Every other inherited variable is dropped, so tokens,
#: keys, secrets, passwords, credentials, cloud/provider credentials and
#: agent/socket variables can never leak into a Wine process by accident.
#: ``XAUTHORITY`` is an intentional exception: it names the X11 auth cookie file
#: and is required for display access.
_ENV_ALWAYS_PRESERVE = frozenset(
    {
        "PATH",
        "HOME",
        "LANG",
        "LANGUAGE",
        "LC_ALL",
        "DISPLAY",
        "WAYLAND_DISPLAY",
        "XDG_RUNTIME_DIR",
        "XAUTHORITY",
        "DBUS_SESSION_BUS_ADDRESS",
        "WSL_INTEROP",
        "WSL_DISTRO_NAME",
        "USER",
        "LOGNAME",
        "SHELL",
        "TERM",
        "TZ",
        "HOSTNAME",
        "TMP",
        "TEMP",
        "TMPDIR",
    }
)

#: Substrings (on a compacted name) that mark an env var as secret-bearing.
#: Covers ``*_TOKEN``, ``*_KEY``, ``*_SECRET``, ``*_PASSWORD``/``*_PASSWD``,
#: ``*_CREDENTIAL*``, cloud/provider credentials (``AWS_ACCESS_KEY_ID``, ...),
#: ``SSH_AUTH_SOCK`` and other agent/socket variables. Applied defensively on
#: top of the allowlist; over-stripping is safe, a single leaked credential is
#: not.
_ENV_SENSITIVE_MARKERS = frozenset(
    {
        "token",
        "passwd",
        "password",
        "secret",
        "credential",
        "apikey",
        "key",
        "auth",
        "agent",
        "socket",
        "cookie",
    }
)


def _is_sensitive_env_var(name: str) -> bool:
    """Whether an inherited env var name smells secret-bearing (conservative)."""
    compact = re.sub(r"[^a-z0-9]", "", str(name).lower())
    return any(marker.replace("_", "") in compact for marker in _ENV_SENSITIVE_MARKERS)


def _safe_inherited_env() -> dict[str, str]:
    """The curated subset of the inherited environment forwarded to Wine.

    Only :data:`_ENV_ALWAYS_PRESERVE` entries (plus ``LC_*`` locale categories)
    are forwarded; anything else is dropped. This is the single boundary that
    keeps sensitive inherited variables out of every Wine subprocess, including
    the version probe.
    """
    env: dict[str, str] = {}
    for name, value in os.environ.items():
        if name in _ENV_ALWAYS_PRESERVE:
            env[name] = value
        elif name.startswith("LC_") and not _is_sensitive_env_var(name):
            env[name] = value
    return env


def _default_resolver(command: str) -> str | None:
    return shutil.which(command)


def _decode_output(result: CommandResult) -> str:
    text = result.stderr.decode("utf-8", errors="replace").strip()
    if not text:
        text = result.stdout.decode("utf-8", errors="replace").strip()
    return redact_text(text or "(无输出)")


class WineBackend:
    """Real Wine runtime backend (M4)."""

    def __init__(
        self,
        *,
        wine_binary: str = "wine",
        wineboot_binary: str = "wineboot",
        wineserver_binary: str = "wineserver",
        winepath_binary: str = "winepath",
        startup_timeout_seconds: float = 60.0,
        shutdown_timeout_seconds: float = 15.0,
        probe_timeout_seconds: float = 10.0,
        roots: DataRoots | None = None,
        executable_resolver: Callable[[str], str | None] | None = None,
        runner: CommandRunner | None = None,
        proc_root: Path | None = None,
    ) -> None:
        self._proc_root = proc_root
        self._binaries = {
            "wine": wine_binary,
            "wineboot": wineboot_binary,
            "wineserver": wineserver_binary,
            "winepath": winepath_binary,
        }
        self._roots = roots
        self._resolver = (
            executable_resolver if executable_resolver is not None else _default_resolver
        )
        self._runner = runner if runner is not None else ProcessRunner()
        self._startup_timeout = float(startup_timeout_seconds)
        # shutdown_timeout_seconds is applied by the Services layer, which passes
        # an explicit per-call timeout to terminate()/shutdown_all(); it is kept
        # as a constructor parameter only for config compatibility.
        self._probe_timeout = float(probe_timeout_seconds)
        self._poll_interval = 0.1
        self._resolved: dict[str, str] | None = None
        self._missing: list[str] = []

    # ------------------------------------------------------------------ paths

    def _prefix_path(self, instance: Instance) -> Path:
        """Absolute WINEPREFIX for *instance*, validated to stay in the data root."""
        if self._roots is None:
            raise BackendError("WineBackend 未配置数据根目录，无法解析实例 prefix 路径。")
        prefix = self._roots.data_home / "instances" / instance.name / instance.prefix_directory
        root = self._roots.data_home.resolve(strict=False)
        if not prefix.resolve(strict=False).is_relative_to(root):
            raise UnsafePathError(f"prefix 路径逃逸出数据根目录：{prefix}")
        return prefix

    # ------------------------------------------------------------- discovery

    def _resolve(self) -> tuple[dict[str, str], list[str]]:
        """Resolve configured binary names to absolute paths (cached)."""
        if self._resolved is None:
            found: dict[str, str] = {}
            missing: list[str] = []
            for name, command in self._binaries.items():
                path = self._resolver(command)
                if path:
                    found[name] = path
                else:
                    missing.append(command)
            self._resolved = found
            self._missing = missing
        return self._resolved, self._missing

    # ---------------------------------------------------------------- probe

    def probe(self) -> BackendInfo:
        if not sys.platform.startswith("linux"):
            return BackendInfo(
                backend=BackendKind.WINE,
                available=False,
                architecture=platform.machine(),
                diagnostics=("LSW 需要 Linux（WSL）。",),
            )
        found, missing = self._resolve()
        diagnostics = tuple(f"未找到可执行文件：{command}" for command in missing)
        if missing:
            return BackendInfo(
                backend=BackendKind.WINE,
                available=False,
                executable=found.get("wine"),
                architecture=platform.machine(),
                diagnostics=diagnostics,
            )
        result = self._runner.run(
            [found["wine"], "--version"],
            env=self._probe_env(),
            timeout=self._probe_timeout,
            capture=True,
        )
        if result.exit_code != 0:
            return BackendInfo(
                backend=BackendKind.WINE,
                available=False,
                executable=found["wine"],
                architecture=platform.machine(),
                diagnostics=(
                    f"`wine --version` 失败（退出码 {result.exit_code}），Wine 可能损坏。",
                ),
            )
        version = self._first_line(result)
        return BackendInfo(
            backend=BackendKind.WINE,
            available=True,
            executable=found["wine"],
            version=version,
            architecture=platform.machine(),
            diagnostics=(),
        )

    @staticmethod
    def _first_line(result: CommandResult) -> str | None:
        for stream in (result.stdout, result.stderr):
            text = stream.decode("utf-8", errors="replace").strip()
            if text:
                return text.splitlines()[0].strip()
        return None

    def _probe_env(self) -> dict[str, str]:
        """Environment for version probing — never any caller prefix or secrets."""
        env = _safe_inherited_env()
        env.pop("WINEPREFIX", None)  # defense in depth: never a caller preset
        env.pop("WINEARCH", None)
        return env

    def _wine_env(self, instance: Instance) -> dict[str, str]:
        """Instance environment: curated inherited vars plus the instance boundary.

        Only the allowlisted inherited variables (see :data:`_ENV_ALWAYS_PRESERVE`)
        are forwarded; everything else — tokens, keys, credentials, agent sockets —
        is dropped, so a caller's ``WINEPREFIX`` can never be reached and a stray
        ``~/.wine`` can never be touched. ``WINEPREFIX``/``WINEARCH`` are always
        derived from the instance.
        """
        env = _safe_inherited_env()
        env["WINEPREFIX"] = str(self._prefix_path(instance))
        env["WINEARCH"] = instance.runtime_config.arch
        return env

    def _require_available(self) -> None:
        if not self.probe().available:
            raise DependencyMissingError(_MISSING_RUNTIME)

    # ------------------------------------------------------------- lifecycle

    def initialize(self, instance: Instance) -> None:
        self._require_available()
        prefix = self._prefix_path(instance)
        env = self._wine_env(instance)
        found, _ = self._resolve()
        boot = self._runner.run(
            [found["wineboot"], "--init"], env=env, timeout=self._startup_timeout, capture=True
        )
        if boot.exit_code != 0:
            raise BackendError(
                f"wineboot --init 失败（退出码 {boot.exit_code}）：{_decode_output(boot)}"
                f"。已保留 prefix {prefix} 供诊断。"
            )
        wait = self._runner.run(
            [found["wineserver"], "-w"], env=env, timeout=self._startup_timeout, capture=True
        )
        if wait.exit_code != 0:
            raise BackendError(
                f"wineserver -w 失败（退出码 {wait.exit_code}）：{_decode_output(wait)}"
                f"。已保留 prefix {prefix} 供诊断。"
            )
        self._apply_filesystem_policy(instance, prefix)
        # Restrictive prefix perms: the prefix tree belongs to this instance and
        # no other local user should read it (spec §3.3, M5 permissions policy).
        if prefix.is_dir():
            prefix.chmod(0o700)

    def status(self, instance: Instance) -> InstanceState:
        self._require_available()
        running = wineserver_running(self._prefix_path(instance), proc_root=self._proc_root)
        if running is None:
            return InstanceState.UNKNOWN
        return InstanceState.RUNNING if running else InstanceState.STOPPED

    def run(self, instance: Instance, argv: Sequence[str], options: RunOptions) -> int:
        self._require_available()
        if options.capture and options.interactive:
            raise BackendError("capture 与 interactive 不能同时设置。")
        self._enforce_filesystem_policy(instance)
        found, _ = self._resolve()
        result = self._runner.run(
            [found["wine"], *argv],
            env=self._env_for_instance(instance, options),
            cwd=options.cwd,
            timeout=options.timeout,
            capture=options.capture,
            interactive=options.interactive,
        )
        return result.exit_code

    def _env_for_instance(self, instance: Instance, options: RunOptions) -> dict[str, str]:
        """Safe merge of ``RunOptions.environment`` over the instance env.

        The instance env is the curated inherited set; explicit overrides are
        then applied on top — except the protected instance boundary variables
        (``WINEPREFIX``/``WINEARCH``), which a caller may never clobber.
        ``RunOptions.environment`` values are intentional, caller-provided
        values (the caller is responsible for any secrets they deliberately
        place there); attempting to override a protected variable fails closed
        with a clear error instead of silently crossing into another prefix.
        """
        env = self._wine_env(instance)
        overrides = dict(options.environment or {})
        conflicts = _PROTECTED_WINE_VARS.intersection(overrides)
        if conflicts:
            raise BackendError(
                "RunOptions.environment 不能覆盖受保护的实例变量：" + "、".join(sorted(conflicts))
            )
        env.update(overrides)
        return env

    def terminate(self, instance: Instance, timeout: float) -> TerminateResult:
        self._require_available()
        prefix = self._prefix_path(instance)
        previous = wineserver_running(prefix, proc_root=self._proc_root)
        if previous is False:
            return TerminateResult(
                instance_name=instance.name, previous_state=InstanceState.STOPPED
            )
        found, _ = self._resolve()
        self._runner.run(
            [found["wineserver"], "-k"],
            env=self._wine_env(instance),
            timeout=timeout,
            capture=True,
        )
        previous_state = InstanceState.RUNNING if previous is True else InstanceState.UNKNOWN
        deadline = time.monotonic() + timeout
        while True:
            if wineserver_running(prefix, proc_root=self._proc_root) is False:
                return TerminateResult(instance_name=instance.name, previous_state=previous_state)
            if time.monotonic() >= deadline:
                raise OperationTimeoutError(f"实例 {instance.name} 在 {timeout:.0f}s 内未能停止。")
            time.sleep(self._poll_interval)

    def shutdown_all(self, instances: Sequence[Instance], timeout: float) -> ShutdownResult:
        terminated: list[str] = []
        failed: list[tuple[str, str]] = []
        for instance in instances:
            try:
                self.terminate(instance, timeout)
                terminated.append(instance.name)
            except (BackendError, OperationTimeoutError, DependencyMissingError) as exc:
                failed.append((instance.name, str(exc)))
        return ShutdownResult(terminated=tuple(terminated), failed=tuple(failed))

    # ------------------------------------------------- dosdevices filesystem policy

    def _apply_filesystem_policy(self, instance: Instance, prefix: Path) -> None:
        """Enforce c:/z: mappings after initialization, then remove forbidden ones."""
        dosdevices = self._safe_dosdevices(prefix)
        if dosdevices is None:
            return
        self._ensure_c_drive(dosdevices, prefix, prefix.resolve(strict=False))
        self._remove_forbidden_mappings(dosdevices, instance.filesystem_policy.map_root)

    def _enforce_filesystem_policy(self, instance: Instance) -> None:
        """Re-enforce the dosdevices policy immediately before a program launch.

        A user, Wine component or Windows program may have recreated a drive
        mapping since installation — including one resolving to ``/mnt/c``,
        ``/mnt/d``, ``/mnt/e`` or host root. Re-check right before the
        executable starts so a forbidden mapping is removed before it can be
        reached. Only symlinks directly under a real ``dosdevices`` directory
        inside the prefix are ever touched; nothing else is mutated.
        """
        dosdevices = self._safe_dosdevices(self._prefix_path(instance))
        if dosdevices is None:
            return
        self._remove_forbidden_mappings(dosdevices, instance.filesystem_policy.map_root)

    def _safe_dosdevices(self, prefix: Path) -> Path | None:
        """Return ``dosdevices`` when it is a real directory inside *prefix*.

        ``None`` when missing, when it is itself a symlink, or when it resolves
        outside the prefix — in which case nothing is mutated, so a malicious
        ``dosdevices`` chain can never direct LSW to touch a directory elsewhere
        on the host.
        """
        dosdevices = prefix / "dosdevices"
        if dosdevices.is_symlink() or not dosdevices.is_dir():
            return None
        prefix_root = prefix.resolve(strict=False)
        if not dosdevices.resolve(strict=False).is_relative_to(prefix_root):
            return None
        return dosdevices

    def _remove_forbidden_mappings(self, dosdevices: Path, map_root: bool) -> None:
        """Remove mappings forbidden by the filesystem policy (default-deny).

        Host-mount drive letters are removed unconditionally; the root ``z:``
        mapping is removed unless ``map_root`` is explicitly enabled. Safe
        custom drives that stay inside the prefix are left untouched.
        """
        self._remove_host_mount_links(dosdevices)
        if not map_root:
            self._remove_drive_link(dosdevices / "z:", dosdevices)

    def _remove_host_mount_links(self, dosdevices: Path) -> None:
        """Remove drive-letter links resolving into a host mount (default-deny).

        Wine on WSL can expose the whole host through ``z:`` → ``/`` (removed
        when ``map_root`` is false), and drive letters can point directly at
        ``/mnt/c``, ``/mnt/d``, ``/mnt/e``, ... None of these host mounts may be
        reachable through a drive letter by default; M5 has no opt-in that would
        allow them. Only symlinks directly under ``dosdevices`` are touched.
        """
        blocklist = tuple(Path(mount) for mount in _HOST_MOUNT_BLOCKLIST)
        for link in dosdevices.iterdir():
            if not link.is_symlink():
                continue
            try:
                target = link.resolve(strict=False)
            except OSError:
                continue
            if any(target.is_relative_to(mount) for mount in blocklist):
                self._remove_drive_link(link, dosdevices)

    def _ensure_c_drive(self, dosdevices: Path, prefix: Path, prefix_root: Path) -> None:
        c_link = dosdevices / "c:"
        if c_link.is_symlink():
            if c_link.resolve(strict=False).is_relative_to(prefix_root):
                return
            c_link.unlink()  # unsafe target → recreate inside the prefix
        elif c_link.exists():
            return  # unusual real directory; leave it untouched
        drive_c = prefix / "drive_c"
        drive_c.mkdir(parents=True, exist_ok=True, mode=0o700)
        relative = os.path.relpath(drive_c, dosdevices)
        try:
            c_link.symlink_to(relative)
        except FileExistsError:
            pass

    def _remove_drive_link(self, link: Path, dosdevices: Path) -> None:
        """Unlink *link* only when it is a symlink directly under dosdevices."""
        if link.parent.resolve(strict=False) != dosdevices.resolve(strict=False):
            return
        try:
            if link.is_symlink():
                link.unlink()
        except OSError:
            # Raced or a real directory swapped in: never delete a real
            # directory, and never let a failed removal fail the launch.
            pass

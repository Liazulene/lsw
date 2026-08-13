"""Security/regression tests for M5 hardening (LSW_SPEC §19).

Each test pins one M5 safety property:

* metadata is untrusted input — malformed/tampered values fail closed;
* instance/config/prefix trees use restrictive permissions;
* deletes are hardened against symlinks and TOCTOU races;
* ``RunOptions.environment`` can never clobber the instance boundary;
* dosdevices never maps host mounts (``/mnt/*``) without an opt-in;
* logs and errors redact tokens, credentials and sensitive environment values;
* event logging is strictly best-effort and never fails an operation.
"""

from __future__ import annotations

import os
import uuid
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import pytest

from lsw import models
from lsw import repository as repo_module
from lsw.backends import WineBackend
from lsw.backends.fake import FakeBackend
from lsw.backends.wine import _is_sensitive_env_var
from lsw.errors import BackendError, InstanceCorruptError, OperationError
from lsw.logging import EventLog
from lsw.models import FilesystemPolicy, InstallState, Instance, RunOptions
from lsw.repository import InstanceRepository
from lsw.services import Services

# ------------------------------------------------------------------- helpers


@pytest.fixture
def repo(isolated_roots) -> InstanceRepository:
    return InstanceRepository(isolated_roots)


def _inst(name: str = "Windows-11", **overrides) -> Instance:
    return Instance(
        id=uuid.uuid4(),
        name=name,
        version=1,
        backend=models.BackendKind.WINE,
        install_state=InstallState.INSTALLED,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
        **overrides,
    )


def _metadata(repo: InstanceRepository, name: str) -> str:
    return (repo.instances_dir / name / "instance.toml").read_text(encoding="utf-8")


def _rewrite_metadata(repo: InstanceRepository, name: str, old: str, new: str) -> None:
    """Tamper with an instance's metadata as an attacker would."""
    path = repo.instances_dir / name / "instance.toml"
    path.write_text(_metadata(repo, name).replace(old, new), encoding="utf-8")


def _mode(path: Path) -> int:
    return path.stat().st_mode & 0o777


class _FakeRunner:
    """Records invocations; succeeds for every Wine tool."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def run(self, argv, *, env=None, cwd=None, timeout=None, capture=False, interactive=False):
        self.calls.append({"argv": list(argv), "env": dict(env or {}), "cwd": cwd})
        if os.path.basename(argv[0]) == "wine" and "--version" in argv:
            return models.CommandResult(tuple(argv), 0, stdout=b"wine-9.0\n", stderr=b"")
        return models.CommandResult(tuple(argv), 0)


def _backend(isolated_roots, runner=None) -> WineBackend:
    return WineBackend(
        roots=isolated_roots,
        executable_resolver=lambda command: "/fake/" + command,
        runner=runner or _FakeRunner(),
    )


def _prefix(isolated_roots, name: str = "Windows-11") -> Path:
    return isolated_roots.data_home / "instances" / name / "prefix"


def _make_dosdevices(isolated_roots, name: str = "Windows-11") -> Path:
    prefix = _prefix(isolated_roots, name)
    dosdevices = prefix / "dosdevices"
    dosdevices.mkdir(parents=True, exist_ok=True)
    (prefix / "drive_c").mkdir(exist_ok=True)
    (dosdevices / "c:").symlink_to("../drive_c")
    return dosdevices


# ------------------------------------------------------ metadata (untrusted)


def test_string_boolean_metadata_is_rejected(repo):
    repo.create("Windows-11")
    _rewrite_metadata(repo, "Windows-11", "map_root = false", 'map_root = "false"')
    with pytest.raises(InstanceCorruptError):
        repo.get("Windows-11")


def test_string_true_map_home_metadata_is_rejected(repo):
    repo.create("Windows-11")
    _rewrite_metadata(repo, "Windows-11", "map_home = false", 'map_home = "true"')
    with pytest.raises(InstanceCorruptError):
        repo.get("Windows-11")


def test_unsupported_arch_metadata_is_rejected(repo):
    repo.create("Windows-11")
    _rewrite_metadata(repo, "Windows-11", 'arch = "win64"', 'arch = "mips"')
    with pytest.raises(InstanceCorruptError):
        repo.get("Windows-11")


def test_metadata_escaping_prefix_directory_is_rejected(repo):
    repo.create("Windows-11")
    _rewrite_metadata(
        repo, "Windows-11", 'prefix_directory = "prefix"', 'prefix_directory = "../escape"'
    )
    with pytest.raises(InstanceCorruptError):
        repo.get("Windows-11")


def test_symlinked_metadata_file_is_rejected(repo, tmp_path):
    repo.create("Windows-11")
    target = repo.instances_dir / "Windows-11" / "instance.toml"
    target.unlink()
    outside = tmp_path / "outside.toml"
    outside.write_text("schema_version = 1\n", encoding="utf-8")
    target.symlink_to(outside)
    with pytest.raises(InstanceCorruptError):
        repo.get("Windows-11")


def test_corrupt_metadata_is_listed_not_fatal(repo):
    repo.create("A")
    repo.create("B")
    _rewrite_metadata(repo, "B", "map_root = false", 'map_root = "false"')
    listing = repo.list()
    assert [item.name for item in listing.instances] == ["A"]
    assert [item.name for item in listing.corrupt] == ["B"]


# ------------------------------------------------------------ permissions


def test_instance_tree_permissions_restrictive(repo):
    repo.create("Windows-11")
    inst = repo.instances_dir / "Windows-11"
    assert _mode(inst) == 0o700
    assert _mode(inst / "prefix") == 0o700
    assert _mode(inst / "locks") == 0o700
    assert _mode(inst / "instance.toml") == 0o600
    assert _mode(repo.instances_dir / ".locks") == 0o700
    assert _mode(repo.instances_dir / ".locks" / "Windows-11.lock") == 0o600


def test_config_file_permissions_restrictive(repo, isolated_roots):
    repo.create("Windows-11")
    repo.update_install_state("Windows-11", InstallState.INSTALLED)
    repo.set_default("Windows-11")
    config = isolated_roots.config_home / "config.toml"
    assert config.is_file()
    assert _mode(config) == 0o600


def test_tombstone_dir_permissions_restrictive(repo, isolated_roots):
    repo.create("Windows-11")
    repo.delete("Windows-11")
    assert _mode(isolated_roots.data_home / "tombstones") == 0o700


# -------------------------------------------------------- delete hardening


def test_delete_retains_lock_file(repo):
    repo.create("Windows-11")
    lock = repo.instances_dir / ".locks" / "Windows-11.lock"
    assert lock.is_file()
    repo.delete("Windows-11")
    assert lock.is_file()  # retained so a same-name re-lock can't race the flock
    assert not repo.exists("Windows-11")


def test_delete_rename_failure_is_domain_error(repo, monkeypatch):
    repo.create("Windows-11")

    def _boom(*_args, **_kwargs):
        raise OSError("simulated rename failure")

    monkeypatch.setattr("lsw.repository.os.rename", _boom)
    with pytest.raises(OperationError):
        repo.delete("Windows-11")
    assert repo.exists("Windows-11")


def test_remove_tombstone_unlinks_symlink_without_following(repo, tmp_path):
    repo.create("Windows-11")
    outside = tmp_path / "keep"
    outside.mkdir()
    marker = outside / "marker"
    marker.write_text("x", encoding="utf-8")
    tombstones = repo.instances_dir.parent / "tombstones"
    tombstones.mkdir(parents=True, exist_ok=True)
    link = tombstones / "evil"
    link.symlink_to(outside)
    repo_module.InstanceRepository._remove_tombstone(link)
    assert not link.exists()
    assert outside.is_dir()
    assert marker.read_text(encoding="utf-8") == "x"


# ------------------------------------------------------- env merge policy


def test_run_options_environment_is_applied(isolated_roots):
    runner = _FakeRunner()
    backend = _backend(isolated_roots, runner)
    backend.run(_inst(), ("cmd.exe",), RunOptions(argv=("cmd.exe",), environment={"FOO": "bar"}))
    env = runner.calls[-1]["env"]
    assert env["FOO"] == "bar"
    assert env["WINEPREFIX"] == str(_prefix(isolated_roots))
    assert env["WINEARCH"] == "win64"


def test_run_options_cannot_override_wineprefix(isolated_roots):
    backend = _backend(isolated_roots)
    with pytest.raises(BackendError):
        backend.run(
            _inst(),
            ("cmd.exe",),
            RunOptions(argv=("cmd.exe",), environment={"WINEPREFIX": "/evil"}),
        )


def test_run_options_cannot_override_winearch(isolated_roots):
    backend = _backend(isolated_roots)
    with pytest.raises(BackendError):
        backend.run(
            _inst(), ("cmd.exe",), RunOptions(argv=("cmd.exe",), environment={"WINEARCH": "win32"})
        )


def test_run_options_conflict_names_both_protected_vars(isolated_roots):
    backend = _backend(isolated_roots)
    with pytest.raises(BackendError) as exc:
        backend.run(
            _inst(),
            ("cmd.exe",),
            RunOptions(argv=("cmd.exe",), environment={"WINEPREFIX": "/a", "WINEARCH": "win32"}),
        )
    message = str(exc.value)
    assert "WINEPREFIX" in message
    assert "WINEARCH" in message


# -------------------------------------------------- dosdevices default-deny


def test_initialize_removes_host_mount_drive_links(isolated_roots):
    dosdevices = _make_dosdevices(isolated_roots)
    (dosdevices / "z:").symlink_to("/")
    (dosdevices / "d:").symlink_to("/mnt/c")
    (dosdevices / "e:").symlink_to("/mnt/d")
    _backend(isolated_roots).initialize(_inst())
    assert (dosdevices / "c:").is_symlink()
    assert not (dosdevices / "z:").exists()  # host root never mapped by default
    assert not (dosdevices / "d:").exists()
    assert not (dosdevices / "e:").exists()


def test_mnt_links_removed_even_when_root_mapping_enabled(isolated_roots):
    dosdevices = _make_dosdevices(isolated_roots)
    (dosdevices / "z:").symlink_to("/")
    (dosdevices / "d:").symlink_to("/mnt/c")
    inst = replace(_inst(), filesystem_policy=FilesystemPolicy(map_root=True))
    _backend(isolated_roots).initialize(inst)
    assert (dosdevices / "z:").is_symlink()  # explicit opt-in honored
    assert not (dosdevices / "d:").exists()  # /mnt still default-deny


def test_initialize_never_auto_maps_arbitrary_host_dirs(isolated_roots):
    dosdevices = _make_dosdevices(isolated_roots)
    _backend(isolated_roots).initialize(_inst())
    prefix_root = _prefix(isolated_roots).resolve(strict=False)
    names = sorted(path.name for path in dosdevices.iterdir())
    assert names == ["c:"]
    assert (dosdevices / "c:").resolve(strict=False).is_relative_to(prefix_root)


def test_initialize_sets_prefix_restrictive_permissions(isolated_roots):
    prefix = _prefix(isolated_roots)
    prefix.mkdir(parents=True, exist_ok=True)
    prefix.chmod(0o755)
    _backend(isolated_roots).initialize(_inst())
    assert _mode(prefix) == 0o700


# -------------------------------------------------------------- redaction


def test_redact_env_masks_sensitive_values_only():
    env = {"API_TOKEN": "s3cret", "WINE_PASSWORD": "x", "PATH": "/bin", "plain": "y"}
    redacted = models.redact_env(env)
    assert redacted["API_TOKEN"] == "<redacted>"
    assert redacted["WINE_PASSWORD"] == "<redacted>"
    assert redacted["PATH"] == "/bin"
    assert redacted["plain"] == "y"
    assert env["API_TOKEN"] == "s3cret"  # input untouched


def test_redact_text_masks_sensitive_pairs():
    assert models.redact_text("token=abc") == "token=<redacted>"
    assert models.redact_text("password: hunter2") == "password: <redacted>"
    assert models.redact_text('"api_key": "xyz"') == '"api_key": <redacted>'


def test_redact_text_leaves_innocent_text_and_protected_vars():
    assert models.redact_text("WINEPREFIX=/data/win") == "WINEPREFIX=/data/win"
    assert models.redact_text("PATH=/bin:/usr/bin") == "PATH=/bin:/usr/bin"
    assert models.redact_text("installed in /home/user") == "installed in /home/user"


def test_redact_text_masks_embedded_token_in_argv_text():
    assert models.redact_text("cmd.exe,--token=abc") == "cmd.exe,--token=<redacted>"


# ------------------------------------------------------------------ logging


def test_event_log_writes_redacted_line(tmp_path):
    root = tmp_path / "logs"
    EventLog(root).log(
        "Windows-11",
        "run",
        argv=("cmd.exe", "--token=abc"),
        env={"API_TOKEN": "s3cret", "PATH": "/bin"},
    )
    date = datetime.now(timezone.utc).date().isoformat()
    text = (root / "Windows-11" / f"{date}.log").read_text(encoding="utf-8")
    assert "API_TOKEN=<redacted>" in text
    assert "--token=<redacted>" in text
    assert "PATH=/bin" in text
    assert "s3cret" not in text
    assert "--token=abc" not in text


def test_event_log_never_raises_on_io_failure(tmp_path, monkeypatch):
    log = EventLog(tmp_path / "logs")

    def _boom(*_args, **_kwargs):
        raise OSError("simulated io failure")

    monkeypatch.setattr("lsw.logging.Path.mkdir", _boom)
    log.log("Windows-11", "install", state="pending")  # must not raise


def test_event_log_drops_unsafe_instance_name(tmp_path):
    root = tmp_path / "logs"
    EventLog(root).log("../evil", "install", state="pending")  # must not raise
    assert not root.exists() or list(root.iterdir()) == []


def test_services_logs_install_event(isolated_roots, tmp_path):
    root = tmp_path / "logs"
    services = Services(
        repo=InstanceRepository(isolated_roots),
        backend=FakeBackend(),
        roots=isolated_roots,
        log=EventLog(root),
    )
    services.install("Windows-11")
    date = datetime.now(timezone.utc).date().isoformat()
    text = (root / "Windows-11" / f"{date}.log").read_text(encoding="utf-8")
    assert "install" in text
    assert "Windows-11" in text


# ---------------------------------------- runtime dosdevices enforcement (M5.1)


def test_run_removes_recreated_host_mount_link_before_launch(isolated_roots):
    dosdevices = _make_dosdevices(isolated_roots)
    runner = _FakeRunner()
    backend = _backend(isolated_roots, runner)
    backend.initialize(_inst())
    (dosdevices / "d:").symlink_to("/mnt/c")  # recreated after install
    backend.run(_inst(), ("cmd.exe",), RunOptions(argv=("cmd.exe",)))
    assert not (dosdevices / "d:").exists()  # removed before the executable starts
    assert runner.calls[-1]["argv"] == ["/fake/wine", "cmd.exe"]


def test_run_removes_recreated_root_mapping_when_map_root_false(isolated_roots):
    dosdevices = _make_dosdevices(isolated_roots)
    backend = _backend(isolated_roots)
    backend.initialize(_inst())
    (dosdevices / "z:").symlink_to("/")  # host root recreated after install
    backend.run(_inst(), ("cmd.exe",), RunOptions(argv=("cmd.exe",)))
    assert not (dosdevices / "z:").exists()


def test_run_keeps_z_when_map_root_true_but_still_removes_mnt(isolated_roots):
    dosdevices = _make_dosdevices(isolated_roots)
    inst = replace(_inst(), filesystem_policy=FilesystemPolicy(map_root=True))
    backend = _backend(isolated_roots)
    backend.initialize(inst)
    (dosdevices / "z:").symlink_to("/")
    (dosdevices / "d:").symlink_to("/mnt/c")
    backend.run(inst, ("cmd.exe",), RunOptions(argv=("cmd.exe",)))
    assert (dosdevices / "z:").is_symlink()  # explicit opt-in honored
    assert not (dosdevices / "d:").exists()  # /mnt still default-deny


def test_run_removes_host_mount_link_reached_through_chain(isolated_roots, tmp_path):
    dosdevices = _make_dosdevices(isolated_roots)
    backend = _backend(isolated_roots)
    backend.initialize(_inst())
    hop = tmp_path / "hop"
    hop.symlink_to("/mnt/c")
    (dosdevices / "d:").symlink_to(hop)  # d: -> hop -> /mnt/c
    backend.run(_inst(), ("cmd.exe",), RunOptions(argv=("cmd.exe",)))
    assert not (dosdevices / "d:").exists()
    assert hop.is_symlink()  # only the dosdevices link was removed


def test_run_ignores_symlinked_dosdevices_outside_prefix(isolated_roots, tmp_path):
    prefix = _prefix(isolated_roots)
    prefix.mkdir(parents=True, exist_ok=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "c:").symlink_to("/mnt/c")
    (prefix / "dosdevices").symlink_to(outside)
    runner = _FakeRunner()
    backend = _backend(isolated_roots, runner)
    backend.run(_inst(), ("cmd.exe",), RunOptions(argv=("cmd.exe",)))
    assert (outside / "c:").is_symlink()  # external tree never touched
    assert runner.calls[-1]["argv"] == ["/fake/wine", "cmd.exe"]


def test_run_does_not_mutate_safe_custom_drives(isolated_roots):
    dosdevices = _make_dosdevices(isolated_roots)
    (dosdevices / "x:").symlink_to("../drive_c")
    backend = _backend(isolated_roots)
    backend.initialize(_inst())
    assert (dosdevices / "x:").is_symlink()
    backend.run(_inst(), ("cmd.exe",), RunOptions(argv=("cmd.exe",)))
    assert (dosdevices / "x:").is_symlink()  # safe mapping untouched
    assert (dosdevices / "c:").is_symlink()


def test_run_without_dosdevices_proceeds_unmodified(isolated_roots):
    prefix = _prefix(isolated_roots)
    prefix.mkdir(parents=True, exist_ok=True)
    runner = _FakeRunner()
    backend = _backend(isolated_roots, runner)
    backend.run(_inst(), ("cmd.exe",), RunOptions(argv=("cmd.exe",)))
    assert runner.calls[-1]["argv"] == ["/fake/wine", "cmd.exe"]
    assert not (prefix / "dosdevices").exists()  # nothing created at run time


# ---------------------------------------------- inherited env policy (M5.1)


def test_wine_env_forwards_only_curated_vars(isolated_roots, monkeypatch):
    for var, value in {
        "GITHUB_TOKEN": "ghs_secret",
        "AWS_ACCESS_KEY_ID": "AKIAEXAMPLE",
        "DATABASE_PASSWORD": "hunter2",
        "SSH_AUTH_SOCK": "/run/user/1000/ssh-agent.sock",
        "SOME_RANDOM_BUILD_VAR": "leftover",
        "PATH": "/usr/bin:/bin",
        "HOME": "/home/user",
        "DISPLAY": ":0",
        "LANG": "en_US.UTF-8",
        "XAUTHORITY": "/home/user/.Xauthority",
    }.items():
        monkeypatch.setenv(var, value)
    runner = _FakeRunner()
    backend = _backend(isolated_roots, runner)
    backend.run(_inst(), ("cmd.exe",), RunOptions(argv=("cmd.exe",)))
    env = runner.calls[-1]["env"]
    assert env["PATH"] == "/usr/bin:/bin"
    assert env["HOME"] == "/home/user"
    assert env["DISPLAY"] == ":0"
    assert env["LANG"] == "en_US.UTF-8"
    assert env["XAUTHORITY"] == "/home/user/.Xauthority"
    assert env["WINEPREFIX"] == str(_prefix(isolated_roots))
    assert env["WINEARCH"] == "win64"
    for secret in (
        "GITHUB_TOKEN",
        "AWS_ACCESS_KEY_ID",
        "DATABASE_PASSWORD",
        "SSH_AUTH_SOCK",
        "SOME_RANDOM_BUILD_VAR",
    ):
        assert secret not in env


def test_probe_env_is_minimal_and_secret_free(isolated_roots, monkeypatch):
    monkeypatch.setenv("SECRET_TOKEN", "abc")
    monkeypatch.setenv("DISPLAY", ":0")
    runner = _FakeRunner()
    backend = _backend(isolated_roots, runner)
    backend.probe()
    env = runner.calls[0]["env"]
    assert "SECRET_TOKEN" not in env
    assert env.get("DISPLAY") == ":0"
    assert "WINEPREFIX" not in env


def test_run_options_environment_is_intentional_caller_provided(isolated_roots):
    runner = _FakeRunner()
    backend = _backend(isolated_roots, runner)
    backend.run(
        _inst(),
        ("cmd.exe",),
        RunOptions(argv=("cmd.exe",), environment={"MY_TOKEN": "caller-provided"}),
    )
    assert runner.calls[-1]["env"]["MY_TOKEN"] == "caller-provided"


def test_sensitive_env_var_detector_covers_named_classes():
    for secret in (
        "GITHUB_TOKEN",
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "DATABASE_PASSWORD",
        "API_PASSWD",
        "GOOGLE_APPLICATION_CREDENTIALS",
        "SSH_AUTH_SOCK",
        "GPG_AGENT_INFO",
        "SESSION_COOKIE",
        "CLIENT_SECRET",
    ):
        assert _is_sensitive_env_var(secret), secret
    for benign in ("PATH", "HOME", "DISPLAY", "LANG", "LC_ALL", "TERM", "USER", "HOSTNAME"):
        assert not _is_sensitive_env_var(benign), benign

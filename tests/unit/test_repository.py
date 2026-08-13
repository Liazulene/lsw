"""Tests for the instance repository: layout, metadata, locks, safety."""

from __future__ import annotations

import os
import threading

import pytest

from lsw import paths
from lsw.errors import (
    ConfigurationError,
    InstanceAlreadyExistsError,
    InstanceBusyError,
    InstanceCorruptError,
    InstanceNotFoundError,
    InvalidInstanceNameError,
    OperationError,
    UnsafePathError,
)
from lsw.locking import exclusive_lock
from lsw.models import BackendKind, InstallState
from lsw.repository import InstanceRepository


@pytest.fixture
def repo(isolated_roots) -> InstanceRepository:
    return InstanceRepository(isolated_roots)


def _read_toml_text(repo: InstanceRepository, name: str) -> str:
    return (repo.instances_dir / name / "instance.toml").read_text(encoding="utf-8")


def test_create_and_get_roundtrip(repo):
    inst = repo.create("Windows-11")
    assert inst.name == "Windows-11"
    assert inst.version == 1
    assert inst.backend is BackendKind.WINE
    assert inst.install_state is InstallState.PENDING
    assert inst.prefix_directory == "prefix"
    assert inst.id.version == 4
    assert inst.created_at.tzinfo is not None
    got = repo.get("Windows-11")
    assert got == inst
    assert (repo.instances_dir / "Windows-11" / "prefix").is_dir()
    assert (repo.instances_dir / "Windows-11" / "locks").is_dir()


def test_create_metadata_schema_keys(repo):
    repo.create("Windows-11")
    text = _read_toml_text(repo, "Windows-11")
    assert "schema_version = 1" in text
    assert 'state = "pending"' in text
    assert 'backend = "wine"' in text


def test_create_twice_same_name_conflicts(repo):
    repo.create("Windows-11")
    with pytest.raises(InstanceAlreadyExistsError):
        repo.create("Windows-11")


def test_create_fake_backend_with_labels(repo):
    inst = repo.create(
        "Windows-XP", backend=BackendKind.FAKE, labels={"profile": "windows-xp-like"}
    )
    assert inst.backend is BackendKind.FAKE
    assert inst.labels == {"profile": "windows-xp-like"}
    assert repo.get("Windows-XP").labels == {"profile": "windows-xp-like"}


def test_get_missing_raises_not_found(repo):
    with pytest.raises(InstanceNotFoundError):
        repo.get("Nope")


def test_get_invalid_name_rejected_before_fs(repo):
    for bad in ("../escape", "/tmp/escape", ".", "..", "a b"):
        with pytest.raises(InvalidInstanceNameError):
            repo.get(bad)


def test_create_invalid_name_rejected_before_fs(repo):
    for bad in ("../escape", "/tmp/escape", ".", "..", "a b"):
        with pytest.raises(InvalidInstanceNameError):
            repo.create(bad)


def test_exists(repo):
    assert not repo.exists("Windows-11")
    repo.create("Windows-11")
    assert repo.exists("Windows-11")
    assert not repo.exists("../escape")


def test_list_empty(repo):
    listing = repo.list()
    assert listing.instances == ()
    assert listing.corrupt == ()


def test_list_returns_sorted_instances(repo):
    repo.create("Windows-XP")
    repo.create("Windows-11")
    names = [inst.name for inst in repo.list().instances]
    assert names == ["Windows-11", "Windows-XP"]


def test_list_marks_corrupt_metadata_not_fatal(repo):
    repo.create("Windows-11")
    repo.create("Windows-XP")
    broken = repo.instances_dir / "Windows-11" / "instance.toml"
    broken.write_text("this is not [ valid toml", encoding="utf-8")
    listing = repo.list()
    assert [inst.name for inst in listing.instances] == ["Windows-XP"]
    assert [c.name for c in listing.corrupt] == ["Windows-11"]


def test_list_marks_dir_without_metadata_corrupt(repo):
    (repo.instances_dir / "empty").mkdir(parents=True)
    listing = repo.list()
    assert listing.instances == ()
    assert [c.name for c in listing.corrupt] == ["empty"]


def test_get_corrupt_raises(repo):
    repo.create("Windows-11")
    (repo.instances_dir / "Windows-11" / "instance.toml").write_text(
        "garbage = [", encoding="utf-8"
    )
    with pytest.raises(InstanceCorruptError):
        repo.get("Windows-11")


def test_metadata_name_mismatch_is_corrupt(repo):
    repo.create("Windows-11")
    text = _read_toml_text(repo, "Windows-11").replace('name = "Windows-11"', 'name = "Other"')
    (repo.instances_dir / "Windows-11" / "instance.toml").write_text(text, encoding="utf-8")
    with pytest.raises(InstanceCorruptError):
        repo.get("Windows-11")


def test_delete_removes_instance(repo):
    repo.create("Windows-11")
    repo.delete("Windows-11")
    assert not repo.exists("Windows-11")
    with pytest.raises(InstanceNotFoundError):
        repo.get("Windows-11")


def test_delete_missing_raises(repo):
    with pytest.raises(InstanceNotFoundError):
        repo.delete("Nope")


def test_delete_invalid_name_rejected(repo):
    with pytest.raises(InvalidInstanceNameError):
        repo.delete("..")


def test_delete_symlinked_instance_refused(repo, tmp_path):
    repo.create("Windows-11")
    outside = tmp_path / "outside"
    outside.mkdir()
    os.symlink(outside, repo.instances_dir / "evil")
    with pytest.raises(UnsafePathError):
        repo.delete("evil")
    assert outside.is_dir()  # untouched


def test_delete_cleans_up_tombstone(repo):
    repo.create("Windows-11")
    repo.delete("Windows-11")
    tombstones = repo.instances_dir.parent / "tombstones"
    leftovers = list(tombstones.glob("Windows-11.*")) if tombstones.is_dir() else []
    assert leftovers == []


def test_delete_keeps_tombstone_when_cleanup_fails(repo, monkeypatch):

    repo.create("Windows-11")

    def boom(*args, **kwargs):
        raise OSError("模拟 IO 错误")

    monkeypatch.setattr("lsw.repository.shutil.rmtree", boom)
    with pytest.raises(OperationError):
        repo.delete("Windows-11")
    # 实例不再列出，但数据保留在可恢复的 tombstone 中
    assert not repo.exists("Windows-11")
    tombstones = repo.instances_dir.parent / "tombstones"
    assert list(tombstones.glob("Windows-11.*"))


def test_create_over_precreated_symlink_refused(repo, tmp_path):
    repo.instances_dir.mkdir(parents=True, exist_ok=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    os.symlink(outside, repo.instances_dir / "foo")
    with pytest.raises(UnsafePathError):
        repo.create("foo")


def test_busy_lock_blocks_create(repo):
    lock = repo.instances_dir / ".locks" / "Windows-11.lock"
    with exclusive_lock(lock, blocking=False):
        with pytest.raises(InstanceBusyError):
            repo.create("Windows-11")


def test_parallel_create_same_name_single_winner(repo):
    outcomes: list[str] = []
    guard = threading.Lock()
    barrier = threading.Barrier(2)

    def worker() -> None:
        try:
            repo.create("Windows-11")
            outcome = "ok"
        except (InstanceAlreadyExistsError, InstanceBusyError):
            outcome = "lost"
        except Exception as exc:  # noqa: BLE001 - record unexpected failures
            outcome = f"other:{type(exc).__name__}"
        barrier.wait()
        with guard:
            outcomes.append(outcome)

    t1 = threading.Thread(target=worker)
    t2 = threading.Thread(target=worker)
    t1.start()
    t2.start()
    t1.join()
    t2.join()
    assert outcomes.count("ok") == 1
    assert all(outcome in ("ok", "lost") for outcome in outcomes)


def test_update_install_state(repo):
    inst = repo.create("Windows-11")
    updated = repo.update_install_state("Windows-11", InstallState.INSTALLED)
    assert updated.install_state is InstallState.INSTALLED
    assert updated.updated_at >= inst.updated_at
    assert repo.get("Windows-11").install_state is InstallState.INSTALLED
    assert 'state = "installed"' in _read_toml_text(repo, "Windows-11")


def test_installed_to_pending_transition_rejected(repo):
    repo.create("Windows-11")
    repo.update_install_state("Windows-11", InstallState.INSTALLED)
    with pytest.raises(ConfigurationError):
        repo.update_install_state("Windows-11", InstallState.PENDING)


def test_failed_retry_transitions_allowed(repo):
    repo.create("Windows-11")
    repo.update_install_state("Windows-11", InstallState.FAILED)
    repo.update_install_state("Windows-11", InstallState.PENDING)
    assert repo.get("Windows-11").install_state is InstallState.PENDING


def test_set_default_requires_installed(repo):
    repo.create("Windows-11")
    with pytest.raises(ConfigurationError):
        repo.set_default("Windows-11")
    repo.update_install_state("Windows-11", InstallState.INSTALLED)
    repo.set_default("Windows-11")
    assert repo.get_default() == "Windows-11"


def test_clear_default(repo):
    repo.create("Windows-11")
    repo.update_install_state("Windows-11", InstallState.INSTALLED)
    repo.set_default("Windows-11")
    repo.clear_default()
    assert repo.get_default() is None


def test_set_default_missing_instance(repo):
    with pytest.raises(InstanceNotFoundError):
        repo.set_default("Nope")


def test_default_instance_persisted_in_config(repo):
    repo.create("Windows-11")
    repo.update_install_state("Windows-11", InstallState.INSTALLED)
    repo.set_default("Windows-11")
    text = paths.config_file().read_text(encoding="utf-8")
    assert 'default_instance = "Windows-11"' in text


def test_prefix_path_derived(repo):
    repo.create("Windows-11")
    expected = repo.instances_dir / "Windows-11" / "prefix"
    assert repo.prefix_path("Windows-11") == expected
    assert expected.is_dir()


def test_atomic_write_leaves_no_temp_files(repo):
    repo.create("Windows-11")
    leftovers = [
        p.name
        for p in (repo.instances_dir / "Windows-11").iterdir()
        if p.name.startswith(".instance.toml.tmp")
    ]
    assert leftovers == []

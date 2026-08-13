"""Tests for the application service layer (use cases)."""

from __future__ import annotations

import pytest

from lsw.backends import FakeBackend
from lsw.errors import (
    ConfirmationRequiredError,
    DependencyMissingError,
    InstanceBusyError,
    OperationError,
)
from lsw.models import InstallState, InstanceState, RunOptions
from lsw.repository import InstanceRepository
from lsw.services import Services


@pytest.fixture
def svc(isolated_roots):
    backend = FakeBackend()
    return Services(repo=InstanceRepository(isolated_roots), backend=backend, roots=isolated_roots)


def test_install_success_flips_to_installed(svc):
    inst = svc.install("Windows-11")
    assert inst.install_state is InstallState.INSTALLED


def test_install_unavailable_backend_raises_without_creating(svc):
    svc.backend.available = False
    with pytest.raises(DependencyMissingError):
        svc.install("Windows-11")
    assert not svc.repo.exists("Windows-11")


def test_install_initialize_failure_marks_failed(svc):
    svc.backend.initialize_error = "boom"
    with pytest.raises(OperationError):
        svc.install("Windows-11")
    assert svc.repo.get("Windows-11").install_state is InstallState.FAILED


def test_run_passes_through_exit_code(svc):
    svc.install("Windows-11")
    svc.backend.run_exit_code = 42
    code = svc.run(
        "Windows-11", ("cmd.exe", "/c", "ver"), RunOptions(argv=("cmd.exe", "/c", "ver"))
    )
    assert code == 42


def test_run_unknown_instance_raises(svc):
    with pytest.raises(Exception) as exc:
        svc.run("Nope", ("cmd.exe",), RunOptions(argv=("cmd.exe",)))
    from lsw.errors import InstanceNotFoundError

    assert isinstance(exc.value, InstanceNotFoundError)


def test_terminate_reports_previous_state(svc):
    svc.install("Windows-11")
    svc.backend.running.add("Windows-11")
    result = svc.terminate("Windows-11", 15.0)
    assert result.previous_state is InstanceState.RUNNING
    assert svc.terminate("Windows-11", 15.0).previous_state is InstanceState.STOPPED


def test_unregister_refuses_running(svc):
    svc.install("Windows-11")
    svc.backend.running.add("Windows-11")
    with pytest.raises(InstanceBusyError):
        svc.unregister("Windows-11", confirmed=True)
    assert svc.repo.exists("Windows-11")


def test_unregister_requires_confirmation(svc):
    svc.install("Windows-11")
    with pytest.raises(ConfirmationRequiredError):
        svc.unregister("Windows-11", confirmed=False)
    assert svc.repo.exists("Windows-11")


def test_unregister_clears_default_when_default(svc):
    svc.install("Windows-11")
    svc.repo.set_default("Windows-11")
    svc.unregister("Windows-11", confirmed=True)
    assert svc.repo.get_default() is None


def test_shutdown_aggregates_failures(svc):
    svc.install("Windows-11")
    svc.install("Windows-XP")
    svc.backend.shutdown_failures = {"Windows-XP"}
    result = svc.shutdown(15.0)
    assert result.terminated == ("Windows-11",)
    assert [name for name, _ in result.failed] == ["Windows-XP"]


def test_list_probe_failure_shows_unknown(svc):
    svc.install("Windows-11")
    svc.backend.available = False
    result = svc.list_instances()
    assert result.instances[0].runtime_state is InstanceState.UNKNOWN


def test_list_marks_default(svc):
    svc.install("Windows-11")
    svc.repo.set_default("Windows-11")
    result = svc.list_instances()
    assert result.default == "Windows-11"
    assert result.instances[0].is_default is True


def test_set_version_only_supports_one(svc):
    svc.install("Windows-11")
    svc.set_version("Windows-11", 1)
    with pytest.raises(OperationError):
        svc.set_version("Windows-11", 2)


def test_status_info(svc):
    svc.install("Windows-11")
    info = svc.status()
    assert info.instance_count == 1
    assert info.corrupt_count == 0
    assert info.backend.available is True
    assert info.default_instance is None


# ------------------------------------------------------------- M4 additions


class _TimeoutBackend(FakeBackend):
    """Backend whose initialize fails by timeout, to pin the exit-code mapping."""

    def initialize(self, instance):
        self._record(f"initialize:{instance.name}")
        from lsw.errors import OperationTimeoutError

        raise OperationTimeoutError("模拟初始化超时")


def test_install_timeout_is_preserved_not_wrapped(isolated_roots):
    from lsw.errors import OperationTimeoutError

    svc = Services(
        repo=InstanceRepository(isolated_roots),
        backend=_TimeoutBackend(),
        roots=isolated_roots,
    )
    with pytest.raises(OperationTimeoutError):
        svc.install("Windows-11")
    assert svc.repo.get("Windows-11").install_state is InstallState.FAILED


def test_runtime_state_maps_pending_to_installing(isolated_roots):
    svc = Services(
        repo=InstanceRepository(isolated_roots), backend=FakeBackend(), roots=isolated_roots
    )
    svc.repo.create("Windows-11")
    result = svc.list_instances()
    assert result.instances[0].runtime_state is InstanceState.INSTALLING


def test_runtime_state_maps_failed_to_broken(isolated_roots):
    svc = Services(
        repo=InstanceRepository(isolated_roots), backend=FakeBackend(), roots=isolated_roots
    )
    svc.repo.create("Windows-11")
    svc.repo.update_install_state("Windows-11", InstallState.FAILED)
    result = svc.list_instances()
    assert result.instances[0].runtime_state is InstanceState.BROKEN


def test_install_uses_default_arch(isolated_roots):
    svc = Services(
        repo=InstanceRepository(isolated_roots),
        backend=FakeBackend(),
        roots=isolated_roots,
        default_arch="win64",
    )
    svc.install("Windows-11")
    assert svc.repo.get("Windows-11").runtime_config.arch == "win64"

"""Unit tests for the FakeBackend used across the M3 test suite."""

from __future__ import annotations

import pytest

from lsw.backends import FakeBackend, create_backend
from lsw.errors import BackendError, ConfigurationError, DependencyMissingError
from lsw.models import BackendKind, InstanceState, RunOptions


@pytest.fixture
def fake() -> FakeBackend:
    return FakeBackend()


def test_probe_reports_available_and_version(fake):
    info = fake.probe()
    assert info.backend is BackendKind.FAKE
    assert info.available is True
    assert info.version == "fake-wine-9.0"


def test_probe_unavailable_has_diagnostics(fake):
    fake.available = False
    info = fake.probe()
    assert info.available is False
    assert info.diagnostics


def test_initialize_records_and_clears_state(fake):
    fake.initialize_error = "boom"
    with pytest.raises(BackendError):
        fake.initialize(_inst("Windows-11"))
    assert fake.calls == ["initialize:Windows-11"]


def test_status_from_running_set(fake):
    fake.running.add("Windows-11")
    assert fake.status(_inst("Windows-11")) is InstanceState.RUNNING
    assert fake.status(_inst("Windows-XP")) is InstanceState.STOPPED


def test_status_override_wins(fake):
    fake.status_override = InstanceState.BROKEN
    assert fake.status(_inst("Windows-11")) is InstanceState.BROKEN


def test_run_records_argv_and_marks_running(fake):
    fake.run_exit_code = 5
    code = fake.run(
        _inst("Windows-11"), ("cmd.exe", "/c", "ver"), _options(("cmd.exe", "/c", "ver"))
    )
    assert code == 5
    assert "run:Windows-11:cmd.exe /c ver" in fake.calls
    assert "Windows-11" in fake.running


def test_run_error_raises_backend_error(fake):
    fake.run_error = "无法启动"
    with pytest.raises(BackendError):
        fake.run(_inst("Windows-11"), ("cmd.exe",), _options(("cmd.exe",)))


def test_unavailable_backend_raises_dependency(fake):
    fake.available = False
    with pytest.raises(DependencyMissingError):
        fake.status(_inst("Windows-11"))
    with pytest.raises(DependencyMissingError):
        fake.run(_inst("Windows-11"), ("cmd.exe",), _options(("cmd.exe",)))


def test_terminate_reports_previous_state(fake):
    fake.running.add("Windows-11")
    assert fake.terminate(_inst("Windows-11"), 15.0).previous_state is InstanceState.RUNNING
    assert fake.terminate(_inst("Windows-11"), 15.0).previous_state is InstanceState.STOPPED


def test_shutdown_all_partial_failures(fake):
    fake.shutdown_failures = {"Windows-XP"}
    result = fake.shutdown_all([_inst("Windows-11"), _inst("Windows-XP")], 15.0)
    assert result.terminated == ("Windows-11",)
    assert [name for name, _ in result.failed] == ["Windows-XP"]


def test_create_backend_registry():
    assert isinstance(create_backend("fake", {}), FakeBackend)
    with pytest.raises(ConfigurationError):
        create_backend("bogus", {})


def _inst(name: str):
    import uuid
    from datetime import datetime, timezone

    from lsw.models import InstallState, Instance

    return Instance(
        id=uuid.uuid4(),
        name=name,
        version=1,
        backend=BackendKind.WINE,
        install_state=InstallState.INSTALLED,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )


def _options(argv):
    return RunOptions(argv=tuple(argv))

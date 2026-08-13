"""Tests for core domain models."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest

from lsw import models
from lsw.models import BackendKind, InstallState, InstanceState


def _instance(**overrides) -> models.Instance:
    base = {
        "id": uuid.uuid4(),
        "name": "Windows-11",
        "version": 1,
        "backend": BackendKind.WINE,
        "install_state": InstallState.PENDING,
        "created_at": datetime.now(timezone.utc),
        "updated_at": datetime.now(timezone.utc),
    }
    base.update(overrides)
    return models.Instance(**base)


def test_instance_defaults_are_safe():
    inst = _instance()
    assert inst.runtime_config.arch == "win64"
    assert inst.runtime_config.initialized_with_wine_version is None
    assert inst.filesystem_policy.map_home is False
    assert inst.filesystem_policy.map_root is False
    assert inst.filesystem_policy.map_current_directory is True
    assert inst.labels == {}


def test_instance_rejects_non_v4_uuid():
    with pytest.raises(ValueError):
        _instance(id=uuid.uuid1())


def test_instance_rejects_version_zero():
    with pytest.raises(ValueError):
        _instance(version=0)


def test_backend_kind_values():
    assert BackendKind.WINE.value == "wine"
    assert BackendKind.FAKE.value == "fake"


def test_install_state_and_runtime_state_are_distinct():
    assert InstallState.INSTALLED.value == "installed"
    assert InstanceState.RUNNING.value == "Running"
    assert InstanceState.STOPPED.value == "Stopped"
    assert InstanceState.BROKEN.value == "Broken"
    assert InstanceState.UNKNOWN.value == "Unknown"


def test_run_options_validation():
    with pytest.raises(ValueError):
        models.RunOptions(argv=())
    with pytest.raises(ValueError):
        models.RunOptions(argv=("cmd.exe",), timeout=0)


def test_run_options_defaults():
    opts = models.RunOptions(argv=("cmd.exe",))
    assert opts.cwd is None
    assert opts.environment == {}
    assert opts.interactive is False
    assert opts.timeout is None
    assert opts.capture is False


def test_command_result_sanitized_argv():
    result = models.CommandResult(
        argv=("wine", "--password=hunter2", "cmd.exe"),
        exit_code=7,
    )
    assert result.sanitized_argv == ("wine", "--password=<redacted>", "cmd.exe")


def test_instance_default_prefix_directory():
    assert _instance().prefix_directory == "prefix"


def test_instance_prefix_directory_validation():
    with pytest.raises(ValueError):
        _instance(prefix_directory="../escape")
    with pytest.raises(ValueError):
        _instance(prefix_directory="/abs")
    with pytest.raises(ValueError):
        _instance(prefix_directory="")
    with pytest.raises(ValueError):
        _instance(prefix_directory="a/b")


def test_redact_argument_handles_forms():
    assert models.redact_argument("--api_key=abc") == "--api_key=<redacted>"
    assert models.redact_argument("--secret-file=/etc/hosts") == "--secret-file=<redacted>"
    assert models.redact_argument("cmd.exe") == "cmd.exe"
    assert models.redact_argument("--=empty-name") == "--=empty-name"
    assert models.redact_argument("--token=") == "--token="  # empty value left alone

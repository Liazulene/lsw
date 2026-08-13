"""Real-Wine integration tests (M4, spec §16.3).

These exercise the Wine backend end-to-end and therefore require an actual Wine
installation. They auto-skip when Wine is absent — this host has none — so they
never fake success. Every prefix lives under per-test temp XDG roots and the
host user's ``~/.wine`` is never touched.
"""

from __future__ import annotations

import shutil

import pytest

from lsw import services
from lsw.models import InstanceState, RunOptions

if shutil.which("wine") is None:
    pytest.skip("未检测到 Wine：真实 Wine 集成测试跳过（不伪造成功）。", allow_module_level=True)

pytestmark = [pytest.mark.wine, pytest.mark.integration]


@pytest.fixture
def svc(isolated_roots) -> services.Services:
    return services.build_services(isolated_roots)


def test_install_creates_isolated_prefix_and_runs_cmd(svc):
    svc.install("Windows-11")
    code = svc.run(
        "Windows-11",
        ("cmd.exe", "/c", "exit 7"),
        RunOptions(argv=("cmd.exe", "/c", "exit 7")),
    )
    assert code == 7


def test_list_and_status_are_readable(svc):
    svc.install("Windows-11")
    listing = svc.list_instances()
    assert listing.instances[0].runtime_state in (InstanceState.STOPPED, InstanceState.RUNNING)
    info = svc.status()
    assert info.backend.available is True
    assert info.backend.version is not None


def test_terminate_reports_stopped(svc):
    svc.install("Windows-11")
    result = svc.terminate("Windows-11", 30.0)
    assert result.previous_state in (InstanceState.STOPPED, InstanceState.RUNNING)
    assert svc.list_instances().instances[0].runtime_state is InstanceState.STOPPED

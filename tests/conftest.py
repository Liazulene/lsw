"""Shared fixtures: every test gets isolated, ephemeral LSW roots."""

from __future__ import annotations

import pytest

from lsw import paths

_OVERRIDE_VARS = ("LSW_CONFIG_HOME", "LSW_DATA_HOME", "LSW_STATE_HOME", "LSW_CACHE_HOME")


def _xdg_for(lsv_var: str) -> str:
    return "XDG_" + lsv_var[len("LSW_") :]


@pytest.fixture
def isolated_roots(tmp_path, monkeypatch) -> paths.DataRoots:
    """Point all four LSW roots at fresh temp dirs and return the resolved roots."""
    for var in _OVERRIDE_VARS:
        monkeypatch.setenv(var, str(tmp_path / var.lower()))
        monkeypatch.delenv(_xdg_for(var), raising=False)
    return paths.data_roots()

"""Tests for XDG path resolution and environment overrides."""

from __future__ import annotations

import pytest

from lsw import paths
from lsw.errors import ConfigurationError

_ALL_LSW_VARS = ("LSW_CONFIG_HOME", "LSW_DATA_HOME", "LSW_STATE_HOME", "LSW_CACHE_HOME")
_ALL_XDG_VARS = ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME", "XDG_CACHE_HOME")


def _clear_all(monkeypatch) -> None:
    for var in _ALL_LSW_VARS + _ALL_XDG_VARS:
        monkeypatch.delenv(var, raising=False)


def test_defaults_without_any_env(monkeypatch):
    _clear_all(monkeypatch)
    roots = paths.data_roots()
    assert roots.config_home == paths.Path.home() / ".config" / "lsw"
    assert roots.data_home == paths.Path.home() / ".local" / "share" / "lsw"
    assert roots.state_home == paths.Path.home() / ".local" / "state" / "lsw"
    assert roots.cache_home == paths.Path.home() / ".cache" / "lsw"


def test_xdg_env_override(monkeypatch, tmp_path):
    _clear_all(monkeypatch)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg-data"))
    roots = paths.data_roots()
    assert roots.data_home == tmp_path / "xdg-data" / "lsw"
    assert roots.config_home == paths.Path.home() / ".config" / "lsw"


def test_lsw_env_wins_over_xdg(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg-data"))
    monkeypatch.setenv("LSW_DATA_HOME", str(tmp_path / "lsw-data"))
    roots = paths.data_roots()
    assert roots.data_home == tmp_path / "lsw-data" / "lsw"


def test_relative_xdg_value_is_ignored(monkeypatch):
    _clear_all(monkeypatch)
    monkeypatch.setenv("XDG_STATE_HOME", "relative/path")
    roots = paths.data_roots()
    assert roots.state_home == paths.Path.home() / ".local" / "state" / "lsw"


def test_relative_lsw_value_is_rejected(monkeypatch):
    monkeypatch.setenv("LSW_CACHE_HOME", "relative/path")
    with pytest.raises(ConfigurationError):
        paths.data_roots()


def test_expanduser_in_lsw_override(monkeypatch):
    monkeypatch.setenv("LSW_DATA_HOME", "~/lsw-data")
    roots = paths.data_roots()
    assert roots.data_home == paths.Path.home() / "lsw-data" / "lsw"


def test_config_file_lives_under_config_home(monkeypatch, tmp_path):
    monkeypatch.setenv("LSW_CONFIG_HOME", str(tmp_path / "cfg"))
    assert paths.config_file() == tmp_path / "cfg" / "lsw" / "config.toml"


def test_roots_via_explicit_environ_dict(tmp_path):
    environ = {"LSW_CONFIG_HOME": str(tmp_path / "c"), "LSW_DATA_HOME": str(tmp_path / "d")}
    roots = paths.data_roots(environ)
    assert roots.config_home == tmp_path / "c" / "lsw"
    assert roots.data_home == tmp_path / "d" / "lsw"
    assert roots.state_home == paths.Path.home() / ".local" / "state" / "lsw"

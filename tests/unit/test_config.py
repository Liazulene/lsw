"""Tests for the global config loader: defaults, validation, unknown keys."""

from __future__ import annotations

import pytest

from lsw import config
from lsw.errors import ConfigurationError, InvalidInstanceNameError


def test_missing_file_returns_defaults(tmp_path):
    table = config.load_config(tmp_path / "nope.toml")
    assert table["schema_version"] == 1
    assert table["default_install_name"] == "Windows-11"
    assert table["backend"]["type"] == "wine"
    assert table["backend"]["shutdown_timeout_seconds"] == 15


def test_partial_file_merges_over_defaults(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('default_install_name = "Windows-XP"\n', encoding="utf-8")
    table = config.load_config(path)
    assert table["default_install_name"] == "Windows-XP"
    assert table["backend"]["arch"] == "win64"  # default preserved


def test_unknown_top_level_key_warns(tmp_path, capsys):
    path = tmp_path / "config.toml"
    path.write_text("unknown_top = 1\n", encoding="utf-8")
    config.load_config(path)
    captured = capsys.readouterr()
    assert "unknown_top" in captured.err
    assert "警告" in captured.err


def test_unknown_nested_key_warns_with_path(tmp_path, capsys):
    path = tmp_path / "config.toml"
    path.write_text('[backend]\nwinee_binary = "wine"\n', encoding="utf-8")
    config.load_config(path)
    captured = capsys.readouterr()
    assert "backend.winee_binary" in captured.err


def test_unsupported_schema_version_is_rejected(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text("schema_version = 2\n", encoding="utf-8")
    with pytest.raises(ConfigurationError):
        config.load_config(path)


def test_malformed_toml_is_rejected(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text("this is not [ valid toml\n", encoding="utf-8")
    with pytest.raises(ConfigurationError):
        config.load_config(path)


def test_schema_version_absent_keeps_default(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text("default_install_name = 'X'\n", encoding="utf-8")
    table = config.load_config(path)
    assert table["schema_version"] == 1


def test_load_via_resolved_default_path(isolated_roots):
    # No config file exists under the isolated config root -> defaults.
    table = config.load_config()
    assert table["schema_version"] == 1
    assert table["default_install_name"] == "Windows-11"


def test_custom_warn_callable_is_used(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text("mystery_key = true\n", encoding="utf-8")
    seen: list[str] = []
    config.load_config(path, warn=seen.append)
    assert seen and "mystery_key" in seen[0]


def test_default_instance_absent_by_default(tmp_path):
    assert config.get_default_instance(path=tmp_path / "nope.toml") is None


def test_set_and_get_default_instance(tmp_path):
    path = tmp_path / "config.toml"
    config.set_default_instance("Windows-11", path=path)
    assert config.get_default_instance(path=path) == "Windows-11"
    assert path.is_file()


def test_set_default_preserves_existing_keys(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('default_install_name = "Windows-XP"\n', encoding="utf-8")
    config.set_default_instance("Windows-11", path=path)
    table = config.load_config(path)
    assert table["default_install_name"] == "Windows-XP"
    assert table["default_instance"] == "Windows-11"


def test_set_default_is_idempotent(tmp_path):
    path = tmp_path / "config.toml"
    config.set_default_instance("X", path=path)
    before = path.read_text(encoding="utf-8")
    config.set_default_instance("X", path=path)
    assert path.read_text(encoding="utf-8") == before


def test_clear_default_instance(tmp_path):
    path = tmp_path / "config.toml"
    config.set_default_instance("X", path=path)
    config.clear_default_instance(path=path)
    assert config.get_default_instance(path=path) is None


def test_set_default_rejects_invalid_name(tmp_path):
    with pytest.raises(InvalidInstanceNameError):
        config.set_default_instance("../escape", path=tmp_path / "config.toml")


def test_set_default_rejects_unsupported_schema(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text("schema_version = 99\n", encoding="utf-8")
    with pytest.raises(ConfigurationError):
        config.set_default_instance("X", path=path)


def test_atomic_write_leaves_no_temp_files(tmp_path):
    path = tmp_path / "config.toml"
    config.set_default_instance("X", path=path)
    leftovers = [p.name for p in tmp_path.iterdir() if p.name.startswith(".config.toml.tmp")]
    assert leftovers == []

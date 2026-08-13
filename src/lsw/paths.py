"""XDG Base Directory resolution.

LSW keeps every piece of state inside four roots — config, data, state and
cache — following the XDG Base Directory specification. Each root may be
redirected for tests or exotic setups through an ``LSW_*`` environment
variable; otherwise the standard ``XDG_*`` variable or a per-user default is
used.

The four roots are the *lsw-specific* directories (``<xdg root>/lsw``), so all
LSW-managed writes stay under one of them. Nothing in this module creates a
directory.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from .errors import ConfigurationError

#: ``LSW_*`` variable that overrides each XDG root (tests/CI inject these).
_LSW_TO_XDG = {
    "LSW_CONFIG_HOME": "XDG_CONFIG_HOME",
    "LSW_DATA_HOME": "XDG_DATA_HOME",
    "LSW_STATE_HOME": "XDG_STATE_HOME",
    "LSW_CACHE_HOME": "XDG_CACHE_HOME",
}

_HOME_DEFAULTS: dict[str, Path] = {
    "XDG_CONFIG_HOME": Path.home() / ".config",
    "XDG_DATA_HOME": Path.home() / ".local" / "share",
    "XDG_STATE_HOME": Path.home() / ".local" / "state",
    "XDG_CACHE_HOME": Path.home() / ".cache",
}

#: Subdirectory under each XDG root that LSW owns exclusively.
_SUBDIR = "lsw"


@dataclass(frozen=True)
class DataRoots:
    """The four LSW-owned root directories (all end with ``/lsw``)."""

    config_home: Path
    data_home: Path
    state_home: Path
    cache_home: Path

    def as_dict(self) -> dict[str, Path]:
        return {
            "config": self.config_home,
            "data": self.data_home,
            "state": self.state_home,
            "cache": self.cache_home,
        }


def _resolve_base(lsv_var: str, xdg_var: str, environ: Mapping[str, str]) -> Path:
    for var in (lsv_var, xdg_var):
        value = environ.get(var)
        if not value:
            continue
        candidate = Path(value).expanduser()
        if not candidate.is_absolute():
            if var == lsv_var:
                raise ConfigurationError(f"{var} 必须是绝对路径（得到 {value!r}）。")
            continue  # XDG Base Directory: relative values are ignored
        return candidate
    return _HOME_DEFAULTS[xdg_var]


def data_roots(environ: Mapping[str, str] | None = None) -> DataRoots:
    """Resolve the four LSW roots from ``environ`` (default: ``os.environ``)."""
    env = os.environ if environ is None else environ
    bases = {xdg: _resolve_base(lsv, xdg, env) for lsv, xdg in _LSW_TO_XDG.items()}
    return DataRoots(
        config_home=bases["XDG_CONFIG_HOME"] / _SUBDIR,
        data_home=bases["XDG_DATA_HOME"] / _SUBDIR,
        state_home=bases["XDG_STATE_HOME"] / _SUBDIR,
        cache_home=bases["XDG_CACHE_HOME"] / _SUBDIR,
    )


def config_file(environ: Mapping[str, str] | None = None) -> Path:
    """Return the path of the global ``config.toml`` (may not exist yet)."""
    return data_roots(environ).config_home / "config.toml"

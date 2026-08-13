"""Instance-name validation.

A name must be usable as a single path segment and as a stable identifier in
configuration, logs and metadata. The grammar follows the task specification:

    ^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$
"""

from __future__ import annotations

import re
import unicodedata

from .errors import InvalidInstanceNameError

_INSTANCE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_MAX_LENGTH = 64
_ILLEGAL_SEPARATORS = "/\\"


def validate_instance_name(name: str) -> str:
    """Return *name* unchanged when valid, otherwise raise ``InvalidInstanceNameError``.

    Rejects empty strings, ``.`` / ``..``, path separators, control characters,
    surrounding whitespace, names that are too long, and anything outside the
    documented ``[A-Za-z0-9][A-Za-z0-9._-]*`` grammar.
    """
    if not isinstance(name, str) or not name:
        raise InvalidInstanceNameError("实例名不能为空。")
    if name != name.strip():
        raise InvalidInstanceNameError(f"实例名不能带有前后空白：{name!r}。")
    if any(char in _ILLEGAL_SEPARATORS for char in name):
        raise InvalidInstanceNameError(f"实例名不能包含路径分隔符：{name!r}。")
    if name in {".", ".."}:
        raise InvalidInstanceNameError(f"实例名不能是 {name!r}。")
    if any(unicodedata.category(char) == "Cc" for char in name):
        raise InvalidInstanceNameError("实例名不能包含控制字符。")
    if len(name) > _MAX_LENGTH:
        raise InvalidInstanceNameError(f"实例名最长 {_MAX_LENGTH} 个字符（得到 {len(name)} 个）。")
    if not _INSTANCE_NAME_RE.fullmatch(name):
        raise InvalidInstanceNameError(
            "实例名只能包含字母、数字、点、下划线和连字符，且必须以字母或数字开头。"
        )
    return name

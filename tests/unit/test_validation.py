"""Tests for instance-name validation boundaries."""

from __future__ import annotations

import pytest

from lsw.errors import InvalidInstanceNameError
from lsw.validation import validate_instance_name

VALID_NAMES = [
    "Windows-11",
    "Windows-XP",
    "a",
    "0",
    "123",
    "foo.bar_baz-1",
    "A" * 64,
]

INVALID_NAMES = [
    "",
    " ",
    ".",
    "..",
    "../escape",
    "/tmp/escape",
    "a/b",
    r"a\b",
    "a b",
    " name",
    "name ",
    "a\u0000b",
    "a\tb",
    "a\nb",
    "-lead",
    ".hidden",
    "名",
    "Windows™",
    "A" * 65,
]


@pytest.mark.parametrize("name", VALID_NAMES)
def test_valid_names_pass_unchanged(name):
    assert validate_instance_name(name) == name


@pytest.mark.parametrize("name", INVALID_NAMES)
def test_invalid_names_are_rejected(name):
    with pytest.raises(InvalidInstanceNameError):
        validate_instance_name(name)


def test_dot_and_dotdot_are_rejected_specifically():
    for name in (".", ".."):
        with pytest.raises(InvalidInstanceNameError):
            validate_instance_name(name)


def test_non_string_input_is_rejected():
    with pytest.raises(InvalidInstanceNameError):
        validate_instance_name(None)  # type: ignore[arg-type]

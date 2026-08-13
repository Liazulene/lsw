"""Tests for the flock-based locking primitive."""

from __future__ import annotations

import pytest

from lsw.locking import LockUnavailable, exclusive_lock


def test_exclusive_lock_contends_same_file(tmp_path):
    path = tmp_path / "a.lock"
    with exclusive_lock(path, blocking=False):
        with pytest.raises(LockUnavailable):
            with exclusive_lock(path, blocking=False):
                pass


def test_lock_is_released_after_block(tmp_path):
    path = tmp_path / "a.lock"
    with exclusive_lock(path, blocking=False):
        pass
    with exclusive_lock(path, blocking=False):
        pass  # re-acquirable once the first holder exited


def test_lock_creates_file_and_parents(tmp_path):
    path = tmp_path / "nested" / "b.lock"
    with exclusive_lock(path, blocking=False):
        assert path.is_file()

# -*- coding: utf-8 -*-
"""Unit tests for utils.atomic_io."""
from __future__ import annotations

import json
import os

import pytest

from qwenpaw.utils.atomic_io import (
    read_json_safe,
    write_bytes_atomic,
    write_json_atomic,
)


class TestWriteBytesAtomic:
    """write_bytes_atomic behavior."""

    def test_writes_content(self, tmp_path):
        path = tmp_path / "f.bin"
        write_bytes_atomic(path, b"hello")
        assert path.read_bytes() == b"hello"

    def test_no_tmp_leftover(self, tmp_path):
        path = tmp_path / "f.bin"
        write_bytes_atomic(path, b"hello")
        assert not list(tmp_path.glob("*.tmp.*"))

    def test_replaces_existing(self, tmp_path):
        path = tmp_path / "f.json"
        write_bytes_atomic(path, b'{"v": 1}')
        write_bytes_atomic(path, b'{"v": 2}')
        assert path.read_bytes() == b'{"v": 2}'

    def test_cleans_tmp_on_failure(self, tmp_path, monkeypatch):
        # Pre-existing content must survive a failed replace.
        path = tmp_path / "f.bin"
        path.write_bytes(b"original")

        def _boom(src, dst):
            raise OSError("replace failed")

        monkeypatch.setattr(os, "replace", _boom)
        with pytest.raises(OSError):
            write_bytes_atomic(path, b"new")
        assert path.read_bytes() == b"original"
        assert not list(tmp_path.glob("*.tmp.*"))

    def test_creates_parent_dir(self, tmp_path):
        path = tmp_path / "nested" / "dir" / "f.bin"
        write_bytes_atomic(path, b"x")
        assert path.read_bytes() == b"x"


class TestWriteJsonAtomic:
    """write_json_atomic behavior."""

    def test_roundtrip(self, tmp_path):
        path = tmp_path / "f.json"
        write_json_atomic(path, {"a": 1, "b": [2, 3]})
        assert json.loads(path.read_text(encoding="utf-8")) == {
            "a": 1,
            "b": [2, 3],
        }

    def test_non_ascii_not_escaped(self, tmp_path):
        path = tmp_path / "f.json"
        write_json_atomic(path, {"name": "龙虾"})
        assert path.read_text(encoding="utf-8") == '{\n  "name": "龙虾"\n}'

    def test_overwrites_existing(self, tmp_path):
        path = tmp_path / "f.json"
        write_json_atomic(path, {"v": 1})
        write_json_atomic(path, {"v": 2})
        assert json.loads(path.read_text(encoding="utf-8")) == {"v": 2}


class TestReadJsonSafe:
    """read_json_safe behavior with json_repair fallback."""

    def test_missing_file_returns_default(self, tmp_path):
        assert read_json_safe(tmp_path / "nope.json", default={"x": 1}) == {
            "x": 1,
        }

    def test_missing_file_default_none(self, tmp_path):
        assert read_json_safe(tmp_path / "nope.json") is None

    def test_valid_json(self, tmp_path):
        path = tmp_path / "f.json"
        path.write_text('{"a": 1}', encoding="utf-8")
        assert read_json_safe(path) == {"a": 1}

    def test_repairs_truncated_json(self, tmp_path):
        path = tmp_path / "f.json"
        path.write_text('{"a": 1', encoding="utf-8")  # truncated
        # json_repair recovers a usable object rather than raising
        result = read_json_safe(path)
        assert isinstance(result, dict)
        assert result.get("a") == 1

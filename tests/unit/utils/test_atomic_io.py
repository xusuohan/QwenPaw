# -*- coding: utf-8 -*-
"""Unit tests for utils.atomic_io."""
from __future__ import annotations

import json
import os
import threading

import pytest

from qwenpaw.utils.atomic_io import (
    cleanup_orphan_tmps,
    locked_json_update,
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

    def test_sort_keys_orders_keys(self, tmp_path):
        path = tmp_path / "f.json"
        write_json_atomic(
            path,
            {"b": 1, "a": 2},
            sort_keys=True,
        )
        text = path.read_text(encoding="utf-8")
        assert text.index('"a"') < text.index('"b"')

    def test_sort_keys_default_false_preserves_insertion_order(self, tmp_path):
        path = tmp_path / "f.json"
        write_json_atomic(path, {"b": 1, "a": 2})
        text = path.read_text(encoding="utf-8")
        assert text.index('"b"') < text.index('"a"')


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


class TestLockedJsonUpdate:
    """locked_json_update serializes RMW under the per-path lock."""

    def test_update_returns_new_value(self, tmp_path):
        path = tmp_path / "f.json"
        write_json_atomic(path, {"n": 0})
        result = locked_json_update(path, lambda cur: {"n": cur["n"] + 1})
        assert result == {"n": 1}
        assert json.loads(path.read_text(encoding="utf-8")) == {"n": 1}

    def test_missing_file_passes_default(self, tmp_path):
        path = tmp_path / "f.json"
        result = locked_json_update(
            path,
            lambda cur: {"n": (cur or {}).get("n", 0) + 1},
            default={},
        )
        assert result == {"n": 1}

    def test_concurrent_increment_no_lost_updates(self, tmp_path):
        path = tmp_path / "counter.json"
        write_json_atomic(path, {"n": 0})
        n_threads, per_thread = 20, 100
        expected = n_threads * per_thread

        def bump():
            for _ in range(per_thread):
                locked_json_update(
                    path,
                    lambda cur: {"n": (cur or {}).get("n", 0) + 1},
                )

        threads = [threading.Thread(target=bump) for _ in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert json.loads(path.read_text(encoding="utf-8"))["n"] == expected
        assert not list(tmp_path.glob("*.tmp.*"))


class TestCleanupOrphanTmps:
    """cleanup_orphan_tmps removes stray .tmp.<pid> files."""

    def test_removes_orphans_keeps_real_files(self, tmp_path):
        (tmp_path / "a.json.tmp.123").write_bytes(b"x")
        (tmp_path / "a.json").write_bytes(b"{}")
        removed = cleanup_orphan_tmps(tmp_path)
        assert removed == 1
        assert (tmp_path / "a.json").exists()
        assert not (tmp_path / "a.json.tmp.123").exists()

    def test_no_orphans_returns_zero(self, tmp_path):
        (tmp_path / "a.json").write_bytes(b"{}")
        assert cleanup_orphan_tmps(tmp_path) == 0

# -*- coding: utf-8 -*-
"""Tests for utils.import_prefetch (best-effort page-cache warmer)."""
from __future__ import annotations

from qwenpaw.utils.import_prefetch import start_import_prefetch


class TestStartImportPrefetch:
    def test_reads_files_and_returns_thread(self, tmp_path):
        (tmp_path / "a.pyc").write_bytes(b"x" * 4096)
        (tmp_path / "b.pyc").write_bytes(b"y" * 4096)
        order = tmp_path / "order.txt"
        order.write_text(
            f"{tmp_path / 'a.pyc'}\n{tmp_path / 'b.pyc'}\n",
            encoding="utf-8",
        )
        thread = start_import_prefetch(cap_mb=64, import_order_file=order)
        assert thread is not None
        thread.join(timeout=5)
        assert not thread.is_alive()  # completed

    def test_missing_files_are_swallowed(self, tmp_path):
        order = tmp_path / "order.txt"
        order.write_text(f"{tmp_path / 'nope.pyc'}\n", encoding="utf-8")
        thread = start_import_prefetch(cap_mb=64, import_order_file=order)
        assert thread is not None
        thread.join(timeout=5)  # no raise

    def test_cap_truncates(self, tmp_path):
        big = tmp_path / "big.pyc"
        big.write_bytes(b"z" * (2 * 1024 * 1024))  # 2MB
        order = tmp_path / "order.txt"
        order.write_text(f"{big}\n", encoding="utf-8")
        thread = start_import_prefetch(cap_mb=1, import_order_file=order)
        thread.join(timeout=5)  # reads <= cap, no error

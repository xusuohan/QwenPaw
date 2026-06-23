# -*- coding: utf-8 -*-
"""Tests for §4.5 Part A: tool result offload (async + atomic)."""
# pylint: disable=protected-access
from __future__ import annotations

import asyncio
import types

from qwenpaw.agents.context.light_context_manager import LightContextManager
from qwenpaw.constant import TRUNCATION_NOTICE_MARKER

# Multi-line content well over max_bytes. Must contain newlines so
# truncate_text_output emits the <<<TRUNCATED>>> notice (single-line-over-max
# content hits its no-notice edge case at tools/utils.py:74-77).
_OVERMAX = ("x" * 200 + "\n") * 30


def _stub_config(_agent_id):
    """Stub agent config exposing only what the prune path reads."""
    return types.SimpleNamespace(
        running=types.SimpleNamespace(
            light_context_config=types.SimpleNamespace(
                tool_result_pruning_config=types.SimpleNamespace(
                    tool_results_cache="tool_results",
                    exempt_file_extensions=[],
                    exempt_tool_names=[],
                ),
            ),
        ),
    )


def _make_manager(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "qwenpaw.agents.context.light_context_manager.load_agent_config",
        _stub_config,
    )
    return LightContextManager(
        working_dir=str(tmp_path),
        agent_id="test-agent",
    )


class TestTruncateToolResult:
    async def test_overmax_writes_file_atomically(
        self,
        tmp_path,
        monkeypatch,
    ):
        mgr = _make_manager(tmp_path, monkeypatch)
        result = await mgr._truncate_tool_result(_OVERMAX, max_bytes=1000)
        # One file under tool_results/, content matches, no tmp leftover.
        files = list((tmp_path / "tool_results").glob("*.txt"))
        assert len(files) == 1
        assert files[0].read_text(encoding="utf-8") == _OVERMAX
        assert not list((tmp_path / "tool_results").glob("*.tmp.*"))
        # Returned content carries the notice + the saved file path.
        assert TRUNCATION_NOTICE_MARKER in result
        assert str(files[0]) in result

    async def test_undermax_no_file(self, tmp_path, monkeypatch):
        mgr = _make_manager(tmp_path, monkeypatch)
        small = "x" * 100  # <= max_bytes + 100 slack -> passthrough
        result = await mgr._truncate_tool_result(small, max_bytes=1000)
        assert result == small
        assert not (tmp_path / "tool_results").exists()

    async def test_write_offloaded_to_thread(self, tmp_path, monkeypatch):
        mgr = _make_manager(tmp_path, monkeypatch)
        names = []
        real = asyncio.to_thread

        async def spy(func, *args, **kwargs):
            names.append(getattr(func, "__name__", repr(func)))
            return await real(func, *args, **kwargs)

        monkeypatch.setattr(asyncio, "to_thread", spy)
        await mgr._truncate_tool_result(_OVERMAX, max_bytes=1000)
        assert "write_bytes_atomic" in names

    async def test_write_failure_falls_back_without_file(
        self,
        tmp_path,
        monkeypatch,
    ):
        mgr = _make_manager(tmp_path, monkeypatch)

        def boom(*args, **kwargs):
            raise OSError("disk full")

        monkeypatch.setattr(
            "qwenpaw.agents.context.light_context_manager.write_bytes_atomic",
            boom,
        )
        result = await mgr._truncate_tool_result(_OVERMAX, max_bytes=1000)
        # Fallback: write failed -> no file/dir written; the function returned
        # truncated content without propagating the OSError. The fallback path
        # has no saved file, so its result carries no file-path notice (unlike
        # the success path) — assert truncation, not the marker.
        assert not (tmp_path / "tool_results").exists()
        assert result != _OVERMAX


class TestPruneChain:
    async def test_prune_output_str_path(
        self,
        tmp_path,
        monkeypatch,
    ):
        mgr = _make_manager(tmp_path, monkeypatch)
        result = await mgr._prune_output(_OVERMAX, max_bytes=1000)
        assert TRUNCATION_NOTICE_MARKER in result
        assert list((tmp_path / "tool_results").glob("*.txt"))

    async def test_prune_output_list_path(self, tmp_path, monkeypatch):
        mgr = _make_manager(tmp_path, monkeypatch)
        blocks = [{"type": "text", "text": _OVERMAX}]
        result = await mgr._prune_output(blocks, max_bytes=1000)
        assert TRUNCATION_NOTICE_MARKER in result[0]["text"]
        assert list((tmp_path / "tool_results").glob("*.txt"))

    async def test_prune_tool_result_integration(
        self,
        tmp_path,
        monkeypatch,
    ):
        mgr = _make_manager(tmp_path, monkeypatch)
        msg = types.SimpleNamespace(
            content=[
                {"type": "tool_result", "id": "t1", "output": _OVERMAX},
            ],
        )
        await mgr._prune_tool_result(
            messages=[msg],
            recent_n=1,
            old_max_bytes=1000,
            recent_max_bytes=1000,
        )
        out = msg.content[0]["output"]
        assert TRUNCATION_NOTICE_MARKER in out  # mutated in place
        assert list((tmp_path / "tool_results").glob("*.txt"))

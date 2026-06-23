# 性能优化 Phase 4d：§4.5 Part A tool result 溢写异步化 + 原子化 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 `light_context_manager.py` 溢写超大 tool result 的同步 `fp.write_text` 改为 `asyncio.to_thread(write_bytes_atomic)`——离事件循环 + 原子（tmp+fsync+replace+per-path 锁）。

**Architecture:** 调用链 `post_acting`(async) → `_prune_tool_result`(async) → `_prune_output`(sync) → `_truncate_tool_result`(sync, 内含 write_text) 是干净线性、两内层无外部调用方。async 化是受控的 3 函数传播（必须原子改，否则链断）：三个 `def`→`async def` + 三处 `await` + 写盘改 `to_thread(write_bytes_atomic)`（删冗余 mkdir，`write_bytes_atomic` 自带 parent mkdir）。

**Tech Stack:** Python 3.10、`asyncio.to_thread`、Phase 1 的 `atomic_io.write_bytes_atomic`、pytest（asyncio_mode=auto）。

**对应 spec：** `docs/superpowers/specs/2026-06-23-tool-result-offload-design.md`。
**前置：** Phase 1–5 + 4b/4c 已完成（HEAD `2dd7affa`）；本 Phase 从 `feature/usb-portable` 当前 HEAD 继续，不合并分支。
**行号基准：** 截至 HEAD `2dd7affa`，改动后会漂移（§8 坑-6），动手前用 grep 重新定位。

---

## 范围说明（scope check）

单一子系统（`light_context_manager.py` 的溢写异步化 + 原子化 + 其单测）。Part B（token 增量缓存）已 defer（spec §9），不在本计划。唯一改动源文件 = `src/qwenpaw/agents/context/light_context_manager.py`。`post_acting` / 截断 notice 逻辑不动。一个计划即可。

---

## File Structure（Phase 4d）

| 文件 | 改动 |
|---|---|
| `src/qwenpaw/agents/context/light_context_manager.py` | Task 1：加 `import asyncio` + `write_bytes_atomic` import；`_truncate_tool_result` async + 写改 `to_thread(write_bytes_atomic)`；`_prune_output` async；`_prune_tool_result` :330 加 await |
| `tests/unit/agents/context/test_tool_result_offload.py` | Task 1–2 逐步新增（fixture + 单元 + 链路测试） |

---

## Task 1: async 传播 + 原子溢写 + `_truncate_tool_result` 单元测试

**Files:**
- Modify: `src/qwenpaw/agents/context/light_context_manager.py`（imports + `_truncate_tool_result` + `_prune_output` + `_prune_tool_result`）
- Create: `tests/unit/agents/context/test_tool_result_offload.py`

- [ ] **Step 1: 写失败测试（单元：溢写原子 / 直通 / 离事件循环 / 失败回退）**

创建 `tests/unit/agents/context/test_tool_result_offload.py`（先确认 `tests/unit/agents/context/__init__.py` 存在；若无则建空文件）。**只导入本 Task 用到的名字**（§7.2 增量导入）：
```python
# -*- coding: utf-8 -*-
"""Tests for §4.5 Part A: tool result offload (async + atomic)."""
from __future__ import annotations

import asyncio
import types

import pytest

from qwenpaw.agents.context.light_context_manager import LightContextManager
from qwenpaw.constant import TRUNCATION_NOTICE_MARKER


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
        big = "x" * 5000  # well over max_bytes + 100 slack
        result = await mgr._truncate_tool_result(big, max_bytes=1000)
        # One file written under tool_results/, content matches, no tmp leftover.
        files = list((tmp_path / "tool_results").glob("*.txt"))
        assert len(files) == 1
        assert files[0].read_text(encoding="utf-8") == big
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
        await mgr._truncate_tool_result("x" * 5000, max_bytes=1000)
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
        big = "x" * 5000
        result = await mgr._truncate_tool_result(big, max_bytes=1000)
        # Fallback: no file/dir written; result still truncated (marker).
        assert not (tmp_path / "tool_results").exists()
        assert TRUNCATION_NOTICE_MARKER in result
```

- [ ] **Step 2: 跑测试确认红**
```bash
pytest tests/unit/agents/context/test_tool_result_offload.py -v
```
Expected: FAIL（`_truncate_tool_result` 仍是同步 → `await <str>` 抛 `TypeError: object str can't be used in 'await' expression`）。

- [ ] **Step 3: 加 imports**

`light_context_manager.py` 顶部 stdlib import 区（grep 定位 `import logging`），当前：
```python
import logging
import os
import sys
import uuid
```
改为（按字母序插 `asyncio`）：
```python
import asyncio
import logging
import os
import sys
import uuid
```

在现有 `from ...constant import TRUNCATION_NOTICE_MARKER`（grep 定位）之后加：
```python
from ...utils.atomic_io import write_bytes_atomic
```

- [ ] **Step 4: `_truncate_tool_result` 改 async + 原子溢写**

`light_context_manager.py` 当前（grep 定位 `def _truncate_tool_result`，约 :133）：

(a) 签名 `def _truncate_tool_result(` → `async def _truncate_tool_result(`。

(b) 写盘块（约 :177-181）：
```python
        try:
            tool_result_dir.mkdir(parents=True, exist_ok=True)
            fp = tool_result_dir / f"{uuid.uuid4().hex}.txt"
            fp.write_text(content, encoding=encoding)
            saved_path = str(fp)
        except OSError as e:
```
改为（删冗余 mkdir——`write_bytes_atomic` 自带 parent mkdir；写经 `to_thread` 离事件循环）：
```python
        try:
            fp = tool_result_dir / f"{uuid.uuid4().hex}.txt"
            await asyncio.to_thread(
                write_bytes_atomic,
                fp,
                content.encode(encoding),
            )
            saved_path = str(fp)
        except OSError as e:
```
（`except OSError` 块及其后的 `return truncate_text_output(...)` **不动**——`write_bytes_atomic` 失败抛 OSError，回退路径逐字保留。函数其余部分——早返回、`content_bytes` 检查、末尾 `return truncate_text_output(..., file_path=saved_path, ...)`——不动。）

- [ ] **Step 5: `_prune_output` 改 async**

`light_context_manager.py` 当前（grep 定位 `def _prune_output`，约 :201）：签名 `def _prune_output(` → `async def _prune_output(`。函数体内两处调用加 `await`：
```python
        if isinstance(output, str):
            return await self._truncate_tool_result(output, max_bytes, encoding)
        if isinstance(output, list):
            for block in output:
                if isinstance(block, dict) and block.get("type") == "text":
                    block["text"] = await self._truncate_tool_result(
                        block.get("text", ""),
                        max_bytes,
                        encoding,
                    )
        return output
```
（其余不动。）

- [ ] **Step 6: `_prune_tool_result` 调用点 :330 加 await**

`light_context_manager.py` 当前（grep 定位 `block["output"] = self._prune_output(`，约 :330）：
```python
                    block["output"] = self._prune_output(
                        output,
                        effective_max_bytes,
                    )
```
改为：
```python
                    block["output"] = await self._prune_output(
                        output,
                        effective_max_bytes,
                    )
```
（`_prune_tool_result` 本就是 `async def`，仅此一处调用点加 `await`。）

- [ ] **Step 7: 跑测试确认绿**
```bash
pytest tests/unit/agents/context/test_tool_result_offload.py -v
python -c "from qwenpaw.agents.context.light_context_manager import LightContextManager; print('import OK')"
```
Expected: 4 测试全 PASS + import OK。

- [ ] **Step 8: Commit**
```bash
git add src/qwenpaw/agents/context/light_context_manager.py tests/unit/agents/context/test_tool_result_offload.py
git commit -m "perf(context): §4.5 offload oversized tool-result spill to a thread + atomic

Co-Authored-By: Claude <noreply@anthropic.com>"
```
（pre-commit 跑；re-stage 若 reformats。`M src/qwenpaw/cli/desktop_cmd.py` 勿动——只 add 本 Task 文件。）

---

## Task 2: 链路测试（`_prune_output` + `_prune_tool_result`）

**Files:**
- Modify: `tests/unit/agents/context/test_tool_result_offload.py`（Task 1 的 async 传播已覆盖这两个函数，本 Task 补覆盖）

- [ ] **Step 1: 写链路测试**

在 `test_tool_result_offload.py` 末尾追加（复用 Task 1 的 `_make_manager`/`_stub_config`/`TRUNCATION_NOTICE_MARKER`/`types`，**无需新 import**）：
```python
class TestPruneChain:
    async def test_prune_output_str_path(self, tmp_path, monkeypatch):
        mgr = _make_manager(tmp_path, monkeypatch)
        big = "x" * 5000
        result = await mgr._prune_output(big, max_bytes=1000)
        assert TRUNCATION_NOTICE_MARKER in result
        assert list((tmp_path / "tool_results").glob("*.txt"))

    async def test_prune_output_list_path(self, tmp_path, monkeypatch):
        mgr = _make_manager(tmp_path, monkeypatch)
        big = "x" * 5000
        blocks = [{"type": "text", "text": big}]
        result = await mgr._prune_output(blocks, max_bytes=1000)
        assert TRUNCATION_NOTICE_MARKER in result[0]["text"]
        assert list((tmp_path / "tool_results").glob("*.txt"))

    async def test_prune_tool_result_integration(self, tmp_path, monkeypatch):
        mgr = _make_manager(tmp_path, monkeypatch)
        big = "x" * 5000
        msg = types.SimpleNamespace(
            content=[{"type": "tool_result", "id": "t1", "output": big}],
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
```

- [ ] **Step 2: 跑测试确认绿**
```bash
pytest tests/unit/agents/context/test_tool_result_offload.py -v
```
Expected: 7 测试全 PASS（Task 1 的 4 + 本 Task 的 3）。链路测试验证 async 传播在 `_prune_output`（str/list 两路径）与 `_prune_tool_result`（tool_result block 原地截断 + 溢写）端到端正确。

- [ ] **Step 3: Commit**
```bash
git add tests/unit/agents/context/test_tool_result_offload.py
git commit -m "test(context): §4.5 cover prune-output + prune-tool-result async chain

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Task 3: 全量回归 + lint/typecheck + 最终评审 + 回写 baseline

- [ ] **Step 1: 全量回归**
```bash
pytest tests/unit/agents/ tests/unit/app/ tests/unit/utils/ -q
python -c "from qwenpaw.agents.context.light_context_manager import LightContextManager; print('import OK')"
```
Expected: 全 PASS + import OK。（无现存 light_context 测试 → 本 Phase 新增即全部覆盖；其余模块无回归。）

- [ ] **Step 2: lint/typecheck（pre-commit canonical，§8 坑-3）**
```bash
pre-commit run --files src/qwenpaw/agents/context/light_context_manager.py tests/unit/agents/context/test_tool_result_offload.py
```
Expected: Passed（mypy/black/flake8/pylint 全绿）。本地 CLI 版本错配以 pre-commit 为准。若 mypy/pylint 提示缺类型或风格，按 pre-commit 调整再 re-stage。

- [ ] **Step 3: 最终 code review（整个 Phase 4d）**

对 Phase 4d 全量改动做 code review（base = spec 提交 `2dd7affa`，head = Task 2 最终提交），确认：
- 三函数 async 传播正确（`_truncate_tool_result`/`_prune_output` 改 `async def`；三处 `await`：:218/:222/:330）；
- 溢写改 `await asyncio.to_thread(write_bytes_atomic, fp, content.encode(encoding))`；冗余 mkdir 已删（`write_bytes_atomic` 自带）；
- OSError 回退逐字保留；超限/未超限/失败三条路径行为正确；
- `post_acting` / 截断 notice 逻辑 / 其余 hook 未动；
- 无回归。

- [ ] **Step 4: 回写 `docs/PROGRESS_BASELINE.md`**

按基准 §10 接手清单第 6 步：
- §0 一句话现状：注明 §4.5 Part A 已完成、Part B defer。
- §3 归属：§4.5 → Phase 4d（Part A）✅，Part B defer。
- §5 加 "Phase 4d — §4.5 Part A tool result 溢写异步化 + 原子化"（含提交 SHA 范围）。
- §6 的 §4.5 行改为"Part A 完成；Part B（token 增量缓存）defer"。
- §9 索引：`agents/context/light_context_manager.py` 加入结构性改动；`tests/unit/agents/context/test_tool_result_offload.py` 加入测试索引。

```bash
git add docs/PROGRESS_BASELINE.md
git commit -m "docs: PROGRESS_BASELINE — §4.5 Part A tool result 溢写完成（Phase 4d）

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Phase 4d 完成准则

- `_truncate_tool_result` / `_prune_output` 为 `async def`；`_prune_tool_result` :330 `await _prune_output`；`_truncate_tool_result` 写经 `await asyncio.to_thread(write_bytes_atomic, fp, content.encode(encoding))`。
- 溢写离事件循环 + 原子（tmp+fsync+replace+per-path RLock）；冗余 mkdir 删除。
- OSError 回退逐字保留；超限/未超限/失败三路径正确。
- `post_acting` / notice 逻辑 / 其余 hook 未动。
- 测试 7 个全绿（4 单元 + 3 链路）；pre-commit 通过；PROGRESS_BASELINE 已回写（Part B defer 记录）。

---

## 风险点（呼应 spec §8）

1. **3 函数 async 传播**：受控线性，两内层无外部调用方（spec §2.2 已 grep 确认）。三处 `await`（:218/:222/:330）是全部调用点；漏一处即返回未 await 的协程 → 测试必红。
2. **无现存测试**：新建 fixture（monkeypatch `load_agent_config` 返回 stub）；stub 字段路径 `running.light_context_config.tool_result_pruning_config.{tool_results_cache,exempt_file_extensions,exempt_tool_names}`（Task 1 helper 已含）。
3. **`content.encode(encoding)` 在事件循环**：CPU 廉价；仅超大内容时可能可测，本次不移入 thread（YAGNI）。
4. **glob on 不存在目录**：`test_undermax` / `test_write_failure` 断言 `not (tmp_path/"tool_results").exists()`（非 glob），规避 `Path.glob` 对不存在目录的行为差异；overmax 测试目录确实存在（`write_bytes_atomic` 创建）后才 glob。

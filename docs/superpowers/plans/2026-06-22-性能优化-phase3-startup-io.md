# 性能优化 Phase 3：启动 IO（遥测后台化 + import 扫描消除）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把启动期最重的两块阻塞 IO 移出关键路径——(a) 遥测采集的 GPU 子进程探测 + 同步网络上传从 Phase 1 同步阻塞改为后台延迟执行（spec §3.1，最大单点收益，最坏 5s → 0s 阻塞）；(b) 消除每次进程启动的 import 期目录扫描（spec §3.3）。顺带把遥测 marker 写入收敛到原子 RMW（修 spec §5.1 表里的 H3）。

**Architecture:** Phase 1 已交付 `utils/background_tasks.py`（`BackgroundTaskRunner`）。本 Phase 把一个 runner 实例接进 `_app.py` lifespan，用 `spawn_after(2.0, ...)` 把同步的 `collect_and_upload_telemetry`（内部 `nvidia-smi`/`lspci`/`wmic` 子进程 + httpx POST）经 `asyncio.to_thread` 后台化；opt-out/collected 判定（读 marker，几 ms）保持同步。`constant.py` 的 `_discover_agent_languages` 改为硬编码（实际产物就是 `{en,zh,ru}`，与现有 fallback 一致，行为保持）。

**Tech Stack:** Python 3.10、Phase 1 的 `BackgroundTaskRunner` + `atomic_io`、pytest + pytest-asyncio（asyncio_mode=auto）。

**对应 spec：** `docs/superpowers/specs/2026-06-22-性能优化-design.md` 第 3.1、3.3 节（§3.3 的 console_static_dir 已确认"包内目录优先 + 命中即短路"，现状已最优，本 Phase 不动；load_envs 迁移因有 import 期依赖风险，推迟到后续 Phase）。

**前置：** Phase 1（基石）+ Phase 2（原子写）已完成。本 Phase 从当前 `feature/usb-portable` HEAD 继续。

---

## 范围说明（scope check）

spec §3.1–3.6 体量大且异质。本 Phase 聚焦**最安全、最高收益的 §3.1（遥测）+ §3.3（语言扫描硬编码）**。其余推迟到后续独立计划：

| spec 节 | 内容 | 本 Phase | 去向 |
|---|---|---|---|
| §3.1 | 遥测后台化 + marker 原子 | ✅ Task 1–2 | — |
| §3.3 语言扫描 | `_discover_agent_languages` 硬编码 | ✅ Task 3 | — |
| §3.3 console_static_dir | 候选路径重排 | — 现状已最优（包内优先+短路），no-op |
| §3.3 load_envs 迁移 | import→lifespan | ⏸ 推迟 | 有 import 期 os.environ 依赖风险，需先分析依赖，单独计划 |
| §3.2 | 迁移戳幂等 | ⏸ 推迟 | novel/高风险（"跳过是否安全"需逐迁移分析），单独计划 |
| §3.4 | restore stat 精简 | ⏸ 推迟 | 需读 safe_swap 内部，单独计划 |
| §3.5 | Workspace.start 去重 | ⏸ 推迟 | 需读 workspace/multi_agent_manager 内部，单独计划 |
| §3.6 | py import 4K 读写 | ⏸ 推迟 | 独立机制（哈希 pyc 构建 + import 预读运行期），Phase 3b |

---

## File Structure（Phase 3）

| 文件 | 改动 |
|---|---|
| `src/qwenpaw/app/_app.py` | Task 1：lifespan 接入 `BackgroundTaskRunner`，遥测改 `spawn_after` 后台；finally 加 `shutdown` |
| `src/qwenpaw/utils/telemetry.py` | Task 2：`mark_telemetry_collected` 改 `locked_json_update`（原子 RMW，修 H3）|
| `src/qwenpaw/constant.py` | Task 3：`_discover_agent_languages` 硬编码（消除 import 期扫盘）|

**测试**：现有 `tests/unit/utils/`（telemetry 若有）、`tests/unit/app/` 覆盖；Task 2 为 marker 原子 RMW 加并发测试（若 telemetry 现无单测，加最小用例）。

---

## Task 1: 遥测后台化（§3.1，最大单点收益）

**Files:**
- Modify: `src/qwenpaw/app/_app.py`（lifespan：接入 runner、遥测改后台、finally shutdown）

**当前**（lifespan Phase 1，遥测段；Phase 2 后行号约 +6，实现时按逻辑定位）：
```python
    try:
        from ..utils.telemetry import (
            collect_and_upload_telemetry,
            has_telemetry_been_collected,
            is_telemetry_opted_out,
        )

        if not is_telemetry_opted_out(
            WORKING_DIR,
        ) and not has_telemetry_been_collected(WORKING_DIR):
            collect_and_upload_telemetry(WORKING_DIR)
    except Exception:
        logger.debug(
            "Telemetry collection skipped due to error",
            exc_info=True,
        )
```

- [ ] **Step 1: 加 BackgroundTaskRunner 导入**

在 `src/qwenpaw/app/_app.py` 顶部 import 区（已有 `from ..utils.atomic_io import cleanup_orphan_tmps`，紧随其后）加：

```python
from ..utils.background_tasks import BackgroundTaskRunner
```

- [ ] **Step 2: 在 Phase 1 实例化 runner**

在 lifespan Phase 1 创建 managers 处（`multi_agent_manager = MultiAgentManager()` 附近）加一行：

```python
    # Fire-and-forget runner for deferrable startup IO (telemetry upload, …).
    _bg_runner = BackgroundTaskRunner()
```

- [ ] **Step 3: 遥测采集改后台 spawn_after**

把上面"当前"的遥测段替换为（opt-out/collected 判定保持同步；仅把昂贵的采集+上传后台化，经 to_thread 不阻塞事件循环）：

```python
    try:
        from ..utils.telemetry import (
            collect_and_upload_telemetry,
            has_telemetry_been_collected,
            is_telemetry_opted_out,
        )

        if not is_telemetry_opted_out(
            WORKING_DIR,
        ) and not has_telemetry_been_collected(WORKING_DIR):
            # Defer the expensive collection (GPU subprocess probes +
            # network upload) off the startup path. The opt-out / already
            # collected checks above stay synchronous (cheap marker read).
            async def _telemetry_task() -> None:
                try:
                    await asyncio.to_thread(
                        collect_and_upload_telemetry, WORKING_DIR,
                    )
                except Exception:
                    logger.debug(
                        "Background telemetry upload failed",
                        exc_info=True,
                    )

            _bg_runner.spawn_after(2.0, _telemetry_task, name="telemetry")
    except Exception:
        logger.debug(
            "Telemetry collection skipped due to error",
            exc_info=True,
        )
```

（`asyncio` 已在 `_app.py` 顶部导入。`collect_and_upload_telemetry` 是同步函数，`to_thread` 使其在工作线程跑、不阻塞事件循环。）

- [ ] **Step 4: lifespan finally 加 runner shutdown**

在 lifespan 的 `finally` 块里（取消 `_bg_task` 之后、停止各 manager 之前），加：

```python
        # Drain / cancel deferred background tasks (e.g. telemetry upload).
        try:
            await _bg_runner.shutdown()
        except Exception as e:
            logger.error(f"Error stopping background task runner: {e}")
```

（放在 `if not _bg_task.done(): _bg_task.cancel() ...` 之后。）

- [ ] **Step 5: 验证**

```bash
python -c "import qwenpaw.app._app; print('import OK')"
pytest tests/unit/app/ tests/unit/utils/ -q
```
Expected: import OK + 全 PASS。

- [ ] **Step 6: Commit**

```bash
git add src/qwenpaw/app/_app.py
git commit -m "perf(app): background telemetry collection via spawn_after (off startup path)

Co-Authored-By: Claude <noreply@anthropic.com>"
```
（pre-commit 跑 black 23.3.0[79]/flake8/pylint/mypy；re-stage 若 reformats。`_app.py` 现含 Phase 2 的附带修复，提交应干净。）

**风险点**：`collect_and_upload_telemetry` 内部会 `mark_telemetry_collected`（Task 2 把它原子化）。后台化后 marker 写发生在后台线程——Task 2 的 `locked_json_update` 保证并发/崩溃安全。

---

## Task 2: 遥测 marker 原子 RMW（§3.1 / H3）

**Files:**
- Modify: `src/qwenpaw/utils/telemetry.py:237-282`（`mark_telemetry_collected`）

`mark_telemetry_collected` 现状是 read-merge-`write_text` 裸写（H3）。改为 `locked_json_update`：把合并逻辑收进闭包，整体在 per-path 锁内、原子落盘。

- [ ] **Step 1: 加导入**

在 `src/qwenpaw/utils/telemetry.py` 顶部 import 区加（同包，1 点）：

```python
from .atomic_io import locked_json_update
```

- [ ] **Step 2: 改 `mark_telemetry_collected`**

把现有函数体（`marker_file = ...` 到 `marker_file.write_text(json.dumps(marker_data), ...)` 这段 read-merge-write）替换为：

```python
    marker_file = working_dir / TELEMETRY_MARKER_FILE
    current = _get_current_version()

    def _merge(old: Any) -> dict[str, Any]:
        collected_versions: list[str] = []
        prev_opted_out = False
        if isinstance(old, dict):
            collected_versions = list(old.get("collected_versions") or [])
            prev_opted_out = old.get("opted_out", False) is True
            # Migrate from v1.1 single-version format
            if not collected_versions:
                old_ver = old.get("qwenpaw_version", "")
                if old_ver:
                    collected_versions = [old_ver]
        if current not in collected_versions:
            collected_versions.append(current)
        return {
            "collected_at": time.time(),
            "qwenpaw_version": current,
            "collected_versions": collected_versions,
            "opted_out": opted_out or prev_opted_out,
            "version": "1.3",
        }

    try:
        locked_json_update(marker_file, _merge, default={})
    except Exception as e:
        logger.warning("Failed to mark telemetry collected: %s", e)
```

（保留外层 `try/except` 错误处理语义；`locked_json_update` 内部用 `read_json_safe` 兜底损坏 marker → `default={}`，吸收了原 `try/except` 读兜底。确认 `Any` 已在 telemetry.py 导入——若未导入，加 `from typing import Any`。）

- [ ] **Step 3: 写并发/原子测试**

确认 telemetry 是否有现成单测：`find tests -name "*telemetry*"`. 若无，新建 `tests/unit/utils/test_telemetry_marker.py`：

```python
# -*- coding: utf-8 -*-
"""Tests for telemetry marker atomic RMW."""
from __future__ import annotations

import threading
from pathlib import Path

from qwenpaw.utils.telemetry import mark_telemetry_collected


class TestMarkTelemetryCollected:
    def test_concurrent_marks_no_lost_version(self, tmp_path, monkeypatch):
        # Pin the version so concurrent callers target the same marker.
        import qwenpaw.utils.telemetry as t

        monkeypatch.setattr(t, "_get_current_version", lambda: "9.9.9")
        n = 20

        def bump():
            mark_telemetry_collected(tmp_path)

        threads = [threading.Thread(target=bump) for _ in range(n)]
        for th in threads:
            th.start()
        for th in threads:
            th.join()

        import json
        data = json.loads((tmp_path / ".telemetry_collected").read_text())
        assert "9.9.9" in data["collected_versions"]
        assert not list(tmp_path.glob("*.tmp.*"))
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/unit/utils/test_telemetry_marker.py -v
```
Expected: PASS（marker 写无损坏、无 tmp 残留）。

- [ ] **Step 5: Commit**

```bash
git add src/qwenpaw/utils/telemetry.py tests/unit/utils/test_telemetry_marker.py
git commit -m "refactor(telemetry): mark_telemetry_collected via locked_json_update (atomic RMW)

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Task 3: 消除 import 期语言目录扫描（§3.3）

**Files:**
- Modify: `src/qwenpaw/constant.py:128-143`（`_discover_agent_languages`）

现状 `_discover_agent_languages()` 在 import 时（`SUPPORTED_AGENT_LANGUAGES = _discover_agent_languages()`，line 143）扫 `agents/md_files/` 各子目录 + `glob("*.md")`。**实际产物是 `{en, ru, zh}`**（local/qa 目录无 `.md`，被 `any(d.glob("*.md"))` 过滤掉），与函数自身的 fallback 完全一致。故硬编码 `{en, zh, ru}` 是行为保持的，消除每次进程启动的扫盘。

- [ ] **Step 1: 改 `_discover_agent_languages` 为硬编码**

把 `src/qwenpaw/constant.py` 的 `_discover_agent_languages` 函数体替换为：

```python
def _discover_agent_languages() -> frozenset[str]:
    # Shipped md_files languages (see agents/md_files/: en/ru/zh have *.md;
    # local/qa are empty). Hardcoded to avoid an import-time iterdir+glob
    # scan on every process start — costly on exFAT. Update this set when a
    # new language dir with *.md is added to the package.
    return frozenset({"en", "zh", "ru"})
```

（保留函数名与 `SUPPORTED_AGENT_LANGUAGES = _discover_agent_languages()` 调用点不动，避免影响其他引用；只去掉函数内的磁盘扫描。）

- [ ] **Step 2: 验证**

```bash
python -c "from qwenpaw.constant import SUPPORTED_AGENT_LANGUAGES; print(sorted(SUPPORTED_AGENT_LANGUAGES)); assert SUPPORTED_AGENT_LANGUAGES == frozenset({'en','zh','ru'}); print('OK')"
pytest tests/unit/ -k "constant or language or agent" -q
```
Expected: `['en', 'ru', 'zh']` / `OK` + 相关测试 PASS。

- [ ] **Step 3: Commit**

```bash
git add src/qwenpaw/constant.py
git commit -m "perf(constant): hardcode SUPPORTED_AGENT_LANGUAGES (drop import-time scan)

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Task 4: Phase 3 验证 + 最终评审

- [ ] **Step 1: 全量回归（受影响模块）**

```bash
pytest tests/unit/utils/ tests/unit/app/ tests/unit/config/ -q
```
Expected: 全 PASS。

- [ ] **Step 2: lint/typecheck**

提交时 pre-commit 自动跑。本地确认：
```bash
flake8 src/qwenpaw/app/_app.py src/qwenpaw/utils/telemetry.py src/qwenpaw/constant.py
pre-commit run black --files src/qwenpaw/ tests/
```
Expected: 无错。

- [ ] **Step 3: 最终 code review（整个 Phase 3）**

对 Phase 3 全量改动做一次 code review（base = Phase 2 head `235b72d6`，head = 本 Phase 最终提交），确认：遥测确实后台化（启动路径不再阻塞采集）、marker 原子 RMW 正确、语言集合硬编码与产物一致、BackgroundTaskRunner 在 lifespan 正确接入并在 shutdown 排空。

---

## Phase 3 完成准则

- 启动 Phase 1 不再同步阻塞遥测采集（opt-out/collected 判定同步、采集上传后台 `spawn_after(2.0)` 经 `to_thread`）。
- `mark_telemetry_collected` 经 `locked_json_update`（原子 + 并发安全，修 H3）。
- `SUPPORTED_AGENT_LANGUAGES` 硬编码，import 期不再扫 `agents/md_files/`。
- `BackgroundTaskRunner` 接入 lifespan，finally 排空。
- 受影响模块测试全绿；lint/typecheck 通过。

后续 Phase（§3.2 迁移戳 / §3.3 load_envs / §3.4 restore 精简 / §3.5 Workspace 去重 / §3.6 import 4K）各出独立计划。

---

## 风险点

1. **遥测后台化的语义变化**：采集从"首启同步上报"变"首启后台上报"。已在 brainstorming 确认可接受。opt-out 仍即时生效（同步判定）。
2. **`_telemetry_task` 闭包捕获 `WORKING_DIR`**：模块级常量，捕获安全。
3. **marker 并发写**：Task 2 的 `locked_json_update` + 并发测试覆盖。
4. **语言集合硬编码与产物漂移**：若未来给 local/qa 加 `.md` 或新增语言目录，需同步更新硬编码。注释已说明；可加一个 CI 校验（本 Phase 不做，YAGNI）。
5. **`_app.py` 提交可能再次触发预存的 black/flakee 摩擦**（Phase 2 已修当前已知的）；若 pre-commit 再次重排无关代码，按 Phase 2 的做法：接受 black 的稳定重排、修任何新 E501，并在 commit message 说明。

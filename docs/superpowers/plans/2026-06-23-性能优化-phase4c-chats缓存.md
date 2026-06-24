# 性能优化 Phase 4c：§4.3 chats.json 缓存（写穿透 + 读缓存）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 `JsonChatRepository.load()/save()` 加进程内读缓存 + 异步原子写，消除每请求 chats.json 的 2 次同步全文件读 + 1 次同步写 → 0 读 + 1 异步写（离事件循环）。

**Architecture:** `BaseChatRepository` 的所有 CRUD 都是 `load()`→改→`save()` 全文件 RMW，故在 `JsonChatRepository.load/save` 单点加缓存 = 所有 CRUD 透明受益。写穿透：`load()` 命中返回缓存深拷贝（0 IO），`save()` 更新缓存 + `asyncio.to_thread(write_json_atomic)`（离事件循环、原子、无丢数据窗口）。kill-switch `QWENPAW_PERF_CHATS_CACHE`（默认开）；关 = 逐字当前同步行为。

**Tech Stack:** Python 3.10、`asyncio.Lock`/`asyncio.to_thread`、Phase 1 的 `atomic_io.write_json_atomic`、pydantic v2 `model_copy(deep=True)`、pytest（asyncio_mode=auto）。

**对应 spec：** `docs/superpowers/specs/2026-06-23-chats缓存-design.md`。
**前置：** Phase 1–5 + Phase 4b 已完成（HEAD `aa532ea1`）；本 Phase 从 `feature/usb-portable` 当前 HEAD 继续，不合并分支。
**行号基准：** 截至 HEAD `aa532ea1`，改动后会漂移（§8 坑-6），动手前用 grep 重新定位。

---

## 范围说明（scope check）

单一子系统（`JsonChatRepository` 的 load/save 缓存 + 单测）。唯一改动源文件 = `src/qwenpaw/app/runner/repo/json_repo.py`。`ChatManager`、`BaseChatRepository` 不改（CRUD 透明受益）。一个计划即可。

---

## File Structure（Phase 4c）

| 文件 | 改动 |
|---|---|
| `src/qwenpaw/app/runner/repo/json_repo.py` | Task 1：flag helper；Task 2：缓存 state + `_load_from_disk_sync` + `load()` 缓存 + `save()` 缓存更新（同步写）；Task 3：flag-on IO 改 `to_thread`（异步） |
| `tests/unit/app/test_chats_cache.py` | Task 1–3 逐步新增（flag + 缓存 + 异步 + 集成测试） |

---

## Task 1: 缓存开关 helper（纯函数）

**Files:**
- Modify: `src/qwenpaw/app/runner/repo/json_repo.py`（imports 之后、`class JsonChatRepository` 之前加模块级 helper）
- Create: `tests/unit/app/test_chats_cache.py`

- [ ] **Step 1: 写失败测试（flag）**

创建 `tests/unit/app/test_chats_cache.py`（**只导入本 Task 用到的名字**，§7.2 增量导入）：
```python
# -*- coding: utf-8 -*-
"""Tests for JsonChatRepository chats cache (§4.3)."""
from __future__ import annotations

import pytest

from qwenpaw.app.runner.repo.json_repo import _chats_cache_enabled


class TestChatsCacheFlag:
    def test_default_on(self, monkeypatch):
        monkeypatch.delenv("QWENPAW_PERF_CHATS_CACHE", raising=False)
        assert _chats_cache_enabled() is True

    @pytest.mark.parametrize("val", ["0", "false", "no", "off", "FALSE"])
    def test_disabled_values(self, monkeypatch, val):
        monkeypatch.setenv("QWENPAW_PERF_CHATS_CACHE", val)
        assert _chats_cache_enabled() is False

    @pytest.mark.parametrize("val", ["1", "true", "yes"])
    def test_enabled_values(self, monkeypatch, val):
        monkeypatch.setenv("QWENPAW_PERF_CHATS_CACHE", val)
        assert _chats_cache_enabled() is True
```

- [ ] **Step 2: 跑测试确认红**
```bash
pytest tests/unit/app/test_chats_cache.py -v
```
Expected: FAIL（`ImportError: cannot import name '_chats_cache_enabled'`）。

- [ ] **Step 3: 实现 helper**

`json_repo.py` 当前 imports（line 5-10）已有 `import os`。在 imports 之后、`class JsonChatRepository` 之前（即 `logger = logging.getLogger(__name__)` 之后）加：
```python
_CHATS_CACHE_DISABLE_VALUES = frozenset({"0", "false", "no", "off"})


def _chats_cache_enabled() -> bool:
    """Chats read-cache is ON by default.

    Disable via QWENPAW_PERF_CHATS_CACHE set to one of {0,false,no,off}
    (case-insensitive). When disabled, load()/save() use the original
    synchronous direct-disk behavior (the kill-switch path).
    """
    raw = os.environ.get("QWENPAW_PERF_CHATS_CACHE")
    if raw is None:
        return True
    return raw.strip().lower() not in _CHATS_CACHE_DISABLE_VALUES
```

- [ ] **Step 4: 跑测试确认绿**
```bash
pytest tests/unit/app/test_chats_cache.py -v
```
Expected: PASS（TestChatsCacheFlag 全绿）。

- [ ] **Step 5: Commit**
```bash
git add src/qwenpaw/app/runner/repo/json_repo.py tests/unit/app/test_chats_cache.py
git commit -m "perf(chat-repo): §4.3 chats cache flag helper

Co-Authored-By: Claude <noreply@anthropic.com>"
```
（pre-commit 跑；re-stage 若 reformats。`M src/qwenpaw/cli/desktop_cmd.py` 勿动——只 add 本 Task 文件。）

---

## Task 2: 读缓存 + 写更新缓存（IO 仍同步）

**Files:**
- Modify: `src/qwenpaw/app/runner/repo/json_repo.py`（加 `import asyncio` + 缓存 state + `_load_from_disk_sync` + 改 `load()`/`save()`）
- Modify: `tests/unit/app/test_chats_cache.py`（加缓存测试）

- [ ] **Step 1: 加 `import asyncio`**

`json_repo.py` 顶部（line 5-10）当前：
```python
import json
import logging
import os
import shutil
import uuid
```
改为（按字母序插 `asyncio`）：
```python
import asyncio
import json
import logging
import os
import shutil
import uuid
```

- [ ] **Step 2: 写失败测试（缓存：load 命中 / 独立副本 / flag-off / save 更新缓存）**

在 `tests/unit/app/test_chats_cache.py` 顶部 import 区追加本 Task 用到的名字：
```python
import json

from qwenpaw.app.runner.models import ChatSpec, ChatsFile
from qwenpaw.app.runner.repo.json_repo import JsonChatRepository
```
并在 `TestChatsCacheFlag` 之后加模块级 helper + 测试类：
```python
def _spec(chat_id: str, name: str | None = None) -> ChatSpec:
    """Minimal valid ChatSpec (session_id + user_id are the only required)."""
    return ChatSpec(
        id=chat_id,
        name=name or chat_id,
        session_id=chat_id,
        user_id="u",
    )


def _write_chats(path, chat_ids):
    """Write a chats.json directly to disk, bypassing the repo cache."""
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "chats": [
                    {"id": cid, "session_id": cid, "user_id": "u"}
                    for cid in chat_ids
                ],
            },
        ),
        encoding="utf-8",
    )


class TestChatsCacheLoadSave:
    def setup_method(self):
        # Ensure the cache flag is on (default) for these tests.
        os.environ.pop("QWENPAW_PERF_CHATS_CACHE", None)

    async def test_load_caches_after_first_read(self, tmp_path):
        path = tmp_path / "chats.json"
        _write_chats(path, ["c1"])
        repo = JsonChatRepository(path)
        first = await repo.load()
        # Overwrite disk AFTER the first load populated the cache.
        _write_chats(path, ["c2"])
        second = await repo.load()
        assert [c.id for c in first.chats] == ["c1"]
        assert [c.id for c in second.chats] == ["c1"]  # cached, not re-read

    async def test_load_returns_independent_copy(self, tmp_path):
        path = tmp_path / "chats.json"
        _write_chats(path, ["c1"])
        repo = JsonChatRepository(path)
        first = await repo.load()
        first.chats.append(_spec("c2"))  # mutate the returned object
        second = await repo.load()
        assert [c.id for c in second.chats] == ["c1"]  # cache unaffected

    async def test_flag_disabled_load_reads_disk_every_time(
        self,
        tmp_path,
        monkeypatch,
    ):
        monkeypatch.setenv("QWENPAW_PERF_CHATS_CACHE", "0")
        path = tmp_path / "chats.json"
        _write_chats(path, ["c1"])
        repo = JsonChatRepository(path)
        first = await repo.load()
        _write_chats(path, ["c2"])
        second = await repo.load()
        assert [c.id for c in first.chats] == ["c1"]
        assert [c.id for c in second.chats] == ["c2"]  # re-read (no cache)

    async def test_save_updates_cache_and_writes_through(self, tmp_path):
        path = tmp_path / "chats.json"
        repo = JsonChatRepository(path)
        await repo.save(ChatsFile(chats=[_spec("c1")]))
        # Cache updated: load sees it.
        loaded = await repo.load()
        assert [c.id for c in loaded.chats] == ["c1"]
        # Write-through: a fresh repo (empty cache) reads it from disk.
        repo2 = JsonChatRepository(path)
        loaded2 = await repo2.load()
        assert [c.id for c in loaded2.chats] == ["c1"]
```
（`setup_method` 用到 `os`，测试文件需 `import os`——在 import 区与 `import json` 同批加：`import json\nimport os`。）

- [ ] **Step 3: 跑测试确认红**
```bash
pytest tests/unit/app/test_chats_cache.py::TestChatsCacheLoadSave -v
```
Expected: FAIL（`test_load_caches_after_first_read` 第二次 load 返回 c2 而非 c1——当前无缓存，每次读盘）。

- [ ] **Step 4: 实现缓存（IO 仍同步）**

`json_repo.py` 当前（grep 定位 `class JsonChatRepository`）的 `__init__` / `load` / `save`：
```python
    def __init__(self, path: Path | str):
        if isinstance(path, str):
            path = Path(path)
        self._path = path.expanduser()

    @property
    def path(self) -> Path:
        return self._path

    async def load(self) -> ChatsFile:
        if not self._path.exists():
            return ChatsFile(version=1, chats=[])
        data = json.loads(self._path.read_text(encoding="utf-8"))
        return ChatsFile.model_validate(data)

    async def save(self, chats_file: ChatsFile) -> None:
        payload = chats_file.model_dump(mode="json")
        write_json_atomic(self._path, payload, sort_keys=True)
```

改为（`__init__` 加缓存 state；抽出 `_load_from_disk_sync`；`load()` 缓存 + 深拷贝；`save()` 更新缓存 + **同步写保留**）：
```python
    def __init__(self, path: Path | str):
        if isinstance(path, str):
            path = Path(path)
        self._path = path.expanduser()
        # §4.3 in-process read cache + write-through. _cache holds the last
        # loaded/saved ChatsFile; _cache_lock self-protects it. Disabled via
        # QWENPAW_PERF_CHATS_CACHE -> load/save fall back to direct disk.
        self._cache: ChatsFile | None = None
        self._cache_lock = asyncio.Lock()

    @property
    def path(self) -> Path:
        return self._path

    def _load_from_disk_sync(self) -> ChatsFile:
        """Read chats.json from disk synchronously (the original load body)."""
        if not self._path.exists():
            return ChatsFile(version=1, chats=[])
        data = json.loads(self._path.read_text(encoding="utf-8"))
        return ChatsFile.model_validate(data)

    async def load(self) -> ChatsFile:
        if not _chats_cache_enabled():
            return self._load_from_disk_sync()
        async with self._cache_lock:
            if self._cache is None:
                self._cache = self._load_from_disk_sync()
            return self._cache.model_copy(deep=True)

    async def save(self, chats_file: ChatsFile) -> None:
        snapshot = chats_file.model_copy(deep=True)
        payload = snapshot.model_dump(mode="json")
        if not _chats_cache_enabled():
            write_json_atomic(self._path, payload, sort_keys=True)
            return
        async with self._cache_lock:
            self._cache = snapshot
        write_json_atomic(self._path, payload, sort_keys=True)
```

- [ ] **Step 5: 跑测试确认绿**
```bash
pytest tests/unit/app/test_chats_cache.py -v
```
Expected: PASS（含 `TestChatsCacheLoadSave` 4 个 + Task 1 的 flag 测试）。

- [ ] **Step 6: 回归（现有 chat 路径不破）**
```bash
pytest tests/unit/app/test_chat_updates.py tests/unit/app/test_chats_cache.py -q
```
Expected: 全 PASS（`test_chat_updates` 经 ChatManager save→load，缓存更新保证其不 stale）。

- [ ] **Step 7: Commit**
```bash
git add src/qwenpaw/app/runner/repo/json_repo.py tests/unit/app/test_chats_cache.py
git commit -m "perf(chat-repo): §4.3 read cache + write-through cache update

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Task 3: flag-on IO 异步化（`to_thread`，离事件循环）

**Files:**
- Modify: `src/qwenpaw/app/runner/repo/json_repo.py`（`load()` miss + `save()` 写改 `to_thread`；flag-off 路径保持同步）
- Modify: `tests/unit/app/test_chats_cache.py`（加异步 + 集成测试）

- [ ] **Step 1: 写失败测试（异步 + 集成）**

在 import 区加（本 Task 首次用到）：
```python
import asyncio
```
在 `TestChatsCacheLoadSave` 之后追加：
```python
class TestChatsCacheAsyncAndIntegration:
    def setup_method(self):
        os.environ.pop("QWENPAW_PERF_CHATS_CACHE", None)

    async def test_load_miss_offloads_to_thread(self, tmp_path, monkeypatch):
        path = tmp_path / "chats.json"
        _write_chats(path, ["c1"])
        repo = JsonChatRepository(path)  # cache empty -> miss
        names = []
        real = asyncio.to_thread

        async def spy(func, *args, **kwargs):
            names.append(getattr(func, "__name__", repr(func)))
            return await real(func, *args, **kwargs)

        monkeypatch.setattr(asyncio, "to_thread", spy)
        await repo.load()
        assert "_load_from_disk_sync" in names

    async def test_save_offloads_write_to_thread(self, tmp_path, monkeypatch):
        path = tmp_path / "chats.json"
        repo = JsonChatRepository(path)
        names = []
        real = asyncio.to_thread

        async def spy(func, *args, **kwargs):
            names.append(getattr(func, "__name__", repr(func)))
            return await real(func, *args, **kwargs)

        monkeypatch.setattr(asyncio, "to_thread", spy)
        await repo.save(ChatsFile(chats=[_spec("c1")]))
        assert "write_json_atomic" in names

    async def test_roundtrip_durability_after_restart(self, tmp_path):
        path = tmp_path / "chats.json"
        repo = JsonChatRepository(path)
        await repo.save(ChatsFile(chats=[_spec("c1")]))
        # New repo instance simulates a restart (cache empty).
        repo2 = JsonChatRepository(path)
        loaded = await repo2.load()
        assert [c.id for c in loaded.chats] == ["c1"]

    async def test_base_crud_works_with_cache(self, tmp_path):
        path = tmp_path / "chats.json"
        repo = JsonChatRepository(path)
        await repo.upsert_chat(_spec("c1"))
        assert (await repo.get_chat("c1")).id == "c1"
        await repo.upsert_chat(_spec("c1", name="renamed"))
        assert (await repo.get_chat("c1")).name == "renamed"
        await repo.upsert_chat(_spec("c2"))
        assert {c.id for c in await repo.filter_chats(user_id="u")} == {
            "c1",
            "c2",
        }
        assert await repo.delete_chats(["c1"]) is True
        assert await repo.get_chat("c1") is None

    async def test_concurrent_load_save_safe(self, tmp_path):
        path = tmp_path / "chats.json"
        repo = JsonChatRepository(path)

        async def writer(i):
            await repo.save(ChatsFile(chats=[_spec(f"c{i}")]))

        async def reader():
            await repo.load()

        # No raise under concurrent access; cache lock serializes updates.
        await asyncio.gather(
            *[writer(i) for i in range(10)],
            *[reader() for _ in range(10)],
        )
        final = await repo.load()
        assert isinstance(final, ChatsFile)
        assert len(final.chats) == 1  # last writer wins; no corruption
        disk = json.loads(path.read_text(encoding="utf-8"))
        assert disk["version"] == 1  # atomic writes kept disk valid
```

- [ ] **Step 2: 跑测试确认红**
```bash
pytest tests/unit/app/test_chats_cache.py::TestChatsCacheAsyncAndIntegration -v
```
Expected: `test_load_miss_offloads_to_thread` 与 `test_save_offloads_write_to_thread` FAIL（Task 2 的 load/save 仍是同步调用，`to_thread` 未被调用 → names 列表为空）。其余 3 个（durability/crud/concurrent）应已 PASS（Task 2 的缓存已满足）。

- [ ] **Step 3: flag-on IO 改 `to_thread`**

`json_repo.py` Task 2 的 `load()`（cache miss 分支）：
```python
        async with self._cache_lock:
            if self._cache is None:
                self._cache = self._load_from_disk_sync()
            return self._cache.model_copy(deep=True)
```
改为（miss 经 `to_thread`）：
```python
        async with self._cache_lock:
            if self._cache is None:
                self._cache = await asyncio.to_thread(
                    self._load_from_disk_sync,
                )
            return self._cache.model_copy(deep=True)
```

Task 2 的 `save()`（flag-on 写那一行，在 `async with self._cache_lock` 块之后）：
```python
        async with self._cache_lock:
            self._cache = snapshot
        write_json_atomic(self._path, payload, sort_keys=True)
```
改为（写经 `to_thread`；flag-off 路径保持同步不变）：
```python
        async with self._cache_lock:
            self._cache = snapshot
        await asyncio.to_thread(
            write_json_atomic,
            self._path,
            payload,
            sort_keys=True,
        )
```
（`save()` 的 flag-off 分支 `write_json_atomic(self._path, payload, sort_keys=True)` **不动**——kill-switch 保持逐字当前同步写。`load()` 的 flag-off 分支 `return self._load_from_disk_sync()` **不动**。）

- [ ] **Step 4: 跑测试确认绿**
```bash
pytest tests/unit/app/test_chats_cache.py -v
```
Expected: 全 PASS（含 `TestChatsCacheAsyncAndIntegration` 5 个）。

- [ ] **Step 5: Commit**
```bash
git add src/qwenpaw/app/runner/repo/json_repo.py tests/unit/app/test_chats_cache.py
git commit -m "perf(chat-repo): §4.3 offload flag-on IO to a thread (off event loop)

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Task 4: 全量回归 + lint/typecheck + 最终评审 + 回写 baseline

- [ ] **Step 1: 全量回归**
```bash
pytest tests/unit/app/ tests/unit/agents/ tests/unit/utils/ -q
python -c "from qwenpaw.app.runner.repo.json_repo import JsonChatRepository; print('import OK')"
```
Expected: 全 PASS + import OK。（重点：`test_chat_updates`、`test_title_generator`、`test_runner_session` 不回归。）

- [ ] **Step 2: lint/typecheck（pre-commit canonical，§8 坑-3）**
```bash
pre-commit run --files src/qwenpaw/app/runner/repo/json_repo.py tests/unit/app/test_chats_cache.py
```
Expected: Passed（mypy/black/flake8/pylint 全绿）。本地 CLI 版本错配以 pre-commit 为准。若 pylint 提示缺类型注解，补最小注解再 re-stage。

- [ ] **Step 3: 最终 code review（整个 Phase 4c）**

对 Phase 4c 全量改动做 code review（base = spec 提交 `aa532ea1`，head = Task 3 最终提交），确认：
- `load()` 命中深拷贝、miss `to_thread` 读盘；`save()` 更新缓存（深拷贝）+ flag-on `to_thread` 原子写、flag-off 同步；
- flag-off = 逐字当前（同步 load/save）；flag-on = 缓存 + 异步；
- repo `_cache_lock` 自保护、写在线程写快照不碰缓存、无死锁；
- 写穿透 → 无丢数据窗口；深拷贝 → 调用方不污染缓存；workspace repo 唯一运行时写者 → 无 stale；
- `ChatManager`/`BaseChatRepository` 未改、CRUD 透明受益；
- 无回归。

- [ ] **Step 4: 回写 `docs/PROGRESS_BASELINE.md`**

按基准 §10 接手清单第 6 步：
- §0 一句话现状：注明 §4.3 已完成。
- §3 归属：§4.3 → Phase 4c ✅。
- §5 加 "Phase 4c — §4.3 chats.json 缓存"（含提交 SHA 范围 + flag）。
- §6 删除 §4.3 行（剩余 §4.5/§3.x/§5.x；下一项建议 §3.2 或 §4.5）。
- §9 索引：`runner/repo/json_repo.py` 加入结构性改动；`tests/unit/app/test_chats_cache.py` 加入测试索引。

```bash
git add docs/PROGRESS_BASELINE.md
git commit -m "docs: PROGRESS_BASELINE — §4.3 chats.json 缓存完成（Phase 4c）

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Phase 4c 完成准则

- `JsonChatRepository.load()`：flag-on 命中返回缓存深拷贝（0 IO）、miss `to_thread` 读盘；flag-off 同步读盘（逐字当前）。
- `JsonChatRepository.save()`：flag-on 更新缓存（深拷贝）+ `to_thread(write_json_atomic)`；flag-off 同步写（逐字当前）。
- 写穿透 → 无丢数据窗口；深拷贝 → 无污染；repo `_cache_lock` 自保护；workspace repo 唯一运行时写者 → 无 stale。
- `ChatManager`/`BaseChatRepository` 未改；所有 CRUD 透明受益。
- kill-switch `QWENPAW_PERF_CHATS_CACHE` 默认开、关 = 逐字当前。
- 每请求 `touch_chat`：2 同步读 + 1 同步写 → 0 读 + 1 异步写。
- 受影响模块测试全绿；pre-commit 通过；PROGRESS_BASELINE 已回写。

---

## 风险点（呼应 spec §8）

1. **深拷贝开销**：`ChatsFile` 小（元数据列表），`model_copy(deep=True)` 微秒级、仅 CRUD 时；可接受。
2. **repo 锁 + cm 锁双层**：无嵌套（load/save 各单独获取 repo 锁）、无回调入 repo → 无死锁。load miss 在锁内 `await to_thread`（一生一次，可接受）。
3. **未来外部写者**：目前无运行时外部写者（spec §2.5）。若将来出现 → 加 mtime 校验或失效钩子，**不在本次范围**。
4. **flag-off 语义**：flag-off load/save 均同步（逐字当前阻塞行为），是 kill-switch 的准确回退；flag-on 才异步。
5. **`asyncio.Lock()` 在 `__init__`**：Python 3.10 惰性绑定运行 loop，sync 构造合法；repo 在 event loop 内使用 → 绑定正确。

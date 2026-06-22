# 性能优化 Phase 1：基石模块 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 建立两个被三大场景复用的基石模块——`utils/atomic_io.py`（安全原子写 + 进程内锁）和 `utils/background_tasks.py`（受管后台任务），各自独立可测、无外部依赖。

**Architecture:** `atomic_io` 把项目里三处已验证的好模式（skills_manager / token_usage / safe_swap）抽成统一工具，提供 tmp+fsync+replace 原子写、`RLock` 进程内锁、read-modify-write 全程持锁、json_repair 兜底读、孤儿 tmp 清理。`background_tasks` 提供受 lifespan 管理的 fire-and-forget 任务注册表（spawn / spawn_after / shutdown）。两者都是纯 stdlib + 已有依赖（json_repair），不引入新依赖。

**Tech Stack:** Python 3.10、stdlib（threading/json/os/asyncio）、json_repair（已是项目依赖）、pytest + pytest-asyncio（asyncio_mode=auto）。

**对应 spec：** `docs/superpowers/specs/2026-06-22-性能优化-design.md` 第 2.1、2.2 节。

---

## Phase Roadmap（本计划为 Phase 1，后续阶段各出独立计划）

| Phase | 范围 | 对应 spec | 产出 |
|---|---|---|---|
| **1（本计划）** | 基石：atomic_io + background_tasks | 2.1 / 2.2 | 两个独立可测工具模块 |
| 2 | 场景三原子写：8 处裸覆写收敛到 atomic_io + ChannelManager 死锁修复 | 5.1 / 5.2 | 并发安全 |
| 3 | 场景一：遥测后台化、迁移戳、import 扫描延迟、哈希 pyc、import 预读 | 3.1–3.6 | 启动 <100ms |
| 4 | 场景二：模型客户端缓存、session 异步、chats 缓存、auth 缓存 | 4.1–4.4 | TTFT 下降 |
| 5 | 收尾：4.5 次要项、日志双文件+QueueHandler（5.3）、secret_store 原子化（5.4） | 4.5 / 5.3 / 5.4 | 健壮性收尾 |

每个 Phase 自带特性开关（见 spec 6.5），可独立验证、可回滚。

---

## File Structure（Phase 1）

- **Create** `src/qwenpaw/utils/atomic_io.py` — 原子写/读/锁/清理。单一职责：所有"安全落盘"原语。
- **Create** `src/qwenpaw/utils/background_tasks.py` — 后台任务注册表。单一职责：fire-and-forget 任务的派发与生命周期。
- **Create** `tests/unit/utils/test_atomic_io.py` — atomic_io 单元测试（含并发）。
- **Create** `tests/unit/utils/test_background_tasks.py` — BackgroundTaskRunner 单元测试（async）。

设计边界：`atomic_io` 只管"把数据安全写到给定路径"，不感知业务语义；`background_tasks` 只管"派发并跟踪协程"，不知道任务内容。两者都不依赖项目其他模块，可被任何场景按需调用。

> **增量导入约定（重要）**：atomic_io 的 5 个函数分 Task 1–5 逐步加入同一文件。**每个 Task 只导入它新增函数用到的名字**——不预先 import 后续 Task 才定义的函数。测试文件同理：每个 Task 的测试类只 import 当前已存在的函数。否则 pytest 在后续函数定义前无法 collect 测试模块，且会触发 pylint `unused-import`。各 Task 的代码块已按此约定给出所需的 import 增量。

---

## Task 1: atomic_io — 模块骨架 + write_bytes_atomic

**Files:**
- Create: `src/qwenpaw/utils/atomic_io.py`
- Test: `tests/unit/utils/test_atomic_io.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/utils/test_atomic_io.py`:

```python
# -*- coding: utf-8 -*-
"""Unit tests for utils.atomic_io."""
from __future__ import annotations

import os

import pytest

from qwenpaw.utils.atomic_io import write_bytes_atomic


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
        path = tmp_path / "f.bin"

        def _boom(src, dst):
            raise OSError("replace failed")

        monkeypatch.setattr(os, "replace", _boom)
        with pytest.raises(OSError):
            write_bytes_atomic(path, b"hello")
        assert not path.exists()
        assert not list(tmp_path.glob("*.tmp.*"))

    def test_creates_parent_dir(self, tmp_path):
        path = tmp_path / "nested" / "dir" / "f.bin"
        write_bytes_atomic(path, b"x")
        assert path.read_bytes() == b"x"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/utils/test_atomic_io.py -v`
Expected: FAIL — `ImportError: cannot import name 'write_bytes_atomic' ...`

- [ ] **Step 3: Write minimal implementation**

Create `src/qwenpaw/utils/atomic_io.py`:

```python
# -*- coding: utf-8 -*-
"""Atomic file writes with process-local locking.

Consolidates the tmp+fsync+replace pattern already used (in slightly
different forms) by skills_manager, token_usage.storage and
backup.safe_swap into one reusable module. Robust on exFAT: writes go to
a sibling tmp file, fsync the file (directory fsync is best-effort and
skipped on Windows), then os.replace. A per-path threading.RLock
serializes read-modify-write within the process; single-instance
deployments do not need cross-process locks.

NOTE: chmod-based protection (e.g. 0o600) is a no-op on exFAT and is NOT
relied upon here.
"""
from __future__ import annotations

import os
import threading
from pathlib import Path

# Module-level registry of per-resolved-path RLocks, guarded by a meta-lock.
_locks_meta = threading.Lock()
_locks: dict[str, threading.RLock] = {}


def _resolve(path: str | os.PathLike) -> str:
    """Resolve a path to its absolute string form (cache key for locks)."""
    return str(Path(path).resolve())


def _get_lock(resolved: str) -> threading.RLock:
    """Return (creating if needed) the process-local RLock for a path."""
    with _locks_meta:
        lock = _locks.get(resolved)
        if lock is None:
            lock = threading.RLock()
            _locks[resolved] = lock
        return lock


def write_bytes_atomic(
    path: str | os.PathLike,
    data: bytes,
    *,
    lock: bool = True,
    fsync: bool = True,
) -> None:
    """Write *data* to *path* atomically.

    Writes to ``<path>.tmp.<pid>``, optionally fsyncs, then ``os.replace``
    onto the target. On any failure the tmp file is removed and the
    original is left untouched. When *lock* is True (default) the write is
    serialized with the per-path RLock — callers that already hold the
    lock (e.g. inside :func:`locked_json_update`) pass ``lock=False``.
    """
    resolved = _resolve(path)
    target = Path(resolved)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(f"{target.name}.tmp.{os.getpid()}")

    def _do_write() -> None:
        try:
            with open(tmp, "wb") as f:
                f.write(data)
                if fsync:
                    f.flush()
                    os.fsync(f.fileno())
            os.replace(tmp, resolved)
        finally:
            try:
                if tmp.exists():
                    tmp.unlink()
            except OSError:
                pass

    if lock:
        with _get_lock(resolved):
            _do_write()
    else:
        _do_write()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/utils/test_atomic_io.py::TestWriteBytesAtomic -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add src/qwenpaw/utils/atomic_io.py tests/unit/utils/test_atomic_io.py
git commit -m "feat(utils): add atomic_io.write_bytes_atomic

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Task 2: atomic_io — write_json_atomic

**Files:**
- Modify: `src/qwenpaw/utils/atomic_io.py` (append `write_json_atomic`)
- Test: `tests/unit/utils/test_atomic_io.py` (append `TestWriteJsonAtomic`)

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/utils/test_atomic_io.py`:

```python
class TestWriteJsonAtomic:
    """write_json_atomic behavior."""

    def test_roundtrip(self, tmp_path):
        path = tmp_path / "f.json"
        write_json_atomic(path, {"a": 1, "b": [2, 3]})
        assert json.loads(path.read_text(encoding="utf-8")) == {"a": 1, "b": [2, 3]}

    def test_non_ascii_not_escaped(self, tmp_path):
        path = tmp_path / "f.json"
        write_json_atomic(path, {"name": "龙虾"})
        assert path.read_text(encoding="utf-8") == '{\n  "name": "龙虾"\n}'

    def test_overwrites_existing(self, tmp_path):
        path = tmp_path / "f.json"
        write_json_atomic(path, {"v": 1})
        write_json_atomic(path, {"v": 2})
        assert json.loads(path.read_text(encoding="utf-8")) == {"v": 2}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/utils/test_atomic_io.py::TestWriteJsonAtomic -v`
Expected: FAIL — `ImportError: cannot import name 'write_json_atomic'`

- [ ] **Step 3: Write minimal implementation**

Append to `src/qwenpaw/utils/atomic_io.py`:

```python
def write_json_atomic(
    path: str | os.PathLike,
    data: Any,
    *,
    lock: bool = True,
    fsync: bool = True,
    indent: int | None = 2,
    ensure_ascii: bool = False,
) -> None:
    """Serialize *data* as UTF-8 JSON and write atomically.

    Defaults to ``indent=2`` / ``ensure_ascii=False`` to match the rest of
    the project's on-disk JSON style.
    """
    payload = json.dumps(data, indent=indent, ensure_ascii=ensure_ascii)
    write_bytes_atomic(path, payload.encode("utf-8"), lock=lock, fsync=fsync)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/utils/test_atomic_io.py::TestWriteJsonAtomic -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add src/qwenpaw/utils/atomic_io.py tests/unit/utils/test_atomic_io.py
git commit -m "feat(utils): add atomic_io.write_json_atomic

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Task 3: atomic_io — read_json_safe（含 json_repair 兜底）

**Files:**
- Modify: `src/qwenpaw/utils/atomic_io.py` (append `read_json_safe`)
- Test: `tests/unit/utils/test_atomic_io.py` (append `TestReadJsonSafe`)

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/utils/test_atomic_io.py`:

```python
class TestReadJsonSafe:
    """read_json_safe behavior with json_repair fallback."""

    def test_missing_file_returns_default(self, tmp_path):
        assert read_json_safe(tmp_path / "nope.json", default={"x": 1}) == {"x": 1}

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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/utils/test_atomic_io.py::TestReadJsonSafe -v`
Expected: FAIL — `ImportError: cannot import name 'read_json_safe'`

- [ ] **Step 3: Write minimal implementation**

Append to `src/qwenpaw/utils/atomic_io.py`:

```python
def read_json_safe(
    path: str | os.PathLike,
    *,
    default: Any = None,
) -> Any:
    """Read JSON from *path* with a json_repair fallback.

    Returns *default* when the file is missing or irrecoverably corrupt.
    A half-written file (crash mid-write) is typically recoverable by
    json_repair, which avoids data loss on exFAT where non-atomic
    overwrites are most fragile.
    """
    p = Path(path)
    if not p.exists():
        return default
    try:
        text = p.read_text(encoding="utf-8")
    except OSError:
        return default
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        try:
            return json.loads(repair_json(text))
        except Exception:
            return default
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/utils/test_atomic_io.py::TestReadJsonSafe -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add src/qwenpaw/utils/atomic_io.py tests/unit/utils/test_atomic_io.py
git commit -m "feat(utils): add atomic_io.read_json_safe with json_repair fallback

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Task 4: atomic_io — locked_json_update（并发 read-modify-write）

**Files:**
- Modify: `src/qwenpaw/utils/atomic_io.py` (append `locked_json_update`)
- Test: `tests/unit/utils/test_atomic_io.py` (append `TestLockedJsonUpdate`)

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/utils/test_atomic_io.py`:

```python
class TestLockedJsonUpdate:
    """locked_json_update serializes read-modify-write under the per-path lock."""

    def test_update_returns_new_value(self, tmp_path):
        path = tmp_path / "f.json"
        write_json_atomic(path, {"n": 0})
        result = locked_json_update(path, lambda cur: {"n": cur["n"] + 1})
        assert result == {"n": 1}
        assert json.loads(path.read_text(encoding="utf-8")) == {"n": 1}

    def test_missing_file_passes_default(self, tmp_path):
        path = tmp_path / "f.json"
        result = locked_json_update(
            path, lambda cur: {"n": (cur or {}).get("n", 0) + 1}, default={},
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
                    path, lambda cur: {"n": (cur or {}).get("n", 0) + 1},
                )

        threads = [threading.Thread(target=bump) for _ in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert json.loads(path.read_text(encoding="utf-8"))["n"] == expected
        assert not list(tmp_path.glob("*.tmp.*"))
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/utils/test_atomic_io.py::TestLockedJsonUpdate -v`
Expected: FAIL — `ImportError: cannot import name 'locked_json_update'`

- [ ] **Step 3: Write minimal implementation**

Append to `src/qwenpaw/utils/atomic_io.py`:

```python
def locked_json_update(
    path: str | os.PathLike,
    fn: Callable[[Any], Any],
    *,
    default: Any = None,
    fsync: bool = True,
) -> Any:
    """Read-modify-write *path* under the per-path RLock.

    *fn* receives the current data (or *default* when the file is
    missing/corrupt) and returns the new data, which is written
    atomically. The whole RMW is serialized so concurrent updaters never
    overwrite each other. Uses an RLock so *fn* may re-enter the same
    path's lock — but callers MUST NOT trigger another
    ``locked_json_update`` on the *same* path from within *fn* (that would
    imply nested RMW which is a logic error, not a supported pattern).
    """
    resolved = _resolve(path)
    with _get_lock(resolved):
        current = read_json_safe(resolved, default=default)
        updated = fn(current)
        # Already hold the lock; write without re-acquiring.
        write_json_atomic(resolved, updated, lock=False, fsync=fsync)
        return updated
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/utils/test_atomic_io.py::TestLockedJsonUpdate -v`
Expected: PASS (3 tests, including the 20×100 concurrent increment)

- [ ] **Step 5: Commit**

```bash
git add src/qwenpaw/utils/atomic_io.py tests/unit/utils/test_atomic_io.py
git commit -m "feat(utils): add atomic_io.locked_json_update for safe RMW

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Task 5: atomic_io — cleanup_orphan_tmps

**Files:**
- Modify: `src/qwenpaw/utils/atomic_io.py` (append `cleanup_orphan_tmps`)
- Test: `tests/unit/utils/test_atomic_io.py` (append `TestCleanupOrphanTmps`)

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/utils/test_atomic_io.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/utils/test_atomic_io.py::TestCleanupOrphanTmps -v`
Expected: FAIL — `ImportError: cannot import name 'cleanup_orphan_tmps'`

- [ ] **Step 3: Write minimal implementation**

Append to `src/qwenpaw/utils/atomic_io.py`:

```python
def cleanup_orphan_tmps(
    directory: str | os.PathLike,
    pattern: str = "*.tmp.*",
) -> int:
    """Remove leftover ``.tmp.<pid>`` files in *directory*.

    A crash between writing the tmp file and ``os.replace`` leaves an
    orphan; call this at startup to reclaim space and avoid confusion.
    Returns the number of files removed.
    """
    removed = 0
    for orphan in Path(directory).glob(pattern):
        try:
            orphan.unlink()
            removed += 1
        except OSError:
            pass
    return removed
```

- [ ] **Step 4: Run the full atomic_io suite**

Run: `pytest tests/unit/utils/test_atomic_io.py -v`
Expected: PASS (all tests across TestWriteBytesAtomic / TestWriteJsonAtomic / TestReadJsonSafe / TestLockedJsonUpdate / TestCleanupOrphanTmps)

- [ ] **Step 5: Commit**

```bash
git add src/qwenpaw/utils/atomic_io.py tests/unit/utils/test_atomic_io.py
git commit -m "feat(utils): add atomic_io.cleanup_orphan_tmps

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Task 6: background_tasks — spawn / shutdown

**Files:**
- Create: `src/qwenpaw/utils/background_tasks.py`
- Test: `tests/unit/utils/test_background_tasks.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/utils/test_background_tasks.py`:

```python
# -*- coding: utf-8 -*-
"""Unit tests for utils.background_tasks."""
from __future__ import annotations

import asyncio
import logging

import pytest

from qwenpaw.utils.background_tasks import BackgroundTaskRunner


class TestSpawn:
    """spawn runs coroutines and tracks them."""

    async def test_runs_to_completion(self):
        runner = BackgroundTaskRunner()
        done = asyncio.Event()

        async def work():
            done.set()

        runner.spawn(work())
        await asyncio.wait_for(done.wait(), timeout=2)
        await runner.shutdown()
        assert done.is_set()

    async def test_exception_is_logged_not_raised(self, caplog):
        runner = BackgroundTaskRunner()

        async def boom():
            raise ValueError("kaboom")

        with caplog.at_level(logging.ERROR):
            runner.spawn(boom())
            await asyncio.sleep(0.1)
            await runner.shutdown()
        assert any("Background task failed" in r.message for r in caplog.records)

    async def test_shutdown_cancels_pending(self):
        runner = BackgroundTaskRunner()
        cancelled = asyncio.Event()

        async def long_work():
            try:
                await asyncio.sleep(10)
            except asyncio.CancelledError:
                cancelled.set()
                raise

        runner.spawn(long_work())
        await asyncio.sleep(0.05)  # let it start
        await runner.shutdown(timeout=1)
        assert cancelled.is_set()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/utils/test_background_tasks.py -v`
Expected: FAIL — `ImportError: cannot import name 'BackgroundTaskRunner'`

- [ ] **Step 3: Write minimal implementation**

Create `src/qwenpaw/utils/background_tasks.py`:

```python
# -*- coding: utf-8 -*-
"""Fire-and-forget background tasks tracked for clean lifespan shutdown.

Distinct from app._app._background_startup (which loads agents): this
module is for small, deferrable, non-critical IO such as telemetry
uploads, redundant migration checks and import prefetch warmup. Tasks are
best-effort — exceptions are logged, never raised to the caller — and all
are cancelled/awaited on shutdown().
"""
from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable

logger = logging.getLogger(__name__)


class BackgroundTaskRunner:
    """Spawn tracked fire-and-forget tasks on the running event loop."""

    def __init__(self) -> None:
        self._tasks: set[asyncio.Task] = set()

    def spawn(self, coro: Awaitable, *, name: str | None = None) -> asyncio.Task:
        """Schedule *coro* as a tracked background task.

        Must be called from within a running event loop (e.g. from the
        FastAPI lifespan). The returned task is tracked so shutdown() can
        cancel it.
        """
        loop = asyncio.get_running_loop()
        task = loop.create_task(self._wrap(coro), name=name)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    async def _wrap(self, coro: Awaitable) -> None:
        try:
            await coro
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Background task failed")

    async def shutdown(self, timeout: float = 5.0) -> None:
        """Cancel all tracked tasks and wait for them to finish."""
        tasks = [t for t in self._tasks if not t.done()]
        for t in tasks:
            t.cancel()
        for t in tasks:
            try:
                await asyncio.wait_for(t, timeout=timeout)
            except (asyncio.TimeoutError, asyncio.CancelledError, Exception):
                pass
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/utils/test_background_tasks.py::TestSpawn -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add src/qwenpaw/utils/background_tasks.py tests/unit/utils/test_background_tasks.py
git commit -m "feat(utils): add BackgroundTaskRunner.spawn/shutdown

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Task 7: background_tasks — spawn_after（延迟派发）

**Files:**
- Modify: `src/qwenpaw/utils/background_tasks.py` (append `spawn_after`)
- Test: `tests/unit/utils/test_background_tasks.py` (append `TestSpawnAfter`)

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/utils/test_background_tasks.py`:

```python
class TestSpawnAfter:
    """spawn_after defers task scheduling by a delay."""

    async def test_runs_after_delay(self):
        runner = BackgroundTaskRunner()
        loop = asyncio.get_running_loop()
        start = loop.time()
        ran_at: list[float] = []

        async def work():
            ran_at.append(loop.time())

        runner.spawn_after(0.05, work)
        await asyncio.sleep(0.2)
        assert ran_at
        assert ran_at[0] - start >= 0.05
        await runner.shutdown()

    async def test_cancelled_during_delay_does_not_run(self):
        runner = BackgroundTaskRunner()
        ran = asyncio.Event()

        async def work():
            ran.set()

        task = runner.spawn_after(1.0, work)
        await asyncio.sleep(0.05)
        await runner.shutdown(timeout=1)
        assert not ran.is_set()
        assert task.done()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/utils/test_background_tasks.py::TestSpawnAfter -v`
Expected: FAIL — `AttributeError: 'BackgroundTaskRunner' object has no attribute 'spawn_after'`

- [ ] **Step 3: Write minimal implementation**

Append to `src/qwenpaw/utils/background_tasks.py` (inside the `BackgroundTaskRunner` class, after `_wrap`):

```python
    async def spawn_after(
        self,
        delay: float,
        coro_factory: Callable[[], Awaitable],
        *,
        name: str | None = None,
    ) -> asyncio.Task:
        """Schedule a task after *delay* seconds.

        *coro_factory* is a zero-arg callable returning a fresh coroutine,
        so the coroutine is only created after the delay elapses (avoids
        holding an un-awaited coroutine object). If shutdown cancels the
        task during the delay, the factory is never called.
        """

        async def _delayed() -> None:
            try:
                await asyncio.sleep(delay)
            except asyncio.CancelledError:
                return
            await coro_factory()

        return self.spawn(_delayed(), name=name)
```

- [ ] **Step 4: Run the full background_tasks suite**

Run: `pytest tests/unit/utils/test_background_tasks.py -v`
Expected: PASS (all 5 tests)

- [ ] **Step 5: Commit**

```bash
git add src/qwenpaw/utils/background_tasks.py tests/unit/utils/test_background_tasks.py
git commit -m "feat(utils): add BackgroundTaskRunner.spawn_after

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Task 8: Phase 1 收尾验证

**Files:** 无新增；验证全量回归。

- [ ] **Step 1: 跑 Phase 1 两个新测试文件**

Run: `pytest tests/unit/utils/test_atomic_io.py tests/unit/utils/test_background_tasks.py -v`
Expected: PASS（atomic_io 16 tests + background_tasks 5 tests）

- [ ] **Step 2: 跑 lint/typecheck（项目用 flake8 + black + mypy，见 .pre-commit-config.yaml 与 .flake8）**

Run: `flake8 src/qwenpaw/utils/atomic_io.py src/qwenpaw/utils/background_tasks.py`
Expected: 无错误

Run: `black --check src/qwenpaw/utils/atomic_io.py src/qwenpaw/utils/background_tasks.py`
Expected: 无 diff（若有，跑 `black src/qwenpaw/utils/atomic_io.py src/qwenpaw/utils/background_tasks.py` 格式化）

Run: `mypy src/qwenpaw/utils/atomic_io.py src/qwenpaw/utils/background_tasks.py`
Expected: 无错误

- [ ] **Step 3: 跑相关回归（utils 与 config，确保未破坏既有）**

Run: `pytest tests/unit/utils/ tests/unit/config/ -q`
Expected: PASS（新模块不影响既有）

- [ ] **Step 4: Commit（若有 lint/格式修正）**

```bash
git add -A
git commit -m "chore(utils): phase1 keystones lint/typecheck pass

Co-Authored-By: Claude <noreply@anthropic.com>"
```

（若无改动可跳过本步。）

---

## Phase 1 完成准则

- `src/qwenpaw/utils/atomic_io.py` 提供 `write_bytes_atomic` / `write_json_atomic` / `read_json_safe` / `locked_json_update` / `cleanup_orphan_tmps`，全部有测试覆盖（含 20×100 并发增量测试）。
- `src/qwenpaw/utils/background_tasks.py` 提供 `BackgroundTaskRunner.spawn` / `spawn_after` / `shutdown`，async 测试覆盖（含异常吞咽、shutdown 取消）。
- 无新依赖（仅 stdlib + 已有 json_repair）。
- 既有 utils/config 测试全绿。

Phase 2（场景三原子写）将开始调用这些基石。届时会在 `docs/superpowers/plans/2026-06-22-性能优化-phase2-atomic-writes.md` 出独立计划。

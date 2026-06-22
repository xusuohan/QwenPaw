# 性能优化 Phase 4：请求路径 IO 消除（session 异步化 + auth TTL 缓存）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把对话请求热路径上的同步文件 IO 移除——(a) `save_session_state` 的同步 `open("w")` 改异步原子写（spec §4.2，消除请求 finally 对事件循环的阻塞）；(b) `auth._load_auth_data` 加短 TTL 内存缓存（spec §4.4，把每请求多达 3 次的读盘+解密降为 ~0）。两者共同达成 spec"请求热路径同步文件写 → 0"目标。

**Architecture:** §4.2 用 Phase 1 的 `write_json_atomic`（`indent=None` 保持紧凑格式）经 `asyncio.to_thread` 异步化（与同文件 `load_session_state` 的 aiofiles 异步对齐，且获得原子 + 锁）。§4.4 在 auth.py 加模块级 TTL 缓存包裹 `_load_auth_data`（rename 旧体为 `_load_auth_data_from_disk`），`_save_auth_data` 写后 `invalidate_auth_cache()` 保证本进程写入即时可见；跨进程撤销吃 ≤TTL 延迟（brainstorming 已确认可接受）。

**Tech Stack:** Python 3.10、Phase 1 的 `atomic_io`、pytest + pytest-asyncio（asyncio_mode=auto）。

**对应 spec：** `docs/superpowers/specs/2026-06-22-性能优化-design.md` 第 4.2、4.4 节。

**前置：** Phase 1–3 已完成。本 Phase 从当前 `feature/usb-portable` HEAD 继续。

---

## 范围说明（scope check）

场景二（§4.1–4.5）体量大。本 Phase 聚焦**风险可控、内聚的"请求路径 IO 消除"两项 §4.2 + §4.4**。其余推迟：

| spec 节 | 内容 | 本 Phase | 去向 |
|---|---|---|---|
| §4.2 | session save 异步化 | ✅ Task 1 | — |
| §4.4 | auth TTL 缓存 | ✅ Task 2 | — |
| §4.1 | 模型/httpx 客户端 per-agent_id 缓存（失效逻辑复杂）| ⏸ 推迟 | Phase 4b 独立计划（flagship TTFT，需 model_factory/provider/watcher 失效设计）|
| §4.3 | chats.json 内存缓存 + 后台 flush | ⏸ 推迟 | Phase 4c 独立计划 |
| §4.5 | tool result 写异步 + token 计数增量缓存 | ⏸ 推迟 | 收尾 Phase |

---

## File Structure（Phase 4）

| 文件 | 改动 |
|---|---|
| `src/qwenpaw/app/runner/session.py` | Task 1：`save_session_state` 改 `asyncio.to_thread(write_json_atomic, ..., indent=None)` |
| `src/qwenpaw/app/auth.py` | Task 2：`_load_auth_data` TTL 缓存 + `_load_auth_data_from_disk` + `_save_auth_data` 失效 |

**测试**：`tests/unit/app/test_session_*`（若存在）/ 新建；`tests/unit/`（auth 缓存）。

**相对导入**：session.py 在 `qwenpaw/app/runner/` → `from ...utils.atomic_io import write_json_atomic`（3 点）。

---

## Task 1: session save 异步化（§4.2）

**Files:**
- Modify: `src/qwenpaw/app/runner/session.py:271-298`（`save_session_state`）

**当前**（`save_session_state`，请求 finally 路径，同步写阻塞事件循环；`load_session_state` 已是 aiofiles 异步）：
```python
        session_save_path = self._get_save_path(
            session_id,
            user_id=user_id,
            channel=channel,
        )
        with open(
            session_save_path,
            "w",
            encoding="utf-8",
        ) as f:
            f.write(json.dumps(state_dicts, ensure_ascii=False))

        logger.info(
            "Saved session state to %s successfully.",
            session_save_path,
        )
```

- [ ] **Step 1: 确认/补 asyncio 与 atomic_io 导入**

检查 `src/qwenpaw/app/runner/session.py` 顶部是否已 `import asyncio`；若无则加。再加（3 点）：

```python
from ...utils.atomic_io import write_json_atomic
```

- [ ] **Step 2: 改 `save_session_state` 写盘段**

把上面的 `with open(...) as f: f.write(json.dumps(state_dicts, ensure_ascii=False))` 替换为（`indent=None` 保持紧凑格式与现状一致；`to_thread` 不阻塞事件循环；write_json_atomic 自带 per-path 锁 + tmp+fsync+replace）：

```python
        session_save_path = self._get_save_path(
            session_id,
            user_id=user_id,
            channel=channel,
        )
        await asyncio.to_thread(
            write_json_atomic,
            session_save_path,
            state_dicts,
            indent=None,
        )

        logger.info(
            "Saved session state to %s successfully.",
            session_save_path,
        )
```

（`save_session_state` 本就是 `async def`，`await to_thread` 合法。`_get_save_path` 已做 `os.makedirs`，write_json_atomic 也自带 mkdir，无冲突。）

- [ ] **Step 3: 验证**

```bash
pytest tests/unit/app/ -k "session or chat or runner" -q
python -c "import qwenpaw.app.runner.session; print('import OK')"
```
Expected: PASS + import OK。（若无 session 相关单测，加一个最小 roundtrip——见 Step 4。）

- [ ] **Step 4: 若无 session save 单测，补一个**

`src/qwenpaw/app/runner/session.py` 的类是 `SafeJSONSession`（`__init__(self, save_dir: str = "./", ...)`，line 196/203）。现有 `tests/unit/agents/test_session.py` 测的是 agents 侧的 session 模块，不覆盖 runner 的 `save_session_state`。新建 `tests/unit/app/test_runner_session.py`：

```python
# -*- coding: utf-8 -*-
"""Tests for runner SafeJSONSession async atomic save."""
from __future__ import annotations

import json

from qwenpaw.app.runner.session import SafeJSONSession


class _State:
    def __init__(self, payload):
        self._payload = payload

    def state_dict(self):
        return self._payload


class TestSaveSessionState:
    async def test_save_writes_async_compact_no_tmp_leftover(self, tmp_path):
        saver = SafeJSONSession(save_dir=str(tmp_path))
        await saver.save_session_state(
            "sid1", user_id="u", channel="c",
            memory=_State({"k": "v"}),
        )
        files = list(tmp_path.glob("*.json"))
        assert files, "session file not written"
        data = json.loads(files[0].read_text(encoding="utf-8"))
        assert data == {"memory": {"k": "v"}}
        # compact (indent=None): no pretty-print newlines beyond structure
        assert "\n  " not in files[0].read_text(encoding="utf-8")
        assert not list(tmp_path.glob("*.tmp.*"))
```

（若 `SafeJSONSession.__init__` 有其他必填参数，按 `sed -n '196,215p' src/qwenpaw/app/runner/session.py` 的真实签名补最小可用值；核心断言不变：异步写出、紧凑 JSON、无 tmp 残留。）

- [ ] **Step 5: Commit**

```bash
git add src/qwenpaw/app/runner/session.py tests/unit/app/test_session_state.py
git commit -m "perf(runner): async + atomic save_session_state (off the event loop)

Co-Authored-By: Claude <noreply@anthropic.com>"
```
（pre-commit 跑；re-stage 若 reformats。`M src/qwenpaw/cli/desktop_cmd.py` 勿动。）

---

## Task 2: auth `_load_auth_data` TTL 缓存（§4.4）

**Files:**
- Modify: `src/qwenpaw/app/auth.py:207-242`（`_load_auth_data`）+ `_save_auth_data`（失效）

**当前**：`_load_auth_data()`（207）每次都 `open(AUTH_FILE) + json.load + decrypt_dict_fields`。每请求经 `verify_token`→`_get_jwt_secret`→`_load_auth_data`、`_is_token_revoked`→`_load_auth_data`、`_should_skip_auth`→`has_registered_users`→`_load_auth_data`，多达 3 次读盘+解密。

**改动**：模块级 TTL 缓存包裹；写后失效。

- [ ] **Step 1: 补 threading 导入 + 缓存状态**

`auth.py` 已 `import time`（line 26）。顶部加：

```python
import threading
```

在模块级常量区（`AUTH_FILE = ...` 附近）加缓存状态：

```python
# Short-lived in-process cache for _load_auth_data(): avoids up to 3
# reads+decrypts per authenticated request. Invalidated on every local
# write (_save_auth_data). Cross-process revocation is seen within TTL
# (~3s) — accepted per design.
AUTH_DATA_CACHE_TTL = 3.0
_auth_data_cache: dict | None = None
_auth_data_cache_ts: float = 0.0
_auth_data_cache_lock = threading.Lock()


def invalidate_auth_cache() -> None:
    """Drop the in-process auth-data cache (call after any auth write)."""
    global _auth_data_cache
    with _auth_data_cache_lock:
        _auth_data_cache = None
```

- [ ] **Step 2: rename 旧 `_load_auth_data` 为 `_load_auth_data_from_disk`**

把现有 `def _load_auth_data() -> dict:`（207）整段（含 docstring + body，到 `return {}`）重命名为 `def _load_auth_data_from_disk() -> dict:`。**body 一字不改**（含内部对 `_save_auth_data(data)` 的明文迁移调用）。

- [ ] **Step 3: 新增带缓存的 `_load_auth_data`**

在 `_load_auth_data_from_disk` 之后加：

```python
def _load_auth_data() -> dict:
    """Return auth data with a short-lived in-process cache.

    Delegates to :func:`_load_auth_data_from_disk` on a cache miss; serves
    the cached dict within ``AUTH_DATA_CACHE_TTL`` seconds. The cache is
    dropped by :func:`invalidate_auth_cache` (called from
    :func:`_save_auth_data`) so local writes are visible immediately.
    """
    global _auth_data_cache, _auth_data_cache_ts
    now = time.monotonic()
    with _auth_data_cache_lock:
        cached = _auth_data_cache
        if cached is not None and (now - _auth_data_cache_ts) < AUTH_DATA_CACHE_TTL:
            return cached
    data = _load_auth_data_from_disk()
    with _auth_data_cache_lock:
        _auth_data_cache = data
        _auth_data_cache_ts = now
    return data
```

（注意：disk loader 内部对明文迁移会调 `_save_auth_data(data)` → 触发 `invalidate_auth_cache()`（Step 4 加）。此处先填缓存再返回；若迁移触发了 invalidate，下次读会重读——语义正确。）

- [ ] **Step 4: `_save_auth_data` 写后失效缓存**

`_save_auth_data`（Phase 2 已改为 `write_json_atomic`）末尾的 `_chmod_best_effort(AUTH_FILE, 0o600)` 之后加一行：

```python
    invalidate_auth_cache()
```

（确保本进程任何写入——注册、登录改密、撤销 token——即时反映，无 TTL 延迟。）

- [ ] **Step 5: 写缓存测试**

新建 `tests/unit/app/test_auth_cache.py`（或 `tests/unit/security/`，按现有 auth 测试位置）：

```python
# -*- coding: utf-8 -*-
"""Tests for auth._load_auth_data TTL cache."""
from __future__ import annotations

import qwenpaw.app.auth as auth
from qwenpaw.app.auth import (
    _load_auth_data,
    invalidate_auth_cache,
)


class TestAuthDataCache:
    def setup_method(self):
        invalidate_auth_cache()

    def teardown_method(self):
        invalidate_auth_cache()

    def test_cache_hit_avoids_reread_within_ttl(self, monkeypatch):
        calls = {"n": 0}
        real = auth._load_auth_data_from_disk

        def counting():
            calls["n"] += 1
            return {"jwt_secret": "s", "revoked_tokens": []}

        monkeypatch.setattr(auth, "_load_auth_data_from_disk", counting)
        _load_auth_data()
        _load_auth_data()
        _load_auth_data()
        assert calls["n"] == 1  # disk read once, rest served from cache

    def test_invalidate_forces_reread(self, monkeypatch):
        calls = {"n": 0}

        def counting():
            calls["n"] += 1
            return {"jwt_secret": "s"}

        monkeypatch.setattr(auth, "_load_auth_data_from_disk", counting)
        _load_auth_data()
        invalidate_auth_cache()
        _load_auth_data()
        assert calls["n"] == 2

    def test_expiry_after_ttl(self, monkeypatch):
        calls = {"n": 0}
        timeline = [100.0]

        def counting():
            calls["n"] += 1
            return {"jwt_secret": "s"}

        monkeypatch.setattr(auth, "_load_auth_data_from_disk", counting)
        monkeypatch.setattr(auth.time, "monotonic", lambda: timeline[0])
        _load_auth_data()  # populate at t=100
        timeline[0] = 100.0 + auth.AUTH_DATA_CACHE_TTL + 0.01  # past TTL
        _load_auth_data()  # miss → reread
        assert calls["n"] == 2
```

- [ ] **Step 6: Run tests**

```bash
pytest tests/unit/app/test_auth_cache.py -v
pytest tests/unit/security/ tests/unit/app/ -q
```
Expected: 缓存测试 PASS + 回归 PASS。

- [ ] **Step 7: Commit**

```bash
git add src/qwenpaw/app/auth.py tests/unit/app/test_auth_cache.py
git commit -m "perf(auth): TTL cache for _load_auth_data + invalidate on save

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Task 3: Phase 4 验证 + 最终评审

- [ ] **Step 1: 全量回归**

```bash
pytest tests/unit/app/ tests/unit/security/ tests/unit/utils/ -q
```
Expected: 全 PASS。

- [ ] **Step 2: lint/typecheck**

```bash
flake8 src/qwenpaw/app/runner/session.py src/qwenpaw/app/auth.py
pre-commit run black --files src/qwenpaw/ tests/
```
Expected: 无错（pre-commit 为 canonical；本地 flake8 对预存 black 风格切片可能误报 E203，以 pre-commit 为准）。

- [ ] **Step 3: 最终 code review（整个 Phase 4）**

对 Phase 4 全量改动做 code review（base = Phase 3 head `ff49e062`，head = 本 Phase 最终提交），确认：session save 确实异步 + 原子、auth 缓存命中正确且写后即时失效、缓存对并发/迁移安全、无回归。

---

## Phase 4 完成准则

- `save_session_state` 经 `asyncio.to_thread(write_json_atomic, ..., indent=None)`，请求 finally 不再阻塞事件循环，且紧凑格式保持。
- `_load_auth_data` TTL 缓存：每请求多次调用只读盘+解密一次（≤TTL 内）；`_save_auth_data` 写后 `invalidate_auth_cache()` 本进程即时生效。
- 受影响模块测试全绿；lint/typecheck（pre-commit）通过。

后续 Phase（§4.1 模型客户端缓存 / §4.3 chats 缓存 / §4.5 收尾）各出独立计划。

---

## 风险点

1. **session 写格式**：必须 `indent=None` 保持紧凑，否则会一次性重格式化所有 session 文件（无害但不必）。`ensure_ascii=False` 是 write_json_atomic 默认，与现状一致。
2. **auth 缓存返回可变对象**：`_load_auth_data` 返回缓存 dict；调用方中 `_add_to_revocation_list`/`_clean_expired_revocations` 会就地改再 `_save_auth_data`+invalidate。改后立即失效，缓存不会被"脏读"长期污染。单进程 GIL 下短暂修改窗口可接受（单实例约束）。
3. **明文迁移路径**：`_load_auth_data_from_disk` 内部对明文字段会 `_save_auth_data(data)` → invalidate。缓存填充与 invalidate 顺序正确（先填后返；迁移 invalidate 后下次重读为已加密态）。
4. **跨进程撤销延迟 ≤TTL**：brainstorming 已确认可接受（~3s）。本进程撤销经 `_save_auth_data`→invalidate 无延迟。
5. **测试用 monkeypatch 改 `auth.time.monotonic`**：确认 `auth.py` 用的是 `time.monotonic`（已 `import time`），patch `auth.time.monotonic` 有效。
6. **`SessionStateSaver` 命名/签名**：Task 1 Step 4 测试需先 grep 确认真实类名与 `__init__(save_dir=...)` 签名。

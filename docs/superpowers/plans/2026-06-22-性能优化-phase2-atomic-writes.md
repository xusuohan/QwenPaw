# 性能优化 Phase 2：场景三原子写 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 8 处裸覆写/无锁写收敛到 Phase 1 的 `atomic_io` 基石（消除并发下的配置/会话/卡片/任务数据丢失与损坏），并修复 `ChannelManager.replace_channel` 的持锁内 `await stop()` 自死锁。

**Architecture:** Phase 1 已交付 `utils/atomic_io.py`（`write_json_atomic` / `locked_json_update` / `read_json_safe` / `cleanup_orphan_tmps`）。Phase 2 是"调用方迁移"——把 8 个站点从手写 `open("w")+json.dump` 或 tmp+move 替换为 `write_json_atomic`（自动获得进程内锁 + tmp+fsync+replace + 孤儿清理）。Task 1 给基石补一个向后兼容的 `sort_keys` 形参（json_repo 需要）。ChannelManager 死锁是结构性修复（swap 在锁内、stop 移到锁外）。

**Tech Stack:** Python 3.10、Phase 1 的 `atomic_io` 基石、pytest + pytest-asyncio（asyncio_mode=auto）。

**对应 spec：** `docs/superpowers/specs/2026-06-22-性能优化-design.md` 第 5.1、5.2 节。

**前置：** Phase 1 已完成（基石 `utils/atomic_io.py` + `utils/background_tasks.py` 已提交，base `0d5ca9ad` → head `2108d464`）。本 Phase 从当前 `feature/usb-portable` HEAD 继续。

---

## File Structure（Phase 2）

**修改（生产代码）**——8 个写站点 + 1 个死锁修复 + 1 个基石扩展：

| 文件 | 改动 |
|---|---|
| `src/qwenpaw/utils/atomic_io.py` | Task 1：`write_json_atomic` 增加 `sort_keys` 形参 |
| `src/qwenpaw/config/utils.py` | Task 2：`save_config` → `write_json_atomic` |
| `src/qwenpaw/config/config.py` | Task 2：`save_agent_config` → `write_json_atomic` |
| `src/qwenpaw/app/auth.py` | Task 3：`_save_auth_data` → `write_json_atomic`（保留 chmod）|
| `src/qwenpaw/app/channels/dingtalk/channel.py` | Task 4：`_save_session_webhook_store_to_disk` → `write_json_atomic` |
| `src/qwenpaw/app/channels/dingtalk/ai_card.py` | Task 4：`save` → `write_json_atomic` |
| `src/qwenpaw/local_models/manager.py` | Task 5：`_write_config_file` → `write_json_atomic`（保留 chmod）|
| `src/qwenpaw/app/crons/repo/json_repo.py` | Task 6：`JsonJobRepository.save` → `write_json_atomic(sort_keys=True)` |
| `src/qwenpaw/app/runner/repo/json_repo.py` | Task 6：`JsonChatRepository.save` → `write_json_atomic(sort_keys=True)` |
| `src/qwenpaw/app/channels/manager.py` | Task 7：`replace_channel` 死锁修复（stop 移出锁）|
| `src/qwenpaw/app/_app.py` | Task 8：启动期 `cleanup_orphan_tmps(WORKING_DIR)` |

**新增（测试）**：
- `tests/unit/utils/test_atomic_io.py`（Task 1 追加 `sort_keys` 测试）
- `tests/unit/app/crons/test_json_repo.py`（Task 6 新建）
- `tests/unit/channels/test_channel_manager.py`（Task 7 新建，死锁回归）

**设计边界**：每个写站点只换"如何落盘"，不改数据结构/序列化字段/调用方语义。`save_config`/`save_agent_config`/`_save_auth_data`/`save` 都是**整体写**（非 RMW），故用 `write_json_atomic`（默认 `lock=True` 仍串行化同路径并发整写）。`chmod 0o600` 在 auth/local_models 保留（best-effort，exFAT 无害）。

**重要：相对导入深度**（各站点到 `qwenpaw.utils.atomic_io`）：
- `config/utils.py`、`config/config.py`、`app/auth.py`、`local_models/manager.py` → `from ..utils.atomic_io import write_json_atomic`（2 点）
- `app/channels/dingtalk/*.py`、`app/crons/repo/*.py`、`app/runner/repo/*.py` → `from ....utils.atomic_io import write_json_atomic`（4 点）

---

## Task 1: 基石扩展 — `write_json_atomic` 增加 `sort_keys`

json_repo 用 `sort_keys=True` 生成确定性输出。给基石加一个向后兼容形参（默认 `False`，不影响 Phase 1 既有调用方）。

**Files:**
- Modify: `src/qwenpaw/utils/atomic_io.py`（`write_json_atomic` 签名 + `json.dumps`）
- Test: `tests/unit/utils/test_atomic_io.py`（追加 `sort_keys` 测试）

- [ ] **Step 1: Write the failing test**

在 `tests/unit/utils/test_atomic_io.py` 的 `TestWriteJsonAtomic` 类里追加：

```python
    def test_sort_keys_orders_keys(self, tmp_path):
        path = tmp_path / "f.json"
        write_json_atomic(
            path, {"b": 1, "a": 2}, sort_keys=True,
        )
        text = path.read_text(encoding="utf-8")
        assert text.index('"a"') < text.index('"b"')

    def test_sort_keys_default_false_preserves_insertion_order(self, tmp_path):
        path = tmp_path / "f.json"
        write_json_atomic(path, {"b": 1, "a": 2})
        text = path.read_text(encoding="utf-8")
        assert text.index('"b"') < text.index('"a"')
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/utils/test_atomic_io.py::TestWriteJsonAtomic::test_sort_keys_orders_keys -v`
Expected: FAIL — `TypeError: write_json_atomic() got an unexpected keyword argument 'sort_keys'`

- [ ] **Step 3: Write minimal implementation**

修改 `src/qwenpaw/utils/atomic_io.py` 的 `write_json_atomic`：

```python
def write_json_atomic(
    path: str | os.PathLike,
    data: Any,
    *,
    lock: bool = True,
    fsync: bool = True,
    indent: int | None = 2,
    ensure_ascii: bool = False,
    sort_keys: bool = False,
) -> None:
    """Serialize *data* as UTF-8 JSON and write atomically.

    Defaults to ``indent=2`` / ``ensure_ascii=False`` to match the rest of
    the project's on-disk JSON style. ``lock`` and ``fsync`` are forwarded
    to :func:`write_bytes_atomic`; see its docstring for the ``lock=False``
    caveat.
    """
    payload = json.dumps(
        data,
        indent=indent,
        ensure_ascii=ensure_ascii,
        sort_keys=sort_keys,
    )
    write_bytes_atomic(path, payload.encode("utf-8"), lock=lock, fsync=fsync)
```

（仅新增 `sort_keys: bool = False` 形参与 `sort_keys=sort_keys` 传参，其余不变。）

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/utils/test_atomic_io.py -v`
Expected: PASS（含两个新 sort_keys 测试 + 既有 17 个，共 19）

- [ ] **Step 5: Commit**

```bash
git add src/qwenpaw/utils/atomic_io.py tests/unit/utils/test_atomic_io.py
git commit -m "feat(utils): add sort_keys kwarg to write_json_atomic

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Task 2: config 写入迁移（save_config + save_agent_config）

**Files:**
- Modify: `src/qwenpaw/config/utils.py:783-802`（`save_config`）
- Modify: `src/qwenpaw/config/config.py:1837-1882`（`save_agent_config`）

行为保持的整写迁移（非 RMW）。`write_json_atomic` 默认 `indent=2, ensure_ascii=False` 与现状一致，且自动 `mkdir` 父目录、串行化同路径并发写。缓存失效逻辑（`_config_cache`/`_agent_config_cache`）保持不动。

- [ ] **Step 1: `config/utils.py` — 加导入**

在 `src/qwenpaw/config/utils.py` 顶部 import 区（与现有 stdlib/第三方导入一起，按字母序）加：

```python
from ..utils.atomic_io import write_json_atomic
```

- [ ] **Step 2: `config/utils.py` — 改 `save_config` 写盘段**

把 `save_config` 内（约 789-796 行）：

```python
    config_path.parent.mkdir(parents=True, exist_ok=True)
    with open(config_path, "w", encoding="utf-8") as file:
        json.dump(
            config.model_dump(mode="json", by_alias=True),
            file,
            indent=2,
            ensure_ascii=False,
        )
```

替换为：

```python
    write_json_atomic(
        config_path,
        config.model_dump(mode="json", by_alias=True),
    )
```

（后续 `with _config_lock: ...` 缓存失效段不动。）

- [ ] **Step 3: `config/config.py` — 加导入**

在 `src/qwenpaw/config/config.py` 顶部 import 区加：

```python
from ..utils.atomic_io import write_json_atomic
```

- [ ] **Step 4: `config/config.py` — 改 `save_agent_config` 写盘段**

把 `save_agent_config` 内（约 1871-1877 行）：

```python
    with open(agent_config_path, "w", encoding="utf-8") as f:
        json.dump(
            agent_config.model_dump(exclude_none=True),
            f,
            ensure_ascii=False,
            indent=2,
        )
```

替换为：

```python
    write_json_atomic(
        agent_config_path,
        agent_config.model_dump(exclude_none=True),
    )
```

（`workspace_dir.mkdir(...)` 与缓存失效段不动。）

- [ ] **Step 5: 验证现有测试不回归**

Run:
```bash
pytest tests/unit/config/ tests/unit/app/test_agents_ordering.py tests/unit/workspace/test_agent_model.py tests/unit/cli/test_cli_agents.py -q
```
Expected: 全 PASS。

- [ ] **Step 6: 原子性冒烟（确认无 .tmp 残留）**

Run:
```bash
python -c "
from pathlib import Path
import tempfile
from qwenpaw.config.utils import save_config
from qwenpaw.config.config import Config
d = Path(tempfile.mkdtemp())
p = d / 'config.json'
import qwenpaw.config.utils as u
u._config_path_for_test = p if hasattr(u,'_config_path_for_test') else None
save_config(Config(), p)
assert p.exists() and not list(d.glob('*.tmp.*'))
print('OK: config written atomically, no tmp leftover')
"
```
Expected: `OK: config written atomically, no tmp leftover`（若 `Config()` 默认构造有必填字段，改成最小可用 Config；重点是断言无 `.tmp.*` 残留。若冒烟脚本因 Config 构造复杂而难写，可跳过本步，依赖 Step 5 的现有测试。）

- [ ] **Step 7: Commit**

```bash
git add src/qwenpaw/config/utils.py src/qwenpaw/config/config.py
git commit -m "refactor(config): save_config/save_agent_config via atomic_io

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Task 3: auth `_save_auth_data` 迁移（保留 chmod）

**Files:**
- Modify: `src/qwenpaw/app/auth.py:244-253`（`_save_auth_data`）

`write_json_atomic` 默认 `indent=2, ensure_ascii=False` 与现状一致。`_chmod_best_effort(AUTH_FILE, 0o600)` 保留（best-effort，exFAT 无效但不依赖；非 exFAT 上仍提供保护，行为与现状一致）。

- [ ] **Step 1: 加导入**

在 `src/qwenpaw/app/auth.py` 顶部 import 区加：

```python
from ..utils.atomic_io import write_json_atomic
```

- [ ] **Step 2: 改 `_save_auth_data` 写盘段**

把 `_save_auth_data` 内（约 249-253 行）：

```python
    _prepare_secret_parent(AUTH_FILE)
    encrypted_data = encrypt_dict_fields(data, AUTH_SECRET_FIELDS)
    with open(AUTH_FILE, "w", encoding="utf-8") as f:
        json.dump(encrypted_data, f, indent=2, ensure_ascii=False)
    _chmod_best_effort(AUTH_FILE, 0o600)
```

替换为：

```python
    _prepare_secret_parent(AUTH_FILE)
    encrypted_data = encrypt_dict_fields(data, AUTH_SECRET_FIELDS)
    write_json_atomic(AUTH_FILE, encrypted_data)
    # chmod is best-effort (no-op on exFAT); kept for non-exFAT defense.
    _chmod_best_effort(AUTH_FILE, 0o600)
```

- [ ] **Step 3: 验证现有测试不回归**

Run: `pytest tests/unit/security/ -q`（若该目录为空或无 auth 相关测试，改跑 `pytest tests/unit/ -k "auth or security" -q`）
Expected: 全 PASS（或无测试则跳过，依赖 Step 4 冒烟）。

- [ ] **Step 4: 原子性冒烟**

Run:
```bash
python -c "
from pathlib import Path
import tempfile
from qwenpaw.app import auth
d = Path(tempfile.mkdtemp())
auth.AUTH_FILE = d / 'auth.json'
auth._save_auth_data({'jwt_secret': 'x', 'users': []})
assert auth.AUTH_FILE.exists() and not list(d.glob('*.tmp.*'))
print('OK: auth written atomically')
"
```
Expected: `OK: auth written atomically`（若 `encrypt_dict_fields` 需要真实 secret key 初始化而难跑，可跳过本步。）

- [ ] **Step 5: Commit**

```bash
git add src/qwenpaw/app/auth.py
git commit -m "refactor(auth): _save_auth_data via atomic_io (keep best-effort chmod)

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Task 4: dingtalk 写入迁移（session_webhook store + ai_card）

**Files:**
- Modify: `src/qwenpaw/app/channels/dingtalk/channel.py:458-475`（`_save_session_webhook_store_to_disk`）
- Modify: `src/qwenpaw/app/channels/dingtalk/ai_card.py:54-78`（`save`）

`ai_card` 现状已 tmp+replace 但**无锁**；`channel` 现状裸 `open("w")`。两者都收敛到 `write_json_atomic`（补进程内锁 + fsync）。

- [ ] **Step 1: `channel.py` — 加导入**

在 `src/qwenpaw/app/channels/dingtalk/channel.py` 顶部 import 区加：

```python
from ....utils.atomic_io import write_json_atomic
```

- [ ] **Step 2: `channel.py` — 改 `_save_session_webhook_store_to_disk`**

把该方法体（约 460-475 行）：

```python
        path = self._session_webhook_store_path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(
                    self._session_webhook_store,
                    f,
                    indent=2,
                    ensure_ascii=False,
                )
        except Exception:
            logger.debug(
                "dingtalk save session_webhook store to %s failed",
                path,
                exc_info=True,
            )
```

替换为：

```python
        path = self._session_webhook_store_path()
        try:
            write_json_atomic(path, self._session_webhook_store)
        except Exception:
            logger.debug(
                "dingtalk save session_webhook store to %s failed",
                path,
                exc_info=True,
            )
```

（`write_json_atomic` 自带 `mkdir`，故删除显式 `mkdir`；`try/except` 日志行为保持。）

- [ ] **Step 3: `ai_card.py` — 加导入**

在 `src/qwenpaw/app/channels/dingtalk/ai_card.py` 顶部 import 区加：

```python
from ....utils.atomic_io import write_json_atomic
```

- [ ] **Step 4: `ai_card.py` — 改 `save`**

把 `save` 末尾（约 73-78 行）：

```python
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        tmp.replace(self._path)
```

替换为：

```python
        write_json_atomic(self._path, data)
```

（其上构造 `data = {...}`、`pending_cards = [...]`、`self._path.parent.mkdir(...)` 段保持；`mkdir` 可保留也可删（write_json_atomic 自带），为最小改动保留它。）

- [ ] **Step 5: 验证现有测试不回归**

Run: `pytest tests/unit/channels/test_dingtalk.py -q`
Expected: 全 PASS。

- [ ] **Step 6: Commit**

```bash
git add src/qwenpaw/app/channels/dingtalk/channel.py src/qwenpaw/app/channels/dingtalk/ai_card.py
git commit -m "refactor(dingtalk): session_webhook store + ai_card save via atomic_io

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Task 5: local_models `_write_config_file` 迁移（保留 chmod）

**Files:**
- Modify: `src/qwenpaw/local_models/manager.py:83-99`（`_write_config_file`）

`write_json_atomic` 默认 `ensure_ascii=False, indent=2` 与现状一致。`config_path.chmod(0o600)` 保留（best-effort）。注意 `_write_config_file` 经 `asyncio.to_thread` 调用（同步函数），`write_json_atomic` 同步、其 `RLock` 适配 `to_thread`。

- [ ] **Step 1: 加导入**

在 `src/qwenpaw/local_models/manager.py` 顶部 import 区加：

```python
from ..utils.atomic_io import write_json_atomic
```

- [ ] **Step 2: 改 `_write_config_file` 写盘段**

把该方法体（约 88-99 行）：

```python
        config_path.parent.mkdir(parents=True, exist_ok=True)
        with open(config_path, "w", encoding="utf-8") as file_obj:
            json.dump(
                payload,
                file_obj,
                ensure_ascii=False,
                indent=2,
            )
        try:
            config_path.chmod(0o600)
        except OSError:
            pass
```

替换为：

```python
        write_json_atomic(config_path, payload)
        try:
            # chmod is best-effort (no-op on exFAT); kept for non-exFAT.
            config_path.chmod(0o600)
        except OSError:
            pass
```

- [ ] **Step 3: 验证现有测试不回归**

Run: `pytest tests/unit/local_models/ -q`
Expected: 全 PASS。

- [ ] **Step 4: Commit**

```bash
git add src/qwenpaw/local_models/manager.py
git commit -m "refactor(local_models): _write_config_file via atomic_io (keep best-effort chmod)

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Task 6: json_repo 迁移（crons jobs + runner chats）+ 原子性测试

**Files:**
- Modify: `src/qwenpaw/app/crons/repo/json_repo.py:41-51`（`JsonJobRepository.save`）
- Modify: `src/qwenpaw/app/runner/repo/json_repo.py:56-75`（`JsonChatRepository.save`）
- Test: `tests/unit/app/crons/test_json_repo.py`（新建）

两者现状 tmp+`shutil.move`、**无锁**。收敛到 `write_json_atomic(sort_keys=True)`（补进程内锁，保留 `sort_keys` 确定性输出）。`save` 是 async，直接调同步 `write_json_atomic`（与现状 `write_text` 同为同步、不新增阻塞；Phase 4 会在 chats 侧加缓存层）。

- [ ] **Step 1: 加导入（两个文件）**

在 `src/qwenpaw/app/crons/repo/json_repo.py` 与 `src/qwenpaw/app/runner/repo/json_repo.py` 顶部 import 区各加：

```python
from ....utils.atomic_io import write_json_atomic
```

- [ ] **Step 2: `crons` json_repo — 改 `JsonJobRepository.save`**

把 `save` 方法体（约 42-51 行）：

```python
        self._path.parent.mkdir(parents=True, exist_ok=True)

        tmp_path = self._path.with_suffix(self._path.suffix + ".tmp")
        payload = jobs_file.model_dump(mode="json")

        tmp_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        shutil.move(str(tmp_path), str(self._path))
```

替换为：

```python
        payload = jobs_file.model_dump(mode="json")
        write_json_atomic(self._path, payload, sort_keys=True)
```

- [ ] **Step 3: `runner` json_repo — 改 `JsonChatRepository.save`**

把 `save` 方法体（约 62-75 行）：

```python
        # Create parent directory if needed
        self._path.parent.mkdir(parents=True, exist_ok=True)

        # Write to temp file first (atomic write)
        tmp_path = self._path.with_suffix(self._path.suffix + ".tmp")
        payload = chats_file.model_dump(mode="json")

        tmp_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )

        # Atomic replace (shutil.move handles cross-disk on Windows)
        shutil.move(str(tmp_path), str(self._path))
```

替换为：

```python
        payload = chats_file.model_dump(mode="json")
        write_json_atomic(self._path, payload, sort_keys=True)
```

- [ ] **Step 4: 写原子性测试**

新建 `tests/unit/app/crons/__init__.py`（空）与 `tests/unit/app/crons/test_json_repo.py`：

```python
# -*- coding: utf-8 -*-
"""Unit tests for crons JsonJobRepository atomic save."""
from __future__ import annotations

from pathlib import Path

import pytest

from qwenpaw.app.crons.models import JobsFile
from qwenpaw.app.crons.repo.json_repo import JsonJobRepository


class TestJsonJobRepositorySave:
    """save writes valid JSON atomically (no tmp leftover)."""

    async def test_save_roundtrip_and_no_tmp_leftover(self, tmp_path):
        # JobsFile() defaults: version=1, jobs=[] (both have defaults in
        # src/qwenpaw/app/crons/models.py), so no need to construct a
        # CronJobSpec. The point is to exercise the atomic save path.
        path = tmp_path / "jobs.json"
        repo = JsonJobRepository(path)
        await repo.save(JobsFile())
        assert path.exists()
        assert not list(tmp_path.glob("*.tmp.*"))
        loaded = await repo.load()
        assert loaded.version == 1
        assert loaded.jobs == []
```

（`JobsFile` 定义于 `src/qwenpaw/app/crons/models.py:171`，`version` 与 `jobs` 均有默认值；`JobsFile()` 即最小可用实例。）

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/unit/app/crons/test_json_repo.py -v`
Expected: PASS。（若 `JobSpec` 构造需要更多字段，先调整测试使其最小可构造；务必保持"无 tmp 残留 + roundtrip"两个断言。）

- [ ] **Step 6: 验证 runner json_repo 同理（冒烟）**

Run:
```bash
python -c "
import asyncio, tempfile
from pathlib import Path
from qwenpaw.app.runner.models import ChatsFile
from qwenpaw.app.runner.repo.json_repo import JsonChatRepository
async def main():
    d = Path(tempfile.mkdtemp()); p = d/'chats.json'
    repo = JsonChatRepository(p)
    await repo.save(ChatsFile(version=1, chats=[]))
    assert p.exists() and not list(d.glob('*.tmp.*'))
    print('OK: chats repo atomic save')
asyncio.run(main())
"
```
Expected: `OK: chats repo atomic save`（若 `ChatsFile` 字段不符，按 `runner/models.py` 调整。）

- [ ] **Step 7: Commit**

```bash
git add src/qwenpaw/app/crons/repo/json_repo.py src/qwenpaw/app/runner/repo/json_repo.py tests/unit/app/crons/
git commit -m "refactor(repo): crons/runner json_repo save via atomic_io (lock + sort_keys)

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Task 7: ChannelManager `replace_channel` 死锁修复 + 回归测试

**Files:**
- Modify: `src/qwenpaw/app/channels/manager.py:704-761`（`replace_channel`）
- Test: `tests/unit/channels/test_channel_manager.py`（新建）

**问题**：`replace_channel` 在 `async with self._lock:` 内部 `await old_channel.stop()`（约 755 行）。若 `stop()` 回调 `get_channel`/`enqueue`/`send_event`（需同一把 `self._lock`），asyncio.Lock 不可重入 → **自死锁**，整个 channel manager 挂起。

**修复**：照搬同文件 `restart_channel`（580）的正确模式——**swap 在锁内、stop 移到锁外**。

- [ ] **Step 1: 写死锁回归测试（先红）**

新建 `tests/unit/channels/test_channel_manager.py`。用**鸭子类型 fake**（不继承 `BaseChannel`，避免实现一堆抽象方法）——`ChannelManager` 运行时按属性查找（`.channel` / `.start` / `.stop` / `uses_manager_queue`），接受鸭子类型对象。`ChannelManager.__init__(channels: List[BaseChannel])` 接受一个 channels 列表（见 `manager.py:73`）；`get_channel(channel)` 只获取 `self._lock` 并遍历 `self.channels`（见 `manager.py:542-547`），无队列依赖。

```python
# -*- coding: utf-8 -*-
"""Regression tests for ChannelManager.replace_channel deadlock."""
from __future__ import annotations

import asyncio

import pytest

from qwenpaw.app.channels.manager import ChannelManager


class _FakeChannel:
    """Minimal duck-typed channel (not a BaseChannel subclass)."""

    uses_manager_queue = False  # skip set_enqueue in replace_channel

    def __init__(self, name: str):
        self.channel = name

    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        return None


class _ReenteringStopChannel:
    """A channel whose stop() calls back into the manager (deadlock case)."""

    uses_manager_queue = False

    def __init__(self, name: str, manager: ChannelManager):
        self.channel = name
        self._manager = manager
        self.stopped = asyncio.Event()

    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        # Re-enter the manager during stop — get_channel acquires self._lock.
        await self._manager.get_channel(self.channel)
        self.stopped.set()


class TestReplaceChannelDeadlock:
    """stop() that re-enters the manager must not self-deadlock."""

    async def test_replace_does_not_deadlock_on_reentering_stop(self):
        manager = ChannelManager(channels=[])
        old = _ReenteringStopChannel("dt", manager)
        manager.channels.append(old)
        new = _FakeChannel("dt")
        # Old code: replace_channel holds self._lock during old.stop(),
        # which awaits get_channel -> self._lock -> hang -> TimeoutError.
        # Fixed code: stop() runs outside the lock -> completes.
        await asyncio.wait_for(manager.replace_channel(new), timeout=3)
        assert old.stopped.is_set()
```

**说明**：`ChannelManager(channels=[])` 的 `__init__`（`manager.py:73`）只赋值 `self.channels`/锁/空容器，不遍历 channels，故空列表 + 后续 append 安全。`get_channel` 是最小重入点（仅取 `self._lock` + 遍历）。测试核心：旧代码下 `replace_channel` 在 `old.stop()` 重入时死锁 → `wait_for` 3s 超时 FAIL；修复后 3s 内完成且 `old.stopped` 置位 PASS。

- [ ] **Step 2: Run test to verify it fails（当前代码应超时）**

Run: `pytest tests/unit/channels/test_channel_manager.py -v`
Expected: FAIL — `asyncio.TimeoutError`（旧代码死锁，`wait_for` 3s 超时）。

- [ ] **Step 3: 修复 `replace_channel`（swap 在锁内，stop 移出锁）**

把 `src/qwenpaw/app/channels/manager.py` 的 `replace_channel` 第 3 步（约 740-761 行）：

```python
        # 3) Swap + stop old inside lock
        async with self._lock:
            old_channel = None
            for i, ch in enumerate(self.channels):
                if ch.channel == new_channel_name:
                    old_channel = ch
                    self.channels[i] = new_channel
                    break

            if old_channel is None:
                logger.info(f"Adding new channel: {new_channel_name}")
                self.channels.append(new_channel)
            else:
                logger.info(f"Stopping old channel: {old_channel.channel}")
                try:
                    await old_channel.stop()
                except asyncio.CancelledError:
                    pass
                except Exception:
                    logger.exception(
                        f"Failed to stop old channel: {old_channel.channel}",
                    )
```

替换为：

```python
        # 3) Swap inside lock; stop the old instance OUTSIDE the lock so a
        #    reentrant callback from stop() (get_channel/enqueue/send_event)
        #    cannot self-deadlock on this non-reentrant asyncio.Lock.
        old_channel = None
        is_new = False
        async with self._lock:
            for i, ch in enumerate(self.channels):
                if ch.channel == new_channel_name:
                    old_channel = ch
                    self.channels[i] = new_channel
                    break
            if old_channel is None:
                is_new = True
                self.channels.append(new_channel)

        if is_new:
            logger.info(f"Adding new channel: {new_channel_name}")
        else:
            logger.info(f"Stopping old channel: {old_channel.channel}")
            try:
                await old_channel.stop()
            except asyncio.CancelledError:
                pass
            except Exception:
                logger.exception(
                    f"Failed to stop old channel: {old_channel.channel}",
                )
```

（第 1、2 步——set enqueue callback、`await new_channel.start()` 在锁外——保持不变。）

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/channels/test_channel_manager.py -v`
Expected: PASS（不再超时，`old.stopped.is_set()` 为真）。

- [ ] **Step 5: 验证 channels 全套不回归**

Run: `pytest tests/unit/channels/ -q`
Expected: 全 PASS。

- [ ] **Step 6: Commit**

```bash
git add src/qwenpaw/app/channels/manager.py tests/unit/channels/test_channel_manager.py
git commit -m "fix(channels): move replace_channel old.stop() outside lock to prevent self-deadlock

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Task 8: 启动期孤儿 tmp 清理 + Phase 2 收尾验证

**Files:**
- Modify: `src/qwenpaw/app/_app.py`（lifespan Phase 1 加 `cleanup_orphan_tmps`）
- 验证：全套受影响测试 + lint/typecheck

`atomic_io` 现在产生的孤儿 tmp 命名为 `<name>.tmp.<pid>`（与旧 `.json.tmp` 不同）。启动期对 `WORKING_DIR` 做一次清理，回收上次崩溃遗留的孤儿。

- [ ] **Step 1: `_app.py` — 加导入与清理调用**

在 `src/qwenpaw/app/_app.py` 顶部 import 区加：

```python
from ..utils.atomic_io import cleanup_orphan_tmps
```

在 `lifespan` Phase 1 段（`cleanup_startup_restore_artifacts()` 之后、`auto_register_from_env()` 之前，约 233 行附近）加：

```python
    # Reclaim orphaned .tmp.<pid> files left by a crashed atomic write.
    try:
        cleanup_orphan_tmps(WORKING_DIR)
    except Exception:
        logger.debug("startup orphan tmp cleanup failed", exc_info=True)
```

（`WORKING_DIR` 已在 `_app.py` 从 `..constant` 导入。best-effort，绝不阻塞启动。）

- [ ] **Step 2: 验证 app 导入与单元测试不回归**

Run:
```bash
python -c "import qwenpaw.app._app; print('import OK')"
pytest tests/unit/utils/ tests/unit/config/ tests/unit/channels/ tests/unit/local_models/ tests/unit/app/crons/ -q
```
Expected: import OK + 全 PASS。

- [ ] **Step 3: lint/typecheck（项目钉死的 pre-commit 工具）**

提交时 pre-commit 自动跑 black(23.3.0, line-length=79)/flake8/pylint/mypy。本地可单独确认：
```bash
flake8 src/qwenpaw/app/_app.py src/qwenpaw/app/channels/manager.py
pre-commit run black --files src/qwenpaw/ tests/
```
Expected: 无错。

- [ ] **Step 4: Commit**

```bash
git add src/qwenpaw/app/_app.py
git commit -m "feat(app): cleanup orphan atomic-write tmps at startup

Co-Authored-By: Claude <noreply@anthropic.com>"
```

- [ ] **Step 5: 最终 code review（整个 Phase 2）**

对 Phase 2 全量改动做一次 code review（base = Phase 1 head `2108d464`，head = 本 Phase 最终提交），确认：8 个写站点都走 `atomic_io`、无遗漏的手写 `open("w")+json.dump`、ChannelManager 死锁修复正确、`sort_keys` 形参向后兼容、无回归。

---

## Phase 2 完成准则

- 8 处写站点全部经 `atomic_io`（config ×2、auth、dingtalk ×2、local_models、crons json_repo、runner json_repo）—— `grep -rn 'open(.*"w"' src/qwenpaw/{config,app/auth.py,app/channels/dingtalk,local_models,app/crons/repo,app/runner/repo}` 在这些写路径上不再出现裸覆写（只读 `open` 与 secret_store 等本 Phase 范围外的除外）。
- `ChannelManager.replace_channel` 在 `stop()` 重入 manager 时不死锁（回归测试覆盖）。
- `write_json_atomic` 新增 `sort_keys` 形参，向后兼容（Phase 1 既有调用不受影响）。
- 启动期清理孤儿 tmp。
- 所有受影响模块的现有测试 + 新增测试全绿；lint/typecheck 通过。

Phase 3（场景一：启动 IO + 哈希 pyc + import 预读）将在 `docs/superpowers/plans/2026-06-22-性能优化-phase3-startup-io.md` 出独立计划。

---

## 风险点（实施时留意）

1. **`save_config`/`save_agent_config` 是整写不是 RMW**：用 `write_json_atomic`（默认 `lock=True` 串行化同路径并发整写），**不要**误用 `locked_json_update`（那是给 read-modify-write 的）。
2. **相对导入深度**：config/auth/local_models 用 2 点；dingtalk/crons-repo/runner-repo 用 4 点。导错会 ImportError。
3. **`sort_keys` 必须先在 Task 1 加好**，否则 Task 6 的 json_repo 会 `TypeError`。Task 顺序不可乱。
4. **ChannelManager 测试用鸭子类型 fake**：不继承 `BaseChannel`（抽象方法多），用带 `.channel`/`.start`/`.stop`/`uses_manager_queue=False` 的普通类即可（`ChannelManager` 运行时按属性查找）。`ChannelManager(channels=[])` 构造安全（`__init__` 不遍历 channels）。死锁回归测试的 3s 超时是"红"的关键信号——旧代码必超时。
5. **保留 chmod**：auth 与 local_models 的 `chmod 0o600` 是 best-effort，保留（非 exFAT 上仍有意义）；不要因"exFAT 无效"就删。
6. **json_repo 的 `save` 是 async**：直接调同步 `write_json_atomic`（与现状 `write_text` 同为同步、不新增事件循环阻塞）；Phase 4 才在 chats 侧加缓存层。

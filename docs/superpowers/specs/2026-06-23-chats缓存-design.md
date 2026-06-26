# §4.3 chats.json 缓存（写穿透 + 读缓存）Design

> **状态**：已 brainstorming + 用户确认设计方向（B 写穿透 + 读缓存），待 writing-plans 出实施计划。
> **对应**：`docs/superpowers/specs/2026-06-22-性能优化-design.md` §4.3；总基准见 `docs/PROGRESS_BASELINE.md` §6。
> **分支**：`feature/usb-portable`（HEAD `745c5d18`）。
> **行号基准**：本文 `file:line` 截至 HEAD `745c5d18`；改动后会漂移（PROGRESS_BASELINE §8 坑-6），写实施计划时用 grep 重新定位。

---

## 1. 目标与范围

**目标**：消除对话请求热路径上 chats.json 的同步全文件 IO——在 `JsonChatRepository.load()/save()` 加进程内读缓存 + 异步原子写。每请求 `touch_chat` 当前 **2 同步读 + 1 同步写（全阻塞事件循环）→ 0 读 + 1 异步写（事件循环空闲）**。

**写策略 = B 写穿透 + 读缓存**（非 spec 原文的"写回 + 后台 flush"——见 §2.4 取舍）。spec 点名的瓶颈是"每请求 2 次全文件 IO"（指 2 次读）；B 直击该瓶颈且风险最低。

**In scope（本 spec）**：
- `JsonChatRepository`（`src/qwenpaw/app/runner/repo/json_repo.py`）`load()`/`save()` 加缓存 + 异步写。
- kill-switch flag `QWENPAW_PERF_CHATS_CACHE`。

**Out of scope（明确不做）**：
- 写回 / 后台 flush / dirty 跟踪 / flush 生命周期接线（B 不需要；若后续实测 USB 写队列争用成瓶颈，再评估写回）。
- `ChatManager` 改动（不动；CRUD 透明受益）。
- `BaseChatRepository` 改动（不动；其 CRUD 经 load/save 自动受益）。
- §4.5（tool result）等其他项。

---

## 2. 调查事实（设计依据）

### 2.1 数据形状

`chats.json` = `{version: int, chats: list[ChatSpec]}`（`runner/models.py:81`）。`ChatSpec` 是**小元数据**：chat_id(UUID) → session_id / user_id / channel / name / created_at / updated_at。**非会话内容**（会话状态在 `session.py`，Phase 4 已异步原子化）。**per-workspace**：每个 agent 一份（`service_factories.py:53` `ws.workspace_dir / "chats.json"`）。

### 2.2 请求路径与瓶颈（确认 spec:302）

```
runner.query_handler (runner.py:302)
  └─ finally: self._chat_manager.touch_chat(chat.id)        # runner.py:796 每请求
       └─ ChatManager.patch_chat (manager.py:150)            # async with cm._lock
            └─ _patch_locked → repo.upsert_chat(merged)      # manager.py:201
                 └─ BaseChatRepository.upsert_chat (base.py:87)
                      ├─ cf = await self.load()              # 全文件读 ①
                      ├─ cf.chats[i] = merged
                      └─ await self.save(cf)                 # 全文件写
                           └─ (save 内部无 load，但 _patch_locked 先 get_chat)
      且 _patch_locked 先 get_chat (base.py:32) → load()      # 全文件读 ②
```
即每请求 `touch_chat` = **2 次 load（读）+ 1 次 save（写）**，全同步、全阻塞事件循环。`get_or_create_chat`（runner.py:609）也 load 一次。无缓存。

### 2.3 缓存单点：load()/save()

`BaseChatRepository`（`base.py`）的**所有** CRUD（`get_chat:32` / `get_chat_by_id:47` / `upsert_chat:87` / `delete_chats:102` / `filter_chats:122` / `list_chats:27`）都是 `cf = await self.load()` → 改 → `await self.save(cf)` 的全文件 RMW。**在 `JsonChatRepository.load()/save()` 这一层加缓存，全部 CRUD 透明受益**——单文件单点改动。

### 2.4 写策略取舍（为何 B 而非 spec 原文写回）

| | B 写穿透+读缓存（选定） | A 写回+后台 flush（spec 原文） |
|---|---|---|
| 每请求 touch | 0 读 + 1 异步写 | 0 读 + 0 写（批） |
| 数据丢失窗口 | **无**（每次 save 立即落盘） | ≤flush_interval（非优雅崩溃） |
| flush 生命周期接线 | **无** | per-workspace ServiceManager start/stop |
| 风险 | **低** | 中（基线给 §4.3 标"中"的主因） |
| 直击 spec 瓶颈（2 读） | ✅ | ✅（并额外批写） |

选 B 的理由：① 直击 spec 点名的 2 读瓶颈；② `session.py` 每请求已在异步写，chats 再加一个异步写与之一致——A 相对 B 的边际收益仅"批掉 chats 那一次写"，而 session 写仍每请求发生 → 边际不大；③ 风险从中降到低（无丢数据窗口、无生命周期接线）。A 的唯一独有优势是 USB 写队列争用更小（§8 坑-4）；若后续实测那才是瓶颈，再升级为写回。

### 2.5 无运行时外部写者（无 stale 风险）

- `agent_stats/service.py:222` 新建 `JsonChatRepository` 但**只 `list_chats()`（读）**、且**每次统计新建实例**（无跨调用缓存）→ 非写者。
- `migration.py:44/51`、`routers/agents.py:592`、`workspace.py:404` 仅在**初始化/迁移**写 `{"version":1,"chats":[]}` 或 weixin→wechat 迁移，发生在 repo 首次 load 之前。
- 结论：workspace 的 `JsonChatRepository` 是其 chats.json 的**唯一运行时写者**。写穿透使磁盘始终最新 → 无 stale。

### 2.6 生命周期（为何 B 无需接线）

`JsonChatRepository` 在 `service_factories.py:54-55` per-workspace 创建，注册为 `chat_manager` service（`workspace.py:230`，**reusable=True**，无 start/stop method）。token_usage 的写回 buffer 是进程单例（lifespan `_app.py:309/569` 起/停）；chats 是 per-workspace——**写回需挂 ServiceManager start/stop，写穿透不需要**（每次 save 自带落盘，进程退出前最后一次 save 已落盘；重启后首 load 读盘重建缓存）。这是 B 相对 A 的重大简化。

---

## 3. 设计：写穿透 + 读缓存

### 3.1 放置层

`JsonChatRepository.load()/save()`（`json_repo.py:45/57`）。唯一改动源文件。

### 3.2 load()

```python
async def load(self) -> ChatsFile:
    if not _chats_cache_enabled():
        return self._load_from_disk_sync()         # 逐字当前：同步读盘（阻塞，同今天）
    async with self._cache_lock:
        if self._cache is None:
            self._cache = await asyncio.to_thread(self._load_from_disk_sync)
        return self._cache.model_copy(deep=True)   # 深拷贝，防调用方污染
```

- 命中：返回 `model_copy(deep=True)`，0 IO、0 阻塞。
- miss：`to_thread` 读盘（离事件循环）+ 填缓存。miss 一生一次（首 CRUD）。

### 3.3 save()

```python
async def save(self, chats_file: ChatsFile) -> None:
    snapshot = chats_file.model_copy(deep=True)    # 深拷贝入缓存
    payload = snapshot.model_dump(mode="json")
    if not _chats_cache_enabled():
        write_json_atomic(self._path, payload, sort_keys=True)  # 逐字当前同步写
        return
    async with self._cache_lock:
        self._cache = snapshot
    await asyncio.to_thread(                       # 离事件循环、原子、per-path 锁
        write_json_atomic, self._path, payload, sort_keys=True,
    )
```

- 缓存←深拷贝；写穿透（立即落盘，无丢数据窗口）。
- flag 关：同步 `write_json_atomic`（逐字当前）。

### 3.4 开关 / 锁

- **flag**：env `QWENPAW_PERF_CHATS_CACHE`，**默认开**，`0/false/no/off`（大小写不敏感）关。模块级 `_chats_cache_enabled()`。
- **repo 级 `asyncio.Lock`**（`self._cache_lock`）：现 repo 无可变状态、靠 `ChatManager._lock`（manager.py:37）串行；加缓存引入可变共享状态 → repo 自带锁自保护（load 填充 / save 更新串行）。写（`to_thread`）在锁外写快照 → 线程不碰缓存。无死锁（load/save 各自单独获取 repo 锁，不嵌套；不回调入 repo）。

---

## 4. 公共 API（新增，全在 `json_repo.py`）

```python
# 模块级
_CHATS_CACHE_DISABLE_VALUES = frozenset({"0", "false", "no", "off"})

def _chats_cache_enabled() -> bool            # 解析 env，默认 True

# JsonChatRepository 实例
self._cache: ChatsFile | None = None          # __init__ 加
self._cache_lock = asyncio.Lock()             # __init__ 加

async def _load_from_disk_sync(self) -> ChatsFile   # 原 load() body 抽出（同步读盘）
async def load(self) -> ChatsFile                   # 缓存 + 深拷贝
async def save(self, chats_file: ChatsFile) -> None # 更新缓存 + to_thread 原子写
```

`ChatManager`、`BaseChatRepository` 不改。

---

## 5. 数据流（after）

```
ChatManager.patch_chat (cm._lock 串行 per-workspace)
  → BaseChatRepository.upsert_chat (base.py:87，不改):
      cf = await repo.load()        # 缓存命中：深拷贝，0 IO / 0 阻塞
      cf.chats[i] = merged          # 改的是深拷贝，不污染缓存
      await repo.save(cf)           # repo: 缓存←深拷贝(cf)；await to_thread(write_json_atomic)
                                    #   离事件循环、原子、per-path RLock
```

每请求 touch：**0 读 + 1 异步写**（vs 当前 2 同步读 + 1 同步写，全阻塞）。flag 关 = 逐字当前。

---

## 6. 并发与持久性

| 维度 | 行为 |
|---|---|
| per-workspace 串行 | `ChatManager._lock` 串行化 CRUD；save 在锁内 `await to_thread`，写完才释锁 → 同 workspace 无并发 save 乱序 |
| 缓存自保护 | repo `_cache_lock` 串行 load 填充 / save 更新；写 to_thread 在锁外、写快照 → 线程不碰缓存 |
| 持久性 | **写穿透**：每次 save 立即落盘（to_thread + atomic）。无丢数据窗口；崩溃不丢（已落盘） |
| 重启 | 进程重启 → 新 repo 实例缓存空 → 首 load 读盘重建。最后一次 save 已落盘 → 无丢失 |
| stale | workspace repo 是唯一运行时写者（§2.5）；写穿透使盘始终最新 → agent_stats 读盘永远新鲜 |
| 深拷贝 | load 返回 / save 存 `model_copy(deep=True)` → BaseChatRepository CRUD 改返回对象不污染缓存 |

---

## 7. 测试（TDD，扩 `tests/unit/app/test_json_repo.py` 或新建 `test_chats_cache.py`）

1. `test_load_caches_after_first_read` — 写 v1、load→v1；磁盘改写 v2；再 load→仍 v1（命中缓存未读盘）。
2. `test_save_updates_cache_and_writes_through` — save(v3) → 再 load→v3（缓存更新）且磁盘读→v3（写穿透）。
3. `test_load_returns_independent_copy` — load() 返回对象 `.chats.append(...)` → 不影响缓存（再 load 仍是原值）。
4. `test_flag_disabled_passthrough` — env=0 → 磁盘改 v2 后 load→v2（每次读盘，未缓存）；save 为同步写（行为同当前）。
5. `test_save_uses_to_thread_off_event_loop` — save() 期间事件循环可推进其他协程（用 monkeypatch 计数 `asyncio.to_thread` 调用，或断言 save 是 awaitable 且不阻塞）。
6. `test_roundtrip_durability_after_restart` — save(chat)；新建 repo 实例（缓存空）→ load → 看到 chat（写穿透持久）。
7. `test_base_crud_works_with_cache` — 经 `BaseChatRepository` 的 upsert/get/delete/filter 全正确（回归，缓存透明）。
8. `test_concurrent_load_save_safe` — 并发 load/save 不破坏缓存（repo 锁）。

回归：`tests/unit/app/test_json_repo.py`（Phase 2）、ChatManager / title_generator（`tests/unit/app/test_title_generator.py`）全绿。

---

## 8. 风险与取舍

1. **深拷贝开销**：`ChatsFile` 是小元数据列表，`model_copy(deep=True)` 微秒级，仅 CRUD 时发生（非热读路径内多次）。可接受；若实测 chats 数量极大再优化。
2. **repo 级锁 + cm 锁双层**：无嵌套（load/save 各单独获取 repo 锁）、无回调入 repo → 无死锁。
3. **未来外部写者**：目前无运行时外部写者（§2.5）。若将来出现（如多进程、外部脚本改 chats.json），写穿透缓存会 stale → 届时加 mtime 校验或失效钩子。**不在本次范围**，仅记为已知边界。
4. **miss 路径同步读？否**：miss 经 `to_thread` 离事件循环（§3.2），不阻塞。命中路径 0 IO。
5. **session.py 仍每请求写**：B 不改 session 写；chats 加一个异步写与之一致。若需进一步降 USB 写争用 → 升级为写回（A），单独评估。

---

## 9. C1–C5 合规

| 约束 | 合规 |
|---|---|
| C1 USB/exFAT | 消除每请求 2 次随机读（USB 高延迟主因）；写离事件循环 + tmp+fsync+replace 原子（exFAT 健壮） |
| C2 单实例进程内并发 | `asyncio.Lock` 进程内；无跨进程 |
| C3 小幅行为变更可接受 | flag 关 = 逐字当前；flag 开 = 读缓存 + 异步写（语义等价、更快、更不易丢） |
| C5 不碰宿主磁盘 | 仅原有 chats.json，无新文件 |

---

## 10. 后续

- 本 spec 经用户过目 → `superpowers:writing-plans` 出 bite-sized TDD 实施计划（每 Task 先红后绿 + before→after 完整代码 + commit）。
- 完成后回写 `PROGRESS_BASELINE.md`：§5 加 Phase、§6 删 §4.3、§0 改现状（下一项 §3.2 迁移戳幂等 或 §4.5 收尾）。

# PROGRESS_BASELINE — 性能优化总基准（所有新会话的统一起点）

> **用途**：本文是 QwenPaw 性能优化工作的**唯一权威基准**。任何新会话接手前，先读完本文，再按需读对应 spec / plan。本文锁定：已完成成果、设计决策、硬约束、剩余任务、流程约定与已知坑。
>
> **最后更新**：2026-06-24（Phase 4g / §5.4 secret_store 原子写完成后）。
> **当前状态**：分支 `feature/usb-portable`，HEAD = `845e3926`。Phase 1–5 + Phase 4b/4c/4d/4e/4f/4g 已实现，部分 spec 章节仍待做。

---

## 0. 一句话现状

三大核心场景（启动 IO / 运行时延迟 / 资源竞争）+ import 专项，**每个都已有实质进展**（Phase 1–5 + Phase 4b/4c/4d/4e/4f/4g），但都只完成了**首个子集**；剩余是各场景的深化项（见 §6）。**§4.1 模型客户端缓存已完成**（Phase 4b：工厂级 config 指纹缓存，复用热 httpx 池降 TTFT；stale 由指纹结构性免疫、免失效钩子；C4 满足仅缓存 client）。**§4.3 chats.json 缓存已完成**（Phase 4c：写穿透读缓存 + 异步原子写，每请求 2 同步读+1 同步写 → 0 读+1 异步写；write-first 保失败一致；kill-switch `QWENPAW_PERF_CHATS_CACHE`）。**§4.5 Part A tool result 溢写已完成**（Phase 4d：`_truncate_tool_result`/`_prune_output`/`_prune_tool_result` 三函数 async 传播，溢写走 `asyncio.to_thread(write_bytes_atomic)` 离事件循环+原子；7 测试全绿）。**§4.5 Part B token 增量缓存已 defer**（`EstimatedTokenCounter` 仅字节除法 `len/4`，代价极低；增量缓存需在 prune/summary 改消息时正确失效，复杂度不配收尾定位）。**§3.2 迁移戳幂等已完成**（Phase 4e：`.migration_stamp` 版本门控，稳态 2× load_config + ~6 stat → 1 戳读取；ensure 函数保持不门控作安全网；kill-switch `QWENPAW_PERF_MIGRATION_STAMP`；21 测试全绿）。**§3.4 restore stat 精简已完成**（Phase 4f：`_cleanup_stale_restore_artifacts_locked` + `recover_mount_point_swap` 用单次 `os.scandir` 替代 5–7 次 `.exists()` stat；exFAT 上 1 listing ≪ N stat；19 测试全绿）。**§5.4 secret_store 原子写已完成**（Phase 4g：`_write_key_file` 从裸 `write_text` + `chmod` 改为 `write_bytes_atomic`；崩溃安全 + 删除 exFAT 无效 chmod；20 测试全绿）。下一个最高价值项见 §6 剩余任务。

---

## 1. 项目与分支上下文

- 仓库：QwenPaw-aixcore（Python 3.10、FastAPI + agentscope_runtime 多 Agent 桌面应用）。
- 工作分支：`feature/usb-portable`（USB 便携版特性分支，承载本次性能优化；**不要合并到 main**——还有未完成项）。
- 规格文档：`docs/superpowers/specs/2026-06-22-性能优化-design.md`（**完整设计**，三大场景 + import 专项，含瓶颈映射基线 file:line）。
- Phase 计划：`docs/superpowers/plans/2026-06-22-性能优化-phase{1..5}-*.md`。
- 本次性能优化提交范围：`9dc5c3b0`（spec）→ `845e3926`（HEAD），共 ~40 个提交。
- 本次工作**未触碰** `src/qwenpaw/cli/desktop_cmd.py`（用户预存的未暂存改动，见 §8 坑）。

---

## 2. 硬约束（brainstorming 已确认，不可违背）

| # | 约束 | 含义 |
|---|---|---|
| C1 | **部署目标：USB 便携版优先（exFAT）** | 减磁盘往返、原子写健壮性、文件锁语义进核心；普通安装版顺带受益。exFAT：无 UNIX 权限（chmod 0o600 无效）、弱/无文件锁、无 journaling、随机 IO 延迟极高。|
| C2 | **单实例，仅进程内并发** | 资源竞争只需进程内锁（`threading.RLock` / `asyncio.Lock`）+ 原子写；**跨进程锁仅** `backup/_utils/safe_swap.py` 的 backup/restore 保留。|
| C3 | **可接受小幅行为变更** | 鉴权短 TTL 缓存（撤销延迟 ≤TTL）、遥测后台化、关键写加 fsync 均可用。|
| C4 | **请求路径深度：中等（仅复用模型/客户端）** | §4.1 缓存模型客户端，**Agent 实例仍按现状每请求重建**（不做深度重构）。|
| C5 | **不碰宿主磁盘** | import 优化**不**用"拷 env 到本地影子盘"（便携语义）；仅构建期 + 进程内手段。|

---

## 3. 设计决策（架构）

**两个共享基石**（`src/qwenpaw/utils/`），被三大场景复用：

1. **`atomic_io.py`** — 安全原子写 + 进程内锁。把项目里三处已验证的好模式（`skills_manager` / `token_usage.storage` / `backup.safe_swap`）抽成统一工具。tmp+fsync+`os.replace`，per-path `RLock` 串行化同路径并发。
2. **`background_tasks.py`** — `BackgroundTaskRunner`：受 lifespan 管理的 fire-and-forget 后台任务（spawn / spawn_after / shutdown）。承接遥测后台化等非关键 IO。

**集成原则**：先建基石，三大场景的改动变成"调用已有工具"，局部、可测、一致。所有写站点收敛到 `write_json_atomic` / `locked_json_update`，消除裸覆写。

**spec 各章节归属**（防止重复/遗漏）：
- §2.1/2.2 基石 → Phase 1 ✅
- §3.1 遥测后台 + §3.3 语言硬编码 → Phase 3 ✅；§3.2 迁移戳幂等 → Phase 4e ✅；§3.4 restore stat 精简 → Phase 4f ✅；§3.5/§3.6 → 见 §6 待办
- §4.2 session 异步 + §4.4 auth 缓存 → Phase 4 ✅；§4.1 模型客户端缓存 → Phase 4b ✅；§4.3 chats 缓存 → Phase 4c ✅；§4.5 Part A tool result 溢写 → Phase 4d ✅；§4.5 Part B token 增量缓存 → defer（字节除法代价极低，失效复杂度不匹配）
- §5.1 八处原子写 + §5.2 ChannelManager 死锁 → Phase 2 ✅；§5.3 日志 → 见 §6；§5.4 secret_store 原子写 → Phase 4g ✅

---

## 4. 基石公共 API（调用方照此用）

### `src/qwenpaw/utils/atomic_io.py`
```python
write_bytes_atomic(path, data: bytes, *, lock=True, fsync=True) -> None
write_json_atomic(path, data, *, lock=True, fsync=True, indent=2,
                  ensure_ascii=False, sort_keys=False) -> None
read_json_safe(path, *, default=None) -> Any          # json_repair 兜底
locked_json_update(path, fn, *, default=None, fsync=True) -> Any  # RMW 全程持锁
cleanup_orphan_tmps(directory, pattern="*.tmp.*") -> int
```
- **整写**用 `write_json_atomic`；**read-modify-write** 用 `locked_json_update`（fn 收旧数据返新数据）。
- **不要**在持锁回调里再触发同路径 `locked_json_update`（RLock 可重入但属逻辑错误）。
- 无 `mode=` 形参（exFAT chmod 无效，by design）。

### `src/qwenpaw/utils/background_tasks.py`
```python
class BackgroundTaskRunner:
    spawn(coro, *, name=None) -> asyncio.Task            # 即发即弃
    spawn_after(delay, coro_factory, *, name=None) -> asyncio.Task  # 延迟（factory 零参返新协程）
    async shutdown(timeout=5.0) -> None                  # cancel + await 全部
```
- 必须在运行中的 event loop 内调用（如 FastAPI lifespan）。
- `spawn_after` 是**普通 `def`**（不是 `async def`）——返回 task，调用方不 await 方法本身。
- 任务异常只记日志、不外抛。

### `src/qwenpaw/utils/import_prefetch.py`
```python
start_import_prefetch(cap_mb=None, import_order_file=None) -> threading.Thread
```
- 立即返回（文件扫描在 worker 线程，**不阻塞调用方**）；daemon；best-effort。
- 开关 `QWENPAW_PERF_IMPORT_PREFETCH`（默认开，`0/false/no` 关）；cap `QWENPAW_PERF_PREFETCH_CAP_MB`（默认 256）。

---

## 5. 已完成 Phase 清单（含提交 SHA）

### Phase 1 — 基石（base 前 → `2108d464`）
- `utils/atomic_io.py`（5 公共函数 + `_resolve`/`_get_lock`）+ `utils/background_tasks.py`（`BackgroundTaskRunner`）。
- 测试：`tests/unit/utils/test_atomic_io.py`（19，含 20×100 并发 RMW）、`test_background_tasks.py`（5 async）。

### Phase 2 — 场景三原子写（`36801322` → `235b72d6`）
- 8 处写迁移到 atomic_io：`config save_config/save_agent_config`、`auth _save_auth_data`、`dingtalk channel/ai_card`、`local_models _write_config_file`、`crons/runner json_repo.save`（sort_keys）。
- 基石加 `sort_keys` 形参（`4d60fab2`，向后兼容）。
- `ChannelManager.replace_channel` 死锁修复：stop 移出锁（`d8d5459e`，回归测试证明旧代码超时）。
- 启动期递归孤儿 tmp 清理 `_app.py`（`828130c8` + `235b72d6`）。

### Phase 3 — 场景一启动 IO 子集（`2dbd4329` → `ff49e062`）
- 遥测后台化：`_app.py` 接入 `BackgroundTaskRunner`，`collect_and_upload_telemetry` 经 `spawn_after(2.0)` + `to_thread`（`b437d2de`）。
- 遥测 marker → `locked_json_update`（`4e68add4`）。
- `SUPPORTED_AGENT_LANGUAGES` 硬编码 `{en,zh,ru}`，消除 import 期扫盘（`ff49e062`）。

### Phase 4 — 场景二请求路径 IO 子集（`60e20a7c` → `6eef23bc`）
- `save_session_state` → `asyncio.to_thread(write_json_atomic, indent=None)`（`0c4f267c`，紧凑格式保持）。
- `_load_auth_data` TTL 缓存（3s）+ `_save_auth_data` 写后 `invalidate_auth_cache()`（`6eef23bc`）。

### Phase 5 — 启动期 import 4K 读写（`8f496119` → `0d0a5c7e`）
- 4 个构建脚本 `compileall` 加 `--invalidation-mode checked-hash`（`cd29f6f9`，实测 flags=3）。
- `utils/import_prefetch.py` 顺序预读 + `cli()` 开关接入（`f72aa31e`）。
- **I1 修复**（`0d0a5c7e`）：扫描移到 worker 线程 + 2s 墙钟上限 + cap 解析容错（详见 §8 坑-4）。

### Phase 4b — §4.1 模型客户端缓存（`53b9b89c` → `e845d2f8`）
- `agents/model_factory.py` 加进程内缓存：`_model_client_cache_enabled`（flag `QWENPAW_PERF_MODEL_CLIENT_CACHE`，默认开，`0/false/no/off` 关）+ `_model_client_fingerprint`（`(provider_id, model_id, base_url, api_key, generate_kwargs)`，无 `default=str` → 非序列化值 fail-loud 保 stale 不变量）+ `_get_cached_inner_model`（持 `threading.Lock` 构建、FIFO cap 64 驱逐）+ `clear_model_client_cache` / `model_client_cache_stats`。
- `create_model_and_formatter` 两分支（agent-specific + global-fallback）走 `_get_cached_inner_model`；fallback 内联 `ProviderManager.get_active_chat_model` 校验（错误消息逐字一致，移除死代码长消息）。wrapper（`TokenRecordingModelWrapper`+`RetryChatModel`）+ formatter 仍每请求新建（**C4 满足：仅缓存 client**）。
- agentscope 在 `__init__` 建 httpx 客户端、`__call__` 复用 → 缓存内层 `OpenAIChatModelCompat` 实例 = 复用热连接池 = TTFT 收益。指纹含全部构建决定输入（4 类 provider 均核实）→ **stale 结构性不可能、免失效钩子**；跨 reload 自愈（值 key 非身份）。
- 测试：`tests/unit/agents/test_model_client_cache.py`（27：flag/指纹/store 含 8 线程并发/cap 驱逐/工厂集成含两 fallback 错误路径）。两阶段评审 + 最终评审（opus）均通过。

### Phase 4c — §4.3 chats.json 缓存（`3e2131a0` → `210beebc`）
- `app/runner/repo/json_repo.py` 加进程内读缓存 + 写穿透：`_chats_cache_enabled`（flag `QWENPAW_PERF_CHATS_CACHE`，默认开）+ `_cache`/`_cache_lock`（`asyncio.Lock`，defense-in-depth；ChatManager 已 per-workspace 串行）+ `_load_from_disk_sync`。
- `load()`：flag-off 同步读盘（逐字当前）；flag-on 命中返回 `model_copy(deep=True)`（0 IO）、miss `asyncio.to_thread` 读盘。`save()`：flag-off 同步写；flag-on **write-first**（`asyncio.to_thread(write_json_atomic)` 成功后再更新缓存）→ 失败时缓存与盘一致。
- 因 `BaseChatRepository` 所有 CRUD 是 `load()→改→save()` RMW，缓存 load/save 单点 = 全 CRUD 透明受益。每请求 `touch_chat`：2 同步读+1 同步写 → **0 读 + 1 异步写**。深拷贝防污染；workspace repo 是 chats.json 唯一运行时写者 → 无 stale。
- 测试：`tests/unit/app/test_chats_cache.py`（19：flag/cache 含写失败一致性/异步 to_thread spy/durability/全 CRUD 透明/并发不腐化）。两阶段评审 + 最终评审（opus）均通过。

### Phase 4d — §4.5 Part A tool result 溢写异步化（`2dd7affa` → `fbb1a951`）
- `agents/context/light_context_manager.py` 三函数 async 传播：`_truncate_tool_result`（溢写走 `await asyncio.to_thread(write_bytes_atomic, ...)`，离事件循环 + 原子 tmp+fsync+replace+per-path 锁）→ `_prune_output`（await 调用）→ `_prune_tool_result`（await 调用，`:339`）。OSError 回退逐字保留；冗余 `mkdir` 删除（`write_bytes_atomic` 创建父目录）。
- **Part B defer**：`EstimatedTokenCounter` 仅 `len(text.encode("utf-8")) / 4`（字节除法），每轮 `pre_reasoning` 对全历史重算的累计代价极低；增量缓存需在 prune/summary 改消息时正确失效，复杂度不配低优先级收尾定位。
- 测试：`tests/unit/agents/context/test_tool_result_offload.py`（7：overmax 原子写/undermax 透传/to_thread 离载/OSError 回退/prune-output async 链/prune-tool-result async 链）。

### Phase 4e — §3.2 迁移戳幂等（`10022d0a`）
- `app/migration.py` 新增戳机制：`_migration_stamp_enabled`（flag `QWENPAW_PERF_MIGRATION_STAMP`，默认开）+ `_should_run_migrations`（戳版本 == 当前版本 → 跳过；缺失/损坏/版本不匹配 → 运行）+ `_write_migration_stamp`（`write_json_atomic` 原子写）+ `run_migrations`（封装函数，戳门控两 legacy 迁移，ensure 函数保持不门控作安全网）。
- `_app.py` lifespan：4 个独立调用 → 1 个 `run_migrations()`。稳态 IO：2× `load_config()` + ~6 stat → 1 戳读取（stat + JSON parse）。
- 安全默认：戳缺失/损坏/缺 `version` 字段/flag OFF → 全量运行（与无戳行为一致）。删 `.migration_stamp` = 强制重跑（kill-switch）。
- 测试：`tests/unit/app/test_migration_stamp.py`（21：flag 值/戳状态含损坏和缺字段/版本匹配与不匹配/写入覆盖/run_migrations 集成含 mock 调用追踪）。

### Phase 4f — §3.4 restore stat 精简（`27f3af62`）
- `backup/_utils/safe_swap.py`：新增 `_list_child_names(parent)` helper（单次 `os.scandir` → name 集合）；`_cleanup_stale_restore_artifacts_locked` 用集合查询替代 `old.exists()` / `base_dir.exists()` / `tmp.exists()`；传 `child_names` 给 `recover_mount_point_swap`。
- `backup/_utils/_mount_swap.py`：`recover_mount_point_swap` 加 `child_names` 可选关键字参数（`None` → 内部 scandir，保持现有调用方不变）；用集合查询替代 `old_dir.exists()` / `STATE_FILE.exists()` / `STATE_TMP.exists()`。
- 同步语义保持（restore 安全性）。每 target 从 5–7 stat 降至 2 scandir（parent + dst）。
- 测试：`tests/unit/backup/test_restore_scandir.py`（8：helper 含空目录/不存在/restore 后缀/recover child_names 参数/cleanup 场景 2+3）。

### Phase 4g — §5.4 secret_store 原子写（`845e3926`）
- `security/secret_store.py`：`_write_key_file` 从裸 `path.write_text` + `os.chmod` 改为 `write_bytes_atomic`（tmp + fsync + os.replace）。崩溃安全：进程中断时旧 key 文件完整保留。chmod 删除（exFAT 无效，C1 by design）。
- 进程内已有 `_master_key_lock`（threading.Lock 双检锁）覆盖并发；单实例（C2）无跨进程风险。
- 测试：`tests/unit/security/test_secret_store.py` 新增 3 个（write_bytes_atomic 委托验证/内容正确/幂等写入）。全 20 测试通过。

---

## 6. 剩余任务（按 spec 章节，含理由 + 风险 + 建议顺序）

| spec 节 | 内容 | 风险/理由 | 建议优先级 |
|---|---|---|---|
| §3.5 | Workspace.start 冗余去重 | 中：`ensure_skill_pool_initialized` 提升到 MultiAgentManager 级；需读 `workspace.py` + `multi_agent_manager.start_all_configured_agents` | 中 |
| §3.3 load_envs | `load_envs_into_environ` 从 import 移到 lifespan | 中：现有注释说 import 期加载是刻意的（envs 在 lifespan 前可能被依赖）——**必须先分析依赖**再移 | 低（除非分析清楚）|
| §5.3 | 日志多进程竞态（QueueHandler + 双文件）| 中：desktop 启动器 + backend 子进程各写独立日志文件 + 进程内 QueueHandler | 中 |
| §3.6.2 实测 | import 预读真机测量 | **必须用户在 USB/exFAT 真机**跑 `-X importtime` 前后对比；若反噬设 `QWENPAW_PERF_IMPORT_PREFETCH=0` | 用户侧验证 |

**建议下一会话**：先做 **§3.2**（迁移戳幂等，需逐迁移函数分析；中风险）。每个出新计划（`docs/superpowers/plans/`）+ subagent 驱动执行。

---

## 7. 流程与工程约定

1. **TDD**：每 Task 先写失败测试 → 跑红 → 实现 → 跑绿 → commit。
2. **增量导入**：同一文件多 Task 逐步加函数时，**每个 Task 只导入它用到的名字**，不预先 import 后续 Task 才定义的（否则 pytest collection 失败 + pylint unused-import）。
3. **执行模式**：subagent 驱动（implementer subagent 每 Task + 两阶段评审：spec 合规 → 代码质量）。subagent 调度触限（5h 上限）时，controller 直接做 trivial task 并自验。
4. **每 Phase 流程**：brainstorming/writing-plans 出计划 → subagent-driven-development 执行 → 里程碑评审（模块完成时）+ 最终评审（Phase 完成时）。Phase 完成后**不合并分支**（多 Phase 分支）。
5. **测试根目录命令**：`pytest tests/unit/<module>/ -q`；asyncio_mode=auto（async 测试无需装饰器）。

---

## 8. 已知坑（踩过的，务必避开）

1. **`desktop_cmd.py` 预存改动**：会话开始时存在 `M src/qwenpaw/cli/desktop_cmd.py`（用户预存的未暂存改动）。pre-commit 在每次提交时 stash/restore 它。**本次工作绝不收录它**（`git add` 只加具体文件）。它时有时无（取决于 pre-commit stash 时机），属正常。

2. **`_app.py` 预存的格式化摩擦**：该文件曾用更新版 black 格式化（如 `response.headers["Cache-Control"] = (...)` 的 hug-parens 风格 + 一个 83 字符 docstring）。**触碰 `_app.py` 时**，pre-commit 的 black 23.3.0 会重排 Cache-Control 赋值、flake8 会 E501 flag 那个 docstring。处理：接受 black 的稳定重排 + 缩短 docstring，在 commit message 说明（参考 `828130c8`）。

3. **构建/lint 工具版本错配**：本地 CLI 工具可能与 pre-commit 钉死版本不一致——
   - 本地 `black` 可能是 26.5.1（默认 88 列 + 新规则），项目钉死 **black 23.3.0 + `--line-length=79`**。
   - 本地 `flake8` 可能对 black 风格切片报 `E203`（如 `key[len("X") :]`），但 pre-commit 的 flake8 接受。
   - **canonical = pre-commit**。本地 CLI 报错时，用 `pre-commit run <hook> --files <path>` 复核，别被本地 CLI 误导。
   - `.flake8` 配置：`max-line-length=79`，`ignore=F401,F403,W503,E731`。
   - 本地可能没装 `mypy`；pre-commit 的 mypy 在隔离环境跑（每提交 Passed 即合规）。

4. **"best-effort/never-blocks" 必须覆盖所有同步前奏**（Phase 5 I1 教训）：`import_prefetch` 最初把 `_build_file_list`（无界 `rglob` 全 sys.path）放在**主线程**、daemon 线程启动前——在 site-packages 庞大的环境阻塞启动、挂死 CLI 测试。修复：扫描移到 worker 线程 + 墙钟上限。**任何标榜"不阻塞"的工具，其文件清单构建/初始化也必须在后台线程或严格有界。**

5. **死锁回归测试用超时变红**：测 `asyncio.Lock` 自死锁时，用 `asyncio.wait_for(coro, timeout=N)`——旧代码死锁→超时 FAIL，新代码→PASS（参考 `tests/unit/channels/test_channel_manager.py`）。

6. **plan 里的行号会漂移**：spec/plan 引用的 `file:line` 在改动后会偏移。写新计划前用 `grep` 重新定位真实当前代码，别盲信旧行号。

---

## 9. 关键文件索引

**基石**：`src/qwenpaw/utils/atomic_io.py`、`background_tasks.py`、`import_prefetch.py`
**已迁移的写站点**（Phase 2/3/4）：`config/utils.py`、`config/config.py`、`app/auth.py`、`app/channels/dingtalk/{channel,ai_card}.py`、`local_models/manager.py`、`app/crons/repo/json_repo.py`、`app/runner/repo/json_repo.py`、`app/runner/session.py`、`utils/telemetry.py`、`constant.py`
**结构性改动**：`app/_app.py`（lifespan：runner 接入 + 遥测后台 + 孤儿清理 + run_migrations 封装）、`app/channels/manager.py`（replace_channel 死锁修复）、`cli/main.py`（prefetch 接入）、`agents/model_factory.py`（§4.1 模型客户端指纹缓存 + 接入工厂两分支）、`app/runner/repo/json_repo.py`（§4.3 chats 读缓存 + 写穿透异步写）、`agents/context/light_context_manager.py`（§4.5 Part A tool result 溢写 async + atomic）、`app/migration.py`（§3.2 戳门控 + run_migrations 封装）、`backup/_utils/safe_swap.py` + `_mount_swap.py`（§3.4 scandir 优化）、`security/secret_store.py`（§5.4 原子写）
**构建脚本**：`scripts/pack/build_{linux,macos,win,win_portable}.{sh,ps1}`（均 `--invalidation-mode checked-hash`）
**测试**：`tests/unit/utils/{test_atomic_io,test_background_tasks,test_import_prefetch,test_telemetry_marker}.py`、`tests/unit/app/{test_runner_session,test_auth_cache,test_json_repo,test_migration_stamp}.py`、`tests/unit/channels/test_channel_manager.py`、`tests/unit/agents/test_model_client_cache.py`、`tests/unit/app/test_chats_cache.py`、`tests/unit/agents/context/test_tool_result_offload.py`、`tests/unit/backup/test_restore_scandir.py`

**规格/计划**：`docs/superpowers/specs/2026-06-22-性能优化-design.md`（设计 + 瓶颈基线）、`docs/superpowers/plans/2026-06-22-性能优化-phase{1..5}-*.md`（各 Phase 任务）

---

## 10. 接手清单（新会话第一步）

1. 通读本文。
2. `git log --oneline 9dc5c3b0..HEAD` 看提交全貌；`git status` 确认工作区（注意 `desktop_cmd.py`）。
3. 决定做 §6 哪一项 → 读对应 spec 章节 + 相关源码（用 grep 定位真实行号）。
4. 走 `docs/superpowers/plans/` 出新计划（bite-sized TDD 任务、完整 before→after 代码、无占位符）。
5. subagent 驱动执行 + 两阶段评审；Phase 完成做最终评审，**不合并分支**。
6. 完成后更新本文（§5 加 Phase、§6 删项、§0 改现状）。

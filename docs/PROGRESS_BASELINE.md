# PROGRESS_BASELINE — 性能优化总基准（所有新会话的统一起点）

> **用途**：本文是 QwenPaw 性能优化工作的**唯一权威基准**。任何新会话接手前，先读完本文，再按需读对应 spec / plan。
>
> **最后更新**：2026-06-25（打包优化 Phase 2 完成后）。
> **当前状态**：分支 `feature/usb-portable`。运行时优化 + 打包优化均已取得实质成果。

---

## 0. 一句话现状

**运行时优化**：Phase 1–5 + Phase 4b–4j 全部完成（~45 提交），三大场景（启动 IO / 运行时延迟 / 资源竞争）+ import 专项均有实质进展。剩余仅 §3.6.2 import 预读真机测量（需用户在 USB/exFAT 真机验证）。

**打包优化**：Phase 1（Profiling 基础设施）+ Phase 2（数据驱动优化）完成。构建时间 18 min → 3 min（缓存命中），包体 948MB → 850MB。缓存链条修复（wheel 保留 + env 保留 + archive 条件修正）。

---

## 1. 项目与分支上下文

- 仓库：QwenPaw-aixcore（Python 3.10、FastAPI + agentscope_runtime 多 Agent 桌面应用）。
- 工作分支：`feature/usb-portable`（**不要合并到 main**——还有未完成项）。
- 运行时优化 spec：`docs/superpowers/specs/2026-06-22-性能优化-design.md`
- 打包优化 spec：`docs/superpowers/specs/2026-06-24-打包优化-design.md`
- 打包优化 plan：`docs/superpowers/plans/2026-06-24-打包优化-phase1-profiling.md`

---

## 2. 硬约束

| # | 约束 | 含义 |
|---|---|---|
| C1 | **USB 便携版优先（exFAT）** | exFAT：无 UNIX 权限、弱文件锁、无 journaling、随机 IO 延迟高 |
| C2 | **单实例，仅进程内并发** | 进程内锁 + 原子写；跨进程锁仅 backup 保留 |
| C3 | **可接受小幅行为变更** | 鉴权短 TTL 缓存、遥测后台化、关键写加 fsync |
| C4 | **请求路径深度：中等** | 仅缓存模型客户端，Agent 实例每请求重建 |
| C5 | **不碰宿主磁盘** | import 优化不用影子盘；仅构建期 + 进程内手段 |

---

## 3. 运行时优化（已完成）

**基石**：`atomic_io.py`（原子写 + 进程内锁）、`background_tasks.py`（fire-and-forget 后台任务）、`import_prefetch.py`（顺序预读）、`logging.py`（QueueHandler+QueueListener + 双文件拆分）。

**已完成 Phase 清单**（共 ~45 提交）：

| Phase | 内容 | 关键文件 |
|---|---|---|
| 1 | 基石：atomic_io + background_tasks | `utils/atomic_io.py`, `utils/background_tasks.py` |
| 2 | 8 处写迁移 + ChannelManager 死锁修复 | `config/`, `auth.py`, `channels/dingtalk/`, `local_models/`, `crons/` |
| 3 | 遥测后台化 + 语言硬编码 | `_app.py`, `constant.py` |
| 4 | session 异步 + auth TTL 缓存 | `app/runner/session.py`, `app/auth.py` |
| 4b | 模型客户端指纹缓存 | `agents/model_factory.py` |
| 4c | chats.json 读缓存 + 写穿透 | `app/runner/repo/json_repo.py` |
| 4d | tool result 溢写异步化 | `agents/context/light_context_manager.py` |
| 4e | 迁移戳幂等 | `app/migration.py` |
| 4f | restore stat 精简（scandir） | `backup/_utils/safe_swap.py`, `_mount_swap.py` |
| 4g | secret_store 原子写 | `security/secret_store.py` |
| 4h | Workspace.start 冗余去重 | `agents/skills_manager.py`, `app/workspace/workspace.py` |
| 4i | 日志多进程竞态（QueueHandler + 双文件） | `utils/logging.py`, `cli/desktop_cmd.py` |
| 4j | load_envs 迁移到 lifespan | `app/_app.py` |
| 5 | hash-based .pyc + import prefetch | 构建脚本, `utils/import_prefetch.py` |

**Kill-switch 环境变量**（默认开，`0/false/no/off` 关）：
- `QWENPAW_PERF_MODEL_CLIENT_CACHE` — 模型客户端缓存
- `QWENPAW_PERF_CHATS_CACHE` — chats.json 缓存
- `QWENPAW_PERF_MIGRATION_STAMP` — 迁移戳幂等
- `QWENPAW_PERF_LOG_QUEUE` — 日志队列
- `QWENPAW_PERF_LOAD_ENVS_DEFER` — load_envs 延迟
- `QWENPAW_PERF_IMPORT_PREFETCH` — import 预读

---

## 4. 打包优化（已完成 Phase 1 + Phase 2）

### 4.1 Profiling 基础设施

新增文件：
- `scripts/pack/build_profiler.py` — 共享计时工具库（库 + CLI 双模式）
- `scripts/pack/analyze_packages.py` — 包体体积分析工具

已注入 profiling 的构建脚本：
- `scripts/pack/build_common.py` — 7 个阶段（conda_create/pip_install/verify_certifi/pip_uninstall/pip_cache_purge/conda_fix/conda_pack）
- `scripts/pack/build_macos.sh` — 5 个阶段（wheel_build/unpack/strip/compileall/platform_pack）
- `scripts/pack/build_linux.sh` — 同上
- `scripts/pack/build_win_portable.ps1` — 5 个阶段 + smoke test

CI 更新（`.github/workflows/build-portable.yml`）：
- 新增 macOS x86_64 job（`macos-13` runner）
- Windows smoke test（import qwenpaw 验证）
- 所有平台上传 `build_profiling.json` artifact

### 4.2 优化成果

**构建耗时**（本机 Intel Mac，首次无缓存 → 缓存命中）：

| 阶段 | 首次 | 缓存命中 | 说明 |
|---|---|---|---|
| wheel_build | 175s | **0s** | 源码未变，跳过 |
| conda_create | 17s | **0s** | env 已存在 |
| pip_install | 186s | **0s** | env 已存在 |
| verify_certifi | 3s | **0s** | env 已存在 |
| pip_uninstall | 6s | **0s** | env 已存在 |
| pip_cache_purge | 4s | **0s** | env 已存在 |
| conda_fix | 14s | **0s** | env 已存在 |
| conda_pack | 59s | **75s** | 始终运行 |
| unpack + strip + compileall | 116s | 116s | 始终运行 |
| platform_pack | 0.1s | 0.1s | 始终运行 |
| **总计** | **~18 min** | **~3 min** | **6x 提速** |

**包体体积**：

| 优化项 | 效果 |
|---|---|
| Strip debug symbols + 删 .a/.h | -70MB |
| 移除 whisper（torch/numba/llvmlite） | -98MB |
| optional-dependencies 重构 | 核心安装可减 ~297MB |
| **便携版总计** | **948MB → 850MB** |

### 4.3 缓存链条（修复后）

三层清理互相破坏缓存的问题已全部修复：

| 层 | 原问题 | 修复 |
|---|---|---|
| `build_portable.sh` | 清理 dist/ 删除 wheel | 保留 `qwenpaw-*.whl` |
| `build_common.py` finally | conda-pack 后删除 conda env | 不删除 env |
| `build_common.py` 缓存条件 | 要求 archive 存在 | 仅要求 env 存在 |
| `wheel_build.sh` | 每次重建 wheel | 源码 hash 未变则跳过 |

### 4.4 optional-dependencies 重构

从硬依赖移到 `[project.optional-dependencies]`：

| 组 | 包 | 大小 |
|---|---|---|
| `ml` | onnxruntime, transformers | ~111MB |
| `browser` | playwright | ~134MB |
| `channels` | discord-py, python-telegram-bot, lark-oapi, matrix-nio, twilio | ~75MB |
| `full` | qwenpaw[local,ml,browser,channels] | 全部 |

便携版使用 `qwenpaw[full]` 保持完整功能。核心安装仅包含必要依赖。

**import guard 改动**：
- `app/channels/telegram/channel.py` — module-level `try/except ImportError`
- `app/channels/matrix/channel.py` — module-level `try/except ImportError`

---

## 5. 剩余任务

| 任务 | 风险 | 优先级 |
|---|---|---|
| §3.6.2 import 预读真机测量 | 需用户在 USB/exFAT 真机验证 | 用户侧 |
| optional-dependencies: 移除 modelscope | 需 LLVM 编译 llvmlite | 低 |
| optional-dependencies: 移除更多未 import 包 | 需逐一验证 | 低 |
| conda-pack compress-level 调优 | 收益 ~30-50s | 低 |
| Windows 便携版运行失败诊断 | 需复现 | 中 |
| macOS Intel 便携版 CI 验证 | 需 `macos-13` runner | 中 |

---

## 6. 已知坑

1. **`desktop_cmd.py` 预存改动**：pre-commit 每次提交时 stash/restore。**本次工作绝不收录它**。
2. **`_app.py` 格式化摩擦**：触碰时接受 black 重排 + 缩短 docstring。
3. **构建/lint 工具版本错配**：canonical = pre-commit，本地 CLI 报错时用 `pre-commit run` 复核。
4. **"best-effort" 必须覆盖所有同步前奏**：文件清单构建/初始化也必须在后台线程或严格有界。
5. **plan 行号会漂移**：写新计划前用 `grep` 重新定位。
6. **wheel hash 不稳定**：npm build 非确定性 → wheel 内容每次不同。已通过源码 hash 跳过重建解决。
7. **llvmlite 无预编译 wheel**：macOS x86_64 Python 3.10 无预编译 wheel，需 LLVM 编译。whisper 已从 `[full]` 中排除。

---

## 7. 关键文件索引

**运行时基石**：`src/qwenpaw/utils/atomic_io.py`、`background_tasks.py`、`import_prefetch.py`、`logging.py`
**打包工具**：`scripts/pack/build_profiler.py`、`analyze_packages.py`、`build_common.py`、`build_portable.sh`、`build_macos.sh`、`build_linux.sh`、`build_win_portable.ps1`
**CI**：`.github/workflows/build-portable.yml`
**规格/计划**：`docs/superpowers/specs/2026-06-24-打包优化-design.md`、`docs/superpowers/plans/2026-06-24-打包优化-phase1-profiling.md`

---

## 8. 接手清单（新会话第一步）

1. 通读本文。
2. `git log --oneline --since="2026-06-22"` 看近期提交全貌。
3. `git status` 确认工作区（注意 `desktop_cmd.py` 预存改动）。
4. 决定做 §5 哪一项 → 读对应 spec 章节 + 相关源码。
5. 走 `docs/superpowers/plans/` 出新计划。
6. subagent 驱动执行 + 两阶段评审。
7. 完成后更新本文。

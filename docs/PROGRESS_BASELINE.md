# PROGRESS_BASELINE — 性能优化总基准（所有新会话的统一起点）

> **用途**：本文是 QwenPaw 性能优化工作的**唯一权威基准**。任何新会话接手前，先读完本文，再按需读对应 spec / plan。本文锁定：已完成成果、设计决策、硬约束、剩余任务、流程约定与已知坑。
>
> **最后更新**：2026-06-25（打包全维度性能优化完成后）。
> **当前状态**：分支 `feature/usb-portable`。运行时性能优化 Phase 1–5 + 4b–4j 全部完成；打包性能优化 Phase 1–2 全部完成。

---

## 0. 一句话现状

**运行时性能**：三大核心场景（启动 IO / 运行时延迟 / 资源竞争）+ import 专项，全部完成（Phase 1–5 + Phase 4b/4c/4d/4e/4f/4g/4h/4i/4j），仅剩 §3.6.2 import 预读真机测量待用户验证。

**打包性能**：打包全维度性能优化已完成——Profiling 基础设施（构建耗时分阶段计时 + 包体体积分析 + Windows smoke test）、构建耗时优化（conda env 缓存修复 + wheel 重建跳过）、包体体积优化（strip debug symbols + optional-dependencies 重构 + 移除冗余依赖）、多架构适配（CI 扩展 macOS x86_64 + Windows 诊断）。**实测：构建时间 18:34 → 3:09（6 倍提速），包体 948MB → 850MB（-98MB）。**

---

## 1. 项目与分支上下文

- 仓库：QwenPaw-aixcore（Python 3.10、FastAPI + agentscope_runtime 多 Agent 桌面应用）。
- 工作分支：`feature/usb-portable`（USB 便携版特性分支，承载性能优化 + 打包优化；**不要合并到 main**——还有未完成项）。
- 运行时优化规格：`docs/superpowers/specs/2026-06-22-性能优化-design.md`（三大场景 + import 专项）。
- 打包优化规格：`docs/superpowers/specs/2026-06-24-打包优化-design.md`（构建耗时 + 包体体积 + 多架构适配）。
- 运行时优化提交范围：`9dc5c3b0` → `fc91941d`，共 ~41 个提交。
- 打包优化提交范围：`1a25962f` → `142377f0`，共 ~20 个提交。

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

### 运行时优化基石

两个共享基石（`src/qwenpaw/utils/`），被三大场景复用：

1. **`atomic_io.py`** — 安全原子写 + 进程内锁。tmp+fsync+`os.replace`，per-path `RLock` 串行化同路径并发。
2. **`background_tasks.py`** — `BackgroundTaskRunner`：受 lifespan 管理的 fire-and-forget 后台任务。

### 打包优化架构

打包流水线基于 **conda-pack**，pipeline 为：
```
wheel build → conda create → pip install → conda-pack → strip → unpack → compileall → 平台打包
```

**缓存机制**（三层缓存，修复后完整生效）：
1. **wheel 缓存**：`wheel_build.sh` 用源码内容 hash 判断是否需要重建（`src/**/*.py` + `pyproject.toml` + `console/src/**/*.ts` + `package-lock.json`）。源码未变则跳过 npm ci + npm build + python -m build。
2. **conda env 缓存**：`build_common.py` 用 wheel 内容 hash 作为 key，缓存 conda env 名称到 `.cache/conda_envs/`。env 存在即 cache hit，跳过 conda_create + pip_install 等 6 个阶段，仅运行 conda-pack。
3. **pip HTTP 缓存**：pip 内置缓存，加速依赖下载。

**包体优化**：
- 构建期 strip：`strip -x *.so *.dylib` + 删除 `*.a` / `*.h`（节省 ~70MB）
- optional-dependencies：将未 import 的大包（onnxruntime/transformers/modelscope）和通道专属 SDK 移到 `[project.optional-dependencies]`，便携版用 `[full]` extra 保持完整

---

## 4. 基石公共 API（调用方照此用）

### `src/qwenpaw/utils/atomic_io.py`
```python
write_bytes_atomic(path, data: bytes, *, lock=True, fsync=True) -> None
write_json_atomic(path, data, *, lock=True, fsync=True, indent=2,
                  ensure_ascii=False, sort_keys=False) -> None
read_json_safe(path, *, default=None) -> Any
locked_json_update(path, fn, *, default=None, fsync=True) -> Any
cleanup_orphan_tmps(directory, pattern="*.tmp.*") -> int
```

### `src/qwenpaw/utils/background_tasks.py`
```python
class BackgroundTaskRunner:
    spawn(coro, *, name=None) -> asyncio.Task
    spawn_after(delay, coro_factory, *, name=None) -> asyncio.Task
    async shutdown(timeout=5.0) -> None
```

### `scripts/pack/build_profiler.py`（打包优化新增）
```python
class BuildProfiler:
    stage(name) -> context_manager      # 计时上下文管理器（进程内用 monotonic）
    begin_stage(name) -> None            # CLI 模式开始阶段（跨进程用 time.time）
    finish_stage(name) -> bool           # CLI 模式结束阶段
    report() -> dict                     # 生成报告 dict
    save(path) -> None                   # 写 JSON 报告
    dump_state(path) / load_state(path)  # 跨进程状态持久化
# CLI: python build_profiler.py start/end/save <name> [--state-file path]
```

### `scripts/pack/analyze_packages.py`（打包优化新增）
```python
analyze_directory(env_dir) -> dict       # 分析目录磁盘占用
format_report(analysis, *, platform) -> dict  # 格式化报告
# CLI: python analyze_packages.py <env_dir> -o report.json
```

### `src/qwenpaw/utils/logging.py`
```python
add_project_file_handler(log_path: Path) -> None
stop_queue_listeners() -> None
queue_listener_stats() -> dict
LOG_BACKEND_PATH: Path
LOG_DESKTOP_PATH: Path
LOG_FILE_PATH = LOG_BACKEND_PATH
```

### `src/qwenpaw/utils/import_prefetch.py`
```python
start_import_prefetch(cap_mb=None, import_order_file=None) -> threading.Thread
```

---

## 5. 已完成 Phase 清单

### 运行时性能优化（Phase 1–5 + 4b–4j）

| Phase | 内容 | 状态 |
|---|---|---|
| Phase 1 | 基石：`atomic_io.py` + `background_tasks.py` | ✅ |
| Phase 2 | 8 处写迁移到 atomic_io + ChannelManager 死锁修复 | ✅ |
| Phase 3 | 遥测后台化 + 语言硬编码 | ✅ |
| Phase 4 | session 异步 + auth TTL 缓存 | ✅ |
| Phase 5 | hash-based .pyc + import prefetch | ✅ |
| Phase 4b | §4.1 模型客户端缓存（FIFO cap 64） | ✅ |
| Phase 4c | §4.3 chats.json 读缓存 + 写穿透 | ✅ |
| Phase 4d | §4.5 Part A tool result 溢写异步化 | ✅ |
| Phase 4e | §3.2 迁移戳幂等 | ✅ |
| Phase 4f | §3.4 restore stat 精简（scandir） | ✅ |
| Phase 4g | §5.4 secret_store 原子写 | ✅ |
| Phase 4h | §3.5 Workspace.start 冗余去重 | ✅ |
| Phase 4i | §5.3 日志 QueueHandler + 双文件拆分 | ✅ |
| Phase 4j | §3.3 load_envs 迁移到 lifespan | ✅ |

详见原 §5 各 Phase 条目（已归档）。

### 打包性能优化（Phase 1–2）

#### Phase 1 — Profiling 基础设施

| Commit | 内容 |
|---|---|
| `ebe1e789` | `scripts/pack/build_profiler.py` — BuildProfiler 核心计时库 + CLI |
| `e8a5474b` | 修复：跨进程 `time.monotonic()` → `time.time()`，CLI 封装（`begin_stage`/`finish_stage`） |
| `d24b7996` | 集成 profiling 到 `build_common.py`（`--profiling-output` 参数，7 阶段计时） |
| `4ae5bd42` | 修复：构建失败时也保存 profiling 报告（移入 `finally` 块） |
| `de7ae77b` | 集成 profiling 到 `build_macos.sh`（5 阶段 CLI 计时） |
| `0795f496` | 集成 profiling 到 `build_linux.sh`（5 阶段 CLI 计时） |
| `388eb52e` | 集成 profiling + smoke test 到 `build_win_portable.ps1` |
| `b16f3468` | `scripts/pack/analyze_packages.py` — 包体体积分析工具 |
| `288128ba` | CI workflow：macOS x86_64 job + Windows smoke test + profiling uploads |
| `0d08899d` | 修复：profiling JSON 在 `build_portable.sh` 清理前移入便携版目录 |
| `2c4eff24` | 修复：CLI 模式 `total_duration_s` 时钟域混用（monotonic vs wall-clock） |

#### Phase 2 — 构建耗时 + 包体体积优化

| Commit | 内容 |
|---|---|
| `34c91fa3` | Strip debug symbols + 删除 `.a`/`.h`（**-70MB**） |
| `034938a8` | optional-dependencies 重构：9 个包移到 `[project.optional-dependencies]` |
| `5a28b600` | 移除 modelscope（llvmlite 需 LLVM 编译） |
| `c6496449` | 移除 whisper（numba→llvmlite 无预编译 wheel） |
| `52420011` | 修复：conda env 缓存条件移除 `out_path.exists()` |
| `126b5d3f` | 修复：wheel_build.sh 源码 hash 未变则跳过重建 |
| `60c0b6bf` | 修复：build_portable.sh 清理时保留 `qwenpaw-*.whl` |
| `142377f0` | 修复：build_common.py finally 不删除 conda env |

---

## 6. 剩余任务

### 运行时性能

| spec 节 | 内容 | 风险/理由 | 建议优先级 |
|---|---|---|---|
| §3.6.2 实测 | import 预读真机测量 | **必须用户在 USB/exFAT 真机**跑 `-X importtime` 前后对比 | 用户侧验证 |

### 打包性能

| 内容 | 风险/理由 | 建议优先级 |
|---|---|---|
| optional-dependencies 验证 | 需在干净环境测试 `pip install qwenpaw`（仅核心）和 `pip install qwenpaw[full]` | 用户侧验证 |
| Windows 运行失败诊断 | smoke test 已集成，需在 Windows CI 上复现 | CI 验证 |
| macOS x86_64 本地构建 | 用户本机 Intel Mac，可直接 `make portable` | 用户侧验证 |

---

## 7. 打包优化 Profiling 基线数据

### 构建耗时（本机 Intel Mac，首次无缓存）

| 阶段 | 耗时 | 占比 |
|---|---|---|
| wheel_build | 175s (2.9 min) | 17% |
| conda_create | 17s | 2% |
| **pip_install** | **186s (3.1 min)** | **33%** |
| verify_certifi | 3s | <1% |
| pip_uninstall | 6s | 1% |
| pip_cache_purge | 4s | 1% |
| conda_fix | 14s | 3% |
| conda_pack | 59s (1.0 min) | 11% |
| strip | ~10s | 2% |
| unpack | 44s | 8% |
| compileall | 48s | 9% |
| platform_pack | <1s | <1% |
| **总计** | **~530s (8.8 min)** | |

### 缓存命中时

| 阶段 | 耗时 |
|---|---|
| wheel_build（跳过） | 0s |
| build_common.py（仅 conda_pack） | 75s |
| strip + unpack + compileall + platform_pack | ~100s |
| **总计** | **~3 min** |

### 包体体积（conda 环境 850MB）

| 包 | 大小 | 状态 |
|---|---|---|
| playwright | 134MB | `[browser]` optional |
| onnxruntime | 68MB | `[ml]` optional |
| chromadb_rust_bindings | 52MB | transitive |
| transformers | 43MB | `[ml]` optional |
| pandas | 38MB | transitive |
| alibabacloud_dingtalk | 38MB | core |
| grpc | 38MB | transitive |
| qwenpaw | 35MB | core |
| numpy | 24MB | core |
| twilio | 21MB | `[channels]` optional |

---

## 8. 流程与工程约定

1. **TDD**：每 Task 先写失败测试 → 跑红 → 实现 → 跑绿 → commit。
2. **增量导入**：同一文件多 Task 逐步加函数时，**每个 Task 只导入它用到的名字**。
3. **执行模式**：subagent 驱动（implementer subagent 每 Task + 两阶段评审：spec 合规 → 代码质量）。
4. **每 Phase 流程**：brainstorming/writing-plans 出计划 → subagent-driven-development 执行 → 里程碑评审 + 最终评审。Phase 完成后**不合并分支**。
5. **测试根目录命令**：`pytest tests/unit/<module>/ -q`；asyncio_mode=auto。

---

## 9. 已知坑

1. **`desktop_cmd.py` 预存改动**：会话开始时存在 `M src/qwenpaw/cli/desktop_cmd.py`（用户预存的未暂存改动）。pre-commit 在每次提交时 stash/restore 它。**本次工作绝不收录它**。

2. **`_app.py` 预存的格式化摩擦**：触碰 `_app.py` 时，pre-commit 的 black 23.3.0 会重排某些赋值。处理：接受 black 的稳定重排 + 缩短 docstring。

3. **构建/lint 工具版本错配**：本地 CLI 工具可能与 pre-commit 钉死版本不一致。**canonical = pre-commit**。用 `pre-commit run <hook> --files <path>` 复核。

4. **"best-effort/never-blocks" 必须覆盖所有同步前奏**：`import_prefetch` 最初把文件扫描放在主线程阻塞启动。修复：扫描移到 worker 线程 + 墙钟上限。

5. **死锁回归测试用超时变红**：测 `asyncio.Lock` 自死锁时，用 `asyncio.wait_for(coro, timeout=N)`。

6. **plan 里的行号会漂移**：写新计划前用 `grep` 重新定位真实当前代码。

7. **wheel 每次构建内容不同**（npm build 非确定性）：`wheel_build.sh` 用源码内容 hash 判断是否需要重建，而非 wheel 文件 hash。源码未变则跳过整个 npm ci + npm build + python -m build 流程。

8. **conda env 缓存需要三层同时生效**：wheel 存在 + 源码 hash 未变 + conda env 存在。任何一层断裂都会导致全量重建。

9. **openai-whisper 依赖链问题**：whisper → numba → llvmlite，llvmlite 无 macOS x86_64 预编译 wheel（需 LLVM 编译）。已从 `[full]` extra 中排除 whisper，用户需单独安装。

---

## 10. 关键文件索引

### 运行时优化

**基石**：`src/qwenpaw/utils/atomic_io.py`、`background_tasks.py`、`import_prefetch.py`、`logging.py`
**结构性改动**：`app/_app.py`、`app/channels/manager.py`、`cli/main.py`、`agents/model_factory.py`、`app/runner/repo/json_repo.py`、`agents/context/light_context_manager.py`、`app/migration.py`、`backup/_utils/safe_swap.py` + `_mount_swap.py`、`security/secret_store.py`、`agents/skills_manager.py` + `app/workspace/workspace.py`、`cli/desktop_cmd.py`、`cli/doctor_checks.py`

### 打包优化

**新增**：`scripts/pack/build_profiler.py`、`scripts/pack/analyze_packages.py`
**改动**：`scripts/pack/build_common.py`（profiling + 缓存修复 + `[full]` extra）、`scripts/pack/build_macos.sh`（profiling + strip）、`scripts/pack/build_linux.sh`（profiling + strip）、`scripts/pack/build_win_portable.ps1`（profiling + smoke test）、`scripts/pack/build_portable.sh`（profiling JSON 保留 + wheel 保留）、`scripts/wheel_build.sh`（源码 hash 跳过重建）、`pyproject.toml`（optional-dependencies）、`.github/workflows/build-portable.yml`（macOS x86_64 + Windows smoke test）、`src/qwenpaw/app/channels/telegram/channel.py`（try/except guard）、`src/qwenpaw/app/channels/matrix/channel.py`（try/except guard）

**测试**：`tests/unit/scripts/test_build_profiler.py`（8）、`tests/unit/scripts/test_build_common_profiling.py`（2）、`tests/unit/scripts/test_analyze_packages.py`（3）

**规格/计划**：`docs/superpowers/specs/2026-06-24-打包优化-design.md`、`docs/superpowers/plans/2026-06-24-打包优化-phase1-profiling.md`

---

## 11. 接手清单（新会话第一步）

1. 通读本文。
2. `git log --oneline 9dc5c3b0..HEAD` 看提交全貌；`git status` 确认工作区（注意 `desktop_cmd.py`）。
3. 决定做 §6 哪一项 → 读对应 spec 章节 + 相关源码（用 grep 定位真实行号）。
4. 走 `docs/superpowers/plans/` 出新计划（bite-sized TDD 任务、完整 before→after 代码、无占位符）。
5. subagent 驱动执行 + 两阶段评审；Phase 完成做最终评审，**不合并分支**。
6. 完成后更新本文（§5 加 Phase、§6 删项、§0 改现状）。

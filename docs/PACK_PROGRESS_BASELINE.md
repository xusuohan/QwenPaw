# PACK_PROGRESS_BASELINE — 打包优化总基准（所有新会话的统一起点）

> **用途**：本文是 QwenPaw 便携版打包优化工作的**唯一权威基准**。任何新会话接手前，先读完本文，再按需读对应 spec / plan。本文锁定：已完成成果、设计决策、硬约束、剩余任务、流程约定与已知坑。
>
> **最后更新**：2026-06-25（缓存链条修复完成后）。
> **当前状态**：分支 `feature/usb-portable`。Phase 1（Profiling）+ Phase 2（优化）已实现。

---

## 0. 一句话现状

三大核心场景（构建耗时 / 包体体积 / 多架构适配）**均有实质进展**。构建耗时：首次 16.5 min → 缓存命中 1.2 min（**13 倍提速**）。包体体积：948 MB → 850 MB（**-104 MB**）。多架构：CI 已扩展 macOS x86_64 + Windows smoke test。**缓存链条已完全修复**——wheel 保留 + env 保留 + archive 条件移除，三层清理不再互相破坏。

---

## 1. 项目与分支上下文

- 仓库：QwenPaw-aixcore（Python 3.10、FastAPI + agentscope_runtime 多 Agent 桌面应用）。
- 工作分支：`feature/usb-portable`（USB 便携版特性分支，承载本次打包优化；**不要合并到 main**——还有未完成项）。
- 规格文档：`docs/superpowers/specs/2026-06-24-打包优化-design.md`（**完整设计**，三大场景 + profiling 基础设施）。
- Phase 计划：`docs/superpowers/plans/2026-06-24-打包优化-phase1-profiling.md`。
- 运行时性能优化基准：`docs/PROGRESS_BASELINE.md`（Phase 1-5 + 4b-4j 已完成）。
- 本次打包优化提交范围：`905d5fd8`（spec）→ `142377f0`（HEAD），共 ~22 个提交。

---

## 2. 硬约束（brainstorming 已确认，不可违背）

| # | 约束 | 含义 |
|---|---|---|
| C1 | **部署目标：USB 便携版优先（exFAT）** | 构建产出需兼容 exFAT：无 UNIX 权限、无 journaling、随机 IO 延迟高。 |
| C2 | **单实例，仅进程内并发** | 构建脚本不需要跨进程协调。 |
| C3 | **可接受小幅行为变更** | 裁剪依赖、调整压缩级别等均可用。 |
| C4 | **不碰宿主磁盘** | 所有裁剪在构建期完成，不影响运行时。 |
| C5 | **分阶段推进** | 先本机 Intel Mac profiling + 优化，再扩展其他架构。 |

---

## 3. 设计决策（架构）

**打包方案**：保留 **conda-pack**（不迁移 PyInstaller/Nuitka）。通过 profiling 数据驱动逐阶段优化。

**缓存三层机制**：
1. **wheel_build.sh**：源码 hash（`src/**/*.py` + `pyproject.toml` + `console/src/**/*.ts` + `package-lock.json`）未变 → 跳过 npm ci + npm build + python -m build。
2. **build_common.py**：wheel 内容 hash 未变 + conda env 存在 → 跳过 conda create + pip install 等全部阶段，仅运行 conda-pack。
3. **build_portable.sh**：清理 dist/ 时保留 wheel 和 sdist，不破坏下次构建的缓存条件。

**optional-dependencies 重构**：9 个包从硬依赖移到 optional extras（ml / browser / channels），便携版用 `[full]` 安装全部。核心安装可减 ~297 MB。

---

## 4. 已完成 Phase 清单（含提交 SHA）

### Phase 1 — Profiling 基础设施（`ebe1e789` → `288128ba`）

| Commit | 内容 |
|---|---|
| `ebe1e789` | `scripts/pack/build_profiler.py` — BuildProfiler 类 + CLI（start/end/save） |
| `e8a5474b` | 修复：跨进程 `time.monotonic()` → `time.time()`，CLI 封装（begin_stage/finish_stage） |
| `d24b7996` | 集成 profiling 到 `build_common.py`（--profiling-output + 7 阶段 stage()） |
| `4ae5bd42` | 修复：构建失败时 profiling 报告也保存（移入 finally） |
| `de7ae77b` | 集成 profiling 到 `build_macos.sh`（5 阶段 CLI 调用） |
| `0795f496` | 集成 profiling 到 `build_linux.sh`（5 阶段 CLI 调用） |
| `388eb52e` | 集成 profiling + smoke test 到 `build_win_portable.ps1` |
| `b16f3468` | `scripts/pack/analyze_packages.py` — 包体体积分析工具 |
| `288128ba` | CI workflow：macOS x86_64 job + Windows smoke test + profiling uploads |
| `0d08899d` | 修复：profiling JSON 在 dist/ 清理前移入便携版目录 |
| `2c4eff24` | 修复：CLI 模式 `total_duration_s` 时钟混用 bug |

**产出物**：
- `scripts/pack/build_profiler.py` — 共享计时工具库（库 + CLI 双模式）
- `scripts/pack/analyze_packages.py` — 包体体积分析工具
- `tests/unit/scripts/test_build_profiler.py`（8 测试）
- `tests/unit/scripts/test_analyze_packages.py`（3 测试）
- `tests/unit/scripts/test_build_common_profiling.py`（2 测试）
- 3 个构建脚本已注入 profiling（macOS / Linux / Windows）
- CI 矩阵扩展：macOS arm64 + x86_64 + Linux + Windows（含 smoke test）

### Phase 2 — 包体优化（`34c91fa3` → `034938a8`）

| Commit | 内容 |
|---|---|
| `34c91fa3` | Strip debug symbols + 删除 `.a`/`.h`（**-70 MB**） |
| `034938a8` | optional-dependencies 重构：9 个包移到 ml/browser/channels 组 |
| `5a28b600` | 移除 modelscope（llvmlite 编译失败） |
| `c6496449` | 从 `[full]` 排除 whisper（numba→llvmlite 需要 LLVM） |

**包体变化**：
| 指标 | 优化前 | 优化后 | 变化 |
|---|---|---|---|
| 便携版总大小 | 948 MB | 850 MB | **-104 MB (-11%)** |
| conda 环境 | 948 MB | 850 MB | -104 MB |

### Phase 3 — 缓存链条修复（`52420011` → `142377f0`）

| Commit | 内容 |
|---|---|
| `52420011` | 修复：conda env 缓存条件移除 `out_path.exists()` |
| `126b5d3f` | `wheel_build.sh` 源码 hash 检查，未变则跳过重建 |
| `60c0b6bf` | `build_portable.sh` 清理时保留 wheel |
| `142377f0` | 修复：不再删除 conda env（保留供下次缓存命中） |

**构建耗时变化**：
| 场景 | 优化前 | 优化后 | 提升 |
|---|---|---|---|
| 首次构建（无缓存） | ~18 min | ~16.5 min | -8% |
| 缓存命中 | ~18 min | **~1.2 min** | **13x 快** |

**缓存命中时各阶段耗时**：
| 阶段 | 耗时 | 说明 |
|---|---|---|
| wheel_build | 0s | 跳过（源码未变） |
| conda_create | 0s | 跳过（env 存在） |
| pip_install | 0s | 跳过（env 存在） |
| conda_pack | 75s | 仅此阶段运行 |
| unpack + strip + compileall + platform_pack | ~30s | 平台脚本 |

---

## 5. Profiling 数据（本机 Intel Mac）

### 首次构建（cache miss）

| 阶段 | 耗时 | 占比 |
|---|---|---|
| wheel_build | ~120s | 12% |
| conda_create | 16.5s | 1.6% |
| **pip_install** | **897.6s (15 min)** | **89.4%** |
| verify_certifi | 3.0s | 0.3% |
| pip_uninstall | 6.9s | 0.7% |
| pip_cache_purge | 3.1s | 0.3% |
| conda_fix | 11.9s | 1.2% |
| conda_pack | 64.0s | 6.4% |
| **总计** | **~16.5 min** | |

### 缓存命中（cache hit）

| 阶段 | 耗时 |
|---|---|
| wheel_build | 0s（跳过） |
| conda_pack | 75s |
| **总计** | **~1.2 min** |

### 包体分析（conda 环境 850 MB）

| 包 | 大小 | 类型 |
|---|---|---|
| playwright | 134 MB | browser extra |
| onnxruntime | 68 MB | ml extra |
| chromadb_rust_bindings | 52 MB | transitive（reme-ai） |
| transformers | 43 MB | ml extra |
| pandas | 38 MB | transitive（reme-ai） |
| alibabacloud_dingtalk | 38 MB | 核心依赖 |
| grpc | 38 MB | transitive（google-genai） |
| qwenpaw | 35 MB | 应用本身 |
| numpy | 24 MB | transitive |
| modelscope | 21 MB | 已移除 |
| twilio | 21 MB | channels extra |
| cryptography | 20 MB | 核心依赖 |
| lark_oapi | 19 MB | channels extra |

---

## 6. optional-dependencies 结构

```toml
[project.optional-dependencies]
ml = ["onnxruntime<1.24", "transformers>=4.30.0"]
browser = ["playwright>=1.49.0"]
channels = ["discord-py>=2.3", "python-telegram-bot>=20.0", "lark-oapi>=1.5.3",
            "matrix-nio>=0.24.0", "twilio>=9.10.2"]
full = ["qwenpaw[local,ml,browser,channels]"]
# whisper excluded: requires numba→llvmlite which needs LLVM to build
```

**核心安装**（无 optional）：~553 MB
**完整安装**（`[full]`）：~850 MB

---

## 7. 剩余任务（按优先级）

| # | 内容 | 风险 | 建议优先级 |
|---|---|---|---|
| 1 | macOS x86_64 本机构建验证 | 低 | 用户侧验证 |
| 2 | Windows 便携版 smoke test 诊断 + 平台适配 | 中 | 需 Windows 环境 |
| 3 | conda-pack compress-level 调优（1 vs 4） | 低 | 可选 |
| 4 | whisper 支持（需解决 llvmlite 编译） | 高 | 需 LLVM 或预编译 wheel |

### 任务 2 详细说明：Windows 便携版 smoke test 诊断 + 平台适配

**前置条件**：macOS Intel 架构打包运行逻辑已调试适配完成（缓存链条、optional-dependencies、strip 优化均已验证通过）。

**第一阶段：Windows 便携版冒烟测试与问题诊断**

1. 在 Windows 环境运行 `make portable-windows`（或 `powershell -File scripts/pack/build_win_portable.ps1`）
2. 运行 smoke test：`windows\env\python.exe -c "import qwenpaw; print(qwenpaw.__version__)"`
3. 若失败，收集诊断信息（`dist/diagnostics/`）：
   - `python_version.txt` — Python 版本
   - `pip_list.txt` — 已安装包列表
   - `path.txt` — PATH 环境变量
   - `unpack_log.txt` — conda-unpack 输出
4. 分析根因（预期可能是 conda-unpack bug #154 变体、长路径问题、DLL 缺失等）

**第二阶段：复用 macOS 适配方案完善 Windows 打包流程**

基于 macOS Intel 架构已验证的适配方案，针对性完善 Windows 平台：

1. **缓存链条适配**：确认 `wheel_build.sh`（Windows 用 `wheel_build.ps1`）的源码 hash 检查在 Windows 上正常工作；确认 `build_win_portable.ps1` 的清理逻辑保留 wheel
2. **optional-dependencies 适配**：确认 `build_common.py` 的 `[full]` extra 在 Windows conda 环境中正确解析（特别是 `onnxruntime` 的 Windows wheel）
3. **strip 优化适配**：Windows 无 `strip` 命令，评估是否需要用 MSVC `editbin` 或跳过（Windows .pyd 已由 MSVC 优化）
4. **profiling 集成验证**：确认 `build_profiler.py` CLI 在 Windows 上的 `time.time()` 跨进程行为正确
5. **conda-unpack bug workaround**：验证 `CONDA_UNPACK_AFFECTED_PACKAGES` 重装逻辑在新版 conda-pack 上是否仍需要
6. **长路径问题**：验证 `extract_zip.py` 的 `\\?\` 前缀在新版 Windows 上是否仍需要

**产出物**：
- Windows 便携版构建 profiling 数据（`build_profiling.json`）
- 问题诊断报告（如有）
- 必要的脚本修复（`build_win_portable.ps1`、`wheel_build.ps1`）

---

## 8. 关键文件索引

**Profiling 工具**：`scripts/pack/build_profiler.py`（BuildProfiler + CLI）、`scripts/pack/analyze_packages.py`（包体分析）
**构建脚本**：`scripts/pack/build_common.py`（conda env + conda-pack）、`scripts/pack/build_macos.sh`、`scripts/pack/build_linux.sh`、`scripts/pack/build_win_portable.ps1`、`scripts/pack/build_portable.sh`（编排 + 清理）、`scripts/wheel_build.sh`（wheel 构建 + 缓存）
**CI**：`.github/workflows/build-portable.yml`（macOS arm64/x86_64 + Linux + Windows）
**配置**：`pyproject.toml`（dependencies + optional-dependencies）
**测试**：`tests/unit/scripts/test_build_profiler.py`、`tests/unit/scripts/test_analyze_packages.py`、`tests/unit/scripts/test_build_common_profiling.py`
**规格/计划**：`docs/superpowers/specs/2026-06-24-打包优化-design.md`、`docs/superpowers/plans/2026-06-24-打包优化-phase1-profiling.md`

---

## 9. 已知坑（踩过的，务必避开）

1. **wheel_build.sh 的 npm build 是非确定性的**：即使源码不变，`npm ci && npm run build` 产出的 wheel 内容也不同（时间戳等）。已通过源码 hash 跳过重建解决。**不要删除 `.cache/wheel_source_hash`**。

2. **conda-pack 归档在 dist/ 清理时被删除**：`build_portable.sh` 的 `find` 清理会删除 dist/ 下所有非便携版文件。已修复：保留 `qwenpaw-*.whl` 和 `qwenpaw-*.tar.gz`。**不要恢复旧的清理逻辑**。

3. **conda env 在 finally 块中被删除**：`build_common.py` 的 `finally` 块曾删除非缓存的 env，导致缓存链断裂。已修复：不再删除 env。**env 会占用 ~1 GB 磁盘空间，但换来 13 倍构建提速**。

4. **llvmlite/numba 编译失败**：`openai-whisper` 依赖 `numba`→`llvmlite`，在 macOS x86_64 上无预编译 wheel，需要 LLVM。已从 `[full]` 排除 whisper。**不要将 whisper 加回 `[full]`** 除非解决了 llvmlite 编译问题。

5. **modelscope 依赖链**：`modelscope`→`numba`→`llvmlite`，同样有编译问题。已从 `ml` 组移除。用户需要时单独 `pip install modelscope`。

6. **profiling JSON 被 dist/ 清理删除**：已修复——`build_portable.sh` 在清理前将 profiling JSON 移入便携版目录。

---

## 10. 接手清单（新会话第一步）

1. 通读本文。
2. `git log --oneline 905d5fd8..HEAD` 看提交全貌；`git status` 确认工作区。
3. 决定做 §7 哪一项 → 读对应 spec 章节 + 相关源码（用 grep 定位真实行号）。
4. 走 `docs/superpowers/plans/` 出新计划（bite-sized TDD 任务、完整 before→after 代码、无占位符）。
5. subagent 驱动执行 + 两阶段评审；Phase 完成做最终评审，**不合并分支**。
6. 完成后更新本文（§4 加 Phase、§7 删项、§0 改现状）。

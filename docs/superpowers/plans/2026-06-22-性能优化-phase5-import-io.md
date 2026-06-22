# 性能优化 Phase 5：启动期 py import 4K 随机读写（哈希 pyc + import 预读）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 改善 USB/exFAT 便携版启动期 Python import 的 4K 随机读写——(a) **哈希 pyc（§3.6.1，确定性收益）**：构建期 `compileall` 改 `--invalidation-mode checked-hash`，让 .pyc 按源码哈希校验而非 mtime，消除 exFAT 上 mtime 漂移导致的 import 期重编译**写** .pyc（4K 随机写的根因）；(b) **import 顺序预读（§3.6.2，投机收益、可开关）**：新增后台线程在重 import 链开始前顺序预读 `.pyc`/`.pyd` 填 OS 页缓存，把随机读转顺序读。

**Architecture:** §3.6.1 是 4 个构建脚本的一行参数改动（`--invalidation-mode checked-hash`），零运行期风险。§3.6.2 是新模块 `utils/import_prefetch.py`（顺序读预热线程，best-effort，吞所有异常）+ 在 `cli/main.py` 的 `cli()` 入口（LazyGroup 派发子命令、即重 import 前）经 `QWENPAW_PERF_IMPORT_PREFETCH` 开关启动。§3.6.2 因 USB 队列竞争可能反噬，故**默认开但可单杀 + 必须实测**（spec §3.6.2 风险点已述）。

**Tech Stack:** Python 3.10、stdlib（threading/os）、pytest。构建脚本：bash/powershell + `compileall`。

**对应 spec：** `docs/superpowers/specs/2026-06-22-性能优化-design.md` 第 3.6 节。

**前置：** Phase 1–4 已完成。本 Phase 从当前 `feature/usb-portable` HEAD 继续。

---

## 范围说明

spec §3.6 拆为两半：§3.6.1（哈希 pyc，构建期，**确定性**消除 import 期 .pyc 重写）+ §3.6.2（预读，运行期，**投机**改善 import 期随机读）。两者都做。§3.6.2 默认开但带开关 + 实测门槛；若实测反噬可关掉仅保留 §3.6.1 的确定性收益。

---

## Task 1: 哈希 pyc（§3.6.1，确定性收益）

**Files:**
- Modify: `scripts/pack/build_linux.sh:51`
- Modify: `scripts/pack/build_macos.sh:58`
- Modify: `scripts/pack/build_win.ps1:213`
- Modify: `scripts/pack/build_win_portable.ps1:201`

4 个 `compileall -q -j 0 ...` 调用都加 `--invalidation-mode checked-hash`（Python 3.7+ 标准）。

- [ ] **Step 1: build_linux.sh**

`scripts/pack/build_linux.sh:51`：
```
"${DIST}/linux/env/bin/python" -m compileall -q -j 0 "${DIST}/linux/env" >/dev/null 2>&1 || true
```
改为（加 `--invalidation-mode checked-hash`）：
```
"${DIST}/linux/env/bin/python" -m compileall -q -j 0 --invalidation-mode checked-hash "${DIST}/linux/env" >/dev/null 2>&1 || true
```

- [ ] **Step 2: build_macos.sh**

`scripts/pack/build_macos.sh:58`：同样在 `-j 0` 后加 `--invalidation-mode checked-hash`。

- [ ] **Step 3: build_win.ps1**

`scripts/pack/build_win.ps1:213`：
```powershell
  & $pythonExe -m compileall -q -j 0 $EnvRoot
```
改为：
```powershell
  & $pythonExe -m compileall -q -j 0 --invalidation-mode checked-hash $EnvRoot
```

- [ ] **Step 4: build_win_portable.ps1**

`scripts/pack/build_win_portable.ps1:201`（注意已有 `-x $skipRx`）：
```powershell
  & $py -m compileall -q -j 0 -x $skipRx $dir
```
改为（`--invalidation-mode` 放在 `-x` 前）：
```powershell
  & $py -m compileall -q -j 0 --invalidation-mode checked-hash -x $skipRx $dir
```

- [ ] **Step 5: 验证 .pyc 确为哈希校验**

跑一次 compileall 到临时目录，检查产物 .pyc 头的 flags 字段 == 2（CHECKED_HASH）：

```bash
python - <<'PY'
import struct, subprocess, sys, tempfile
from pathlib import Path
d = Path(tempfile.mkdtemp())
src = d / "m.py"; src.write_text("x = 1\n")
r = subprocess.run([sys.executable, "-m", "compileall", "-q", "-j", "0",
                    "--invalidation-mode", "checked-hash", str(src)],
                   capture_output=True)
assert r.returncode == 0, r.stderr
pyc = next((d / "__pycache__").glob("*.pyc"))
with open(pyc, "rb") as f:
    flags = struct.unpack("<I", f.read(16)[4:8])[0]
# PEP 552: bit0=1 => hash-based (no mtime). checked-hash sets bits 0+1 => flags=3.
# (timestamp=0, checked-hash=3, unchecked-hash=1, empirically on CPython 3.10.)
assert flags & 1 == 1, f"expected hash-based .pyc, got flags={flags}"
assert flags == 3, f"expected checked-hash (3), got flags={flags}"
print(f"OK: checked-hash .pyc produced, flags={flags}")
PY
```
Expected: `OK: checked-hash .pyc produced, flags=3`。

- [ ] **Step 6: Commit**

```bash
git add scripts/pack/build_linux.sh scripts/pack/build_macos.sh scripts/pack/build_win.ps1 scripts/pack/build_win_portable.ps1
git commit -m "build(pack): hash-based .pyc (--invalidation-mode checked-hash) to stop exFAT mtime-driven recompile writes

Co-Authored-By: Claude <noreply@anthropic.com>"
```
（pre-commit 对 .sh/.ps1 一般只跑 trailing-whitespace；若 reformats 则 re-stage。`M src/qwenpaw/cli/desktop_cmd.py` 勿动。）

---

## Task 2: import 顺序预读（§3.6.2，投机收益、可开关）

**Files:**
- Create: `src/qwenpaw/utils/import_prefetch.py`
- Modify: `src/qwenpaw/cli/main.py`（`cli()` 入口启动预读，开关 `QWENPAW_PERF_IMPORT_PREFETCH`）
- Test: `tests/unit/utils/test_import_prefetch.py`

**设计**：新模块提供 `start_import_prefetch(cap_mb, import_order_file=None) -> threading.Thread | None`，起一个 daemon 线程，按顺序读（`read` 1MB 块直至 EOF，触发 OS readahead 填页缓存）`.pyc`/`.pyd` 文件清单：优先用 `import_order_file`（`-X importtime` 采集的顺序）；缺失则对 site-packages + qwenpaw 包做目录序顺序扫描。best-effort：吞所有 OSError，绝不抛、绝不阻塞主线程。`cap_mb` 截断总量。开关默认开（`QWENPAW_PERF_IMPORT_PREFETCH` 未设/为真时启动；设为 `0`/`false` 关闭）；另 `QWENPAW_PERF_PREFETCH_CAP_MB` 覆盖上限。

- [ ] **Step 1: 写失败测试**

新建 `tests/unit/utils/test_import_prefetch.py`：

```python
# -*- coding: utf-8 -*-
"""Tests for utils.import_prefetch (best-effort page-cache warmer)."""
from __future__ import annotations

import os
import threading
from pathlib import Path

from qwenpaw.utils.import_prefetch import start_import_prefetch


class TestStartImportPrefetch:
    def test_reads_files_and_returns_thread(self, tmp_path):
        # Two .pyc files to warm.
        (tmp_path / "a.pyc").write_bytes(b"x" * 4096)
        (tmp_path / "b.pyc").write_bytes(b"y" * 4096)
        order = tmp_path / "order.txt"
        order.write_text(
            f"{tmp_path / 'a.pyc'}\n{tmp_path / 'b.pyc'}\n",
            encoding="utf-8",
        )
        thread = start_import_prefetch(cap_mb=64, import_order_file=order)
        assert thread is not None
        thread.join(timeout=5)
        assert not thread.is_alive()  # completed

    def test_missing_files_are_swallowed(self, tmp_path):
        order = tmp_path / "order.txt"
        order.write_text(f"{tmp_path / 'nope.pyc'}\n", encoding="utf-8")
        thread = start_import_prefetch(cap_mb=64, import_order_file=order)
        assert thread is not None
        thread.join(timeout=5)  # no raise

    def test_cap_truncates(self, tmp_path):
        big = tmp_path / "big.pyc"
        big.write_bytes(b"z" * (2 * 1024 * 1024))  # 2MB
        order = tmp_path / "order.txt"
        order.write_text(f"{big}\n", encoding="utf-8")
        thread = start_import_prefetch(cap_mb=1, import_order_file=order)
        thread.join(timeout=5)  # reads ≤ cap, no error
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/utils/test_import_prefetch.py -v`
Expected: FAIL — `ImportError: cannot import name 'start_import_prefetch'`。

- [ ] **Step 3: 写实现**

新建 `src/qwenpaw/utils/import_prefetch.py`：

```python
# -*- coding: utf-8 -*-
"""Best-effort import-time page-cache warmer for USB/exFAT startup.

On slow USB media, the import-driven pattern of opening many small .pyc/.pyd
files is dominated by random 4K reads (high latency). A daemon thread that
sequentially pre-reads those files converts some of that random IO into
sequential reads, warming the OS page cache so the import machinery then
hits warm pages.

Best-effort: swallows all OSError, never blocks the main thread, never
raises. Toggled by QWENPAW_PERF_IMPORT_PREFETCH (default on); bounded by
QWENPAW_PERF_PREFETCH_CAP_MB (default 256). If a measured USB workload
regresses (prefetch competes for the single USB queue), disable via env.
"""
from __future__ import annotations

import logging
import os
import sys
import threading
from pathlib import Path

logger = logging.getLogger(__name__)

_DEFAULT_CAP_MB = 256
_CHUNK = 1024 * 1024  # 1MB sequential reads


def _build_file_list(import_order_file: Path | None) -> list[Path]:
    """Return the list of .pyc/.pyd files to pre-read, in order."""
    files: list[Path] = []
    if import_order_file and import_order_file.is_file():
        try:
            for line in import_order_file.read_text(encoding="utf-8").splitlines():
                p = line.strip()
                if p:
                    files.append(Path(p))
        except OSError:
            pass
    if files:
        return files
    # Fallback: directory-order sweep of site-packages + the qwenpaw package.
    roots: list[Path] = []
    for entry in sys.path:
        try:
            ep = Path(entry).resolve()
        except OSError:
            continue
        if ep.is_dir():
            roots.append(ep)
    pkg_root = Path(__file__).resolve().parent.parent  # qwenpaw/
    roots.append(pkg_root)
    seen: set[str] = set()
    for root in roots:
        try:
            for p in root.rglob("*.pyc"):
                key = str(p)
                if key not in seen:
                    seen.add(key)
                    files.append(p)
            for p in root.rglob("*.pyd"):
                files.append(p)
            for p in root.rglob("*.so"):
                files.append(p)
        except OSError:
            continue
    return files


def _prefetch_worker(files: list[Path], cap_bytes: int) -> None:
    read_total = 0
    for f in files:
        if read_total >= cap_bytes:
            break
        try:
            size = f.stat().st_size
        except OSError:
            continue
        try:
            with open(f, "rb") as fh:
                while True:
                    if read_total >= cap_bytes:
                        break
                    chunk = fh.read(_CHUNK)
                    if not chunk:
                        break
                    read_total += len(chunk)
        except OSError:
            continue


def start_import_prefetch(
    cap_mb: int | None = None,
    import_order_file: Path | str | None = None,
) -> threading.Thread | None:
    """Start a daemon thread that sequentially pre-reads bytecode/C-ext files.

    Returns the thread (or None if disabled / no files). Best-effort: the
    worker swallows all OSError and never raises.
    """
    if cap_mb is None:
        cap_mb = _DEFAULT_CAP_MB
    order_path = (
        Path(import_order_file) if import_order_file is not None else None
    )
    files = _build_file_list(order_path)
    if not files:
        return None
    thread = threading.Thread(
        target=_prefetch_worker,
        args=(files, cap_mb * 1024 * 1024),
        name="import-prefetch",
        daemon=True,
    )
    thread.start()
    logger.debug("import prefetch started: %d files, cap=%dMB", len(files), cap_mb)
    return thread
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/utils/test_import_prefetch.py -v`
Expected: 3 PASS。

- [ ] **Step 5: 在 cli() 入口接入（开关）**

读 `src/qwenpaw/cli/main.py` 找到 `cli()` 函数（LazyGroup 派发子命令处，重 import 之前）。在 `cli()` 函数体最早期（`ensure_standard_streams(...)` 之后、子命令派发之前）加：

```python
    # Warm OS page cache for bytecode/C-ext on slow USB media. Best-effort,
    # daemon thread, toggle via QWENPAW_PERF_IMPORT_PREFETCH (default on).
    if os.environ.get("QWENPAW_PERF_IMPORT_PREFETCH", "1").lower() not in (
        "0",
        "false",
        "no",
    ):
        try:
            cap = os.environ.get("QWENPAW_PERF_PREFETCH_CAP_MB")
            from ..utils.import_prefetch import start_import_prefetch

            start_import_prefetch(cap_mb=int(cap) if cap else None)
        except Exception:
            logger.debug("import prefetch start skipped", exc_info=True)
```

（确认 `os` 与 `logging.getLogger(__name__)` 在 cli/main.py 可用——`os` 若未导入则加 `import os`，logger 若无则 `logger = logging.getLogger(__name__)`。）

- [ ] **Step 6: 验证**

```bash
python -c "import qwenpaw.utils.import_prefetch; print('module OK')"
QWENPAW_PERF_IMPORT_PREFETCH=0 python -c "from qwenpaw.cli.main import cli; print('cli import OK')"
python -m pytest tests/unit/utils/ -q
```
Expected: module OK / cli import OK / utils 测试全 PASS。

- [ ] **Step 7: Commit**

```bash
git add src/qwenpaw/utils/import_prefetch.py src/qwenpaw/cli/main.py tests/unit/utils/test_import_prefetch.py
git commit -m "perf(cli): import prefetch warmer (flagged, best-effort) for USB/exFAT startup

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Task 3: Phase 5 验证 + 最终评审

- [ ] **Step 1: 回归 + lint**

```bash
python -m pytest tests/unit/utils/ -q
flake8 src/qwenpaw/utils/import_prefetch.py src/qwenpaw/cli/main.py
pre-commit run black --files src/qwenpaw/ tests/
```
Expected: 全 PASS / 无错。

- [ ] **Step 2: 构建脚本 sanity（compileall 参数正确）**

```bash
grep -n "invalidation-mode" scripts/pack/build_*.sh scripts/pack/build_win*.ps1
```
Expected: 4 处 `--invalidation-mode checked-hash`。

- [ ] **Step 3: 实测门槛（用户在 USB/exFAT 真机执行）**

记录改造前后冷启基线（`python -X importtime -m qwenpaw app 2>importtime.log` 的累计 import 墙钟；或冷启到 HTTP ready 的秒数）。若 §3.6.2 预读反噬（预读线程抢 USB 队列使 import 变慢），设 `QWENPAW_PERF_IMPORT_PREFETCH=0` 关闭——§3.6.1 哈希 pyc 的确定性收益仍保留。

- [ ] **Step 4: 最终 code review（整个 Phase 5）**

base = Phase 4 head，head = 本 Phase 最终提交。确认：4 个 compileall 都带 `--invalidation-mode checked-hash`；import_prefetch 模块 best-effort（吞异常、daemon、不阻塞）；cli() 接入正确且开关默认开；无回归。

---

## Phase 5 完成准则

- 4 个构建脚本的 `compileall` 都用 `--invalidation-mode checked-hash`（.pyc flags=2 验证通过）——消除 exFAT mtime 漂移导致的 import 期 .pyc 重写。
- `utils/import_prefetch.py` 提供 best-effort 顺序预读；`cli()` 入口开关 `QWENPAW_PERF_IMPORT_PREFETCH`（默认开）启动。
- 单测覆盖（读文件、缺文件吞异常、cap 截断）；utils 回归全绿。
- 用户在 USB 真机实测 importtime/冷启；若反噬可关 §3.6.2 保留 §3.6.1。

后续剩余（§3.2 迁移戳 / §3.4 restore 精简 / §3.5 Workspace 去重 / §4.1 模型客户端缓存 / §4.3 chats 缓存 / §4.5 收尾 / §5.3 日志 / §5.4 secret_store）各出独立计划。

---

## 风险点

1. **§3.6.2 预读反噬**：单 USB 队列下，预读线程与 import 主线程竞争带宽可能使 import 变慢。缓解：开关默认开但可 `QWENPAW_PERF_IMPORT_PREFETCH=0` 关；cap 截断；Task 3 实测门槛。即便反噬关掉，§3.6.1 的确定性收益不受影响。
2. **import_order_file 缺失**：首启无 `-X importtime` 清单 → 退化为 site-packages + qwenpaw 包的 `rglob` 目录序扫描（仍是随机→顺序的收益，但读的可能比实际 import 的多，浪费带宽+RAM）。cap 截断缓解。
3. **`_build_file_list` 的 `rglob` 范围**：对巨大 site-packages 全扫可能慢。cap_mb 截断读取量（不截断扫描量）；若扫描本身慢，可后续优化（按 importtime 清单精确化）。
4. **cli() 接入位置**：必须在 LazyGroup 派发子命令之前（即 `import qwenpaw.app._app` 之前）才对当前进程的 import 链有预热意义。实现时读 cli() 确认位置。
5. **构建脚本改动无法在 CI 外验证打包效果**：Step 5 只验证 compileall 参数产出的 .pyc 为哈希校验；真实便携包的 exFAT 行为需用户在 Windows 实测。

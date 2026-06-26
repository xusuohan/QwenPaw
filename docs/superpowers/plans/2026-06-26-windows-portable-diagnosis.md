# Windows Portable Diagnosis & Platform Adaptation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Diagnose Windows portable build, fix platform adaptation gaps, and update the progress baseline.

**Architecture:** Four-phase approach: (1) fix pre-identified code issues in `build_win_portable.ps1` (cache chain + strip logic), (2) run local Windows build + smoke test to collect real diagnostic data, (3) address any failures found in phase 2, (4) update `PACK_PROGRESS_BASELINE.md` with Phase 4 results.

**Tech Stack:** PowerShell 5.1+, Python 3.10, conda-pack, pytest, GitHub Actions

## Global Constraints

- USB portable (exFAT): no UNIX permissions, no journaling, high random IO latency
- Single instance, in-process concurrency only
- Minor behavior changes acceptable (strip, compress levels)
- Don't touch host disk — all trimming at build time
- Phased approach: macOS verified first, now extending to Windows
- `build_portable.sh` is the reference for cleanup/cache logic — `build_win_portable.ps1` must match its behavior

---

### Task 1: Fix cleanup logic to preserve wheel files (cache chain)

**Files:**
- Modify: `scripts/pack/build_win_portable.ps1:516-532`

**Interfaces:**
- Produces: Cleanup that preserves `qwenpaw-*.whl`, `qwenpaw-*.tar.gz`, `build_profiling.json`, `build_common_profiling.json`
- Matches: `build_portable.sh:92-97` behavior

- [ ] **Step 1: Read the current cleanup section**

Read `scripts/pack/build_win_portable.ps1` lines 516-532. The current logic:

```powershell
# Lines 521-524: deletes everything except QwenPaw-Portable_* directories
Get-ChildItem -Path $Dist -ErrorAction SilentlyContinue | Where-Object {
  $_.Name -notmatch '^QwenPaw-Portable_'
} | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
```

This deletes wheel files, breaking cache chain for next build.

- [ ] **Step 2: Update cleanup filter to preserve cache files**

Replace lines 521-524 with:

```powershell
if (Test-Path $Dist) {
  Get-ChildItem -Path $Dist -ErrorAction SilentlyContinue | Where-Object {
    $_.Name -notmatch '^QwenPaw-Portable_' -and
    $_.Name -notmatch '^qwenpaw-.*\.whl$' -and
    $_.Name -notmatch '^qwenpaw-.*\.tar\.gz$' -and
    $_.Name -ne 'build_profiling.json' -and
    $_.Name -ne 'build_common_profiling.json'
  } | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
}
```

- [ ] **Step 3: Verify the change**

Read the modified file and confirm the filter now preserves:
- `QwenPaw-Portable_*` directories (existing)
- `qwenpaw-*.whl` wheel files (new)
- `qwenpaw-*.tar.gz` sdist files (new)
- `build_profiling.json` profiling output (new)
- `build_common_profiling.json` profiling output (new)

- [ ] **Step 4: Commit**

```bash
git add scripts/pack/build_win_portable.ps1
git commit -m "fix(pack): preserve wheel and profiling files in Windows cleanup"
```

---

### Task 2: Add optional strip logic for Windows DLLs

**Files:**
- Modify: `scripts/pack/build_win_portable.ps1` (insert after compileall block ~L243, before icon copy ~L246)

**Interfaces:**
- Produces: `editbin /RELEASE` stripping of `.dll` and `.pyd` files if MSVC toolchain available
- Falls back gracefully if `editbin` not in PATH
- Matches: `build_macos.sh:78-84` strip behavior (adapted for Windows)

- [ ] **Step 1: Read the insertion point**

Read `scripts/pack/build_win_portable.ps1` lines 240-250. Insert new block after `Stop-ProfilStage "compileall"` (line 243) and before `# --- Copy icon ---` (line 246).

- [ ] **Step 2: Add the strip block**

Insert after line 243:

```powershell
# --- Strip debug symbols from DLLs (optional, requires MSVC toolchain) ---
Start-ProfilStage "strip"
Write-Host "== Stripping debug symbols from DLLs =="
$_editbin = Get-Command editbin -ErrorAction SilentlyContinue
if ($_editbin) {
  $stripStart = Get-Date
  $stripCount = 0
  $stripSaved = 0
  Get-ChildItem -Path $EnvDir -Recurse -Include "*.dll","*.pyd" -ErrorAction SilentlyContinue | ForEach-Object {
    $origSize = $_.Length
    & editbin /RELEASE $_.FullName 2>$null
    if ($LASTEXITCODE -eq 0) {
      $stripCount++
      $stripSaved += ($origSize - $_.Length)
    }
    $global:LASTEXITCODE = 0
  }
  $stripEnd = Get-Date
  $stripTime = ($stripEnd - $stripStart).TotalSeconds
  $savedMB = [math]::Round($stripSaved / 1MB, 1)
  Write-Host "[build_win_portable] Stripped $stripCount files, saved ${savedMB}MB in $([math]::Round($stripTime, 1))s"
} else {
  Write-Host "[build_win_portable] editbin not found — skipping DLL stripping (Windows DLLs are typically pre-stripped by MSVC)" -ForegroundColor Yellow
}
Stop-ProfilStage "strip"
```

- [ ] **Step 3: Verify the change**

Read the modified file and confirm:
- Strip block is between `compileall` and `copy icon` sections
- `editbin` availability check with graceful fallback
- Profiling stages `strip` wraps the operation
- File size reporting in MB

- [ ] **Step 4: Commit**

```bash
git add scripts/pack/build_win_portable.ps1
git commit -m "feat(pack): add optional DLL stripping for Windows builds"
```

---

### Task 3: Run local Windows build and smoke test

**Files:**
- Read: `dist/build_profiling.json` (after build)
- Read: `dist/diagnostics/*` (if smoke test fails)

**Interfaces:**
- Consumes: `scripts/pack/build_win_portable.ps1` (modified in Tasks 1-2)
- Consumes: `scripts/smoke-test.py`
- Produces: Build output in `dist/QwenPaw-Portable_<timestamp>/`
- Produces: `dist/build_profiling.json` with stage timings
- Produces: `dist/diagnostics/` on smoke test failure

- [ ] **Step 1: Check build prerequisites**

Run these commands to verify the build environment:

```bash
conda --version
node --version
npm --version
python --version
```

Expected: conda 4.x+, node 18+, npm 9+, python 3.10+

- [ ] **Step 2: Run the Windows portable build**

```bash
cd /c/Users/king/Desktop/Project/QwenPaw
powershell -File scripts/pack/build_win_portable.ps1
```

Expected: Build completes, creates `dist/QwenPaw-Portable_<timestamp>/windows/` directory.
Timeout: 30 minutes for first build, 5 minutes for cache hit.

- [ ] **Step 3: Run the smoke test**

```bash
python scripts/smoke-test.py --verbose
```

Expected output on success:
```
Platform: windows
Python:   dist/QwenPaw-Portable_<timestamp>/windows/env/python.exe
Version:  X.Y.Z
PASS: All smoke test checks passed
```

Expected output on failure:
```
FAIL: <failing check>
Diagnostics saved to dist/diagnostics
```

- [ ] **Step 4: Collect profiling data**

```bash
cat dist/build_profiling.json
```

Verify all stages have valid timing data: `wheel_build`, `conda_pack_env`, `unpack`, `compileall`, `strip`, `platform_pack`.

- [ ] **Step 5: If smoke test failed, collect diagnostics**

```bash
ls dist/diagnostics/
cat dist/diagnostics/python_version.txt
cat dist/diagnostics/pip_list.txt
cat dist/diagnostics/path.txt
cat dist/diagnostics/smoke_test_output.txt
```

Record the failure mode and root cause for Task 4.

---

### Task 4: Fix smoke test failures (conditional)

**Files:**
- Modify: `scripts/pack/build_win_portable.ps1` (if build-stage issue)
- Modify: `scripts/smoke-test.py` (if test-stage issue)

**Interfaces:**
- Consumes: Diagnostic output from Task 3
- Produces: Fixed script(s) that pass smoke test

> This task is conditional — only execute if Task 3 revealed failures. If smoke test passed in Task 3, skip to Task 5.

- [ ] **Step 1: Analyze the failure root cause**

Based on Task 3 diagnostics, determine which failure scenario occurred:

| Symptom | Root cause | Fix direction |
|---------|------------|---------------|
| `ImportError: DLL load failed` | Missing VC++ Runtime DLLs | Add VC++ redist detection or copy runtime DLLs |
| `ModuleNotFoundError: No module named 'qwenpaw'` | site-packages not on sys.path | Check PYTHONPATH or conda-unpack |
| `FileNotFoundError` with long paths | MAX_PATH 260 limit | Enable long paths or shorten directory |
| `__version__` empty/wrong | Package metadata issue | Check pip install output |
| Timeout (30s) | Python hang or conda-unpack blocking | Check process tree |

- [ ] **Step 2: Implement the fix**

Apply the appropriate fix based on root cause. Examples:

**If DLL issue:** Add to `build_win_portable.ps1` before smoke test:
```powershell
# Check for VC++ Runtime
$_vcruntime = Join-Path $EnvDir "vcruntime140.dll"
if (-not (Test-Path $_vcruntime)) {
  Write-Host "[build_win_portable] WARN: vcruntime140.dll not found in env" -ForegroundColor Yellow
}
```

**If import path issue:** Add to `smoke-test.py` `run_check`:
```python
env["PYTHONPATH"] = str(portable_dir / platform / "env" / "Lib" / "site-packages")
```

**If long path issue:** Add to `build_win_portable.ps1` at start:
```powershell
# Enable long paths on Windows 10 1607+
try {
  $regPath = "HKLM:\SYSTEM\CurrentControlSet\Control\FileSystem"
  $longPaths = Get-ItemPropertyValue -Path $regPath -Name "LongPathsEnabled" -ErrorAction SilentlyContinue
  if ($longPaths -ne 1) {
    Write-Host "[build_win_portable] WARN: LongPathsEnabled is not set. Some operations may fail with long paths." -ForegroundColor Yellow
  }
} catch {}
```

- [ ] **Step 3: Re-run build and smoke test to verify fix**

```bash
powershell -File scripts/pack/build_win_portable.ps1
python scripts/smoke-test.py --verbose
```

Expected: `PASS: All smoke test checks passed`

- [ ] **Step 4: Commit the fix**

```bash
git add <modified files>
git commit -m "fix(pack): <description of the fix>"
```

---

### Task 5: Update PACK_PROGRESS_BASELINE.md

**Files:**
- Modify: `docs/PACK_PROGRESS_BASELINE.md`

**Interfaces:**
- Consumes: Results from Tasks 1-4 (profiling data, smoke test result, any fixes applied)
- Produces: Updated baseline document reflecting Phase 4 completion

- [ ] **Step 1: Read the current baseline**

Read `docs/PACK_PROGRESS_BASELINE.md` sections 0, 4, 7, and 9.

- [ ] **Step 2: Update section 0 (one-line status)**

Change the status line to include Windows smoke test result. Example:

```
**当前状态**：分支 `feature/usb-portable`。Phase 1-4 已实现。Windows 便携版 smoke test 通过。
```

- [ ] **Step 3: Add Phase 4 to section 4**

Add a new Phase 4 section after Phase 3:

```markdown
### Phase 4 — Windows 平台适配（`<first-commit>` → `<last-commit>`）

| Commit     | 内容                                                       |
| ---------- | -------------------------------------------------------- |
| `<sha>`    | fix(pack): preserve wheel and profiling files in Windows cleanup |
| `<sha>`    | feat(pack): add optional DLL stripping for Windows builds       |
| `<sha>`    | fix(pack): <any smoke test diagnosis fixes>                      |

**Windows 构建验证**：
- smoke test：PASS
- 构建 profiling：<paste key metrics>
- DLL strip 节省：<X> MB（如 editbin 可用）
```

- [ ] **Step 4: Update section 7 (remaining tasks)**

Mark task 2 as complete:

```markdown
| # | 内容                                   | 风险 | 建议优先级             |
| - | ------------------------------------ | -- | ----------------- |
| 1 | macOS x86_64 本机构建验证                 | 低  | 用户侧验证             |
| ~~2~~ | ~~Windows 便携版 smoke test 诊断 + 平台适配~~ | ~~中~~ | ~~已完成 (Phase 4)~~ |
| 3 | conda-pack compress-level 调优（1 vs 4） | 低  | 可选                |
| 4 | whisper 支持（需解决 llvmlite 编译）          | 高  | 需 LLVM 或预编译 wheel |
```

- [ ] **Step 5: Update section 9 (known pitfalls) if needed**

If any new pitfalls were discovered during diagnosis, add them. Example format:

```markdown
7. **<pitfall title>**：<description>. <workaround>.
```

- [ ] **Step 6: Commit**

```bash
git add docs/PACK_PROGRESS_BASELINE.md
git commit -m "docs: update baseline with Phase 4 Windows platform adaptation"
```

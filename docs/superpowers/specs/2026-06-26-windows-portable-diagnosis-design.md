# Windows Portable Diagnosis & Platform Adaptation Design

Date: 2026-06-26
Branch: feature/usb-portable

## Context

Phase 3 (smoke test script + CI workflow) is complete. The baseline doc (PACK_PROGRESS_BASELINE.md) lists "Windows smoke test diagnosis + platform adaptation" as remaining task #2. This spec covers:

1. Code review of Windows build pipeline against 6 platform adaptation checkpoints
2. Local Windows build + smoke test execution + diagnosis
3. Fixes for identified issues
4. Baseline document update

Prerequisites verified: macOS Intel packaging (cache chain, optional-dependencies, strip optimization) all working.

## Phase 1: Code Review — 6 Checkpoints

### Checkpoint 1: Cache chain (wheel retention)

**Current state** (`build_win_portable.ps1:521-525`): Cleanup deletes all non-`QwenPaw-Portable_*` content including wheel files. This breaks the cache chain — next build cannot skip wheel construction.

**Fix**: Preserve `qwenpaw-*.whl`, `qwenpaw-*.tar.gz`, `build_profiling.json` during cleanup. Match `build_portable.sh` logic.

### Checkpoint 2: optional-dependencies `[full]` extra

**Current state**: `build_common.py` uses `pip install qwenpaw[full]`. Windows conda env has pre-compiled wheels for `onnxruntime`, `playwright`, etc.

**Expected**: No issue. Verify during build.

### Checkpoint 3: strip optimization adaptation

**Current state**: macOS/Linux `build_portable.sh` runs `strip` on shared libraries (-70MB). `build_win_portable.ps1` has no equivalent.

**Fix**: Add optional strip logic using MSVC `editbin /RELEASE` if available. Fall back gracefully — Windows `.pyd` files are typically pre-stripped by MSVC.

### Checkpoint 4: profiling integration

**Current state**: Profiling fully integrated (L63, L94, L472). Uses `time.time()` which works cross-platform.

**Expected**: No issue. Verify `build_profiling.json` output after build.

### Checkpoint 5: conda-unpack workaround

**Current state**: Skipped entirely (L201-204). Python's `site.py` dynamically resolves `sys.prefix` at runtime.

**Expected**: No issue. Verify via smoke test import checks.

### Checkpoint 6: Long path handling

**Current state**: 7-Zip (native long path support) preferred. Python fallback uses `\\?\` prefix. `robocopy /MOVE` for nested directory flattening.

**Expected**: No issue on Windows 10+.

## Phase 2: Local Build + Smoke Test

1. Run `powershell -File scripts/pack/build_win_portable.ps1`
2. Run `python scripts/smoke-test.py --verbose`
3. On failure: collect `dist/diagnostics/` (python_version.txt, pip_list.txt, path.txt, smoke_test_output.txt)
4. Review `dist/build_profiling.json`

**Expected failure scenarios**:

| Scenario | Likely root cause | Diagnostic clue |
|----------|-------------------|-----------------|
| `import qwenpaw` fails | conda-pack hardcoded paths, site-packages not found | pip_list.txt, path.txt |
| DLL load failure | Missing VC++ Runtime | stderr `ImportError: DLL load failed` |
| `__version__` empty | Package not installed or `__version__.py` missing | pip_list.txt qwenpaw version |
| Timeout | conda-unpack path issue | smoke_test_output.txt |
| Long path error | MAX_PATH limit | stderr `FileNotFoundError` |

## Phase 3: Fixes

### Fix A: Cleanup preserves wheel files

**File**: `scripts/pack/build_win_portable.ps1:521-525`

Change cleanup filter to also preserve:
- `qwenpaw-*.whl`
- `qwenpaw-*.tar.gz`
- `build_profiling.json`
- `build_common_profiling.json`

### Fix B: Optional strip logic

**File**: `scripts/pack/build_win_portable.ps1` (insert after compileall, before icon copy)

```
1. Check if editbin is available (MSVC toolchain)
2. If yes: iterate all .dll and .pyd in env/, run editbin /RELEASE
3. If no: print "Windows DLLs are typically pre-stripped by MSVC, skipping"
4. Report bytes saved
```

### Fix C: Smoke test diagnosis fixes

Address root causes found in Phase 2. Possible directions:
- Import path issues → modify smoke-test.py PATH construction or PYTHONPATH
- DLL issues → add VC++ Runtime redist detection in build_win_portable.ps1
- Long path issues → verify LongPathsEnabled or adjust directory structure

## Phase 4: Baseline Update

Update `PACK_PROGRESS_BASELINE.md`:
- Section 0: Add Windows portable smoke test pass conclusion
- Section 4: Add Phase 4 record (Windows platform adaptation)
- Section 7: Mark task 2 complete, keep tasks 3-4
- Section 9: Add new known pitfalls if discovered

## Commit Strategy

- Phase 1 fixes: `fix(pack): ...` (one commit per fix)
- Phase 3 diagnosis fixes: `fix(pack): ...` (as needed)
- Baseline update: `docs: ...` (single commit)

## Files Changed

| File | Action | Phase |
|------|--------|-------|
| `scripts/pack/build_win_portable.ps1` | Edit (cleanup logic, strip logic) | 1, 3 |
| `scripts/smoke-test.py` | Edit (if diagnosis reveals issues) | 3 |
| `PACK_PROGRESS_BASELINE.md` | Edit | 4 |

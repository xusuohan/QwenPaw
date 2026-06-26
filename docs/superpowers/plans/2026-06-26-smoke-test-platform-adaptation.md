# Smoke Test Platform Adaptation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Create standalone cross-platform smoke test script, pytest fixture, and CI workflow for portable package verification.

**Architecture:** One Python script (`scripts/smoke-test.py`) auto-detects platform from directory structure, runs import checks, dumps diagnostics on failure. A `dist_dir` pytest fixture enables future test integration. A matrix CI workflow builds and smoke-tests on all 3 platforms.

**Tech Stack:** Python 3.10+, pytest, GitHub Actions, PowerShell (Windows), Bash (macOS/Linux)

## Global Constraints

- No pnpm, no Python venv, no npm scripts — packaging uses conda-pack + shx only
- `smoke-test.py` must be self-contained — no pytest or third-party dependencies
- Portable Python isolated via `PYTHONNOUSERSITE=1` + PATH prepend
- Shared `data/` dir must be cross-platform compatible

---

### Task 1: Create `scripts/smoke-test.py`

**Files:**
- Create: `scripts/smoke-test.py`

**Interfaces:**
- Produces: `smoke_test(portable_dir: Path) -> bool` — callable by future CI/test integration
- Produces: CLI entry point with `--dist-dir`, `--portable-dir`, `--verbose` args
- Produces: `dist/diagnostics/` directory on failure with 4 diagnostic files

- [ ] **Step 1: Create the smoke test script**

Create `scripts/smoke-test.py` with the following content:

```python
#!/usr/bin/env python3
"""Standalone portable package smoke test. No third-party dependencies."""
import argparse
import re
import subprocess
import sys
from pathlib import Path

PLATFORM_DEFS = [
    ("windows/env/python.exe", "windows"),
    ("macOS/env/bin/python3", "macOS"),
    ("linux/env/bin/python3", "linux"),
]

IMPORT_CHECKS = [
    "import qwenpaw",
    "from qwenpaw.__version__ import __version__",
    "import qwenpaw.desktop",
    "import qwenpaw.config",
]

SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+")


def find_latest_portable(dist_dir: Path) -> Path | None:
    candidates = sorted(dist_dir.glob("QwenPaw-Portable_*"), reverse=True)
    return candidates[0] if candidates else None


def detect_python(portable_dir: Path) -> tuple[Path, str] | None:
    for rel, platform in PLATFORM_DEFS:
        p = portable_dir / rel
        if p.exists():
            return p, platform
    return None


def run_check(python: Path, code: str) -> tuple[bool, str]:
    try:
        result = subprocess.run(
            [str(python), "-c", code],
            capture_output=True, text=True, timeout=30,
        )
        return result.returncode == 0, result.stdout.strip() + result.stderr.strip()
    except Exception as e:
        return False, str(e)


def collect_diagnostics(python: Path, output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    for label, code in [
        ("python_version", "import sys; print(sys.version)"),
        ("pip_list", "import subprocess; subprocess.run([sys.executable, '-m', 'pip', 'list'])"),
    ]:
        ok, out = run_check(python, code)
        (output_dir / f"{label}.txt").write_text(out or "(no output)", encoding="utf-8")
    (output_dir / "path.txt").write_text(str(Path.home()) + "\n" + str(python), encoding="utf-8")


def smoke_test(portable_dir: Path, verbose: bool = False) -> bool:
    detected = detect_python(portable_dir)
    if not detected:
        print(f"FAIL: No python executable found in {portable_dir}")
        return False
    python, platform = detected
    print(f"Platform: {platform}")
    print(f"Python:   {python}")

    for check in IMPORT_CHECKS:
        ok, out = run_check(python, check)
        if not ok:
            print(f"FAIL: {check}")
            if verbose:
                print(f"  {out}")
            diag_dir = portable_dir.parent / "diagnostics"
            collect_diagnostics(python, diag_dir)
            (diag_dir / "smoke_test_output.txt").write_text(
                f"Failed: {check}\n{out}", encoding="utf-8"
            )
            print(f"Diagnostics saved to {diag_dir}")
            return False

    ok, version = run_check(python, "from qwenpaw.__version__ import __version__; print(__version__)")
    if not ok or not SEMVER_RE.match(version):
        print(f"FAIL: Invalid version '{version}'")
        return False

    print(f"Version:  {version}")
    print("PASS: All smoke test checks passed")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description="Portable package smoke test")
    parser.add_argument("--dist-dir", type=Path, default=Path("dist"))
    parser.add_argument("--portable-dir", type=Path, default=None)
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    if args.portable_dir:
        portable = args.portable_dir
    else:
        portable = find_latest_portable(args.dist_dir)
        if not portable:
            print(f"FAIL: No QwenPaw-Portable_* found in {args.dist_dir}")
            return 1

    print(f"Testing:  {portable}")
    return 0 if smoke_test(portable, verbose=args.verbose) else 1


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: Verify script runs without errors (no built package)**

Run: `python scripts/smoke-test.py --dist-dir dist`
Expected: `FAIL: No QwenPaw-Portable_* found in dist` or `FAIL: No python executable found` (depending on whether dist/ has portable dirs). Exit code 1.

- [ ] **Step 3: Verify script works against a real portable build (if available)**

Run: `python scripts/smoke-test.py --portable-dir dist/QwenPaw-Portable_20260618_142800`
Expected: `PASS: All smoke test checks passed` with version `1.1.6`. Exit code 0.

- [ ] **Step 4: Verify --verbose flag**

Run: `python scripts/smoke-test.py --portable-dir /nonexistent --verbose`
Expected: `FAIL: No python executable found in /nonexistent`. Exit code 1.

- [ ] **Step 5: Commit**

```bash
git add scripts/smoke-test.py
git commit -m "feat(pack): add standalone cross-platform smoke test script"
```

---

### Task 2: Add `dist_dir` fixture to `tests/conftest.py`

**Files:**
- Modify: `tests/conftest.py:36-38` (after "Directory Fixtures" header)

**Interfaces:**
- Produces: `dist_dir` pytest fixture → `Path` (latest `QwenPaw-Portable_*` directory)

- [ ] **Step 1: Add the fixture**

Insert the following fixture after the `# Directory Fixtures` section header (after line 38, before `temp_workspace`):

```python
@pytest.fixture
def dist_dir() -> Path:
    """Path to the latest QwenPaw-Portable_* directory in dist/.

    Skips the test if no portable build is available.
    """
    dist = Path("dist")
    candidates = sorted(dist.glob("QwenPaw-Portable_*"), reverse=True)
    if not candidates:
        pytest.skip("No QwenPaw-Portable_* found in dist/")
    return candidates[0]
```

- [ ] **Step 2: Verify fixture loads without errors**

Run: `python -m pytest tests/conftest.py --collect-only 2>&1 | head -5`
Expected: No import errors. Collection succeeds.

- [ ] **Step 3: Commit**

```bash
git add tests/conftest.py
git commit -m "test(pack): add dist_dir fixture for portable package tests"
```

---

### Task 3: Create `.github/workflows/packaging-ci.yml`

**Files:**
- Create: `.github/workflows/packaging-ci.yml`

**Interfaces:**
- Produces: CI workflow triggered on `workflow_dispatch` + `push` to `feature/*`
- Consumes: `scripts/smoke-test.py` (Task 1), `scripts/pack/build_win_portable.ps1`, `scripts/pack/build_portable.sh`

- [ ] **Step 1: Create the CI workflow**

Create `.github/workflows/packaging-ci.yml`:

```yaml
name: Packaging CI

on:
  push:
    branches: [feature/*]
  workflow_dispatch:

concurrency:
  group: ${{ github.workflow }}-${{ github.ref }}
  cancel-in-progress: true

jobs:
  build-and-test:
    name: Build & Smoke Test (${{ matrix.platform }})
    runs-on: ${{ matrix.os }}
    timeout-minutes: 45
    strategy:
      fail-fast: false
      matrix:
        include:
          - os: windows-latest
            platform: windows
          - os: macos-latest
            platform: macos
          - os: ubuntu-latest
            platform: linux

    steps:
      - name: Checkout repository
        uses: actions/checkout@v4

      - name: Set up Miniconda
        uses: conda-incubator/setup-miniconda@v3
        with:
          miniconda-version: "latest"
          activate-environment: ""
          auto-activate-base: false

      - name: Set up Node.js
        uses: actions/setup-node@v4
        with:
          node-version: '20'
          cache: 'npm'
          cache-dependency-path: console/package-lock.json

      - name: Install npm dependencies
        run: cd console && npm ci

      - name: Build portable (Windows)
        if: matrix.platform == 'windows'
        run: |
          conda activate base
          .\scripts\pack\build_win_portable.ps1
        env:
          DIST: dist
        shell: powershell

      - name: Build portable (macOS/Linux)
        if: matrix.platform != 'windows'
        run: |
          source "$CONDA/bin/activate"
          bash ./scripts/pack/build_portable.sh
        env:
          DIST: dist

      - name: Run smoke test
        run: python scripts/smoke-test.py --verbose
        shell: bash

      - name: Upload diagnostics (on failure)
        if: failure()
        uses: actions/upload-artifact@v4
        with:
          name: diagnostics-${{ matrix.platform }}
          path: dist/diagnostics/
          retention-days: 7

      - name: Upload portable artifact
        uses: actions/upload-artifact@v4
        with:
          name: QwenPaw-Portable-${{ matrix.platform }}
          path: dist/QwenPaw-Portable_*/
          retention-days: 7
```

- [ ] **Step 2: Validate YAML syntax**

Run: `python -c "import yaml; yaml.safe_load(open('.github/workflows/packaging-ci.yml'))"`
Expected: No error output.

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/packaging-ci.yml
git commit -m "ci(pack): add packaging CI workflow with smoke test matrix"
```

---

### Task 4: Update `build_win_portable.ps1` to use `smoke-test.py`

**Files:**
- Modify: `scripts/pack/build_win_portable.ps1:474-498`

**Interfaces:**
- Consumes: `scripts/smoke-test.py` (Task 1)
- Removes: inline smoke test logic (replaced by script call)

- [ ] **Step 1: Replace inline smoke test with script call**

Replace lines 474-498 in `build_win_portable.ps1` (the `# --- Smoke test ---` section) with:

```powershell
# --- Smoke test ---
Write-Host "== Running smoke test =="
$smokeStart = Get-Date
$smokeScript = Join-Path $RepoRoot "scripts\smoke-test.py"
if (Test-Path $smokeScript) {
  & $PythonExePath $smokeScript --portable-dir $PortableRoot --verbose
  if ($LASTEXITCODE -ne 0) {
    Write-Host "[build_win_portable] Smoke test FAILED" -ForegroundColor Red
  }
} else {
  Write-Host "[build_win_portable] WARN: smoke-test.py not found, running inline check" -ForegroundColor Yellow
  $smokeOut = & $PythonExePath -c "from qwenpaw.__version__ import __version__; print(__version__)" 2>&1
  if ($LASTEXITCODE -eq 0) {
    Write-Host "[build_win_portable] Smoke test PASSED: $smokeOut" -ForegroundColor Green
  } else {
    Write-Host "[build_win_portable] Smoke test FAILED (exit code $LASTEXITCODE)" -ForegroundColor Red
  }
}
$smokeEnd = Get-Date
Write-Host "[build_win_portable] Smoke test took $([math]::Round(($smokeEnd - $smokeStart).TotalSeconds, 1))s"
```

- [ ] **Step 2: Commit**

```bash
git add scripts/pack/build_win_portable.ps1
git commit -m "refactor(pack): use smoke-test.py in build_win_portable.ps1"
```

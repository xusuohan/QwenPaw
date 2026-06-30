# Smoke Test Platform Adaptation Design

Date: 2026-06-26
Branch: feature/usb-portable

## Context

Phase 2 of the packaging optimization project lists 3 deliverables that don't yet exist:
- `scripts/smoke-test.py` — standalone smoke test script
- `tests/conftest.py` `dist_dir` fixture
- `.github/workflows/packaging-ci.yml` — packaging CI workflow

The smoke test currently exists only as inline PowerShell in `build_win_portable.ps1` (lines 474-498). Task 4 (Windows smoke test diagnostic + platform adaptation) depends on these deliverables.

## Design

### 1. `scripts/smoke-test.py`

Standalone cross-platform smoke test. No pytest dependency.

**Behavior:**
1. Scan `dist/` for latest `QwenPaw-Portable_*` directory (sorted by name, newest first)
2. Detect platform from directory structure:
   - `windows/env/python.exe` → Windows
   - `macOS/env/bin/python3` → macOS
   - `linux/env/bin/python3` → Linux
3. Run verification checks:
   - Python executable exists and runs
   - `import qwenpaw` succeeds
   - `qwenpaw.__version__` returns a valid semver string
   - Key submodules importable (`qwenpaw.desktop`, `qwenpaw.config`)
4. On failure: dump diagnostics to `dist/diagnostics/`:
   - `python_version.txt` — `sys.version`
   - `pip_list.txt` — `pip list` output
   - `path.txt` — PATH environment variable
   - `smoke_test_output.txt` — full error traceback
5. Exit 0 on pass, 1 on fail

**CLI args:**
- `--dist-dir` — override dist path (default: `dist`)
- `--portable-dir` — override specific portable dir (skip auto-detect)
- `--verbose` — show full traceback on failure

**~120 lines.**

### 2. `tests/conftest.py` fixture

Add one fixture to existing `tests/conftest.py`:

```python
@pytest.fixture
def dist_dir() -> Path:
    """Path to the latest QwenPaw-Portable_* directory in dist/."""
    dist = Path("dist")
    candidates = sorted(dist.glob("QwenPaw-Portable_*"), reverse=True)
    if not candidates:
        pytest.skip("No QwenPaw-Portable_* found in dist/")
    return candidates[0]
```

### 3. `.github/workflows/packaging-ci.yml`

Matrix strategy across 3 platforms:

```yaml
jobs:
  build-and-test:
    strategy:
      matrix:
        include:
          - os: windows-latest
            platform: windows
          - os: macos-latest
            platform: macos
          - os: ubuntu-latest
            platform: linux
    runs-on: ${{ matrix.os }}
    steps:
      - checkout
      - setup miniconda
      - setup node.js 20
      - npm ci (console/)
      - build portable (platform-specific script)
      - run smoke test: python scripts/smoke-test.py
      - upload artifact (7-day retention)
      - upload diagnostics on failure
```

**Triggers:** `workflow_dispatch` (manual) + `push` to `feature/*` branches.

**Platform-specific build steps:**
- Windows: `build_win_portable.ps1` with `shell: powershell`
- macOS/Linux: `build_portable.sh`

## Files Changed

| File | Action |
|------|--------|
| `scripts/smoke-test.py` | Create |
| `tests/conftest.py` | Edit (add fixture) |
| `.github/workflows/packaging-ci.yml` | Create |

## Constraints

- No pnpm, no Python venv, no npm scripts
- smoke-test.py must be self-contained (no pytest dependency)
- Portable Python isolated via `PYTHONNOUSERSITE=1` + PATH prepend
- Shared `data/` dir must be cross-platform compatible

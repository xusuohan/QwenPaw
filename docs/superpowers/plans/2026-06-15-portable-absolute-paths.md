# Portable Absolute Paths Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ensure `config.json` stores correct absolute paths for the current device, with a startup-time rewrite command that fixes stale paths when the USB drive moves between machines.

**Architecture:** A new `qwenpaw fix-paths` CLI command runs on every launcher startup. It reads `config.json` as raw JSON, detects stale absolute paths via WORKING_DIR markers (`workspaces/`, `media/`), and rewrites them to the current device's `WORKING_DIR`. The old in-memory normalization layer (`_normalize_working_dir_bound_paths()`) is removed. New agent profiles and default factories are changed to write absolute paths instead of relative.

**Tech Stack:** Python, Click CLI, Pydantic config models, bash/PowerShell launchers

---

## File Map

| File | Action | Purpose |
|------|--------|---------|
| `src/qwenpaw/config/utils.py` | Modify | Add `rewrite_stale_paths_on_disk()`, remove `_normalize_working_dir_bound_paths()` |
| `src/qwenpaw/cli/fix_paths_cmd.py` | Create | `qwenpaw fix-paths` Click subcommand |
| `src/qwenpaw/cli/main.py:145` | Modify | Register `fix-paths` in `lazy_subcommands` |
| `src/qwenpaw/app/migration.py:200,697,880` | Modify | Use absolute paths for `workspace_dir` |
| `src/qwenpaw/config/config.py:1096` | Modify | Default factory uses absolute path |
| `scripts/pack/build_macos.sh:118` | Modify | Add `qwenpaw fix-paths` call |
| `scripts/pack/build_linux.sh:106` | Modify | Add `qwenpaw fix-paths` call |
| `scripts/pack/build_win_portable.ps1:282` | Modify | Add `qwenpaw fix-paths` call |
| `tests/unit/config/test_rewrite_stale_paths.py` | Create | Unit tests for `rewrite_stale_paths_on_disk()` |

---

### Task 1: Add `rewrite_stale_paths_on_disk()` and remove normalization layer

**Files:**
- Modify: `src/qwenpaw/config/utils.py`
- Test: `tests/unit/config/test_rewrite_stale_paths.py`

- [ ] **Step 1: Write failing tests for `rewrite_stale_paths_on_disk()`**

```python
# tests/unit/config/test_rewrite_stale_paths.py
# -*- coding: utf-8 -*-
"""Tests for rewrite_stale_paths_on_disk()."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from qwenpaw.config.utils import rewrite_stale_paths_on_disk
import qwenpaw.config.utils as utils_module


class TestRewriteStalePathsOnDisk:
    """rewrite_stale_paths_on_disk rewrites stale absolute paths to current WORKING_DIR."""

    def test_no_config_file_returns_false(self, tmp_path: Path) -> None:
        """Returns False when config.json doesn't exist."""
        result = rewrite_stale_paths_on_disk(tmp_path / "config.json")
        assert result is False

    def test_current_paths_unchanged(self, tmp_path: Path) -> None:
        """Paths already matching current WORKING_DIR are not modified."""
        config_path = tmp_path / "config.json"
        config_path.write_text(json.dumps({
            "agents": {
                "profiles": {
                    "default": {
                        "id": "default",
                        "workspace_dir": str(tmp_path / "workspaces" / "default"),
                    }
                }
            }
        }))
        monkeypatch = pytest.MonkeyPatch()
        with monkeypatch.context() as m:
            m.setattr(utils_module, "WORKING_DIR", tmp_path)
            result = rewrite_stale_paths_on_disk(config_path)
        assert result is False
        data = json.loads(config_path.read_text())
        assert data["agents"]["profiles"]["default"]["workspace_dir"] == str(
            tmp_path / "workspaces" / "default"
        )

    def test_stale_path_rewritten(self, tmp_path: Path) -> None:
        """Stale absolute path from another machine is rewritten to current WORKING_DIR."""
        config_path = tmp_path / "config.json"
        config_path.write_text(json.dumps({
            "agents": {
                "profiles": {
                    "default": {
                        "id": "default",
                        "workspace_dir": "/old/machine/data/workspaces/default",
                    }
                }
            }
        }))
        monkeypatch = pytest.MonkeyPatch()
        with monkeypatch.context() as m:
            m.setattr(utils_module, "WORKING_DIR", tmp_path)
            result = rewrite_stale_paths_on_disk(config_path)
        assert result is True
        data = json.loads(config_path.read_text())
        assert data["agents"]["profiles"]["default"]["workspace_dir"] == str(
            tmp_path / "workspaces" / "default"
        )

    def test_stale_media_dir_rewritten(self, tmp_path: Path) -> None:
        """Stale media_dir path is also rewritten."""
        config_path = tmp_path / "config.json"
        config_path.write_text(json.dumps({
            "some_section": {
                "media_dir": "/old/machine/data/media",
            }
        }))
        monkeypatch = pytest.MonkeyPatch()
        with monkeypatch.context() as m:
            m.setattr(utils_module, "WORKING_DIR", tmp_path)
            result = rewrite_stale_paths_on_disk(config_path)
        assert result is True
        data = json.loads(config_path.read_text())
        assert data["some_section"]["media_dir"] == str(tmp_path / "media")

    def test_non_path_strings_untouched(self, tmp_path: Path) -> None:
        """Strings that don't contain WORKING_DIR markers are not modified."""
        config_path = tmp_path / "config.json"
        config_path.write_text(json.dumps({
            "agents": {
                "profiles": {
                    "default": {
                        "id": "default",
                        "workspace_dir": "workspaces/default",
                    }
                }
            }
        }))
        monkeypatch = pytest.MonkeyPatch()
        with monkeypatch.context() as m:
            m.setattr(utils_module, "WORKING_DIR", tmp_path)
            result = rewrite_stale_paths_on_disk(config_path)
        assert result is False
        data = json.loads(config_path.read_text())
        assert data["agents"]["profiles"]["default"]["workspace_dir"] == "workspaces/default"

    def test_windows_backslash_path_rewritten(self, tmp_path: Path) -> None:
        """Windows-style backslash paths are normalized and rewritten."""
        config_path = tmp_path / "config.json"
        config_path.write_text(json.dumps({
            "agents": {
                "profiles": {
                    "default": {
                        "id": "default",
                        "workspace_dir": "C:\\old\\machine\\data\\workspaces\\default",
                    }
                }
            }
        }))
        monkeypatch = pytest.MonkeyPatch()
        with monkeypatch.context() as m:
            m.setattr(utils_module, "WORKING_DIR", tmp_path)
            result = rewrite_stale_paths_on_disk(config_path)
        assert result is True
        data = json.loads(config_path.read_text())
        assert data["agents"]["profiles"]["default"]["workspace_dir"] == str(
            tmp_path / "workspaces" / "default"
        )
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/king/Projects/高乐申辉/龙虾盒子/QwenPaw-aixcore && python -m pytest tests/unit/config/test_rewrite_stale_paths.py -v`
Expected: FAIL with `ImportError: cannot import name 'rewrite_stale_paths_on_disk'`

- [ ] **Step 3: Implement `rewrite_stale_paths_on_disk()` in `config/utils.py`**

Add after `resolve_workspace_path()` (after line 120):

```python
def rewrite_stale_paths_on_disk(config_path: Path | None = None) -> bool:
    """Rewrite stale WORKING_DIR-bound paths in config.json to current paths.

    Detects absolute paths that contain known WORKING_DIR subdirectory markers
    (workspaces/, media/) but have a different prefix (from another machine),
    and rewrites them to the current WORKING_DIR.

    Returns True if any changes were written to disk.
    """
    if config_path is None:
        config_path = get_config_path()
    if not config_path.is_file():
        return False

    with open(config_path, "r", encoding="utf-8") as f:
        raw = json.load(f)

    modified = False
    _WORKING_DIR_MARKERS = ("workspaces", "media")
    new_root = str(WORKING_DIR)

    def _rewrite(v: object) -> object:
        nonlocal modified
        if not isinstance(v, str) or not v or v.startswith(new_root):
            return v
        for marker in _WORKING_DIR_MARKERS:
            for sep in ("/", "\\"):
                needle = sep + marker + sep
                idx = v.rfind(needle)
                if idx >= 0:
                    suffix = v[idx + 1 :].replace("\\", "/")
                    new_val = str(WORKING_DIR / suffix)
                    if new_val != v:
                        modified = True
                        return new_val
                    return v
                needle_end = sep + marker
                if v.endswith(needle_end):
                    new_val = str(WORKING_DIR / marker)
                    if new_val != v:
                        modified = True
                        return new_val
                    return v
        return v

    def _walk(obj: object, key: str | None = None) -> object:
        if isinstance(obj, dict):
            return {k: _walk(v, str(k)) for k, v in obj.items()}
        if isinstance(obj, list):
            return [_walk(x, key) for x in obj]
        if key in {"workspace_dir", "media_dir"}:
            return _rewrite(obj)
        return obj

    fixed = _walk(raw)
    if modified:
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(fixed, f, indent=2, ensure_ascii=False)
    return modified
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/king/Projects/高乐申辉/龙虾盒子/QwenPaw-aixcore && python -m pytest tests/unit/config/test_rewrite_stale_paths.py -v`
Expected: All 6 tests PASS

- [ ] **Step 5: Remove `_normalize_working_dir_bound_paths()` and its call**

In `src/qwenpaw/config/utils.py`:

1. Delete the `_normalize_working_dir_bound_paths()` function (lines 52-108)
2. In `_load_and_validate_config()`, delete line 588: `data = _normalize_working_dir_bound_paths(data)`

- [ ] **Step 6: Run tests again to verify removal doesn't break anything**

Run: `cd /Users/king/Projects/高乐申辉/龙虾盒子/QwenPaw-aixcore && python -m pytest tests/unit/config/ -v`
Expected: All tests PASS (existing `test_resolve_workspace_path.py` + new tests)

- [ ] **Step 7: Commit**

```bash
git add src/qwenpaw/config/utils.py tests/unit/config/test_rewrite_stale_paths.py
git commit -m "feat(config): add rewrite_stale_paths_on_disk() and remove normalization layer

- New function rewrites stale absolute paths in config.json to current WORKING_DIR
- Removes _normalize_working_dir_bound_paths() (in-memory normalization)
- resolve_workspace_path() retained as read-site safety net"
```

---

### Task 2: Create `qwenpaw fix-paths` CLI subcommand

**Files:**
- Create: `src/qwenpaw/cli/fix_paths_cmd.py`
- Modify: `src/qwenpaw/cli/main.py:145`

- [ ] **Step 1: Create `fix_paths_cmd.py`**

```python
# src/qwenpaw/cli/fix_paths_cmd.py
# -*- coding: utf-8 -*-
"""``qwenpaw fix-paths`` — rewrite stale WORKING_DIR-bound paths in config.json."""
from __future__ import annotations

from pathlib import Path

import click


@click.command(
    "fix-paths",
    help="Rewrite stale WORKING_DIR-bound paths in config.json to current paths.",
)
@click.option(
    "--config-path",
    type=click.Path(),
    default=None,
    help="Path to config.json (default: auto-detect from WORKING_DIR).",
)
def fix_paths_cmd(config_path: str | None) -> None:
    """Rewrite stale absolute paths in config.json."""
    from qwenpaw.config.utils import rewrite_stale_paths_on_disk

    path = Path(config_path) if config_path else None
    changed = rewrite_stale_paths_on_disk(path)
    if changed:
        click.echo("已重写陈旧路径。")
```

- [ ] **Step 2: Register in `cli/main.py`**

Add to `lazy_subcommands` dict in `src/qwenpaw/cli/main.py` (after line 145, before the closing `}`):

```python
        "fix-paths": (
            "qwenpaw.cli.fix_paths_cmd",
            "fix_paths_cmd",
            ".fix_paths_cmd",
        ),
```

- [ ] **Step 3: Verify CLI loads without error**

Run: `cd /Users/king/Projects/高乐申辉/龙虾盒子/QwenPaw-aixcore && python -m qwenpaw fix-paths --help`
Expected: Help text displayed with `--config-path` option

- [ ] **Step 4: Commit**

```bash
git add src/qwenpaw/cli/fix_paths_cmd.py src/qwenpaw/cli/main.py
git commit -m "feat(cli): add qwenpaw fix-paths subcommand"
```

---

### Task 3: Update `migration.py` to use absolute paths

**Files:**
- Modify: `src/qwenpaw/app/migration.py:200,697,880`

- [ ] **Step 1: Update `_do_migrate_legacy_workspace()` (line 200)**

In `src/qwenpaw/app/migration.py`, line 200:

```python
# Before:
workspace_dir="workspaces/default",

# After:
workspace_dir=str(WORKING_DIR / "workspaces/default"),
```

Confirm `WORKING_DIR` is imported. Check the imports at the top of the file — it should already be imported from `qwenpaw.constant`. If not, add:
```python
from qwenpaw.constant import WORKING_DIR
```

- [ ] **Step 2: Update `_do_ensure_default_agent()` (line 697)**

Line 697:

```python
# Before:
workspace_dir="workspaces/default",

# After:
workspace_dir=str(WORKING_DIR / "workspaces/default"),
```

- [ ] **Step 3: Update `_do_ensure_qa_agent()` (line 880)**

Line 880:

```python
# Before:
workspace_dir=f"workspaces/{qa_id}",

# After:
workspace_dir=str(WORKING_DIR / f"workspaces/{qa_id}"),
```

- [ ] **Step 4: Verify no import errors**

Run: `cd /Users/king/Projects/高乐申辉/龙虾盒子/QwenPaw-aixcore && python -c "from qwenpaw.app.migration import ensure_default_agent_exists, ensure_qa_agent_exists"`
Expected: No output (import succeeds)

- [ ] **Step 5: Commit**

```bash
git add src/qwenpaw/app/migration.py
git commit -m "feat(migration): use absolute paths for workspace_dir in agent profiles"
```

---

### Task 4: Update `config.py` default factory to use absolute paths

**Files:**
- Modify: `src/qwenpaw/config/config.py:1096`

- [ ] **Step 1: Update `AgentsConfig.profiles` default factory (line 1096)**

```python
# Before:
workspace_dir="workspaces/default",

# After:
workspace_dir=str(WORKING_DIR / "workspaces/default"),
```

Confirm `WORKING_DIR` is imported at the top of `config.py`. If not, add:
```python
from ..constant import WORKING_DIR
```

- [ ] **Step 2: Verify import**

Run: `cd /Users/king/Projects/高乐申辉/龙虾盒子/QwenPaw-aixcore && python -c "from qwenpaw.config.config import Config; c = Config(); print(c.agents.profiles['default'].workspace_dir)"`
Expected: An absolute path string (e.g., `/Users/king/.qwenpaw/workspaces/default`)

- [ ] **Step 3: Commit**

```bash
git add src/qwenpaw/config/config.py
git commit -m "feat(config): default agent profile uses absolute workspace_dir"
```

---

### Task 5: Update launcher scripts

**Files:**
- Modify: `scripts/pack/build_macos.sh:118`
- Modify: `scripts/pack/build_linux.sh:106`
- Modify: `scripts/pack/build_win_portable.ps1:282`

- [ ] **Step 1: Update macOS launcher (`build_macos.sh`)**

After line 118 (`fi` that closes the init check), add:

```bash
# 重写陈旧路径（跨设备迁移时修正 config.json 中的绝对路径）
"$ENV_DIR/bin/python" -u -m qwenpaw fix-paths
```

The section becomes:
```bash
CONFIG_FILE="$QWENPAW_WORKING_DIR/config.json"
if [ ! -f "$CONFIG_FILE" ]; then
  "$ENV_DIR/bin/python" -u -m qwenpaw init --defaults --accept-security
fi
# 重写陈旧路径（跨设备迁移时修正 config.json 中的绝对路径）
"$ENV_DIR/bin/python" -u -m qwenpaw fix-paths
```

- [ ] **Step 2: Update Linux launcher (`build_linux.sh`)**

After line 106 (`fi` that closes the init check), add the same:

```bash
# 重写陈旧路径（跨设备迁移时修正 config.json 中的绝对路径）
"$ENV_DIR/bin/python" -u -m qwenpaw fix-paths
```

- [ ] **Step 3: Update Windows launcher (`build_win_portable.ps1`)**

After line 282 (`)` that closes the init check), add:

```bat
REM Rewrite stale paths from previous device
"%~dp0env\python.exe" -u -m qwenpaw fix-paths
```

- [ ] **Step 4: Commit**

```bash
git add scripts/pack/build_macos.sh scripts/pack/build_linux.sh scripts/pack/build_win_portable.ps1
git commit -m "feat(pack): launchers run qwenpaw fix-paths on every startup"
```

---

### Task 6: Run full test suite and verify

- [ ] **Step 1: Run all config-related tests**

Run: `cd /Users/king/Projects/高乐申辉/龙虾盒子/QwenPaw-aixcore && python -m pytest tests/unit/config/ -v`
Expected: All tests PASS

- [ ] **Step 2: Run full unit test suite**

Run: `cd /Users/king/Projects/高乐申辉/龙虾盒子/QwenPaw-aixcore && python -m pytest tests/unit/ -v --timeout=30`
Expected: All tests PASS

- [ ] **Step 3: Manual smoke test — fix-paths command**

Run: `cd /Users/king/Projects/高乐申辉/龙虾盒子/QwenPaw-aixcore && python -m qwenpaw fix-paths`
Expected: No output (current config.json paths are already correct, so no rewrite needed)

- [ ] **Step 4: Verify config.json still uses absolute paths**

Run: `cd /Users/king/Projects/高乐申辉/龙虾盒子/QwenPaw-aixcore && python -c "import json; d = json.load(open('.qwenpaw_data/config.json')); print(d['agents']['profiles']['default']['workspace_dir'])"`
Expected: An absolute path string (e.g., `/Users/king/.../workspaces/default`)

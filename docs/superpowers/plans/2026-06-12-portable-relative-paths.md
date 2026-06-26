# Portable Relative Paths Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace hardcoded absolute `workspace_dir` paths in `config.json` with relative paths, making portable builds work across devices.

**Architecture:** Add a `resolve_workspace_path()` helper that resolves relative paths against `WORKING_DIR`. Change all write sites to store relative paths and all read sites to use the resolver. Old absolute paths continue to work via the resolver and existing `_normalize_working_dir_bound_paths()`.

**Tech Stack:** Python 3.11, Pydantic, pathlib

---

### Task 1: Add `resolve_workspace_path()` helper and test

**Files:**
- Modify: `src/qwenpaw/config/utils.py:50-52`
- Create: `tests/unit/config/test_resolve_workspace_path.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/config/test_resolve_workspace_path.py`:

```python
"""Tests for resolve_workspace_path()."""
from pathlib import Path

from qwenpaw.config.utils import resolve_workspace_path


def test_relative_path_resolved_against_working_dir(monkeypatch, tmp_path):
    """Relative paths should be resolved against WORKING_DIR."""
    import qwenpaw.config.utils as utils_module

    monkeypatch.setattr(utils_module, "WORKING_DIR", tmp_path)

    result = resolve_workspace_path("workspaces/default")
    assert result == tmp_path / "workspaces" / "default"


def test_absolute_path_returned_as_is_with_expanduser(monkeypatch, tmp_path):
    """Absolute paths should be returned unchanged (backward compat)."""
    import qwenpaw.config.utils as utils_module

    monkeypatch.setattr(utils_module, "WORKING_DIR", tmp_path)

    abs_path = str(tmp_path / "workspaces" / "my_agent")
    result = resolve_workspace_path(abs_path)
    assert result == Path(abs_path)


def test_tilde_path_expanded(monkeypatch, tmp_path):
    """Paths starting with ~ should be expanded."""
    import qwenpaw.config.utils as utils_module

    monkeypatch.setattr(utils_module, "WORKING_DIR", tmp_path)

    result = resolve_workspace_path("~/my_workspace")
    assert result.is_absolute()
    assert "my_workspace" in str(result)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/king/Projects/高乐申辉/龙虾盒子/QwenPaw-aixcore && python -m pytest tests/unit/config/test_resolve_workspace_path.py -v`
Expected: FAIL with `ImportError: cannot import name 'resolve_workspace_path'`

- [ ] **Step 3: Write minimal implementation**

In `src/qwenpaw/config/utils.py`, add after the `_normalize_working_dir_bound_paths` function (after line 106):

```python
def resolve_workspace_path(path_str: str) -> Path:
    """Resolve a workspace_dir value to an absolute Path.

    - Relative paths: resolve against WORKING_DIR
    - Absolute paths: expanduser() as-is (backward compat)
    """
    p = Path(path_str).expanduser()
    if not p.is_absolute():
        return WORKING_DIR / p
    return p
```

Also add `resolve_workspace_path` to the public API in `src/qwenpaw/config/__init__.py`. Add to the `from .utils import (...)` block and to `__all__`:

```python
# In from .utils import (...):
from .utils import (
    ...
    resolve_workspace_path,
)

# In __all__:
__all__ = [
    ...
    "resolve_workspace_path",
]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/king/Projects/高乐申辉/龙虾盒子/QwenPaw-aixcore && python -m pytest tests/unit/config/test_resolve_workspace_path.py -v`
Expected: all 3 tests PASS

- [ ] **Step 5: Commit**

```bash
git add tests/unit/config/test_resolve_workspace_path.py src/qwenpaw/config/utils.py
git commit -m "feat(config): add resolve_workspace_path() helper for relative path support"
```

---

### Task 2: Change `AgentProfileRef` defaults to relative paths

**Files:**
- Modify: `src/qwenpaw/config/config.py:1092-1098`

- [ ] **Step 1: Update `AgentsConfig.profiles` default**

In `src/qwenpaw/config/config.py`, change lines 1092-1098 from:

```python
    profiles: Dict[str, AgentProfileRef] = Field(
        default_factory=lambda: {
            "default": AgentProfileRef(
                id="default",
                workspace_dir=f"{WORKING_DIR}/workspaces/default",
            ),
        },
        description="Agent profile references (ID and workspace path only)",
    )
```

To:

```python
    profiles: Dict[str, AgentProfileRef] = Field(
        default_factory=lambda: {
            "default": AgentProfileRef(
                id="default",
                workspace_dir="workspaces/default",
            ),
        },
        description="Agent profile references (ID and workspace path only)",
    )
```

- [ ] **Step 2: Verify no other hardcoded WORKING_DIR defaults exist in AgentProfileRef or AgentsConfig**

Search for `WORKING_DIR` usage in `config/config.py` that produces absolute defaults:

Run: `grep -n "WORKING_DIR" src/qwenpaw/config/config.py`

Only the one changed above should exist in the `AgentsConfig` area. The `AgentProfileConfig.workspace_dir` default at line ~1007 also uses `WORKING_DIR` — check it:

Read `src/qwenpaw/config/config.py` around line 1007. `AgentProfileConfig.workspace_dir` defaults to `""` (empty string) — no change needed there.

- [ ] **Step 3: Run existing tests to verify no breakage**

Run: `cd /Users/king/Projects/高乐申辉/龙虾盒子/QwenPaw-aixcore && python -m pytest tests/ -v --timeout=30`
Expected: all tests PASS (same as before — the default `Config()` still creates a valid object, just with a relative path string)

- [ ] **Step 4: Commit**

```bash
git add src/qwenpaw/config/config.py
git commit -m "feat(config): use relative paths in AgentProfileRef defaults"
```

---

### Task 3: Update `migration.py` write sites and read sites

**Files:**
- Modify: `src/qwenpaw/app/migration.py`

This file has the most changes. Two categories:

**A. Write sites** — where `workspace_dir` is set on `AgentProfileRef` or `AgentProfileConfig`:

| Line | Current | Change To |
|------|---------|-----------|
| 120 | `default_workspace = Path(f"{WORKING_DIR}/workspaces/default").expanduser()` | Keep as-is (this computes the actual directory to create) |
| 129 | `workspace_dir=str(default_workspace)` | `workspace_dir="workspaces/default"` |
| 196-201 | legacy migration block | `workspace_dir="workspaces/default"` |
| 672 | `default_workspace = Path(f"{WORKING_DIR}/workspaces/default").expanduser()` | Keep as-is (directory creation) |
| 696 | `workspace_dir=str(default_workspace)` | `workspace_dir="workspaces/default"` |
| 836 | `qa_workspace = Path(f"{WORKING_DIR}/workspaces/{qa_id}").expanduser()` | Keep as-is (directory creation) |
| 880 | `workspace_dir=str(qa_workspace)` | `workspace_dir=f"workspaces/{qa_id}"` |

**B. Read sites** — where `workspace_dir` is consumed as a path:

| Line | Current | Change To |
|------|---------|-----------|
| 104 | `workspace_dir = Path(agent_ref.workspace_dir).expanduser()` | `workspace_dir = resolve_workspace_path(agent_ref.workspace_dir)` |
| 452 | `Path(profile.workspace_dir).expanduser()` | `resolve_workspace_path(profile.workspace_dir)` |
| 668 | `default_workspace = Path(agent_ref.workspace_dir).expanduser()` | `default_workspace = resolve_workspace_path(agent_ref.workspace_dir)` |
| 729 | `other = Path(ref.workspace_dir).expanduser()` | `other = resolve_workspace_path(ref.workspace_dir)` |
| 832 | `qa_workspace = Path(agent_ref.workspace_dir).expanduser()` | `qa_workspace = resolve_workspace_path(agent_ref.workspace_dir)` |

- [ ] **Step 1: Add import for `resolve_workspace_path` at top of file**

In `src/qwenpaw/app/migration.py`, add to the existing config imports:

```python
from ..config.utils import resolve_workspace_path
```

- [ ] **Step 2: Update all read sites**

For each read site listed above, replace `Path(xxx.workspace_dir).expanduser()` with `resolve_workspace_path(xxx.workspace_dir)`.

- [ ] **Step 3: Update all write sites**

For each write site listed above, change `workspace_dir=str(absolute_path)` to the relative form.

- [ ] **Step 4: Run tests**

Run: `cd /Users/king/Projects/高乐申辉/龙虾盒子/QwenPaw-aixcore && python -m pytest tests/ -v --timeout=30`
Expected: all tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/qwenpaw/app/migration.py
git commit -m "feat(migration): store relative workspace_dir paths and use resolve_workspace_path()"
```

---

### Task 4: Update `multi_agent_manager.py` read sites

**Files:**
- Modify: `src/qwenpaw/app/multi_agent_manager.py`

- [ ] **Step 1: Add import**

At the top of `src/qwenpaw/app/multi_agent_manager.py`, add:

```python
from ..config.utils import resolve_workspace_path
```

- [ ] **Step 2: Update read sites**

Lines 112 and 325 pass `agent_ref.workspace_dir` to `Workspace()`. The `Workspace.__init__` does `Path(workspace_dir).expanduser()` internally (line 59). This means `Workspace` receives the raw string from config.

Two options:
- **Option A (simpler):** Change `Workspace.__init__` line 59 to call `resolve_workspace_path()` instead of `Path(...).expanduser()`.
- **Option B:** Resolve before passing to `Workspace`.

Go with **Option A** — change `src/qwenpaw/app/workspace/workspace.py` line 59 from:

```python
self.workspace_dir = Path(workspace_dir).expanduser()
```

To:

```python
from ...config.utils import resolve_workspace_path
# ...
self.workspace_dir = resolve_workspace_path(workspace_dir)
```

This fixes the resolution for ALL callers of `Workspace`, including both `multi_agent_manager.py` lines.

- [ ] **Step 3: Run tests**

Run: `cd /Users/king/Projects/高乐申辉/龙虾盒子/QwenPaw-aixcore && python -m pytest tests/ -v --timeout=30`
Expected: all tests PASS

- [ ] **Step 4: Commit**

```bash
git add src/qwenpaw/app/workspace/workspace.py
git commit -m "feat(workspace): use resolve_workspace_path() for workspace_dir resolution"
```

---

### Task 5: Update `routers/agents.py` read and write sites

**Files:**
- Modify: `src/qwenpaw/app/routers/agents.py`

- [ ] **Step 1: Add import**

Add to the config imports at top of file:

```python
from ...config.utils import resolve_workspace_path
```

- [ ] **Step 2: Update `_read_profile_description` (line 132)**

Change:
```python
profile_path = Path(workspace_dir) / "PROFILE.md"
```
To:
```python
profile_path = resolve_workspace_path(workspace_dir) / "PROFILE.md"
```

- [ ] **Step 3: Update `AgentSummary` construction (lines 189, 200)**

Lines 189 and 200 set `workspace_dir=agent_ref.workspace_dir` in the response model. These are API response fields — they should return the raw value from config (already a relative path). **No change needed** — the API returns whatever is stored.

- [ ] **Step 4: Update create agent endpoint (lines 303-342)**

Line 303-305: computing workspace directory path — keep as-is (needs real absolute path for directory creation).

Line 323: `workspace_dir=str(workspace_dir)` in `AgentProfileConfig` — this is `agent.json` (workspace-local), not root config. Keep as absolute (it's inside the workspace directory itself and doesn't need to be portable).

Line 342: `workspace_dir=str(workspace_dir)` in `AgentProfileRef` — **change to relative path**:

```python
workspace_dir=f"workspaces/{new_id}",
```

- [ ] **Step 5: Run tests**

Run: `cd /Users/king/Projects/高乐申辉/龙虾盒子/QwenPaw-aixcore && python -m pytest tests/ -v --timeout=30`
Expected: all tests PASS

- [ ] **Step 6: Commit**

```bash
git add src/qwenpaw/app/routers/agents.py
git commit -m "feat(agents-router): use relative paths for new agent profiles"
```

---

### Task 6: Update `agent_config_watcher.py`

**Files:**
- Modify: `src/qwenpaw/app/agent_config_watcher.py:50,65-66`

- [ ] **Step 1: Assess impact**

The watcher receives `workspace_dir: Path` (already a `Path` object) from `Workspace`. Since Task 4 changed `Workspace.__init__` to resolve via `resolve_workspace_path()`, the watcher already gets a correct absolute path. **No change needed** — the watcher is downstream of the resolution.

- [ ] **Step 2: Commit (no-op, skip if nothing changed)**

---

### Task 7: Verify portable build produces relative paths

**Files:** None (verification only)

- [ ] **Step 1: Run `qwenpaw init --defaults --accept-security` with a temp WORKING_DIR**

```bash
cd /Users/king/Projects/高乐申辉/龙虾盒子/QwenPaw-aixcore
QWENPAW_WORKING_DIR=/tmp/qwenpaw-portable-test python -m qwenpaw init --defaults --accept-security
```

- [ ] **Step 2: Verify config.json has relative paths**

```bash
cat /tmp/qwenpaw-portable-test/config.json | python -m json.tool | grep workspace_dir
```

Expected output:
```json
"workspace_dir": "workspaces/default"
"workspace_dir": "workspaces/QwenPaw_QA_Agent_0.2"
```

Both should be relative paths, NOT absolute paths containing `/tmp/qwenpaw-portable-test/`.

- [ ] **Step 3: Cleanup test directory**

```bash
rm -rf /tmp/qwenpaw-portable-test
```

- [ ] **Step 4: Commit (verification only, no code changes)**

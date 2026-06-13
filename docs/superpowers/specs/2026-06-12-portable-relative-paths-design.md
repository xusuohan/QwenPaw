# Portable Relative Paths Design

## Problem

After packaging the portable build, `data/config.json` contains hardcoded absolute paths from the build machine:

```json
"workspace_dir": "/Users/king/Projects/.../dist/QwenPaw-Portable_20260612_115921/data/workspaces/default"
```

When the portable app runs on a different device, these paths are invalid, breaking agent workspace loading and other features.

## Root Cause

`AgentProfileRef.workspace_dir` defaults to `f"{WORKING_DIR}/workspaces/default"` (`config/config.py:1096`). Since `WORKING_DIR` resolves to an absolute path at import time, `qwenpaw init` during the build bakes the build-machine path into `config.json`.

The existing `_normalize_working_dir_bound_paths()` mitigates this at load time, but it's a runtime patch rather than a structural fix.

## Solution

Store `workspace_dir` as a path **relative to `WORKING_DIR`** in `config.json`. Resolve to absolute at consumption time via a helper function.

### 1. Add `resolve_workspace_path()` in `config/utils.py`

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

### 2. Change `AgentProfileRef` defaults in `config/config.py`

```python
# Before
workspace_dir=f"{WORKING_DIR}/workspaces/default"

# After
workspace_dir="workspaces/default"
```

Same for QA agent default: `"workspaces/QwenPaw_QA_Agent_0.2"`.

### 3. Update write sites in `app/migration.py`

Where `ensure_default_agent_exists()` and `ensure_qa_agent_exists()` set `workspace_dir`, use relative strings:

```python
workspace_dir="workspaces/default"
workspace_dir="workspaces/QwenPaw_QA_Agent_0.2"
```

### 4. Update all read sites to use `resolve_workspace_path()`

Every call site that does `Path(agent_ref.workspace_dir).expanduser()` changes to `resolve_workspace_path(agent_ref.workspace_dir)`:

| File | Functions |
|------|-----------|
| `app/migration.py` | `ensure_default_agent_exists`, `ensure_qa_agent_exists`, `reconcile_all_agents`, `_reconcile_agent_md_file`, and other readers |
| `app/multi_agent_manager.py` | Agent loading paths |
| `app/routers/agents.py` | `_read_profile_description`, agent listing |
| `app/agent_config_watcher.py` | Constructor |

### 5. Keep `_normalize_working_dir_bound_paths()` unchanged

Continues to handle old configs with absolute paths. Serves as a backward-compat safety net.

## Files Changed

| File | Change Type |
|------|-------------|
| `src/qwenpaw/config/utils.py` | Add `resolve_workspace_path()` |
| `src/qwenpaw/config/config.py` | Change `AgentProfileRef` defaults to relative paths |
| `src/qwenpaw/app/migration.py` | Write relative paths; read via `resolve_workspace_path()` |
| `src/qwenpaw/app/multi_agent_manager.py` | Read via `resolve_workspace_path()` |
| `src/qwenpaw/app/routers/agents.py` | Read via `resolve_workspace_path()` |
| `src/qwenpaw/app/agent_config_watcher.py` | Read via `resolve_workspace_path()` |

## Risk Control

- Old configs with absolute paths continue to work: `resolve_workspace_path()` handles both relative and absolute inputs, and `_normalize_working_dir_bound_paths()` remains as a safety net.
- No data model or API interface changes.
- Build scripts unchanged — the fix propagates naturally through `qwenpaw init`.

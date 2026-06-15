# Portable Absolute Paths Design (Enhanced)

## Background

Previous design (`2026-06-12-portable-relative-paths-design.md`) changed `workspace_dir` to store relative paths in `config.json`, with `resolve_workspace_path()` resolving them at runtime. While this works, the user prefers config.json to contain correct absolute paths on disk — disk data should be clean and self-contained, not requiring runtime resolution.

The challenge: `qwenpaw init` only runs once (when config.json doesn't exist). Once written, the absolute paths from the first machine become stale when the USB drive moves to another device.

## Solution

**Startup-time path rewriting + absolute paths everywhere.**

Each launcher invocation runs `qwenpaw fix-paths` before starting the main app. This command reads config.json, detects stale absolute paths (from a different machine), and rewrites them to the current device's actual paths. The normalization layer (`_normalize_working_dir_bound_paths()`) is removed since disk data is always correct.

## Data Flow

```
Launcher starts
  ├─ Sets QWENPAW_WORKING_DIR=/current/device/data
  ├─ config.json missing? → qwenpaw init (writes current absolute paths)
  ├─ config.json exists?  → qwenpaw fix-paths (rewrites stale paths)
  └─ Starts main app (config.json has correct absolute paths)
```

## Changes

### 1. New: `src/qwenpaw/cli/fix_paths_cmd.py`

New CLI subcommand `qwenpaw fix-paths`:

```python
@click.command("fix-paths", help="Rewrite stale WORKING_DIR-bound paths in config.json.")
@click.option("--config-path", type=click.Path(), default=None, help="Config file path.")
def fix_paths_cmd(config_path):
    from qwenpaw.config.utils import rewrite_stale_paths_on_disk
    changed = rewrite_stale_paths_on_disk(
        Path(config_path) if config_path else None
    )
    if changed:
        click.echo("已重写陈旧路径。")
```

### 2. New function: `rewrite_stale_paths_on_disk()` in `config/utils.py`

Reads config.json as raw JSON, walks the tree, rewrites `workspace_dir` / `media_dir` values that contain stale absolute paths (detected via WORKING_DIR markers: `workspaces/`, `media/`). Saves back to disk if any changes were made.

Reuses the marker-detection logic from `_normalize_working_dir_bound_paths()` (which this function replaces), but persists changes to disk instead of only modifying in memory.

Note: the old normalization layer also handled `~/.copaw` legacy paths. Since the user specified no backward compatibility with old configs, this is intentionally dropped. Only WORKING_DIR-marker-based detection is retained.

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

    def _rewrite(v):
        nonlocal modified
        if not isinstance(v, str) or not v or v.startswith(new_root):
            return v
        for marker in _WORKING_DIR_MARKERS:
            for sep in ("/", "\\"):
                needle = sep + marker + sep
                idx = v.rfind(needle)
                if idx >= 0:
                    suffix = v[idx + 1:].replace("\\", "/")
                    new_val = str(WORKING_DIR / suffix)
                    if new_val != v:
                        modified = True
                        return new_val
                    return v
                if v.endswith(sep + marker):
                    new_val = str(WORKING_DIR / marker)
                    if new_val != v:
                        modified = True
                        return new_val
                    return v
        return v

    def _walk(obj, key=None):
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

### 3. Register in `cli/main.py`

Add to `lazy_subcommands`:

```python
"fix-paths": ("qwenpaw.cli.fix_paths_cmd", "fix_paths_cmd", ".fix_paths_cmd"),
```

### 4. Update launcher scripts

All three platform launchers add `qwenpaw fix-paths` after the init check:

**macOS** (`scripts/pack/build_macos.sh`):
```bash
CONFIG_FILE="$QWENPAW_WORKING_DIR/config.json"
if [ ! -f "$CONFIG_FILE" ]; then
  "$ENV_DIR/bin/python" -u -m qwenpaw init --defaults --accept-security
fi
# Rewrite stale paths from previous device
"$ENV_DIR/bin/python" -u -m qwenpaw fix-paths
```

**Linux** (`scripts/pack/build_linux.sh`): same pattern.
**Windows** (`scripts/pack/build_win_portable.ps1`): same pattern with `%~dp0env\python.exe`.

### 5. Update `migration.py` — use absolute paths for new profiles

`ensure_default_agent_exists()`:
```python
# Before
workspace_dir="workspaces/default"
# After
workspace_dir=str(WORKING_DIR / "workspaces/default")
```

`ensure_qa_agent_exists()`:
```python
# Before
workspace_dir=f"workspaces/{qa_id}"
# After
workspace_dir=str(WORKING_DIR / "workspaces/{qa_id}")
```

### 6. Update `config.py` — default factory uses absolute paths

```python
# Before
workspace_dir="workspaces/default"
# After
workspace_dir=str(WORKING_DIR / "workspaces/default")
```

### 7. Remove `_normalize_working_dir_bound_paths()` from `config/utils.py`

- Remove the `_normalize_working_dir_bound_paths()` function (lines 52-108)
- Remove its call in `_load_and_validate_config()` (line 588)
- `resolve_workspace_path()` is **retained** — it is not the normalization layer. It's a path resolver used at read sites. With absolute paths now stored on disk, it acts as a passthrough (`is_absolute()` → return as-is). It can also resolve any relative paths that slip in from manual edits.

## Files Changed

| # | File | Change |
|---|------|--------|
| 1 | `src/qwenpaw/cli/fix_paths_cmd.py` | **New** — `qwenpaw fix-paths` subcommand |
| 2 | `src/qwenpaw/cli/main.py` | Register `fix-paths` in lazy_subcommands |
| 3 | `src/qwenpaw/config/utils.py` | Add `rewrite_stale_paths_on_disk()`; remove `_normalize_working_dir_bound_paths()` and its call |
| 4 | `src/qwenpaw/app/migration.py` | `ensure_default_agent_exists` / `ensure_qa_agent_exists` use absolute paths |
| 5 | `src/qwenpaw/config/config.py` | `AgentsConfig.profiles` default factory uses absolute paths |
| 6 | `scripts/pack/build_macos.sh` | Add `qwenpaw fix-paths` call |
| 7 | `scripts/pack/build_linux.sh` | Add `qwenpaw fix-paths` call |
| 8 | `scripts/pack/build_win_portable.ps1` | Add `qwenpaw fix-paths` call |

## Behavior Matrix

| Scenario | Behavior |
|----------|----------|
| Fresh USB (no config.json) | `init` writes absolute paths for current device ✅ |
| Same device, subsequent launch | `fix-paths` finds no stale paths, exits silently ✅ |
| Different device, same mount point | `fix-paths` finds no stale paths (paths happen to match) ✅ |
| Different device, different mount point | `fix-paths` rewrites stale paths to current device ✅ |
| User copies config.json manually | `fix-paths` rewrites on next launch ✅ |

## Risk Control

- `rewrite_stale_paths_on_disk()` only modifies paths that match known WORKING_DIR markers — it won't touch unrelated config values.
- `resolve_workspace_path()` is retained at read sites as a fallback for any relative paths that slip in (e.g., manual edits).
- The `fix-paths` command is idempotent — safe to run multiple times. No-op (exit 0, no output) when all paths are already correct.
- Launcher runs `fix-paths` on every startup, so even if a user manually edits config.json with stale paths, the next launch corrects them.
- `~/.copaw` legacy path handling is intentionally dropped (per user requirement: no backward compatibility with old configs).

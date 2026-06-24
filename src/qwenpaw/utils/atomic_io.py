# -*- coding: utf-8 -*-
"""Atomic file writes with process-local locking.

Consolidates the tmp+fsync+replace pattern already used (in slightly
different forms) by skills_manager, token_usage.storage and
backup.safe_swap into one reusable module. Robust on exFAT: writes go to
a sibling tmp file, fsync the file, then os.replace. (Directory fsync is
NOT performed here; the file-level fsync is the load-bearing durability
measure.) A per-path threading.RLock serializes read-modify-write within
the process; single-instance deployments do not need cross-process locks.
The per-path lock registry holds one RLock per resolved path for the life of
the process (never pruned), so it suits a bounded set of paths (config,
session, auth, …); callers writing to an unbounded path set should
re-evaluate.

NOTE: chmod-based protection (e.g. 0o600) is a no-op on exFAT and is NOT
relied upon here.
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any, Callable

from json_repair import repair_json

# Module-level registry of per-resolved-path RLocks, guarded by a meta-lock.
_locks_meta = threading.Lock()
_locks: dict[str, threading.RLock] = {}


def _resolve(path: str | os.PathLike) -> str:
    """Resolve a path to its absolute string form (cache key for locks)."""
    return str(Path(path).resolve())


def _get_lock(resolved: str) -> threading.RLock:
    """Return (creating if needed) the process-local RLock for a path."""
    with _locks_meta:
        lock = _locks.get(resolved)
        if lock is None:
            lock = threading.RLock()
            _locks[resolved] = lock
        return lock


def write_bytes_atomic(
    path: str | os.PathLike,
    data: bytes,
    *,
    lock: bool = True,
    fsync: bool = True,
) -> None:
    """Write *data* to *path* atomically.

    Writes to ``<path>.tmp.<pid>``, optionally fsyncs, then ``os.replace``
    onto the target. On any failure the tmp file is removed and the
    original is left untouched. When *lock* is True (default) the write is
    serialized with the per-path RLock — callers that already hold the
    lock (e.g. inside :func:`locked_json_update`) pass ``lock=False``.
    Passing ``lock=False`` without already holding the path's lock breaks
    RMW serialization under concurrency; the default is safe for all
    standalone writes. The temp file is created with default umask; no
    ``mode=`` parameter is supported by design (chmod is a no-op on exFAT
    — see the module docstring).
    """
    resolved = _resolve(path)
    target = Path(resolved)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(f"{target.name}.tmp.{os.getpid()}")

    def _do_write() -> None:
        try:
            with open(tmp, "wb") as f:
                f.write(data)
                if fsync:
                    f.flush()
                    os.fsync(f.fileno())
            os.replace(tmp, resolved)
        finally:
            try:
                if tmp.exists():
                    tmp.unlink()
            except OSError:
                pass

    if lock:
        with _get_lock(resolved):
            _do_write()
    else:
        _do_write()


def write_json_atomic(
    path: str | os.PathLike,
    data: Any,
    *,
    lock: bool = True,
    fsync: bool = True,
    indent: int | None = 2,
    ensure_ascii: bool = False,
    sort_keys: bool = False,
) -> None:
    """Serialize *data* as UTF-8 JSON and write atomically.

    Defaults to ``indent=2`` / ``ensure_ascii=False`` to match the rest of
    the project's on-disk JSON style. ``lock`` and ``fsync`` are forwarded
    to :func:`write_bytes_atomic`; see its docstring for the ``lock=False``
    caveat.
    """
    payload = json.dumps(
        data,
        indent=indent,
        ensure_ascii=ensure_ascii,
        sort_keys=sort_keys,
    )
    write_bytes_atomic(path, payload.encode("utf-8"), lock=lock, fsync=fsync)


def read_json_safe(
    path: str | os.PathLike,
    *,
    default: Any = None,
) -> Any:
    """Read JSON from *path* with a json_repair fallback.

    Returns *default* when the file is missing or irrecoverably corrupt.
    A half-written file (crash mid-write) is typically recoverable by
    json_repair, which avoids data loss on exFAT where non-atomic
    overwrites are most fragile.
    """
    p = Path(path)
    if not p.exists():
        return default
    try:
        text = p.read_text(encoding="utf-8")
    except OSError:
        return default
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        try:
            return json.loads(repair_json(text))
        except Exception:
            return default


def locked_json_update(
    path: str | os.PathLike,
    fn: Callable[[Any], Any],
    *,
    default: Any = None,
    fsync: bool = True,
) -> Any:
    """Read-modify-write *path* under the per-path RLock.

    *fn* receives the current data (or *default* when the file is
    missing/corrupt) and returns the new data, which is written
    atomically. The whole RMW is serialized so concurrent updaters never
    overwrite each other. Uses an RLock so *fn* may re-enter the same
    path's lock — but callers MUST NOT trigger another
    ``locked_json_update`` on the *same* path from within *fn* (that would
    imply nested RMW which is a logic error, not a supported pattern).
    """
    resolved = _resolve(path)
    with _get_lock(resolved):
        current = read_json_safe(resolved, default=default)
        updated = fn(current)
        # Already hold the lock; write without re-acquiring.
        write_json_atomic(resolved, updated, lock=False, fsync=fsync)
        return updated


def cleanup_orphan_tmps(
    directory: str | os.PathLike,
    pattern: str = "*.tmp.*",
) -> int:
    """Remove leftover ``.tmp.<pid>`` files in *directory*.

    A crash between writing the tmp file and ``os.replace`` leaves an
    orphan; call this at startup to reclaim space and avoid confusion.
    Returns the number of files removed. The default *pattern* assumes this
    module's ``<name>.tmp.<pid>`` naming; pass a custom pattern if the
    directory also holds unrelated ``*.tmp.*`` files.
    """
    removed = 0
    for orphan in Path(directory).glob(pattern):
        try:
            orphan.unlink()
            removed += 1
        except OSError:
            pass
    return removed

# -*- coding: utf-8 -*-
"""Best-effort import-time page-cache warmer for USB/exFAT startup.

On slow USB media, the import-driven pattern of opening many small .pyc/.pyd
files is dominated by random 4K reads (high latency). A daemon thread that
sequentially pre-reads those files converts some of that random IO into
sequential reads, warming the OS page cache so the import machinery then
hits warm pages.

Best-effort: swallows all OSError, never blocks the main thread, never
raises. File-list building (which may scan sys.path) runs ON the worker
thread — never on the caller's thread — so startup is never blocked even
when site-packages is huge. The fallback sweep is wall-clock bounded.
Toggled by QWENPAW_PERF_IMPORT_PREFETCH (default on); bounded by
QWENPAW_PERF_PREFETCH_CAP_MB (default 256). If a measured USB workload
regresses (prefetch competes for the single USB queue), disable via env.
"""
from __future__ import annotations

import logging
import sys
import threading
import time
from pathlib import Path

logger = logging.getLogger(__name__)

_DEFAULT_CAP_MB = 256
_CHUNK = 1024 * 1024  # 1MB sequential reads
_SWEEP_BUDGET_SEC = 2.0  # wall-clock cap on the fallback directory scan


def _read_order_file(order_file: Path) -> list[Path]:
    """Read an explicit import-order file (one path per line)."""
    files: list[Path] = []
    try:
        for line in order_file.read_text(encoding="utf-8").splitlines():
            p = line.strip()
            if p:
                files.append(Path(p))
    except OSError:
        pass
    return files


def _sweep_roots() -> list[Path]:
    """Fallback: sweep sys.path dirs + the qwenpaw package for ext files.

    Bounded by ``_SWEEP_BUDGET_SEC`` wall-clock so a huge site-packages
    can't make the (background) scan run away. Runs on the worker thread.
    """
    roots: list[Path] = []
    for entry in sys.path:
        try:
            ep = Path(entry).resolve()
        except OSError:
            continue
        if ep.is_dir():
            roots.append(ep)
    # qwenpaw/ package root (this file is qwenpaw/utils/import_prefetch.py)
    pkg_root = Path(__file__).resolve().parent.parent
    roots.append(pkg_root)

    deadline = time.monotonic() + _SWEEP_BUDGET_SEC
    files: list[Path] = []
    seen: set[str] = set()
    for root in roots:
        try:
            for pattern in ("*.pyc", "*.pyd", "*.so"):
                for p in root.rglob(pattern):
                    if time.monotonic() > deadline:
                        return files
                    key = str(p)
                    if key not in seen:
                        seen.add(key)
                        files.append(Path(p))
        except OSError:
            continue
    return files


def _build_file_list(import_order_file: Path | None) -> list[Path]:
    """Return the list of .pyc/.pyd/.so files to pre-read, in order."""
    if import_order_file and import_order_file.is_file():
        ordered = _read_order_file(import_order_file)
        if ordered:
            return ordered
    return _sweep_roots()


def _prefetch_worker(order_path: Path | None, cap_bytes: int) -> None:
    read_total = 0
    for f in _build_file_list(order_path):
        if read_total >= cap_bytes:
            break
        try:
            with open(f, "rb") as fh:
                while read_total < cap_bytes:
                    chunk = fh.read(_CHUNK)
                    if not chunk:
                        break
                    read_total += len(chunk)
        except OSError:
            continue


def start_import_prefetch(
    cap_mb: int | None = None,
    import_order_file: Path | str | None = None,
) -> threading.Thread:
    """Start a daemon thread that sequentially pre-reads bytecode/C-ext files.

    Returns the thread immediately. The file-list scan happens ON the worker
    thread (never blocking the caller), so this returns at once even when
    site-packages is large. Best-effort: the worker swallows all OSError and
    never raises.
    """
    if cap_mb is None:
        cap_mb = _DEFAULT_CAP_MB
    order_path = (
        Path(import_order_file) if import_order_file is not None else None
    )
    thread = threading.Thread(
        target=_prefetch_worker,
        args=(order_path, cap_mb * 1024 * 1024),
        name="import-prefetch",
        daemon=True,
    )
    thread.start()
    logger.debug("import prefetch started: cap=%dMB", cap_mb)
    return thread

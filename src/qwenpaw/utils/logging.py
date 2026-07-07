# -*- coding: utf-8 -*-
"""Logging setup for application logging and optional file output."""

import logging
import logging.handlers
import os
import platform
import queue
import sys
import threading
from pathlib import Path

from ..constant import PROJECT_NAME, WORKING_DIR

# Rotating file handler limits (idempotent add avoids duplicate handlers)
_LOG_MAX_BYTES = 5 * 1024 * 1024  # 5 MiB
_LOG_BACKUP_COUNT = 3


_LEVEL_MAP = {
    "critical": logging.CRITICAL,
    "error": logging.ERROR,
    "warning": logging.WARNING,
    "info": logging.INFO,
    "debug": logging.DEBUG,
}

# Logger namespace MUST match the actual import package name (qwenpaw) so
# records from `logging.getLogger(__name__)` in qwenpaw.* modules propagate
# up to this logger where the file handler is attached. PROJECT_NAME may
# differ (display branding) — do not derive LOG_NAMESPACE from it, or
# 99% of log records (workspace, multi_agent_manager, services, ...) get
# silently dropped because they live in the qwenpaw.* hierarchy.
LOG_NAMESPACE = "qwenpaw"

# Canonical log file name and path — import these instead of reconstructing.
LOG_FILE_BASENAME = f"{LOG_NAMESPACE}.log"
# §5.3 dual-file split: backend subprocess + desktop launcher each write
# to independent files, eliminating cross-process log contention.
LOG_BACKEND_PATH = WORKING_DIR / "qwenpaw-backend.log"
LOG_DESKTOP_PATH = WORKING_DIR / "qwenpaw-desktop.log"
# Backward-compatible alias: existing readers (console.py, daemon_commands.py)
# import LOG_FILE_PATH which now points to the backend log.
LOG_FILE_PATH = LOG_BACKEND_PATH


# ---------------------------------------------------------------------------
# §5.3 Queue-based logging (QueueHandler + QueueListener)
# ---------------------------------------------------------------------------
# Module-level state: maps resolved log path → QueueListener instance.
_queue_listeners: dict[Path, logging.handlers.QueueListener] = {}
_queue_lock = threading.Lock()
# Paths that already have a handler (covers both queue and direct modes
# so idempotency works regardless of the flag value).
_tracked_paths: set[Path] = set()


def _log_queue_enabled() -> bool:
    """Return True when queue-based logging is active (default: on)."""
    val = os.environ.get("QWENPAW_PERF_LOG_QUEUE", "1").strip().lower()
    return val not in ("0", "false", "no", "off")


def _enable_windows_ansi() -> None:
    """Enable ANSI escape code support on Windows 10+."""
    if platform.system() != "Windows":
        return
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        # STD_OUTPUT_HANDLE = -11, ENABLE_VIRTUAL_TERMINAL_PROCESSING = 0x0004
        handle = kernel32.GetStdHandle(-11)
        mode = ctypes.c_ulong()  # pylint: disable=no-value-for-parameter
        kernel32.GetConsoleMode(handle, ctypes.byref(mode))
        kernel32.SetConsoleMode(handle, mode.value | 0x0004)
    except Exception:
        pass


# Call once at import time
_enable_windows_ansi()


class ColorFormatter(logging.Formatter):
    COLORS = {
        logging.DEBUG: "\033[34m",
        logging.INFO: "\033[32m",
        logging.WARNING: "\033[33m",
        logging.ERROR: "\033[31m",
        logging.CRITICAL: "\033[41m\033[97m",
    }
    RESET = "\033[0m"

    def format(self, record):
        # Disable colors if output is not a terminal (e.g. piped/redirected)
        use_color = hasattr(sys.stderr, "isatty") and sys.stderr.isatty()
        color = self.COLORS.get(record.levelno, "") if use_color else ""
        reset = self.RESET if use_color else ""
        level = f"{color}{record.levelname}{reset}"

        full_path = record.pathname
        cwd = os.getcwd()
        # Use os.path for cross-platform path prefix stripping
        try:
            if os.path.commonpath([full_path, cwd]) == cwd:
                full_path = os.path.relpath(full_path, cwd)
        except ValueError:
            # Different drives on Windows (e.g., C: vs D:) are not comparable.
            pass

        prefix = f"{level} {full_path}:{record.lineno}"
        original_msg = super().format(record)

        return f"{prefix} | {original_msg}"


class _SafeRotatingFileHandler(logging.handlers.RotatingFileHandler):
    """RotatingFileHandler that tolerates Windows file-locking errors.

    On Windows, ``os.rename()`` inside ``doRollover()`` raises
    ``PermissionError`` when the log file is held open by another
    process (e.g. a log viewer or the debug-log console reader).
    This subclass catches the error, reopens the stream so logging
    continues without data loss, and defers rotation to the next
    size-exceeding emit.
    """

    def doRollover(self):
        try:
            super().doRollover()
        except PermissionError:
            if self.stream:
                self.stream.close()
            self.stream = self._open()


class PlainFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        full_path = record.pathname
        cwd = os.getcwd()
        try:
            if os.path.commonpath([full_path, cwd]) == cwd:
                full_path = os.path.relpath(full_path, cwd)
        except ValueError:
            pass

        prefix = f"{record.levelname} | {full_path}:{record.lineno}"
        formatted_time = self.formatTime(record, self.datefmt)
        msg = f"{formatted_time} | {prefix} | {record.getMessage()}"

        if record.exc_info and not record.exc_text:
            record.exc_text = self.formatException(record.exc_info)
        if record.exc_text:
            msg = msg + "\n" + record.exc_text
        if record.stack_info:
            msg = msg + "\n" + self.formatStack(record.stack_info)

        return msg


class SuppressPathAccessLogFilter(logging.Filter):
    """
    Filter out uvicorn access log lines whose message contains any of the
    given path substrings. path_substrings: list of substrings; if any
    appears in the log message, the record is suppressed.
    Empty list = allow all.
    """

    def __init__(self, path_substrings: list[str]) -> None:
        super().__init__()
        self.path_substrings = path_substrings

    def filter(self, record: logging.LogRecord) -> bool:
        if not self.path_substrings:
            return True
        try:
            msg = record.getMessage()
            return not any(s in msg for s in self.path_substrings)
        except Exception:
            return True


def setup_logger(level: int | str = logging.INFO):
    """Configure logging to only output from this package, not deps."""
    log_format = "%(asctime)s | %(message)s"
    datefmt = "%Y-%m-%d %H:%M:%S"

    if isinstance(level, str):
        level = _LEVEL_MAP.get(level.lower(), logging.INFO)

    formatter = ColorFormatter(log_format, datefmt)

    # Suppress third-party: set root logger level and configure handlers.
    root = logging.getLogger()
    for handler in root.handlers:
        if isinstance(
            handler,
            (logging.FileHandler, logging.handlers.RotatingFileHandler),
        ):
            handler.setLevel(logging.INFO)
        else:
            handler.setLevel(logging.WARNING)

    # Only attach handler to the project namespace
    # so only app logs are printed.
    logger = logging.getLogger(LOG_NAMESPACE)
    logger.setLevel(level)
    logger.propagate = False
    if not logger.handlers:
        # Use sys.stderr directly. Wrapping sys.stderr.buffer in a
        # TextIOWrapper takes ownership of the buffer and closes it on GC,
        # which corrupts sys.stderr for subsequent tests/code.
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(formatter)
        logger.addHandler(handler)

    return logger


def add_project_file_handler(log_path: Path) -> None:
    """Add a rotating file handler to the project logger for daemon logs.

    When ``QWENPAW_PERF_LOG_QUEUE`` is enabled (default), the file handler
    is wrapped in a ``QueueListener`` running on a dedicated background
    thread.  Application threads enqueue records via ``QueueHandler`` —
    writes never block request processing and cannot interleave.

    When the flag is disabled, falls back to the original
    ``_SafeRotatingFileHandler`` attached directly to the logger.

    Uses _SafeRotatingFileHandler on all platforms with automatic log
    rotation (max 5 MiB per file, 3 backups).  On Windows, rotation
    errors caused by file locking are tolerated gracefully.

    Idempotent: if the logger already has a handler for the same path,
    no new handler is added (avoids duplicate lines and leaked descriptors
    when lifespan runs multiple times in the same process).

    Args:
        log_path: Path to the log file.
    """
    log_path = Path(log_path).resolve()
    log_path.parent.mkdir(parents=True, exist_ok=True)

    with _queue_lock:
        if log_path in _tracked_paths:
            return
        _tracked_paths.add(log_path)

    logger = logging.getLogger(LOG_NAMESPACE)

    file_handler = _SafeRotatingFileHandler(
        log_path,
        encoding="utf-8",
        maxBytes=_LOG_MAX_BYTES,
        backupCount=_LOG_BACKUP_COUNT,
    )

    file_handler.setLevel(logger.level or logging.INFO)

    file_handler.setFormatter(
        PlainFormatter("%(asctime)s | %(message)s", "%Y-%m-%d %H:%M:%S"),
    )

    if _log_queue_enabled():
        log_queue: queue.Queue = queue.Queue(-1)
        listener = logging.handlers.QueueListener(
            log_queue,
            file_handler,
            respect_handler_level=True,
        )
        listener.start()

        with _queue_lock:
            _queue_listeners[log_path] = listener

        logger.addHandler(logging.handlers.QueueHandler(log_queue))
    else:
        logger.addHandler(file_handler)


def stop_queue_listeners() -> None:
    """Stop all active queue listeners and flush pending log records.

    Blocks until each listener thread has drained its queue and exited.
    Safe to call multiple times (idempotent).
    """
    with _queue_lock:
        listeners = dict(_queue_listeners)
        _queue_listeners.clear()

    for listener in listeners.values():
        try:
            listener.stop()
        except Exception:
            pass


def queue_listener_stats() -> dict:
    """Return diagnostic counts for queue-based logging."""
    with _queue_lock:
        active = len(_queue_listeners)
        alive = sum(
            1
            for listener in _queue_listeners.values()
            # pylint: disable=protected-access
            if listener._thread is not None and listener._thread.is_alive()
        )
    return {"active_listeners": active, "alive_threads": alive}

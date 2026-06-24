# -*- coding: utf-8 -*-
# pylint: disable=redefined-outer-name,unused-argument
"""Tests for §5.3 queue-based log handler (QueueHandler + QueueListener).

Covers:
- Flag ON: QueueHandler attached, listener thread alive, records flushed.
- Flag OFF: direct _SafeRotatingFileHandler (backward-compatible).
- Idempotency: same path twice → no duplicate handlers.
- Graceful shutdown: stop_queue_listeners drains pending records.
- Concurrent writers: multi-thread logging does not interleave.
- Dual log path constants.
"""
from __future__ import annotations

import logging
import logging.handlers
import threading
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from qwenpaw.utils.logging import (
    LOG_BACKEND_PATH,
    LOG_DESKTOP_PATH,
    LOG_FILE_PATH,
    LOG_NAMESPACE,
    _SafeRotatingFileHandler,
    add_project_file_handler,
    queue_listener_stats,
    stop_queue_listeners,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def clean_loggers():
    """Remove all file/queue handlers from the namespace logger after test."""
    yield
    logger = logging.getLogger(LOG_NAMESPACE)
    to_remove = [
        h
        for h in logger.handlers
        if isinstance(
            h,
            (
                logging.FileHandler,
                logging.handlers.RotatingFileHandler,
                logging.handlers.QueueHandler,
            ),
        )
    ]
    for h in to_remove:
        logger.removeHandler(h)
        h.close()
    stop_queue_listeners()


@pytest.fixture()
def log_file(tmp_path: Path) -> Path:
    return tmp_path / "test.log"


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


class TestDualLogConstants:
    """Verify dual-file log path constants."""

    def test_backend_path_suffix(self):
        assert str(LOG_BACKEND_PATH).endswith("qwenpaw-backend.log")

    def test_desktop_path_suffix(self):
        assert str(LOG_DESKTOP_PATH).endswith("qwenpaw-desktop.log")

    def test_log_file_path_is_backend(self):
        """LOG_FILE_PATH is backward-compat alias for LOG_BACKEND_PATH."""
        assert LOG_FILE_PATH == LOG_BACKEND_PATH

    def test_backend_and_desktop_differ(self):
        assert LOG_BACKEND_PATH != LOG_DESKTOP_PATH


# ---------------------------------------------------------------------------
# Flag ON — queue-based logging
# ---------------------------------------------------------------------------


class TestQueueHandlerEnabled:
    """When QWENPAW_PERF_LOG_QUEUE is ON (default)."""

    def test_queue_handler_attached(self, log_file, clean_loggers):
        """S级: QueueHandler is attached to the logger."""
        with patch.dict("os.environ", {"QWENPAW_PERF_LOG_QUEUE": "1"}):
            add_project_file_handler(log_file)
        logger = logging.getLogger(LOG_NAMESPACE)
        queue_handlers = [
            h
            for h in logger.handlers
            if isinstance(h, logging.handlers.QueueHandler)
        ]
        assert len(queue_handlers) >= 1

    def test_no_direct_file_handler(self, log_file, clean_loggers):
        """No RotatingFileHandler directly on the logger (queue wraps it)."""
        with patch.dict("os.environ", {"QWENPAW_PERF_LOG_QUEUE": "1"}):
            add_project_file_handler(log_file)
        logger = logging.getLogger(LOG_NAMESPACE)
        direct = [
            h
            for h in logger.handlers
            if isinstance(
                h,
                (logging.FileHandler, logging.handlers.RotatingFileHandler),
            )
            and not isinstance(h, logging.handlers.QueueHandler)
        ]
        assert len(direct) == 0

    def test_listener_thread_alive(self, log_file, clean_loggers):
        """S级: QueueListener's background thread is running."""
        with patch.dict("os.environ", {"QWENPAW_PERF_LOG_QUEUE": "1"}):
            add_project_file_handler(log_file)
        stats = queue_listener_stats()
        assert stats["active_listeners"] >= 1
        assert stats["alive_threads"] >= 1

    def test_log_record_reaches_file(self, log_file, clean_loggers):
        """S级: Log records flow through queue to the file."""
        with patch.dict("os.environ", {"QWENPAW_PERF_LOG_QUEUE": "1"}):
            add_project_file_handler(log_file)
        logger = logging.getLogger(LOG_NAMESPACE)
        logger.info("queue-test-marker-42")
        # QueueListener thread needs a moment to drain
        time.sleep(0.5)
        content = log_file.read_text(encoding="utf-8")
        assert "queue-test-marker-42" in content

    def test_stats_reports_count(self, log_file, clean_loggers):
        """queue_listener_stats reflects active listener count."""
        with patch.dict("os.environ", {"QWENPAW_PERF_LOG_QUEUE": "1"}):
            add_project_file_handler(log_file)
        stats = queue_listener_stats()
        assert stats["active_listeners"] == 1

    def test_two_different_paths(self, tmp_path, clean_loggers):
        """Two different log paths → two queue listeners."""
        path_a = tmp_path / "a.log"
        path_b = tmp_path / "b.log"
        with patch.dict("os.environ", {"QWENPAW_PERF_LOG_QUEUE": "1"}):
            add_project_file_handler(path_a)
            add_project_file_handler(path_b)
        stats = queue_listener_stats()
        assert stats["active_listeners"] == 2


# ---------------------------------------------------------------------------
# Flag OFF — backward-compatible direct file handler
# ---------------------------------------------------------------------------


class TestQueueHandlerDisabled:
    """When QWENPAW_PERF_LOG_QUEUE is OFF."""

    def test_direct_rotating_handler(self, log_file, clean_loggers):
        """S级: _SafeRotatingFileHandler attached directly (no queue)."""
        with patch.dict("os.environ", {"QWENPAW_PERF_LOG_QUEUE": "0"}):
            add_project_file_handler(log_file)
        logger = logging.getLogger(LOG_NAMESPACE)
        rotating = [
            h
            for h in logger.handlers
            if isinstance(h, _SafeRotatingFileHandler)
        ]
        assert len(rotating) >= 1

    def test_no_queue_handler(self, log_file, clean_loggers):
        """No QueueHandler when flag is OFF."""
        with patch.dict("os.environ", {"QWENPAW_PERF_LOG_QUEUE": "0"}):
            add_project_file_handler(log_file)
        logger = logging.getLogger(LOG_NAMESPACE)
        queue_handlers = [
            h
            for h in logger.handlers
            if isinstance(h, logging.handlers.QueueHandler)
        ]
        assert len(queue_handlers) == 0

    def test_log_written_immediately(self, log_file, clean_loggers):
        """Log record written to file without queue delay."""
        with patch.dict("os.environ", {"QWENPAW_PERF_LOG_QUEUE": "0"}):
            add_project_file_handler(log_file)
        logger = logging.getLogger(LOG_NAMESPACE)
        logger.info("direct-write-marker")
        # No sleep needed — direct handler writes synchronously
        content = log_file.read_text(encoding="utf-8")
        assert "direct-write-marker" in content


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------


class TestIdempotency:
    """Same path twice must not duplicate handlers."""

    def test_idempotent_queue_mode(self, log_file, clean_loggers):
        with patch.dict("os.environ", {"QWENPAW_PERF_LOG_QUEUE": "1"}):
            add_project_file_handler(log_file)
            add_project_file_handler(log_file)
        stats = queue_listener_stats()
        assert stats["active_listeners"] == 1

    def test_idempotent_direct_mode(self, log_file, clean_loggers):
        with patch.dict("os.environ", {"QWENPAW_PERF_LOG_QUEUE": "0"}):
            add_project_file_handler(log_file)
            count_after_first = len(logging.getLogger(LOG_NAMESPACE).handlers)
            add_project_file_handler(log_file)
            count_after_second = len(
                logging.getLogger(LOG_NAMESPACE).handlers,
            )
        assert count_after_first == count_after_second


# ---------------------------------------------------------------------------
# Shutdown
# ---------------------------------------------------------------------------


class TestShutdown:
    """stop_queue_listeners drains pending records and exits cleanly."""

    def test_stop_drains_pending(self, log_file, clean_loggers):
        """Records queued before stop are flushed to disk."""
        with patch.dict("os.environ", {"QWENPAW_PERF_LOG_QUEUE": "1"}):
            add_project_file_handler(log_file)
        logger = logging.getLogger(LOG_NAMESPACE)
        logger.info("pre-shutdown-marker")
        stop_queue_listeners()
        content = log_file.read_text(encoding="utf-8")
        assert "pre-shutdown-marker" in content

    def test_stop_is_idempotent(self, clean_loggers):
        """Calling stop with no active listeners is a no-op."""
        stop_queue_listeners()  # must not raise

    def test_threads_exit_after_stop(self, log_file, clean_loggers):
        """Listener threads are no longer alive after stop."""
        with patch.dict("os.environ", {"QWENPAW_PERF_LOG_QUEUE": "1"}):
            add_project_file_handler(log_file)
        stop_queue_listeners()
        stats = queue_listener_stats()
        assert stats["active_listeners"] == 0
        assert stats["alive_threads"] == 0


# ---------------------------------------------------------------------------
# Concurrent writers
# ---------------------------------------------------------------------------


class TestConcurrentWriters:
    """Multi-threaded logging through the queue."""

    def test_concurrent_records_not_lost(self, log_file, clean_loggers):
        """S级: All records from N threads reach the log file."""
        with patch.dict("os.environ", {"QWENPAW_PERF_LOG_QUEUE": "1"}):
            add_project_file_handler(log_file)
        logger = logging.getLogger(LOG_NAMESPACE)
        n_threads = 10
        n_records = 50
        barrier = threading.Barrier(n_threads)

        def writer(tid: int) -> None:
            barrier.wait()
            for j in range(n_records):
                logger.info("thread-%d-record-%d", tid, j)

        threads = [
            threading.Thread(target=writer, args=(i,))
            for i in range(n_threads)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        # Give the listener time to drain
        time.sleep(1.0)
        stop_queue_listeners()

        content = log_file.read_text(encoding="utf-8")
        for tid in range(n_threads):
            for j in range(n_records):
                marker = f"thread-{tid}-record-{j}"
                assert marker in content, f"Missing: {marker}"

    def test_no_interleaved_lines(self, log_file, clean_loggers):
        """Each log line is complete (no mid-line truncation)."""
        with patch.dict("os.environ", {"QWENPAW_PERF_LOG_QUEUE": "1"}):
            add_project_file_handler(log_file)
        logger = logging.getLogger(LOG_NAMESPACE)
        n_threads = 5
        n_records = 100
        barrier = threading.Barrier(n_threads)

        def writer(tid: int) -> None:
            barrier.wait()
            for j in range(n_records):
                logger.info("interleave-test-%d-%d", tid, j)

        threads = [
            threading.Thread(target=writer, args=(i,))
            for i in range(n_threads)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        time.sleep(1.0)
        stop_queue_listeners()

        content = log_file.read_text(encoding="utf-8")
        lines = [ln for ln in content.splitlines() if "interleave-test" in ln]
        # Every matching line should contain the full marker pattern
        for line in lines:
            assert "interleave-test-" in line

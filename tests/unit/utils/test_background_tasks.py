# -*- coding: utf-8 -*-
"""Unit tests for utils.background_tasks."""
from __future__ import annotations

import asyncio
import logging

import pytest  # noqa: F401  pylint: disable=unused-import

# pytest is used implicitly for fixture discovery (e.g. caplog).

from qwenpaw.utils.background_tasks import BackgroundTaskRunner


class TestSpawn:
    """spawn runs coroutines and tracks them."""

    async def test_runs_to_completion(self):
        runner = BackgroundTaskRunner()
        done = asyncio.Event()

        async def work():
            done.set()

        runner.spawn(work())
        await asyncio.wait_for(done.wait(), timeout=2)
        await runner.shutdown()
        assert done.is_set()

    async def test_exception_is_logged_not_raised(self, caplog):
        runner = BackgroundTaskRunner()

        async def boom():
            raise ValueError("kaboom")

        # The package logger (qwenpaw namespace) has propagate disabled at
        # import time (qwenpaw/__init__.py -> setup_logger). Re-enable
        # propagation so caplog's root handler can observe the record.
        namespace = logging.getLogger("qwenpaw")
        original_propagate = namespace.propagate
        namespace.propagate = True
        try:
            with caplog.at_level(logging.ERROR):
                runner.spawn(boom())
                await asyncio.sleep(0.1)
                await runner.shutdown()
        finally:
            namespace.propagate = original_propagate
        assert any(
            "Background task failed" in r.message for r in caplog.records
        )

    async def test_shutdown_cancels_pending(self):
        runner = BackgroundTaskRunner()
        cancelled = asyncio.Event()

        async def long_work():
            try:
                await asyncio.sleep(10)
            except asyncio.CancelledError:
                cancelled.set()
                raise

        runner.spawn(long_work())
        await asyncio.sleep(0.05)  # let it start
        await runner.shutdown(timeout=1)
        assert cancelled.is_set()

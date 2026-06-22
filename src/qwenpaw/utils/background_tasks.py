# -*- coding: utf-8 -*-
"""Fire-and-forget background tasks tracked for clean lifespan shutdown.

Distinct from app._app._background_startup (which loads agents): this
module is for small, deferrable, non-critical IO such as telemetry
uploads, redundant migration checks and import prefetch warmup. Tasks are
best-effort — exceptions are logged, never raised to the caller — and all
are cancelled/awaited on shutdown().
"""
from __future__ import annotations

import asyncio
import logging
from typing import Awaitable

logger = logging.getLogger(__name__)


class BackgroundTaskRunner:
    """Spawn tracked fire-and-forget tasks on the running event loop."""

    def __init__(self) -> None:
        self._tasks: set[asyncio.Task] = set()

    def spawn(
        self,
        coro: Awaitable,
        *,
        name: str | None = None,
    ) -> asyncio.Task:
        """Schedule *coro* as a tracked background task.

        Must be called from within a running event loop (e.g. from the
        FastAPI lifespan). The returned task is tracked so shutdown() can
        cancel it.
        """
        loop = asyncio.get_running_loop()
        task = loop.create_task(self._wrap(coro), name=name)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    async def _wrap(self, coro: Awaitable) -> None:
        try:
            await coro
        except Exception:
            # Note: asyncio.CancelledError is a BaseException (Py3.8+), so it
            # is NOT caught here and cancellation propagates cleanly.
            logger.exception("Background task failed")

    async def shutdown(self, timeout: float = 5.0) -> None:
        """Cancel all tracked tasks and wait for them to finish."""
        tasks = [t for t in self._tasks if not t.done()]
        for t in tasks:
            t.cancel()
        for t in tasks:
            try:
                await asyncio.wait_for(t, timeout=timeout)
            except (asyncio.TimeoutError, asyncio.CancelledError, Exception):
                pass

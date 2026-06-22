# -*- coding: utf-8 -*-
"""Regression tests for ChannelManager.replace_channel deadlock."""
from __future__ import annotations

import asyncio

from qwenpaw.app.channels.manager import ChannelManager


class _FakeChannel:
    """Minimal duck-typed channel (not a BaseChannel subclass)."""

    uses_manager_queue = False  # skip set_enqueue in replace_channel

    def __init__(self, name: str):
        self.channel = name

    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        return None


class _ReenteringStopChannel:
    """A channel whose stop() calls back into the manager (deadlock case)."""

    uses_manager_queue = False

    def __init__(self, name: str, manager: ChannelManager):
        self.channel = name
        self._manager = manager
        self.stopped = asyncio.Event()

    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        # Re-enter the manager during stop — get_channel acquires self._lock.
        await self._manager.get_channel(self.channel)
        self.stopped.set()


class TestReplaceChannelDeadlock:
    """stop() that re-enters the manager must not self-deadlock."""

    async def test_replace_does_not_deadlock_on_reentering_stop(self):
        manager = ChannelManager(channels=[])
        old = _ReenteringStopChannel("dt", manager)
        manager.channels.append(old)
        new = _FakeChannel("dt")
        # Old code: replace_channel holds self._lock during old.stop(),
        # which awaits get_channel -> self._lock -> hang -> TimeoutError.
        # Fixed code: stop() runs outside the lock -> completes.
        await asyncio.wait_for(manager.replace_channel(new), timeout=3)
        assert old.stopped.is_set()

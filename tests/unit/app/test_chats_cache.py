# -*- coding: utf-8 -*-
"""Tests for JsonChatRepository chats cache (§4.3)."""
from __future__ import annotations

import asyncio
import json
import os

import pytest

from qwenpaw.app.runner.models import ChatSpec, ChatsFile
from qwenpaw.app.runner.repo.json_repo import JsonChatRepository
from qwenpaw.app.runner.repo.json_repo import _chats_cache_enabled


class TestChatsCacheFlag:
    def test_default_on(self, monkeypatch):
        monkeypatch.delenv("QWENPAW_PERF_CHATS_CACHE", raising=False)
        assert _chats_cache_enabled() is True

    @pytest.mark.parametrize("val", ["0", "false", "no", "off", "FALSE"])
    def test_disabled_values(self, monkeypatch, val):
        monkeypatch.setenv("QWENPAW_PERF_CHATS_CACHE", val)
        assert _chats_cache_enabled() is False

    @pytest.mark.parametrize("val", ["1", "true", "yes"])
    def test_enabled_values(self, monkeypatch, val):
        monkeypatch.setenv("QWENPAW_PERF_CHATS_CACHE", val)
        assert _chats_cache_enabled() is True


def _spec(chat_id: str, name: str | None = None) -> ChatSpec:
    """Minimal valid ChatSpec (session_id + user_id are the only required)."""
    return ChatSpec(
        id=chat_id,
        name=name or chat_id,
        session_id=chat_id,
        user_id="u",
    )


def _write_chats(path, chat_ids):
    """Write a chats.json directly to disk, bypassing the repo cache."""
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "chats": [
                    {"id": cid, "session_id": cid, "user_id": "u"}
                    for cid in chat_ids
                ],
            },
        ),
        encoding="utf-8",
    )


class TestChatsCacheLoadSave:
    def setup_method(self):
        # Ensure the cache flag is on (default) for these tests.
        os.environ.pop("QWENPAW_PERF_CHATS_CACHE", None)

    async def test_load_caches_after_first_read(self, tmp_path):
        path = tmp_path / "chats.json"
        _write_chats(path, ["c1"])
        repo = JsonChatRepository(path)
        first = await repo.load()
        # Overwrite disk AFTER the first load populated the cache.
        _write_chats(path, ["c2"])
        second = await repo.load()
        assert [c.id for c in first.chats] == ["c1"]
        assert [c.id for c in second.chats] == ["c1"]  # cached, not re-read

    async def test_load_returns_independent_copy(self, tmp_path):
        path = tmp_path / "chats.json"
        _write_chats(path, ["c1"])
        repo = JsonChatRepository(path)
        first = await repo.load()
        first.chats.append(_spec("c2"))  # mutate the returned object
        second = await repo.load()
        assert [c.id for c in second.chats] == ["c1"]  # cache unaffected

    async def test_flag_disabled_load_reads_disk_every_time(
        self,
        tmp_path,
        monkeypatch,
    ):
        monkeypatch.setenv("QWENPAW_PERF_CHATS_CACHE", "0")
        path = tmp_path / "chats.json"
        _write_chats(path, ["c1"])
        repo = JsonChatRepository(path)
        first = await repo.load()
        _write_chats(path, ["c2"])
        second = await repo.load()
        assert [c.id for c in first.chats] == ["c1"]
        assert [c.id for c in second.chats] == ["c2"]  # re-read (no cache)

    async def test_save_updates_cache_and_writes_through(self, tmp_path):
        path = tmp_path / "chats.json"
        repo = JsonChatRepository(path)
        await repo.save(ChatsFile(chats=[_spec("c1")]))
        # Cache updated: load sees it.
        loaded = await repo.load()
        assert [c.id for c in loaded.chats] == ["c1"]
        # Write-through: a fresh repo (empty cache) reads it from disk.
        repo2 = JsonChatRepository(path)
        loaded2 = await repo2.load()
        assert [c.id for c in loaded2.chats] == ["c1"]

    async def test_save_failure_leaves_cache_consistent(
        self,
        tmp_path,
        monkeypatch,
    ):
        path = tmp_path / "chats.json"
        repo = JsonChatRepository(path)
        await repo.save(ChatsFile(chats=[_spec("c1")]))  # persisted + cached

        def boom(*args, **kwargs):
            raise OSError("disk full")

        monkeypatch.setattr(
            "qwenpaw.app.runner.repo.json_repo.write_json_atomic",
            boom,
        )
        with pytest.raises(OSError):
            await repo.save(ChatsFile(chats=[_spec("c2")]))
        # Cache reflects the last SUCCESSFUL write (c1), not the failed one.
        loaded = await repo.load()
        assert [c.id for c in loaded.chats] == ["c1"]


class TestChatsCacheAsyncAndIntegration:
    def setup_method(self):
        os.environ.pop("QWENPAW_PERF_CHATS_CACHE", None)

    async def test_load_miss_offloads_to_thread(self, tmp_path, monkeypatch):
        path = tmp_path / "chats.json"
        _write_chats(path, ["c1"])
        repo = JsonChatRepository(path)  # cache empty -> miss
        names = []
        real = asyncio.to_thread

        async def spy(func, *args, **kwargs):
            names.append(getattr(func, "__name__", repr(func)))
            return await real(func, *args, **kwargs)

        monkeypatch.setattr(asyncio, "to_thread", spy)
        await repo.load()
        assert "_load_from_disk_sync" in names

    async def test_save_offloads_write_to_thread(self, tmp_path, monkeypatch):
        path = tmp_path / "chats.json"
        repo = JsonChatRepository(path)
        names = []
        real = asyncio.to_thread

        async def spy(func, *args, **kwargs):
            names.append(getattr(func, "__name__", repr(func)))
            return await real(func, *args, **kwargs)

        monkeypatch.setattr(asyncio, "to_thread", spy)
        await repo.save(ChatsFile(chats=[_spec("c1")]))
        assert "write_json_atomic" in names

    async def test_roundtrip_durability_after_restart(self, tmp_path):
        path = tmp_path / "chats.json"
        repo = JsonChatRepository(path)
        await repo.save(ChatsFile(chats=[_spec("c1")]))
        # New repo instance simulates a restart (cache empty).
        repo2 = JsonChatRepository(path)
        loaded = await repo2.load()
        assert [c.id for c in loaded.chats] == ["c1"]

    async def test_base_crud_works_with_cache(self, tmp_path):
        path = tmp_path / "chats.json"
        repo = JsonChatRepository(path)
        await repo.upsert_chat(_spec("c1"))
        assert (await repo.get_chat("c1")).id == "c1"
        await repo.upsert_chat(_spec("c1", name="renamed"))
        assert (await repo.get_chat("c1")).name == "renamed"
        await repo.upsert_chat(_spec("c2"))
        assert {c.id for c in await repo.filter_chats(user_id="u")} == {
            "c1",
            "c2",
        }
        assert await repo.delete_chats(["c1"]) is True
        assert await repo.get_chat("c1") is None

    async def test_concurrent_load_save_safe(self, tmp_path):
        path = tmp_path / "chats.json"
        repo = JsonChatRepository(path)

        async def writer(i):
            await repo.save(ChatsFile(chats=[_spec(f"c{i}")]))

        async def reader():
            await repo.load()

        # No raise under concurrent access; cache lock serializes updates.
        await asyncio.gather(
            *[writer(i) for i in range(10)],
            *[reader() for _ in range(10)],
        )
        final = await repo.load()
        assert isinstance(final, ChatsFile)
        assert len(final.chats) == 1  # last writer wins; no corruption
        disk = json.loads(path.read_text(encoding="utf-8"))
        assert disk["version"] == 1  # atomic writes kept disk valid

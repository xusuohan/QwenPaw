# -*- coding: utf-8 -*-
"""Tests for JsonChatRepository chats cache (§4.3)."""
from __future__ import annotations

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

# -*- coding: utf-8 -*-
"""JSON-based chat repository."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import uuid
from pathlib import Path

from .base import BaseChatRepository
from ..models import ChatsFile
from ....utils.atomic_io import write_json_atomic

logger = logging.getLogger(__name__)

_CHATS_CACHE_DISABLE_VALUES = frozenset({"0", "false", "no", "off"})


def _chats_cache_enabled() -> bool:
    """Chats read-cache is ON by default.

    Disable via QWENPAW_PERF_CHATS_CACHE set to one of {0,false,no,off}
    (case-insensitive). When disabled, load()/save() use the original
    synchronous direct-disk behavior (the kill-switch path).
    """
    raw = os.environ.get("QWENPAW_PERF_CHATS_CACHE")
    if raw is None:
        return True
    return raw.strip().lower() not in _CHATS_CACHE_DISABLE_VALUES


class JsonChatRepository(BaseChatRepository):
    """chats.json repository (single-file storage).

    Stores chat_id (UUID) -> session_id mappings in a JSON file.
    Similar to JsonJobRepository pattern from crons.

    Notes:
    - Single-machine, no cross-process lock.
    - Atomic write: write tmp then replace.
    """

    def __init__(self, path: Path | str):
        """Initialize JSON chat repository.

        Args:
            path: Path to chats.json file
        """
        if isinstance(path, str):
            path = Path(path)
        self._path = path.expanduser()
        # §4.3 in-process read cache + write-through. _cache holds the last
        # successfully-persisted ChatsFile; _cache_lock guards it as
        # defense-in-depth (ChatManager already serializes CRUD per-workspace
        # via its own lock; this protects against direct repo use). Disabled
        # via QWENPAW_PERF_CHATS_CACHE -> load/save fall back to direct disk.
        self._cache: ChatsFile | None = None
        self._cache_lock = asyncio.Lock()

    @property
    def path(self) -> Path:
        """Get the repository file path."""
        return self._path

    def _load_from_disk_sync(self) -> ChatsFile:
        """Read chats.json from disk synchronously (the original load body)."""
        if not self._path.exists():
            return ChatsFile(version=1, chats=[])
        data = json.loads(self._path.read_text(encoding="utf-8"))
        return ChatsFile.model_validate(data)

    async def load(self) -> ChatsFile:
        """Load chat specs from JSON file.

        Returns:
            ChatsFile with all chat specs
        """
        if not _chats_cache_enabled():
            return self._load_from_disk_sync()
        async with self._cache_lock:
            if self._cache is None:
                # Cache miss: read disk off the event loop (once per repo
                # lifetime); subsequent loads hit the cache (0 IO).
                self._cache = await asyncio.to_thread(
                    self._load_from_disk_sync,
                )
            return self._cache.model_copy(deep=True)

    async def save(self, chats_file: ChatsFile) -> None:
        """Save chat specs to JSON file atomically.

        Args:
            chats_file: ChatsFile to persist
        """
        if not _chats_cache_enabled():
            write_json_atomic(
                self._path,
                chats_file.model_dump(mode="json"),
                sort_keys=True,
            )
            return
        snapshot = chats_file.model_copy(deep=True)
        payload = snapshot.model_dump(mode="json")
        # Write-through: persist first, update the cache only on success so
        # the cache always reflects the last successfully-persisted state. A
        # failed write raises before the cache is touched, leaving cache and
        # disk consistent.
        await asyncio.to_thread(
            write_json_atomic,
            self._path,
            payload,
            sort_keys=True,
        )
        async with self._cache_lock:
            self._cache = snapshot


def migrate_legacy_weixin_chats_file(chats_path: Path | str) -> None:
    """Rewrite legacy ``weixin:`` session_id prefixes to ``wechat:``.

    Idempotent; backs up the original file before rewrite.
    """
    path = (
        Path(chats_path).expanduser()
        if isinstance(
            chats_path,
            str,
        )
        else chats_path
    )
    if not path.is_file():
        return
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    if not isinstance(data, dict):
        return

    chats = data.get("chats")
    if not isinstance(chats, list):
        return

    mutated = False
    for chat in chats:
        if not isinstance(chat, dict):
            continue
        sid = chat.get("session_id")
        if isinstance(sid, str) and sid.startswith("weixin:"):
            chat["session_id"] = "wechat:" + sid[len("weixin:") :]
            mutated = True

    if not mutated:
        return

    try:
        backup_path = path.with_suffix(
            path.suffix + f".{uuid.uuid4().hex[:8]}.weixin-migrate.bak",
        )
        shutil.copy2(path, backup_path)
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        # newline="\n" prevents Windows from translating LF -> CRLF and
        # polluting the file's line endings on rewrite.
        tmp_path.write_text(
            json.dumps(
                data,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            ),
            encoding="utf-8",
            newline="\n",
        )
        # os.replace is the documented atomic-overwrite primitive on all
        # supported platforms (POSIX rename + Windows ReplaceFile).
        os.replace(tmp_path, path)
        logger.warning(
            "Migrated legacy 'weixin' chat entries -> 'wechat' in %s "
            "(backup: %s)",
            path,
            backup_path,
        )
    except OSError as exc:
        logger.error(
            "Failed to migrate legacy 'weixin' chat entries in %s: %s",
            path,
            exc,
        )

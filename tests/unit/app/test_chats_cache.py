# -*- coding: utf-8 -*-
"""Tests for JsonChatRepository chats cache (§4.3)."""
from __future__ import annotations

import pytest

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

# -*- coding: utf-8 -*-
"""Tests for auth._load_auth_data TTL cache."""
from __future__ import annotations

import qwenpaw.app.auth as auth_mod
from qwenpaw.app.auth import _load_auth_data, invalidate_auth_cache


class TestAuthDataCache:
    def setup_method(self):
        invalidate_auth_cache()

    def teardown_method(self):
        invalidate_auth_cache()

    def test_cache_hit_avoids_reread_within_ttl(self, monkeypatch):
        calls = {"n": 0}

        def counting():
            calls["n"] += 1
            return {"jwt_secret": "s", "revoked_tokens": []}

        monkeypatch.setattr(auth_mod, "_load_auth_data_from_disk", counting)
        _load_auth_data()
        _load_auth_data()
        _load_auth_data()
        assert calls["n"] == 1  # disk read once, rest served from cache

    def test_invalidate_forces_reread(self, monkeypatch):
        calls = {"n": 0}

        def counting():
            calls["n"] += 1
            return {"jwt_secret": "s"}

        monkeypatch.setattr(auth_mod, "_load_auth_data_from_disk", counting)
        _load_auth_data()
        invalidate_auth_cache()
        _load_auth_data()
        assert calls["n"] == 2

    def test_expiry_after_ttl(self, monkeypatch):
        calls = {"n": 0}
        timeline = [100.0]

        def counting():
            calls["n"] += 1
            return {"jwt_secret": "s"}

        monkeypatch.setattr(auth_mod, "_load_auth_data_from_disk", counting)
        monkeypatch.setattr(auth_mod.time, "monotonic", lambda: timeline[0])
        _load_auth_data()  # populate at t=100
        timeline[0] = 100.0 + auth_mod.AUTH_DATA_CACHE_TTL + 0.01  # past TTL
        _load_auth_data()  # miss -> reread
        assert calls["n"] == 2

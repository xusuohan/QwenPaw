# -*- coding: utf-8 -*-
"""Tests for the model-client cache in qwenpaw.agents.model_factory (§4.1)."""
# pylint: disable=protected-access,unused-argument
from __future__ import annotations

import pytest

from qwenpaw.agents.model_factory import (
    _model_client_cache_enabled,
    _model_client_fingerprint,
)


class _FakeModel:
    """Minimal stand-in for an inner chat model (identity-comparable)."""

    def __init__(self, tag: str) -> None:
        self.tag = tag


class _FakeProvider:
    """Stand-in provider exposing the fingerprint surface + build counter."""

    def __init__(
        self,
        base_url: str = "https://x.example",
        api_key: str = "k",
        generate_kwargs=None,
    ) -> None:
        self.base_url = base_url
        self.api_key = api_key
        self._generate_kwargs = generate_kwargs or {}
        self.build_calls = 0

    def get_effective_generate_kwargs(self, model_id: str):
        return dict(self._generate_kwargs)

    def get_chat_model_instance(self, model_id: str):
        self.build_calls += 1
        return _FakeModel(f"{self.base_url}|{model_id}|{self.api_key}")


class TestCacheFlag:
    def test_default_on(self, monkeypatch):
        monkeypatch.delenv("QWENPAW_PERF_MODEL_CLIENT_CACHE", raising=False)
        assert _model_client_cache_enabled() is True

    @pytest.mark.parametrize(
        "val",
        ["0", "false", "no", "off", "FALSE", " off ", "\nfalse\n"],
    )
    def test_disabled_values(self, monkeypatch, val):
        monkeypatch.setenv("QWENPAW_PERF_MODEL_CLIENT_CACHE", val)
        assert _model_client_cache_enabled() is False

    @pytest.mark.parametrize("val", ["1", "true", "yes"])
    def test_enabled_values(self, monkeypatch, val):
        monkeypatch.setenv("QWENPAW_PERF_MODEL_CLIENT_CACHE", val)
        assert _model_client_cache_enabled() is True


class TestFingerprint:
    def test_same_config_equal(self):
        p = _FakeProvider(api_key="k")
        assert _model_client_fingerprint(p, "p1", "m1") == (
            _model_client_fingerprint(p, "p1", "m1")
        )

    def test_different_api_key_differs(self):
        p = _FakeProvider(api_key="k1")
        a = _model_client_fingerprint(p, "p1", "m1")
        p.api_key = "k2"
        b = _model_client_fingerprint(p, "p1", "m1")
        assert a != b

    def test_different_generate_kwargs_differs(self):
        p = _FakeProvider(generate_kwargs={"temperature": 0.5})
        a = _model_client_fingerprint(p, "p1", "m1")
        p._generate_kwargs = {"temperature": 0.9}
        b = _model_client_fingerprint(p, "p1", "m1")
        assert a != b

    def test_different_model_id_differs(self):
        p = _FakeProvider()
        assert _model_client_fingerprint(p, "p1", "m1") != (
            _model_client_fingerprint(p, "p1", "m2")
        )

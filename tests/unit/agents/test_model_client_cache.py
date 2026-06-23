# -*- coding: utf-8 -*-
"""Tests for the model-client cache in qwenpaw.agents.model_factory (§4.1)."""
# pylint: disable=protected-access,unused-argument
from __future__ import annotations

import json
import threading

import pytest

from qwenpaw.agents.model_factory import (
    _get_cached_inner_model,
    _model_client_cache_enabled,
    _model_client_fingerprint,
    clear_model_client_cache,
    model_client_cache_stats,
)
import qwenpaw.agents.model_factory as mf


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


class TestCachedInnerModel:
    def setup_method(self):
        clear_model_client_cache()

    def teardown_method(self):
        clear_model_client_cache()

    def test_cache_hit_returns_same_instance(self):
        prov = _FakeProvider()
        a = _get_cached_inner_model(prov, "p1", "m1")
        b = _get_cached_inner_model(prov, "p1", "m1")
        assert a is b
        assert prov.build_calls == 1

    def test_different_api_key_misses(self):
        prov = _FakeProvider(api_key="k1")
        a = _get_cached_inner_model(prov, "p1", "m1")
        prov.api_key = "k2"
        b = _get_cached_inner_model(prov, "p1", "m1")
        assert a is not b
        assert prov.build_calls == 2

    def test_different_generate_kwargs_misses(self):
        prov = _FakeProvider(generate_kwargs={"temperature": 0.5})
        a = _get_cached_inner_model(prov, "p1", "m1")
        prov._generate_kwargs = {"temperature": 0.9}
        b = _get_cached_inner_model(prov, "p1", "m1")
        assert a is not b
        assert prov.build_calls == 2

    def test_shared_across_providers_same_fingerprint(self):
        # Two distinct provider instances with identical config share one
        # cached client (the warm pool is process-global by fingerprint).
        prov_a = _FakeProvider(api_key="k")
        prov_b = _FakeProvider(api_key="k")
        a = _get_cached_inner_model(prov_a, "p1", "m1")
        b = _get_cached_inner_model(prov_b, "p1", "m1")
        assert a is b

    def test_flag_disabled_passthrough(self, monkeypatch):
        monkeypatch.setenv("QWENPAW_PERF_MODEL_CLIENT_CACHE", "0")
        prov = _FakeProvider()
        a = _get_cached_inner_model(prov, "p1", "m1")
        b = _get_cached_inner_model(prov, "p1", "m1")
        assert a is not b
        assert prov.build_calls == 2
        assert model_client_cache_stats()["size"] == 0

    def test_no_stale_on_provider_mutation(self):
        prov = _FakeProvider(api_key="old")
        a = _get_cached_inner_model(prov, "p1", "m1")
        prov.api_key = "new"  # simulate update_config() in-place change
        b = _get_cached_inner_model(prov, "p1", "m1")
        assert a is not b
        assert b.tag != a.tag  # built from the new api_key

    def test_stats_never_expose_key(self):
        prov = _FakeProvider(
            api_key="SECRET_KEY",
            base_url="https://secret.example",
        )
        _get_cached_inner_model(prov, "p1", "m1")

        blob = json.dumps(model_client_cache_stats())
        assert "SECRET_KEY" not in blob
        assert "secret.example" not in blob
        assert model_client_cache_stats()["size"] == 1
        assert model_client_cache_stats()["misses"] == 1
        assert model_client_cache_stats()["hits"] == 0

    def test_concurrent_build_safe(self):
        prov = _FakeProvider()
        barrier = threading.Barrier(8)
        results: list = []
        results_lock = threading.Lock()

        def worker():
            barrier.wait()
            m = _get_cached_inner_model(prov, "p1", "m1")
            with results_lock:
                results.append(m)

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert len(results) == 8
        assert all(r is results[0] for r in results)  # all share one client
        assert prov.build_calls == 1  # no duplicate build

    def test_cap_evicts_oldest(self, monkeypatch):
        monkeypatch.setattr(mf, "_MODEL_CLIENT_CACHE_CAP", 3)
        prov = _FakeProvider()
        for i in range(5):
            _get_cached_inner_model(prov, "p1", f"m{i}")
        assert model_client_cache_stats()["size"] <= 3

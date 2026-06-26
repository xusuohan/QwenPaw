# -*- coding: utf-8 -*-
# pylint: disable=redefined-outer-name,unused-argument,protected-access
"""Tests for §3.3 load_envs_into_environ deferred to lifespan.

Verifies:
- _app.py module-level code does NOT call load_envs_into_environ
  (the __init__.py:43 call handles it before constant.py import).
- lifespan Phase 0 calls load_envs_into_environ as safety net.
- Kill-switch QWENPAW_PERF_LOAD_ENVS_DEFER controls the optimization.
"""
from __future__ import annotations

import ast
import inspect
from pathlib import Path
from unittest.mock import patch

import pytest

from qwenpaw.app import _app as _app_module


# ---------------------------------------------------------------------------
# Kill-switch
# ---------------------------------------------------------------------------


class TestLoadEnvsDeferFlag:
    """_load_envs_defer_enabled() respects QWENPAW_PERF_LOAD_ENVS_DEFER."""

    def test_default_enabled(self):
        with patch.dict("os.environ", {}, clear=False):
            assert _app_module._load_envs_defer_enabled() is True

    @pytest.mark.parametrize(
        "val",
        ["0", "false", "no", "off"],
    )
    def test_disabled_values(self, val):
        with patch.dict(
            "os.environ",
            {"QWENPAW_PERF_LOAD_ENVS_DEFER": val},
        ):
            assert _app_module._load_envs_defer_enabled() is False

    @pytest.mark.parametrize("val", ["1", "true", "yes", "on", ""])
    def test_enabled_values(self, val):
        with patch.dict(
            "os.environ",
            {"QWENPAW_PERF_LOAD_ENVS_DEFER": val},
        ):
            assert _app_module._load_envs_defer_enabled() is True


# ---------------------------------------------------------------------------
# Module-level call removed (static analysis)
# ---------------------------------------------------------------------------


class TestModuleLevelCallRemoved:
    """_app.py must NOT call load_envs_into_environ at module level."""

    def test_no_module_level_call(self):
        """Parse _app.py AST: no top-level Call to load_envs_into_environ."""
        app_path = Path(inspect.getfile(_app_module))
        tree = ast.parse(app_path.read_text(encoding="utf-8"))
        for node in ast.iter_child_nodes(tree):
            if isinstance(node, ast.Expr) and isinstance(
                node.value,
                ast.Call,
            ):
                func = node.value.func
                name = getattr(func, "id", "") or getattr(func, "attr", "")
                assert name != "load_envs_into_environ", (
                    "_app.py must not call load_envs_into_environ at "
                    "module level — __init__.py:43 handles it."
                )

    def test_init_py_still_calls(self):
        """qwenpaw/__init__.py:43 MUST still call load_envs_into_environ.

        This is the correctness-critical call: it runs before constant.py
        is first imported, ensuring 26+ module-level env-backed constants
        see persisted values.
        """
        init_path = (
            Path(inspect.getfile(_app_module)).parent.parent / "__init__.py"
        )
        source = init_path.read_text(encoding="utf-8")
        assert "load_envs_into_environ()" in source, (
            "qwenpaw/__init__.py must call load_envs_into_environ() "
            "before constant.py is imported."
        )


# ---------------------------------------------------------------------------
# Lifespan safety-net call
# ---------------------------------------------------------------------------


class TestLifespanSafetyNet:
    """lifespan source contains the safety-net call."""

    def test_lifespan_source_has_safety_call(self):
        """lifespan function body calls load_envs_into_environ."""
        source = inspect.getsource(_app_module.lifespan)
        assert (
            "load_envs_into_environ()" in source
        ), "lifespan must call load_envs_into_environ() as safety net."

    def test_lifespan_source_checks_flag(self):
        """The safety-net call is gated by _load_envs_defer_enabled."""
        source = inspect.getsource(_app_module.lifespan)
        assert "_load_envs_defer_enabled()" in source, (
            "lifespan must gate load_envs_into_environ behind the "
            "kill-switch."
        )

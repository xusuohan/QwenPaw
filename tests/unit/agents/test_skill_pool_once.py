# -*- coding: utf-8 -*-
"""Tests for §3.5 skill pool initialization once-flag."""
# pylint: disable=protected-access
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

import qwenpaw.agents.skills_manager as sm


@pytest.fixture(autouse=True)
def _reset_once_flag():
    """Reset the process-level once flag before and after each test."""
    sm._skill_pool_initialized = False
    yield
    sm._skill_pool_initialized = False


class TestEnsureSkillPoolOnce:
    def test_first_call_does_io(self, tmp_path: Path, monkeypatch):
        """First call runs full initialization."""
        monkeypatch.setattr(sm, "get_skill_pool_dir", lambda: tmp_path)
        monkeypatch.setattr(
            sm,
            "get_pool_skill_manifest_path",
            lambda: tmp_path / "skill.json",
        )

        with patch.object(sm, "import_builtin_skills") as mock_import:
            result = sm.ensure_skill_pool_initialized()

        assert result is True  # pool was created
        mock_import.assert_called_once()
        assert sm._skill_pool_initialized is True

    def test_second_call_skips_io(self, tmp_path: Path, monkeypatch):
        """Second call returns False immediately (no IO)."""
        monkeypatch.setattr(sm, "get_skill_pool_dir", lambda: tmp_path)
        monkeypatch.setattr(
            sm,
            "get_pool_skill_manifest_path",
            lambda: tmp_path / "skill.json",
        )

        # First call — does IO
        with patch.object(sm, "import_builtin_skills"):
            sm.ensure_skill_pool_initialized()

        # Second call — should skip entirely
        with patch.object(
            sm,
            "get_skill_pool_dir",
            side_effect=AssertionError("should not be called"),
        ):
            result = sm.ensure_skill_pool_initialized()

        assert result is False

    def test_flag_survives_across_calls(self, tmp_path: Path, monkeypatch):
        """Multiple calls: only first does IO."""
        monkeypatch.setattr(sm, "get_skill_pool_dir", lambda: tmp_path)
        monkeypatch.setattr(
            sm,
            "get_pool_skill_manifest_path",
            lambda: tmp_path / "skill.json",
        )

        with patch.object(sm, "import_builtin_skills"):
            sm.ensure_skill_pool_initialized()

        # Subsequent calls don't even check the filesystem
        with patch.object(
            sm,
            "get_skill_pool_dir",
            side_effect=AssertionError("should not be called"),
        ):
            for _ in range(5):
                assert sm.ensure_skill_pool_initialized() is False

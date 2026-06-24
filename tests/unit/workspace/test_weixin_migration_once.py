# -*- coding: utf-8 -*-
"""Tests for §3.5 weixin migration workspace-level once flag."""
# pylint: disable=protected-access
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from qwenpaw.app.workspace.workspace import Workspace


@pytest.fixture(autouse=True)
def _reset_once_flag():
    """Reset the workspace-level once flag before and after each test."""
    Workspace._weixin_migrated_workspaces = set()
    yield
    Workspace._weixin_migrated_workspaces = set()


class TestWeixinMigrationOnce:
    def test_first_call_runs_migration(self, tmp_path: Path):
        """First call for a workspace runs the migration."""
        ws = Workspace.__new__(Workspace)
        ws.agent_id = "test-agent"
        ws.workspace_dir = tmp_path

        with patch(
            "qwenpaw.app.runner.repo.json_repo."
            "migrate_legacy_weixin_chats_file",
        ) as mock_chats, patch(
            "qwenpaw.app.crons.repo.json_repo."
            "migrate_legacy_weixin_jobs_file",
        ) as mock_jobs, patch(
            "qwenpaw.app.runner.session."
            "migrate_legacy_weixin_session_files",
        ) as mock_sessions:
            ws._migrate_legacy_weixin_data()

        mock_chats.assert_called_once()
        mock_jobs.assert_called_once()
        mock_sessions.assert_called_once()
        assert str(tmp_path) in Workspace._weixin_migrated_workspaces

    def test_second_call_skips_migration(self, tmp_path: Path):
        """Second call for same workspace skips migration (no IO)."""
        ws = Workspace.__new__(Workspace)
        ws.agent_id = "test-agent"
        ws.workspace_dir = tmp_path

        # First call — runs migration
        with patch(
            "qwenpaw.app.runner.repo.json_repo."
            "migrate_legacy_weixin_chats_file",
        ), patch(
            "qwenpaw.app.crons.repo.json_repo."
            "migrate_legacy_weixin_jobs_file",
        ), patch(
            "qwenpaw.app.runner.session."
            "migrate_legacy_weixin_session_files",
        ):
            ws._migrate_legacy_weixin_data()

        # Second call — should skip entirely
        with patch(
            "qwenpaw.app.runner.repo.json_repo."
            "migrate_legacy_weixin_chats_file",
            side_effect=AssertionError("should not be called"),
        ):
            ws._migrate_legacy_weixin_data()

    def test_different_workspaces_both_run(self, tmp_path: Path):
        """Different workspace paths each run migration once."""
        ws_a = Workspace.__new__(Workspace)
        ws_a.agent_id = "agent-a"
        ws_a.workspace_dir = tmp_path / "workspace_a"
        ws_a.workspace_dir.mkdir(parents=True)

        ws_b = Workspace.__new__(Workspace)
        ws_b.agent_id = "agent-b"
        ws_b.workspace_dir = tmp_path / "workspace_b"
        ws_b.workspace_dir.mkdir(parents=True)

        calls = {"a": 0, "b": 0}

        def count_a(*_args, **_kwargs):
            calls["a"] += 1

        def count_b(*_args, **_kwargs):
            calls["b"] += 1

        with patch(
            "qwenpaw.app.runner.repo.json_repo."
            "migrate_legacy_weixin_chats_file",
            side_effect=count_a,
        ), patch(
            "qwenpaw.app.crons.repo.json_repo."
            "migrate_legacy_weixin_jobs_file",
        ), patch(
            "qwenpaw.app.runner.session."
            "migrate_legacy_weixin_session_files",
        ):
            ws_a._migrate_legacy_weixin_data()

        with patch(
            "qwenpaw.app.runner.repo.json_repo."
            "migrate_legacy_weixin_chats_file",
            side_effect=count_b,
        ), patch(
            "qwenpaw.app.crons.repo.json_repo."
            "migrate_legacy_weixin_jobs_file",
        ), patch(
            "qwenpaw.app.runner.session."
            "migrate_legacy_weixin_session_files",
        ):
            ws_b._migrate_legacy_weixin_data()

        assert calls["a"] == 1
        assert calls["b"] == 1

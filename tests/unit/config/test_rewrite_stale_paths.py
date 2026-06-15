# -*- coding: utf-8 -*-
"""Tests for rewrite_stale_paths_on_disk()."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from qwenpaw.config.utils import rewrite_stale_paths_on_disk
import qwenpaw.config.utils as utils_module


class TestRewriteStalePathsOnDisk:
    """rewrite_stale_paths_on_disk rewrites stale absolute paths
    to current WORKING_DIR."""

    def test_no_config_file_returns_false(self, tmp_path: Path) -> None:
        """Returns False when config.json doesn't exist."""
        result = rewrite_stale_paths_on_disk(tmp_path / "config.json")
        assert result is False

    def test_current_paths_unchanged(self, tmp_path: Path) -> None:
        """Paths already matching current WORKING_DIR are not modified."""
        config_path = tmp_path / "config.json"
        config_path.write_text(
            json.dumps(
                {
                    "agents": {
                        "profiles": {
                            "default": {
                                "id": "default",
                                "workspace_dir": str(
                                    tmp_path / "workspaces" / "default",
                                ),
                            },
                        },
                    },
                },
            ),
        )
        monkeypatch = pytest.MonkeyPatch()
        with monkeypatch.context() as m:
            m.setattr(utils_module, "WORKING_DIR", tmp_path)
            result = rewrite_stale_paths_on_disk(config_path)
        assert result is False
        data = json.loads(config_path.read_text())
        assert data["agents"]["profiles"]["default"]["workspace_dir"] == str(
            tmp_path / "workspaces" / "default",
        )

    def test_stale_path_rewritten(self, tmp_path: Path) -> None:
        """Stale absolute path from another machine is rewritten."""
        config_path = tmp_path / "config.json"
        config_path.write_text(
            json.dumps(
                {
                    "agents": {
                        "profiles": {
                            "default": {
                                "id": "default",
                                "workspace_dir": (
                                    "/old/machine/data" "/workspaces/default"
                                ),
                            },
                        },
                    },
                },
            ),
        )
        monkeypatch = pytest.MonkeyPatch()
        with monkeypatch.context() as m:
            m.setattr(utils_module, "WORKING_DIR", tmp_path)
            result = rewrite_stale_paths_on_disk(config_path)
        assert result is True
        data = json.loads(config_path.read_text())
        assert data["agents"]["profiles"]["default"]["workspace_dir"] == str(
            tmp_path / "workspaces" / "default",
        )

    def test_stale_media_dir_rewritten(self, tmp_path: Path) -> None:
        """Stale media_dir path is also rewritten."""
        config_path = tmp_path / "config.json"
        config_path.write_text(
            json.dumps(
                {
                    "some_section": {
                        "media_dir": "/old/machine/data/media",
                    },
                },
            ),
        )
        monkeypatch = pytest.MonkeyPatch()
        with monkeypatch.context() as m:
            m.setattr(utils_module, "WORKING_DIR", tmp_path)
            result = rewrite_stale_paths_on_disk(config_path)
        assert result is True
        data = json.loads(config_path.read_text())
        assert data["some_section"]["media_dir"] == str(tmp_path / "media")

    def test_non_path_strings_untouched(self, tmp_path: Path) -> None:
        """Strings that don't contain WORKING_DIR markers are not modified."""
        config_path = tmp_path / "config.json"
        config_path.write_text(
            json.dumps(
                {
                    "agents": {
                        "profiles": {
                            "default": {
                                "id": "default",
                                "workspace_dir": "workspaces/default",
                            },
                        },
                    },
                },
            ),
        )
        monkeypatch = pytest.MonkeyPatch()
        with monkeypatch.context() as m:
            m.setattr(utils_module, "WORKING_DIR", tmp_path)
            result = rewrite_stale_paths_on_disk(config_path)
        assert result is False
        data = json.loads(config_path.read_text())
        assert (
            data["agents"]["profiles"]["default"]["workspace_dir"]
            == "workspaces/default"
        )

    def test_windows_backslash_path_rewritten(self, tmp_path: Path) -> None:
        """Windows-style backslash paths are normalized and rewritten."""
        config_path = tmp_path / "config.json"
        config_path.write_text(
            json.dumps(
                {
                    "agents": {
                        "profiles": {
                            "default": {
                                "id": "default",
                                "workspace_dir": (
                                    "C:\\old\\machine\\data"
                                    "\\workspaces\\default"
                                ),
                            },
                        },
                    },
                },
            ),
        )
        monkeypatch = pytest.MonkeyPatch()
        with monkeypatch.context() as m:
            m.setattr(utils_module, "WORKING_DIR", tmp_path)
            result = rewrite_stale_paths_on_disk(config_path)
        assert result is True
        data = json.loads(config_path.read_text())
        assert data["agents"]["profiles"]["default"]["workspace_dir"] == str(
            tmp_path / "workspaces" / "default",
        )

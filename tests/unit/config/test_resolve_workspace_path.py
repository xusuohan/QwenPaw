# -*- coding: utf-8 -*-
"""Tests for resolve_workspace_path() helper."""
from __future__ import annotations

from pathlib import Path

import pytest

from qwenpaw.config.utils import resolve_workspace_path
import qwenpaw.config.utils as utils_module


class TestResolveWorkspacePath:
    """resolve_workspace_path resolves relative / absolute / tilde paths."""

    def test_relative_path_resolved_against_working_dir(
        self,
        tmp_path: Path,
    ) -> None:
        """Relative paths are resolved against WORKING_DIR."""
        monkeypatch = pytest.MonkeyPatch()
        with monkeypatch.context() as m:
            m.setattr(utils_module, "WORKING_DIR", tmp_path)
            result = resolve_workspace_path("workspaces/default")
            assert result == tmp_path / "workspaces" / "default"

    def test_absolute_path_returned_as_is_with_expanduser(self) -> None:
        """Absolute paths are returned unchanged (with expanduser applied)."""
        abs_path = "/some/absolute/path"
        result = resolve_workspace_path(abs_path)
        assert result == Path(abs_path)

    def test_tilde_path_expanded(self) -> None:
        """Paths starting with ~ are expanded to the user's home directory."""
        result = resolve_workspace_path("~/my_workspace")
        expected = Path("~/my_workspace").expanduser()
        assert result == expected
        assert result.is_absolute()

# -*- coding: utf-8 -*-
"""Tests for the workspace_dir_abs property on config models.

``workspace_dir`` stores a portable (possibly relative) value so config.json
stays relocatable; ``workspace_dir_abs`` is the canonical accessor for
filesystem access — it anchors relative paths to WORKING_DIR, matching
``resolve_workspace_path()``. The raw field must stay unchanged and the
property must not leak into serialization (config.json must stay portable).
"""
from __future__ import annotations

from pathlib import Path

import pytest

import qwenpaw.config.utils as utils_module
from qwenpaw.config.config import AgentProfileConfig, AgentProfileRef


class TestWorkspaceDirAbs:
    """workspace_dir_abs resolves relative/absolute paths correctly."""

    def test_ref_relative_anchored_to_working_dir(
        self,
        tmp_path: Path,
    ) -> None:
        ref = AgentProfileRef(
            id="x",
            workspace_dir="workspaces/x",
        )
        with pytest.MonkeyPatch().context() as mp:
            mp.setattr(utils_module, "WORKING_DIR", tmp_path)
            assert ref.workspace_dir_abs == tmp_path / "workspaces" / "x"
        # raw storage unchanged (portable)
        assert ref.workspace_dir == "workspaces/x"

    def test_ref_absolute_returned_as_is(self, tmp_path: Path) -> None:
        absolute = tmp_path / "elsewhere" / "x"
        ref = AgentProfileRef(id="x", workspace_dir=str(absolute))
        assert ref.workspace_dir_abs == absolute

    def test_config_relative_anchored_to_working_dir(
        self,
        tmp_path: Path,
    ) -> None:
        cfg = AgentProfileConfig(
            id="x",
            name="X",
            workspace_dir="workspaces/x",
        )
        with pytest.MonkeyPatch().context() as mp:
            mp.setattr(utils_module, "WORKING_DIR", tmp_path)
            assert cfg.workspace_dir_abs == tmp_path / "workspaces" / "x"

    def test_config_empty_falls_back_to_working_dir(
        self,
        tmp_path: Path,
    ) -> None:
        cfg = AgentProfileConfig(id="x", name="X", workspace_dir="")
        with pytest.MonkeyPatch().context() as mp:
            mp.setattr(utils_module, "WORKING_DIR", tmp_path)
            assert cfg.workspace_dir_abs == tmp_path

    def test_property_excluded_from_serialization(self) -> None:
        """config.json must keep storing the portable raw value."""
        ref = AgentProfileRef(
            id="x",
            workspace_dir="workspaces/x",
        )
        dumped = ref.model_dump()
        assert "workspace_dir_abs" not in dumped
        assert dumped["workspace_dir"] == "workspaces/x"

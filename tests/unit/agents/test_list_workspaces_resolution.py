# -*- coding: utf-8 -*-
"""Regression test for list_workspaces() path resolution.

list_workspaces() must resolve a profile's ``workspace_dir`` against
``WORKING_DIR`` before returning it — the same contract as
``resolve_workspace_path()`` and ``Workspace.__init__``. Storing a relative
path (e.g. ``workspaces/fashion-commerce-img-agent``) is intentional for a
portable config, but it must be anchored to ``WORKING_DIR`` at read time.

Previously list_workspaces() returned the raw relative path, which propagated
through ``_workspace_dir_for_agent`` -> ``download_to_workspace`` ->
``target_dir.parent.mkdir(...)``. ``mkdir`` resolves a relative path against
the process CWD, and in a packaged macOS app the CWD is read-only (``/``),
producing ``OSError: [Errno 30] Read-only file system: 'workspaces'``.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

import qwenpaw.config.utils as utils_module
from qwenpaw.agents.skills_manager import list_workspaces


def test_list_workspaces_resolves_relative_workspace_dir(
    tmp_path: Path,
) -> None:
    """A relative ``workspace_dir`` must resolve to an absolute path."""
    agent_id = "fashion-commerce-img-agent"
    profile = SimpleNamespace(
        id=agent_id,
        workspace_dir=f"workspaces/{agent_id}",
    )
    config = SimpleNamespace(
        agents=SimpleNamespace(profiles={agent_id: profile}),
    )
    agent_cfg = SimpleNamespace(name="Fashion Commerce")

    with pytest.MonkeyPatch().context() as mp:
        mp.setattr(utils_module, "WORKING_DIR", tmp_path)
        with (
            patch("qwenpaw.config.utils.load_config", return_value=config),
            patch(
                "qwenpaw.config.config.load_agent_config",
                return_value=agent_cfg,
            ),
        ):
            workspaces = list_workspaces()

    ws = next(w for w in workspaces if w["agent_id"] == agent_id)
    resolved = Path(ws["workspace_dir"])
    assert (
        resolved.is_absolute()
    ), f"workspace_dir must be absolute, got {resolved!r}"
    assert resolved == tmp_path / "workspaces" / agent_id


def test_list_workspaces_keeps_absolute_workspace_dir(
    tmp_path: Path,
) -> None:
    """An already-absolute ``workspace_dir`` must be returned unchanged."""
    agent_id = "external-agent"
    absolute_dir = tmp_path / "elsewhere" / agent_id
    profile = SimpleNamespace(id=agent_id, workspace_dir=str(absolute_dir))
    config = SimpleNamespace(
        agents=SimpleNamespace(profiles={agent_id: profile}),
    )
    agent_cfg = SimpleNamespace(name="External")

    with pytest.MonkeyPatch().context() as mp:
        mp.setattr(utils_module, "WORKING_DIR", tmp_path)
        with (
            patch("qwenpaw.config.utils.load_config", return_value=config),
            patch(
                "qwenpaw.config.config.load_agent_config",
                return_value=agent_cfg,
            ),
        ):
            workspaces = list_workspaces()

    ws = next(w for w in workspaces if w["agent_id"] == agent_id)
    assert Path(ws["workspace_dir"]) == absolute_dir

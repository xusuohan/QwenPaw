# -*- coding: utf-8 -*-
"""``qwenpaw fix-paths`` — rewrite stale paths in config and agent files."""
from __future__ import annotations

from pathlib import Path

import click


@click.command(
    "fix-paths",
    help="Rewrite stale paths in config.json and agent.json to current paths.",
)
@click.option(
    "--config-path",
    type=click.Path(),
    default=None,
    help="Path to config.json (default: auto-detect from WORKING_DIR).",
)
def fix_paths_cmd(config_path: str | None) -> None:
    """Rewrite stale absolute paths in config and agent files."""
    from qwenpaw.config.utils import (
        rewrite_stale_agent_json_on_disk,
        rewrite_stale_paths_on_disk,
    )

    path = Path(config_path) if config_path else None
    config_changed = rewrite_stale_paths_on_disk(path)
    agent_count = rewrite_stale_agent_json_on_disk()
    if config_changed or agent_count:
        parts = []
        if config_changed:
            parts.append("config.json")
        if agent_count:
            parts.append(f"{agent_count} 个 agent.json")
        click.echo(f"已重写陈旧路径: {', '.join(parts)}")

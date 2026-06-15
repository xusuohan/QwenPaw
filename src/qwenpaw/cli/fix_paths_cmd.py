# -*- coding: utf-8 -*-
"""``qwenpaw fix-paths`` — rewrite stale paths in config.json."""
from __future__ import annotations

from pathlib import Path

import click


@click.command(
    "fix-paths",
    help="Rewrite stale paths in config.json to current paths.",
)
@click.option(
    "--config-path",
    type=click.Path(),
    default=None,
    help="Path to config.json (default: auto-detect from WORKING_DIR).",
)
def fix_paths_cmd(config_path: str | None) -> None:
    """Rewrite stale absolute paths in config.json."""
    from qwenpaw.config.utils import rewrite_stale_paths_on_disk

    path = Path(config_path) if config_path else None
    changed = rewrite_stale_paths_on_disk(path)
    if changed:
        click.echo("已重写陈旧路径。")

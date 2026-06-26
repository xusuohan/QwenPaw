# -*- coding: utf-8 -*-
"""Fast portable launcher — runs init + fix-paths in a single Python process."""
from __future__ import annotations

import os
import sys
from pathlib import Path


def main() -> None:
    usb_root = Path(__file__).resolve().parent.parent  # windows/ -> root
    working_dir = usb_root / "data"

    # Ensure data directories exist
    for sub in ("", ".secret", ".backups"):
        (working_dir / sub).mkdir(parents=True, exist_ok=True)

    # Set env vars for downstream
    os.environ["QWENPAW_WORKING_DIR"] = str(working_dir)
    os.environ["QWENPAW_SECRET_DIR"] = str(working_dir / ".secret")
    os.environ["QWENPAW_BACKUP_DIR"] = str(working_dir / ".backups")

    # Auto-init config if missing
    config_path = working_dir / "config.json"
    if not config_path.exists():
        from qwenpaw.cli.main import cli
        sys.argv = ["qwenpaw", "init", "--defaults", "--accept-security"]
        cli(standalone_mode=False)

    # Fix stale paths
    from qwenpaw.config.utils import (
        rewrite_stale_agent_json_on_disk,
        rewrite_stale_paths_on_disk,
    )
    rewrite_stale_paths_on_disk()
    rewrite_stale_agent_json_on_disk()


if __name__ == "__main__":
    main()

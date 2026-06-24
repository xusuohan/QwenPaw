#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Build profiling tool — stage timing + structured JSON report.

Usage as library:
    from build_profiler import BuildProfiler
    profiler = BuildProfiler(platform="macOS-x86_64", python_version="3.10.14")
    with profiler.stage("conda_create"):
        ...
    profiler.save("dist/build_profiling.json")

Usage as CLI (for shell/PowerShell scripts):
    python build_profiler.py start <stage> [--state-file path]
    python build_profiler.py end <stage> [--state-file path]
    python build_profiler.py save <output> [--state-file path]
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Generator

DEFAULT_STATE_FILE = Path("dist/.build_profiler_state.json")


class BuildProfiler:
    """Records timing for named build stages and produces a JSON report."""

    def __init__(
        self,
        *,
        platform: str = "unknown",
        python_version: str = "unknown",
        wheel_hash: str | None = None,
        cache_hit: bool = False,
    ) -> None:
        self._platform = platform
        self._python_version = python_version
        self._wheel_hash = wheel_hash
        self._cache_hit = cache_hit
        self._stages: list[dict[str, Any]] = []
        self._global_start = time.monotonic()

    @contextmanager
    def stage(self, name: str) -> Generator[None, None, None]:
        """Context manager that times a named build stage (in-process, uses monotonic)."""
        start_ts = time.monotonic()
        exit_code = 0
        try:
            yield
        except BaseException:
            exit_code = 1
            raise
        finally:
            end_ts = time.monotonic()
            self._stages.append({
                "name": name,
                "start_ts": start_ts,
                "end_ts": end_ts,
                "duration_s": round(end_ts - start_ts, 3),
                "exit_code": exit_code,
            })

    def begin_stage(self, name: str) -> None:
        """Open a new stage entry with a wall-clock start timestamp.

        Intended for CLI / cross-process use.  For in-process usage prefer
        the :meth:`stage` context manager.
        """
        self._stages.append({
            "name": name,
            "start_ts": time.time(),
            "end_ts": None,
            "duration_s": None,
            "exit_code": 0,
        })

    def finish_stage(self, name: str) -> bool:
        """Close the last open stage matching *name*.

        Returns ``True`` if a matching open stage was found and closed,
        ``False`` otherwise (a warning is printed to stderr).
        """
        for stage in reversed(self._stages):
            if stage["name"] == name and stage["end_ts"] is None:
                now = time.time()
                stage["end_ts"] = now
                stage["duration_s"] = round(now - stage["start_ts"], 3)
                return True
        print(
            f"WARNING: no open stage named {name!r} to finish",
            file=sys.stderr,
        )
        return False

    def report(self) -> dict[str, Any]:
        """Generate the full profiling report as a dict."""
        total = time.monotonic() - self._global_start
        return {
            "platform": self._platform,
            "python_version": self._python_version,
            "wheel_hash": self._wheel_hash,
            "cache_hit": self._cache_hit,
            "stages": list(self._stages),
            "total_duration_s": round(total, 3),
        }

    def save(self, path: str | Path) -> None:
        """Write the report as JSON to *path*."""
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(
            json.dumps(self.report(), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    # -- Persistence helpers for CLI mode (shell/PowerShell interop) --

    def dump_state(self, path: str | Path) -> None:
        """Serialize internal state to JSON for cross-process continuation.

        Uses ``time.time()`` for *global_start* so that the value is
        comparable across process boundaries (important on Windows where
        ``time.monotonic()`` is **not** guaranteed to be consistent across
        processes).
        """
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(
            json.dumps(
                {
                    "platform": self._platform,
                    "python_version": self._python_version,
                    "wheel_hash": self._wheel_hash,
                    "cache_hit": self._cache_hit,
                    "global_start": time.time(),
                    "stages": list(self._stages),
                },
                indent=2,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

    @classmethod
    def load_state(cls, path: str | Path) -> BuildProfiler:
        """Restore a profiler from a previously saved state file."""
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        prof = cls(
            platform=data["platform"],
            python_version=data["python_version"],
            wheel_hash=data.get("wheel_hash"),
            cache_hit=data.get("cache_hit", False),
        )
        prof._global_start = data["global_start"]
        prof._stages = data["stages"]
        return prof


def _cli_main() -> None:  # pylint: disable=too-many-branches
    """CLI entry point for shell/PowerShell integration."""
    parser = argparse.ArgumentParser(description="Build profiler CLI")
    parser.add_argument(
        "command",
        choices=["start", "end", "save"],
        help="start/end a stage, or save the final report",
    )
    parser.add_argument("name", help="Stage name (for start/end) or output path (for save)")
    parser.add_argument(
        "--state-file",
        default=str(DEFAULT_STATE_FILE),
        help="Path to intermediate state file (default: dist/.build_profiler_state.json)",
    )
    parser.add_argument("--platform", default="unknown")
    parser.add_argument("--python-version", default="unknown")
    args = parser.parse_args()

    state_path = Path(args.state_file)

    if args.command == "start":
        if state_path.exists():
            prof = BuildProfiler.load_state(state_path)
        else:
            prof = BuildProfiler(platform=args.platform, python_version=args.python_version)
        prof.begin_stage(args.name)
        prof.dump_state(state_path)
    elif args.command == "end":
        prof = BuildProfiler.load_state(state_path)
        if not prof.finish_stage(args.name):
            raise SystemExit(1)
        prof.dump_state(state_path)
    elif args.command == "save":
        prof = BuildProfiler.load_state(state_path)
        prof.save(args.name)
        print(f"Profiling report saved to {args.name}")


if __name__ == "__main__":
    _cli_main()

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Analyze package sizes in a conda-packed environment directory.

Usage:
    python analyze_packages.py <env_dir> [--output report.json]

Produces a JSON report with per-package sizes and file-type breakdown.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from operator import itemgetter
from pathlib import Path
from typing import Any


def analyze_directory(env_dir: Path) -> dict[str, Any]:
    """Analyze disk usage of an unpacked conda environment.

    Returns dict with:
        - total_bytes: total size
        - top_dirs: list of {name, bytes} sorted by size desc
        - file_types: dict of ext -> {count, bytes}
    """
    total_bytes = 0
    dir_sizes: dict[str, int] = {}
    file_types: dict[str, dict[str, int]] = {}

    for root, _dirs, files in os.walk(env_dir):
        for f in files:
            fp = Path(root) / f
            try:
                size = fp.stat().st_size
            except OSError:
                continue
            total_bytes += size

            ext = fp.suffix.lower() or "(no ext)"
            if ext not in file_types:
                file_types[ext] = {"count": 0, "bytes": 0}
            file_types[ext]["count"] += 1
            file_types[ext]["bytes"] += size

            rel = fp.relative_to(env_dir)
            parts = rel.parts
            if len(parts) > 1:
                top_key = "/".join(parts[:4]) if len(parts) >= 4 else parts[0]
            else:
                top_key = parts[0]
            dir_sizes[top_key] = dir_sizes.get(top_key, 0) + size

    top_dirs = sorted(
        [{"name": k, "bytes": v} for k, v in dir_sizes.items()],
        key=itemgetter("bytes"),
        reverse=True,
    )

    return {
        "total_bytes": total_bytes,
        "top_dirs": top_dirs[:50],
        "file_types": file_types,
    }


def format_report(analysis: dict[str, Any], *, platform: str = "unknown") -> dict[str, Any]:
    """Format analysis into a human-readable report dict."""
    total_mb = round(analysis["total_bytes"] / (1024 * 1024), 1)
    packages = []
    for d in analysis["top_dirs"][:30]:
        packages.append({
            "path": d["name"],
            "size_mb": round(d["bytes"] / (1024 * 1024), 1),
        })
    file_types = {}
    for ext, info in sorted(
        analysis["file_types"].items(),
        key=lambda x: x[1]["bytes"],  # type: ignore[call-overload]
        reverse=True,
    ):
        file_types[ext] = {
            "count": info["count"],
            "total_mb": round(info["bytes"] / (1024 * 1024), 1),
        }
    return {
        "platform": platform,
        "total_size_mb": total_mb,
        "packages": packages,
        "file_types": file_types,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Analyze conda env package sizes")
    parser.add_argument("env_dir", help="Path to unpacked conda environment")
    parser.add_argument("--output", "-o", default=None, help="Output JSON report path")
    parser.add_argument("--platform", default="unknown")
    args = parser.parse_args()

    env_dir = Path(args.env_dir)
    if not env_dir.is_dir():
        print(f"Error: {env_dir} is not a directory", file=sys.stderr)
        return 1

    print(f"Analyzing {env_dir} ...")
    analysis = analyze_directory(env_dir)
    report = format_report(analysis, platform=args.platform)

    print(f"\nTotal size: {report['total_size_mb']} MB")
    print("\nTop packages by size:")
    for p in report["packages"][:15]:
        print(f"  {p['size_mb']:>8.1f} MB  {p['path']}")
    print("\nFile type breakdown:")
    for ext, info in list(report["file_types"].items())[:10]:
        print(f"  {ext:>10s}: {info['count']:>6d} files, {info['total_mb']:>8.1f} MB")

    if args.output:
        out = Path(args.output)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"\nReport saved to {out}")

    return 0


if __name__ == "__main__":
    sys.exit(main())

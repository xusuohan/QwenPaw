# -*- coding: utf-8 -*-
# pylint: disable=wrong-import-position
"""Tests for scripts/pack/analyze_packages.py."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(
    0,
    str(Path(__file__).resolve().parents[3] / "scripts" / "pack"),
)

from analyze_packages import analyze_directory, format_report  # noqa: E402


class TestAnalyzePackages:
    """Unit tests for package size analysis."""

    def test_analyze_directory_sizes(self, tmp_path: Path) -> None:
        """analyze_directory returns correct size breakdown."""
        pkg_dir = tmp_path / "lib" / "python3.10" / "site-packages" / "numpy"
        pkg_dir.mkdir(parents=True)
        (pkg_dir / "__init__.py").write_text("# numpy\n" * 100)
        (pkg_dir / "core.so").write_bytes(b"\x00" * 5000)

        result = analyze_directory(tmp_path)
        assert result["total_bytes"] > 0
        assert len(result["top_dirs"]) > 0

    def test_format_report_json(self) -> None:
        """format_report produces valid JSON with expected fields."""
        analysis = {
            "total_bytes": 1024 * 1024,
            "top_dirs": [{"name": "numpy", "bytes": 512 * 1024}],
            "file_types": {".py": {"count": 10, "bytes": 100 * 1024}},
        }
        report = format_report(analysis, platform="test")
        assert report["total_size_mb"] == 1.0
        assert len(report["packages"]) > 0
        assert "file_types" in report

    def test_empty_directory(self, tmp_path: Path) -> None:
        """Empty directory produces zero-size report."""
        result = analyze_directory(tmp_path)
        assert result["total_bytes"] == 0

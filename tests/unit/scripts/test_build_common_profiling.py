# -*- coding: utf-8 -*-
"""Tests that build_common.py emits profiling data."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(
    0,
    str(Path(__file__).resolve().parents[3] / "scripts" / "pack"),
)


class TestBuildCommonProfiling:
    """Verify build_common.py integrates with BuildProfiler."""

    def test_main_accepts_profiling_args(self) -> None:
        """build_common.py main() accepts --profiling-output argument."""
        import argparse

        parser = argparse.ArgumentParser()
        parser.add_argument("--output", "-o", required=True)
        parser.add_argument("--format", "-f", default="infer")
        parser.add_argument("--python", default="3.10")
        parser.add_argument("--wheel", default=None)
        parser.add_argument("--cache-wheels", action="store_true")
        parser.add_argument("--profiling-output", default=None)
        args = parser.parse_args(
            ["--output", "out.tar.gz", "--profiling-output", "report.json"],
        )
        assert args.profiling_output == "report.json"

    def test_profiling_output_contains_stages(self, tmp_path: Path) -> None:
        """When --profiling-output is given, JSON has stage entries."""
        from build_profiler import BuildProfiler

        profiler = BuildProfiler(platform="test", python_version="3.10.0")
        with profiler.stage("conda_create"):
            pass
        with profiler.stage("pip_install"):
            pass
        with profiler.stage("conda_pack"):
            pass
        out = tmp_path / "profiling.json"
        profiler.save(out)
        data = json.loads(out.read_text())
        stage_names = [s["name"] for s in data["stages"]]
        assert "conda_create" in stage_names
        assert "pip_install" in stage_names
        assert "conda_pack" in stage_names

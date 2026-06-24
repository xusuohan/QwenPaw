# -*- coding: utf-8 -*-
# pylint: disable=wrong-import-position
"""Tests for scripts/pack/build_profiler.py."""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(
    0,
    str(Path(__file__).resolve().parents[3] / "scripts" / "pack"),
)

from build_profiler import BuildProfiler  # noqa: E402


class TestBuildProfiler:
    """Unit tests for BuildProfiler class."""

    def test_stage_context_manager_records_timing(
        self,
        tmp_path: Path,
    ) -> None:
        """stage() context manager records start/end/duration."""
        _ = tmp_path
        profiler = BuildProfiler(platform="test", python_version="3.10.0")
        with profiler.stage("test_stage"):
            time.sleep(0.05)
        report = profiler.report()
        assert len(report["stages"]) == 1
        stage = report["stages"][0]
        assert stage["name"] == "test_stage"
        assert stage["duration_s"] >= 0.04
        assert stage["start_ts"] < stage["end_ts"]

    def test_multiple_stages_in_order(self, tmp_path: Path) -> None:
        """Multiple stages are recorded in execution order."""
        _ = tmp_path
        profiler = BuildProfiler(platform="test", python_version="3.10.0")
        with profiler.stage("first"):
            pass
        with profiler.stage("second"):
            pass
        report = profiler.report()
        names = [s["name"] for s in report["stages"]]
        assert names == ["first", "second"]

    def test_stage_records_exception(self, tmp_path: Path) -> None:
        """Stage records exit_code=1 when exception occurs."""
        _ = tmp_path
        profiler = BuildProfiler(platform="test", python_version="3.10.0")
        with pytest.raises(ValueError):
            with profiler.stage("fail_stage"):
                raise ValueError("boom")
        stage = profiler.report()["stages"][0]
        assert stage["exit_code"] == 1

    def test_save_writes_json(self, tmp_path: Path) -> None:
        """save() writes valid JSON report to file."""
        profiler = BuildProfiler(
            platform="macOS-x86_64",
            python_version="3.10.14",
        )
        with profiler.stage("s1"):
            pass
        out = tmp_path / "report.json"
        profiler.save(out)
        data = json.loads(out.read_text(encoding="utf-8"))
        assert data["platform"] == "macOS-x86_64"
        assert data["python_version"] == "3.10.14"
        assert len(data["stages"]) == 1
        assert "total_duration_s" in data

    def test_report_metadata(self, tmp_path: Path) -> None:
        """report() includes platform, python_version, wheel_hash."""
        _ = tmp_path
        profiler = BuildProfiler(
            platform="Linux-x86_64",
            python_version="3.10.12",
            wheel_hash="abc123",
            cache_hit=True,
        )
        report = profiler.report()
        assert report["platform"] == "Linux-x86_64"
        assert report["python_version"] == "3.10.12"
        assert report["wheel_hash"] == "abc123"
        assert report["cache_hit"] is True

    def test_total_duration_covers_all_stages(self, tmp_path: Path) -> None:
        """total_duration_s >= sum of individual stage durations."""
        _ = tmp_path
        profiler = BuildProfiler(platform="test", python_version="3.10.0")
        with profiler.stage("a"):
            time.sleep(0.02)
        with profiler.stage("b"):
            time.sleep(0.02)
        report = profiler.report()
        sum_stages = sum(s["duration_s"] for s in report["stages"])
        assert report["total_duration_s"] >= sum_stages

    def test_cli_start_end_save(self, tmp_path: Path) -> None:
        """CLI interface: start/end/save subcommands work via argv."""
        import subprocess

        script = (
            Path(__file__).resolve().parents[3]
            / "scripts"
            / "pack"
            / "build_profiler.py"
        )
        env_data = tmp_path / "env.json"

        # start stage
        subprocess.check_call(
            [
                sys.executable,
                str(script),
                "start",
                "my_stage",
                "--state-file",
                str(env_data),
            ],
        )
        time.sleep(0.05)
        # end stage
        subprocess.check_call(
            [
                sys.executable,
                str(script),
                "end",
                "my_stage",
                "--state-file",
                str(env_data),
            ],
        )
        # save
        out = tmp_path / "out.json"
        subprocess.check_call(
            [
                sys.executable,
                str(script),
                "save",
                str(out),
                "--state-file",
                str(env_data),
            ],
        )
        data = json.loads(out.read_text(encoding="utf-8"))
        assert len(data["stages"]) == 1
        assert data["stages"][0]["name"] == "my_stage"
        assert data["stages"][0]["duration_s"] >= 0.04

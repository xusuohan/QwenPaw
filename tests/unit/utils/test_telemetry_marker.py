# -*- coding: utf-8 -*-
"""Tests for telemetry marker atomic RMW."""
from __future__ import annotations

import threading

import qwenpaw.utils.telemetry as t
from qwenpaw.utils.telemetry import mark_telemetry_collected


class TestMarkTelemetryCollected:
    def test_concurrent_marks_no_lost_version(self, tmp_path, monkeypatch):
        # Pin the version so concurrent callers target the same marker.
        monkeypatch.setattr(t, "_get_current_version", lambda: "9.9.9")
        n = 20

        def bump():
            mark_telemetry_collected(tmp_path)

        threads = [threading.Thread(target=bump) for _ in range(n)]
        for th in threads:
            th.start()
        for th in threads:
            th.join()

        import json

        data = json.loads((tmp_path / ".telemetry_collected").read_text())
        assert "9.9.9" in data["collected_versions"]
        assert not list(tmp_path.glob("*.tmp.*"))

# -*- coding: utf-8 -*-
"""Unit tests for crons JsonJobRepository atomic save."""
from __future__ import annotations

from qwenpaw.app.crons.models import JobsFile
from qwenpaw.app.crons.repo.json_repo import JsonJobRepository


class TestJsonJobRepositorySave:
    """save writes valid JSON atomically (no tmp leftover)."""

    async def test_save_roundtrip_and_no_tmp_leftover(self, tmp_path):
        # JobsFile() defaults: version=1, jobs=[] (both have defaults in
        # src/qwenpaw/app/crons/models.py), so no CronJobSpec needed.
        # The point is to exercise the atomic save path end to end.
        path = tmp_path / "jobs.json"
        repo = JsonJobRepository(path)
        await repo.save(JobsFile())
        assert path.exists()
        assert not list(tmp_path.glob("*.tmp.*"))
        loaded = await repo.load()
        assert loaded.version == 1
        assert loaded.jobs == []

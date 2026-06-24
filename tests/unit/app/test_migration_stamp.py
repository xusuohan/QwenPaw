# -*- coding: utf-8 -*-
"""Tests for migration stamp helpers (§3.2)."""
# pylint: disable=redefined-outer-name
from __future__ import annotations

import json

import pytest

import qwenpaw.app.migration as migration_mod
from qwenpaw.app.migration import (
    _migration_stamp_enabled,
    _should_run_migrations,
    _write_migration_stamp,
)


@pytest.fixture()
def stamp_dir(tmp_path, monkeypatch):
    """Redirect WORKING_DIR to a temp dir for stamp tests."""
    monkeypatch.setattr(migration_mod, "WORKING_DIR", tmp_path)
    return tmp_path


class TestMigrationStampEnabled:
    def test_default_on(self, monkeypatch):
        monkeypatch.delenv("QWENPAW_PERF_MIGRATION_STAMP", raising=False)
        assert _migration_stamp_enabled() is True

    @pytest.mark.parametrize("val", ["0", "false", "no", "off"])
    def test_disabled(self, monkeypatch, val):
        monkeypatch.setenv("QWENPAW_PERF_MIGRATION_STAMP", val)
        assert _migration_stamp_enabled() is False

    @pytest.mark.parametrize("val", ["1", "true", "yes", "on", "anything"])
    def test_enabled_explicit(self, monkeypatch, val):
        monkeypatch.setenv("QWENPAW_PERF_MIGRATION_STAMP", val)
        assert _migration_stamp_enabled() is True


class TestShouldRunMigrations:
    def test_flag_off_always_runs(self, monkeypatch, stamp_dir):
        """Flag disabled → always run, regardless of stamp."""
        monkeypatch.setenv("QWENPAW_PERF_MIGRATION_STAMP", "0")
        # Write a matching stamp — should still return True
        stamp_path = stamp_dir / ".migration_stamp"
        stamp_path.write_text(
            json.dumps({"version": "999.0.0", "ts": "2026-01-01"}),
        )
        assert _should_run_migrations() is True

    def test_no_stamp_runs(
        self,
        stamp_dir,  # pylint: disable=unused-argument
    ):
        """No stamp file → run migrations.

        stamp_dir fixture needed for WORKING_DIR monkeypatch.
        """
        assert _should_run_migrations() is True

    def test_corrupt_stamp_runs(self, stamp_dir):
        """Corrupt stamp → run migrations (safe default)."""
        stamp_path = stamp_dir / ".migration_stamp"
        stamp_path.write_text("not valid json {{{")
        assert _should_run_migrations() is True

    def test_matching_version_skips(self, monkeypatch, stamp_dir):
        """Stamp version matches current → skip migrations."""
        monkeypatch.setattr(
            migration_mod,
            "_get_current_version",
            lambda: "1.2.3",
        )
        stamp_path = stamp_dir / ".migration_stamp"
        stamp_path.write_text(
            json.dumps({"version": "1.2.3", "ts": "2026-06-24"}),
        )
        assert _should_run_migrations() is False

    def test_version_mismatch_runs(self, monkeypatch, stamp_dir):
        """Stamp version differs from current → run migrations."""
        monkeypatch.setattr(
            migration_mod,
            "_get_current_version",
            lambda: "2.0.0",
        )
        stamp_path = stamp_dir / ".migration_stamp"
        stamp_path.write_text(
            json.dumps({"version": "1.0.0", "ts": "2026-06-24"}),
        )
        assert _should_run_migrations() is True

    def test_missing_version_field_runs(self, stamp_dir):
        """Stamp JSON missing 'version' key → run migrations."""
        stamp_path = stamp_dir / ".migration_stamp"
        stamp_path.write_text(json.dumps({"ts": "2026-06-24"}))
        assert _should_run_migrations() is True


class TestWriteMigrationStamp:
    def test_writes_valid_json(self, monkeypatch, stamp_dir):
        monkeypatch.setattr(
            migration_mod,
            "_get_current_version",
            lambda: "1.2.3",
        )
        _write_migration_stamp()

        stamp_path = stamp_dir / ".migration_stamp"
        assert stamp_path.exists()

        data = json.loads(stamp_path.read_text())
        assert data["version"] == "1.2.3"
        assert "ts" in data

    def test_overwrites_existing(self, monkeypatch, stamp_dir):
        monkeypatch.setattr(
            migration_mod,
            "_get_current_version",
            lambda: "3.0.0",
        )
        # Write an old stamp first
        stamp_path = stamp_dir / ".migration_stamp"
        stamp_path.write_text(
            json.dumps({"version": "1.0.0", "ts": "old"}),
        )

        _write_migration_stamp()

        data = json.loads(stamp_path.read_text())
        assert data["version"] == "3.0.0"
        assert data["ts"] != "old"


class TestRunMigrations:
    """Integration tests for run_migrations() wrapper."""

    def test_stamp_match_skips_legacy_runs_ensure(
        self,
        monkeypatch,
        stamp_dir,
    ):
        """Stamp matches → legacy migrations skipped, ensure fns run."""
        from qwenpaw.app.migration import run_migrations

        monkeypatch.setattr(
            migration_mod,
            "_get_current_version",
            lambda: "1.2.3",
        )
        # Write matching stamp
        stamp_path = stamp_dir / ".migration_stamp"
        stamp_path.write_text(
            json.dumps({"version": "1.2.3", "ts": "2026-06-24"}),
        )

        calls = []
        monkeypatch.setattr(
            migration_mod,
            "migrate_legacy_workspace_to_default_agent",
            lambda: calls.append("workspace"),
        )
        monkeypatch.setattr(
            migration_mod,
            "migrate_legacy_skills_to_skill_pool",
            lambda: calls.append("skills"),
        )
        monkeypatch.setattr(
            migration_mod,
            "ensure_default_agent_exists",
            lambda: calls.append("ensure_default"),
        )
        monkeypatch.setattr(
            migration_mod,
            "ensure_qa_agent_exists",
            lambda: calls.append("ensure_qa"),
        )

        run_migrations()

        assert calls == ["ensure_default", "ensure_qa"]

    def test_stamp_missing_runs_all_and_writes_stamp(
        self,
        monkeypatch,
        stamp_dir,
    ):
        """No stamp → all migrations run + stamp written."""
        from qwenpaw.app.migration import run_migrations

        monkeypatch.setattr(
            migration_mod,
            "_get_current_version",
            lambda: "2.0.0",
        )

        calls = []
        monkeypatch.setattr(
            migration_mod,
            "migrate_legacy_workspace_to_default_agent",
            lambda: calls.append("workspace"),
        )
        monkeypatch.setattr(
            migration_mod,
            "migrate_legacy_skills_to_skill_pool",
            lambda: calls.append("skills"),
        )
        monkeypatch.setattr(
            migration_mod,
            "ensure_default_agent_exists",
            lambda: calls.append("ensure_default"),
        )
        monkeypatch.setattr(
            migration_mod,
            "ensure_qa_agent_exists",
            lambda: calls.append("ensure_qa"),
        )

        run_migrations()

        assert calls == [
            "workspace",
            "skills",
            "ensure_default",
            "ensure_qa",
        ]
        # Stamp should be written
        stamp_path = stamp_dir / ".migration_stamp"
        assert stamp_path.exists()
        data = json.loads(stamp_path.read_text())
        assert data["version"] == "2.0.0"

    def test_flag_off_runs_all_and_writes_stamp(
        self,
        monkeypatch,
        stamp_dir,
    ):
        """Flag OFF → all run (stamp bypass), stamp still written."""
        from qwenpaw.app.migration import run_migrations

        monkeypatch.setenv("QWENPAW_PERF_MIGRATION_STAMP", "0")
        monkeypatch.setattr(
            migration_mod,
            "_get_current_version",
            lambda: "1.0.0",
        )

        calls = []
        monkeypatch.setattr(
            migration_mod,
            "migrate_legacy_workspace_to_default_agent",
            lambda: calls.append("workspace"),
        )
        monkeypatch.setattr(
            migration_mod,
            "migrate_legacy_skills_to_skill_pool",
            lambda: calls.append("skills"),
        )
        monkeypatch.setattr(
            migration_mod,
            "ensure_default_agent_exists",
            lambda: calls.append("ensure_default"),
        )
        monkeypatch.setattr(
            migration_mod,
            "ensure_qa_agent_exists",
            lambda: calls.append("ensure_qa"),
        )

        run_migrations()

        assert calls == [
            "workspace",
            "skills",
            "ensure_default",
            "ensure_qa",
        ]
        # Stamp is still written (flag controls read, not write)
        stamp_path = stamp_dir / ".migration_stamp"
        assert stamp_path.exists()

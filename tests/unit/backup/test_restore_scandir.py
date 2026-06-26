# -*- coding: utf-8 -*-
"""Tests for §3.4 scandir-based stat optimization in restore cleanup."""
# pylint: disable=redefined-outer-name
from __future__ import annotations

from pathlib import Path

from qwenpaw.backup._utils.safe_swap import (
    _RESTORE_OLD_SUFFIX,
    _RESTORE_TMP_SUFFIX,
    _list_child_names,
)


class TestListChildNames:
    def test_empty_directory(self, tmp_path: Path) -> None:
        """Empty directory returns empty set."""
        assert _list_child_names(tmp_path) == set()

    def test_with_files_and_dirs(self, tmp_path: Path) -> None:
        """Returns names of all direct children (files + dirs)."""
        (tmp_path / "file.txt").write_text("hello")
        (tmp_path / "subdir").mkdir()
        (tmp_path / "another.dat").write_bytes(b"\x00")

        result = _list_child_names(tmp_path)
        assert result == {"file.txt", "subdir", "another.dat"}

    def test_nonexistent_directory(self, tmp_path: Path) -> None:
        """Nonexistent directory returns empty set (safe default)."""
        missing = tmp_path / "does_not_exist"
        assert _list_child_names(missing) == set()

    def test_restore_artifacts_detected(self, tmp_path: Path) -> None:
        """Restore artifact suffixes appear in the set."""
        base = tmp_path / "workspace"
        base.mkdir()
        base.with_name(base.name + _RESTORE_TMP_SUFFIX).mkdir()
        base.with_name(base.name + _RESTORE_OLD_SUFFIX).mkdir()
        (tmp_path / "other_file").write_text("x")

        names = _list_child_names(tmp_path)
        assert "workspace" in names
        assert "workspace" + _RESTORE_TMP_SUFFIX in names
        assert "workspace" + _RESTORE_OLD_SUFFIX in names
        assert "other_file" in names


class TestRecoverMountPointSwapChildNames:
    """recover_mount_point_swap accepts optional child_names parameter."""

    def test_child_names_none_scans_internally(self, tmp_path: Path) -> None:
        """Default behavior (child_names=None) works — scans internally."""
        from qwenpaw.backup._utils._mount_swap import (
            recover_mount_point_swap,
        )

        dst = tmp_path / "workspace"
        dst.mkdir()
        tmp_dst = dst.with_name(dst.name + _RESTORE_TMP_SUFFIX)

        # No artifacts → should be a no-op
        recover_mount_point_swap(dst, tmp_dst)

    def test_child_names_provided_skips_scan(self, tmp_path: Path) -> None:
        """When child_names is provided, no scandir is performed."""
        from qwenpaw.backup._utils._mount_swap import (
            recover_mount_point_swap,
        )

        dst = tmp_path / "workspace"
        dst.mkdir()
        tmp_dst = dst.with_name(dst.name + _RESTORE_TMP_SUFFIX)

        # Pass empty child_names — simulates empty parent scan
        recover_mount_point_swap(dst, tmp_dst, child_names=set())


class TestCleanupUsesScandir:
    """Verify cleanup path uses scandir (fewer stat calls)."""

    def test_scenario2_tmp_cleanup(self, tmp_path: Path) -> None:
        """Scenario 2: .restore_tmp exists → removed."""
        from qwenpaw.backup._utils.safe_swap import (
            _cleanup_stale_restore_artifacts_locked,
        )

        base = tmp_path / "workspace"
        base.mkdir()
        tmp = base.with_name(base.name + _RESTORE_TMP_SUFFIX)
        tmp.mkdir()
        (tmp / "data.txt").write_text("stale")

        _cleanup_stale_restore_artifacts_locked(base)

        assert not tmp.exists()
        assert base.exists()

    def test_scenario3_old_cleanup(self, tmp_path: Path) -> None:
        """Scenario 3: .restore_old exists with base → removed."""
        from qwenpaw.backup._utils.safe_swap import (
            _cleanup_stale_restore_artifacts_locked,
        )

        base = tmp_path / "workspace"
        base.mkdir()
        old = base.with_name(base.name + _RESTORE_OLD_SUFFIX)
        old.mkdir()
        (old / "data.txt").write_text("obsolete")

        _cleanup_stale_restore_artifacts_locked(base)

        assert not old.exists()
        assert base.exists()

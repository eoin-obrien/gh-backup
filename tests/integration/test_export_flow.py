"""End-to-end integration tests for the full export flow.

Exercises the complete pipeline using real filesystem I/O (tmp_path)
with only subprocess calls mocked at the boundary.
"""

from __future__ import annotations

import json
from io import StringIO
from pathlib import Path

import pytest
from rich.console import Console

from gh_backup.auth import AccountType
from gh_backup.compress import ArchiveFormat
from gh_backup.exporter import ExportConfig, run_export
from tests.conftest import REPO_LIST_JSON, make_completed_process


def _console() -> Console:
    return Console(file=StringIO(), highlight=False)


ISSUES_JSON = json.dumps([{"id": 1, "title": "Bug"}])
PULLS_JSON = json.dumps([{"id": 10, "title": "Feature PR"}])


@pytest.fixture
def full_mock_subprocess(mocker):
    """Mock subprocess.run globally, dispatching on command content."""

    def side_effect(cmd, **kwargs):
        cmd_str = " ".join(str(c) for c in cmd)
        if "repo list" in cmd_str:
            return make_completed_process(stdout=REPO_LIST_JSON)
        if "git clone" in cmd_str:
            # Simulate a successful clone by creating the destination directory
            dest = Path(cmd[-1])
            dest.mkdir(parents=True, exist_ok=True)
            return make_completed_process()
        if "/issues" in cmd_str:
            return make_completed_process(stdout=ISSUES_JSON)
        if "/pulls" in cmd_str:
            return make_completed_process(stdout=PULLS_JSON)
        return make_completed_process()

    mocker.patch("subprocess.run", side_effect=side_effect)


def _make_config(tmp_path, **overrides) -> ExportConfig:
    defaults = dict(
        org="myorg",
        output_dir=tmp_path / "output",
        workers=1,
        compress=False,
        fmt=ArchiveFormat.ZST,
        skip_issues=False,
        only_repos=[],
        token="tok",
        account_type=AccountType.ORG,
        keep_dir=False,
    )
    defaults.update(overrides)
    return ExportConfig(**defaults)


class TestFullExportFlow:
    def test_happy_path_two_repos(self, full_mock_subprocess, tmp_path):
        """2 repos cloned, issues exported, no compression."""
        stats = run_export(_make_config(tmp_path), _console())
        assert stats.repos_total == 2
        assert stats.repos_cloned == 2
        assert stats.repos_failed == 0

    def test_creates_metadata_json_with_correct_org(self, full_mock_subprocess, tmp_path):
        run_export(_make_config(tmp_path), _console())
        output_dirs = list((tmp_path / "output").iterdir())
        assert len(output_dirs) == 1
        data = json.loads((output_dirs[0] / "metadata.json").read_text())
        assert data["org"] == "myorg"

    def test_creates_issues_json_files(self, full_mock_subprocess, tmp_path):
        run_export(_make_config(tmp_path), _console())
        export_dir = next((tmp_path / "output").iterdir())
        issues_files = list((export_dir / "issues").rglob("issues.json"))
        assert len(issues_files) > 0

    def test_only_repos_filter_restricts_count(self, full_mock_subprocess, tmp_path):
        """only_repos=["repo-a"] → just one repo exported."""
        stats = run_export(_make_config(tmp_path, only_repos=["repo-a"]), _console())
        assert stats.repos_total == 1

    def test_skip_issues_produces_zero_counts(self, full_mock_subprocess, tmp_path):
        stats = run_export(_make_config(tmp_path, skip_issues=True), _console())
        assert stats.issues_exported == 0
        assert stats.pulls_exported == 0

    def test_export_dir_structure_is_correct(self, full_mock_subprocess, tmp_path):
        """Export directory contains repos/ and issues/ subdirectories."""
        run_export(_make_config(tmp_path), _console())
        export_dir = next((tmp_path / "output").iterdir())
        assert (export_dir / "repos").is_dir()
        assert (export_dir / "issues").is_dir()
        assert (export_dir / "metadata.json").exists()

    @pytest.mark.parametrize("fmt", ["zst", "gz", "xz"])
    def test_compress_produces_valid_archive(self, full_mock_subprocess, tmp_path, fmt):
        """Full pipeline with compression enabled produces a readable archive."""
        from gh_backup.compress import ArchiveFormat, verify_archive

        archive_fmt = ArchiveFormat(fmt)
        stats = run_export(
            _make_config(tmp_path, compress=True, fmt=archive_fmt),
            _console(),
        )
        assert stats.bytes_compressed > 0

        output_dir = tmp_path / "output"
        archives = list(output_dir.glob(f"*.tar.{fmt}"))
        assert len(archives) == 1, f"Expected one .tar.{fmt} archive, found: {archives}"

        member_count = verify_archive(archives[0])
        assert member_count > 0

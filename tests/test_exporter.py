"""Tests for gh_backup/exporter.py."""

from __future__ import annotations

import json
import re
import subprocess
from io import StringIO
from unittest.mock import MagicMock

import pytest
from rich.console import Console

from gh_backup import __version__
from gh_backup.exporter import (
    _clone_repo,
    _export_repo,
    _export_repo_issues,
    create_export_dir,
    run_export,
    write_metadata,
)
from gh_backup.github import ExportStats


def _console() -> Console:
    return Console(file=StringIO(), highlight=False)


def _make_progress() -> MagicMock:
    """Return a minimal Rich Progress mock."""
    p = MagicMock()
    p.add_task.return_value = 0
    return p


# ── create_export_dir ─────────────────────────────────────────────────────────


class TestCreateExportDir:
    def test_creates_directory_starting_with_org_name(self, tmp_path):
        export_dir = create_export_dir(tmp_path, "myorg")
        assert export_dir.name.startswith("myorg-")
        assert export_dir.exists()

    def test_creates_repos_and_issues_subdirs(self, tmp_path):
        export_dir = create_export_dir(tmp_path, "myorg")
        assert (export_dir / "repos").is_dir()
        assert (export_dir / "issues").is_dir()

    def test_timestamp_format_is_yyyymmdd_hhmmss(self, tmp_path):
        export_dir = create_export_dir(tmp_path, "myorg")
        assert re.match(r"myorg-\d{8}-\d{6}", export_dir.name)


# ── write_metadata ────────────────────────────────────────────────────────────


class TestWriteMetadata:
    def _make_export_dir(self, tmp_path):
        d = tmp_path / "myorg-20240101-120000"
        d.mkdir(parents=True)
        return d

    def test_creates_metadata_json(self, tmp_path, two_repos, export_config):
        export_dir = self._make_export_dir(tmp_path)
        write_metadata(export_dir, "myorg", two_repos, export_config)
        assert (export_dir / "metadata.json").exists()

    def test_correct_total_repo_count(self, tmp_path, two_repos, export_config):
        export_dir = self._make_export_dir(tmp_path)
        write_metadata(export_dir, "myorg", two_repos, export_config)
        data = json.loads((export_dir / "metadata.json").read_text())
        assert data["stats"]["total_repos"] == 2

    def test_correct_private_and_public_counts(
        self, tmp_path, two_repos, export_config
    ):
        export_dir = self._make_export_dir(tmp_path)
        write_metadata(export_dir, "myorg", two_repos, export_config)
        data = json.loads((export_dir / "metadata.json").read_text())
        # two_repos: repo-b is private, repo-a is public
        assert data["stats"]["private_repos"] == 1
        assert data["stats"]["public_repos"] == 1

    def test_contains_org_name(self, tmp_path, two_repos, export_config):
        export_dir = self._make_export_dir(tmp_path)
        write_metadata(export_dir, "myorg", two_repos, export_config)
        data = json.loads((export_dir / "metadata.json").read_text())
        assert data["org"] == "myorg"

    def test_contains_tool_version(self, tmp_path, two_repos, export_config):
        export_dir = self._make_export_dir(tmp_path)
        write_metadata(export_dir, "myorg", two_repos, export_config)
        data = json.loads((export_dir / "metadata.json").read_text())
        assert data["tool_version"] == __version__

    def test_repo_entries_have_required_keys(self, tmp_path, two_repos, export_config):
        export_dir = self._make_export_dir(tmp_path)
        write_metadata(export_dir, "myorg", two_repos, export_config)
        data = json.loads((export_dir / "metadata.json").read_text())
        required_keys = {
            "name",
            "url",
            "is_private",
            "is_fork",
            "is_archived",
            "disk_usage_kb",
            "default_branch",
            "description",
        }
        for repo_entry in data["repos"]:
            assert required_keys.issubset(repo_entry.keys())

    def test_zero_repos(self, tmp_path, export_config):
        export_dir = self._make_export_dir(tmp_path)
        write_metadata(export_dir, "myorg", [], export_config)
        data = json.loads((export_dir / "metadata.json").read_text())
        assert data["stats"]["total_repos"] == 0
        assert data["repos"] == []


# ── _clone_repo ───────────────────────────────────────────────────────────────


class TestCloneRepo:
    def test_calls_git_clone_mirror(self, mocker, repo, tmp_path):
        mock_run = mocker.patch("gh_backup.exporter.subprocess.run")
        _clone_repo(repo, tmp_path / "repo.git", "mytoken")
        args = mock_run.call_args[0][0]
        assert "git" in args
        assert "--mirror" in args

    def test_injects_token_into_clone_url(self, mocker, repo, tmp_path):
        mock_run = mocker.patch("gh_backup.exporter.subprocess.run")
        _clone_repo(repo, tmp_path / "repo.git", "mytoken")
        args = mock_run.call_args[0][0]
        clone_url = next(a for a in args if "https://" in a)
        assert "oauth2:mytoken@" in clone_url

    def test_sets_git_terminal_prompt_env(self, mocker, repo, tmp_path):
        mock_run = mocker.patch("gh_backup.exporter.subprocess.run")
        _clone_repo(repo, tmp_path / "repo.git", "mytoken")
        env = mock_run.call_args[1]["env"]
        assert env.get("GIT_TERMINAL_PROMPT") == "0"

    def test_retries_three_times_on_called_process_error(self, mocker, repo, tmp_path):
        """tenacity: exhausts all 3 attempts on repeated CalledProcessError."""
        mock_run = mocker.patch(
            "gh_backup.exporter.subprocess.run",
            side_effect=subprocess.CalledProcessError(128, "git"),
        )
        mocker.patch("tenacity.nap.time.sleep")
        with pytest.raises(subprocess.CalledProcessError):
            _clone_repo(repo, tmp_path / "repo.git", "mytoken")
        assert mock_run.call_count == 3

    def test_succeeds_on_second_attempt(self, mocker, repo, tmp_path):
        """tenacity: recovers after one failure."""
        call_count = {"n": 0}

        def side_effect(*args, **kwargs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise subprocess.CalledProcessError(128, "git")

        mocker.patch("gh_backup.exporter.subprocess.run", side_effect=side_effect)
        mocker.patch("tenacity.nap.time.sleep")
        _clone_repo(repo, tmp_path / "repo.git", "mytoken")
        assert call_count["n"] == 2


# ── _export_repo_issues ───────────────────────────────────────────────────────


class TestExportRepoIssues:
    def test_writes_issues_and_pulls_json(self, mocker, tmp_path):
        mocker.patch("gh_backup.exporter.fetch_issues", return_value=[{"id": 1}])
        mocker.patch("gh_backup.exporter.fetch_pulls", return_value=[{"id": 10}])
        issues_dir = tmp_path / "issues"
        issues_dir.mkdir()
        issues_count, pulls_count = _export_repo_issues("myorg", "repo-a", issues_dir)
        assert (issues_dir / "repo-a" / "issues.json").exists()
        assert (issues_dir / "repo-a" / "pulls.json").exists()
        assert issues_count == 1
        assert pulls_count == 1

    def test_creates_repo_subdirectory(self, mocker, tmp_path):
        mocker.patch("gh_backup.exporter.fetch_issues", return_value=[])
        mocker.patch("gh_backup.exporter.fetch_pulls", return_value=[])
        issues_dir = tmp_path / "issues"
        issues_dir.mkdir()
        _export_repo_issues("myorg", "new-repo", issues_dir)
        assert (issues_dir / "new-repo").is_dir()

    def test_returns_correct_counts(self, mocker, tmp_path):
        mocker.patch(
            "gh_backup.exporter.fetch_issues",
            return_value=[{"id": i} for i in range(5)],
        )
        mocker.patch(
            "gh_backup.exporter.fetch_pulls",
            return_value=[{"id": i} for i in range(3)],
        )
        issues_dir = tmp_path / "issues"
        issues_dir.mkdir()
        issues_count, pulls_count = _export_repo_issues("myorg", "repo-a", issues_dir)
        assert issues_count == 5
        assert pulls_count == 3

    def test_written_issues_json_is_valid(self, mocker, tmp_path):
        issues = [{"id": 1, "title": "Bug"}]
        mocker.patch("gh_backup.exporter.fetch_issues", return_value=issues)
        mocker.patch("gh_backup.exporter.fetch_pulls", return_value=[])
        issues_dir = tmp_path / "issues"
        issues_dir.mkdir()
        _export_repo_issues("myorg", "repo-a", issues_dir)
        written = json.loads((issues_dir / "repo-a" / "issues.json").read_text())
        assert written == issues

    def test_retries_three_times_on_fetch_error(self, mocker, tmp_path):
        mocker.patch("tenacity.nap.time.sleep")
        mock_fetch = mocker.patch(
            "gh_backup.exporter.fetch_issues",
            side_effect=subprocess.CalledProcessError(1, "gh"),
        )
        issues_dir = tmp_path / "issues"
        issues_dir.mkdir()
        with pytest.raises(subprocess.CalledProcessError):
            _export_repo_issues("myorg", "repo-a", issues_dir)
        assert mock_fetch.call_count == 3


# ── _export_repo ──────────────────────────────────────────────────────────────


class TestExportRepo:
    def test_returns_success_result(self, mocker, repo, export_config, tmp_path):
        mocker.patch("gh_backup.exporter._clone_repo")
        mocker.patch("gh_backup.exporter._export_repo_issues", return_value=(5, 3))
        repos_dir = tmp_path / "repos"
        repos_dir.mkdir()
        issues_dir = tmp_path / "issues"
        issues_dir.mkdir()

        result = _export_repo(
            repo, export_config, repos_dir, issues_dir, _make_progress(), 0
        )
        assert result.success is True
        assert result.issues_count == 5
        assert result.pulls_count == 3

    def test_clone_failure_returns_failure_result(
        self, mocker, repo, export_config, tmp_path
    ):
        mocker.patch(
            "gh_backup.exporter._clone_repo",
            side_effect=subprocess.CalledProcessError(128, "git"),
        )
        repos_dir = tmp_path / "repos"
        repos_dir.mkdir()
        issues_dir = tmp_path / "issues"
        issues_dir.mkdir()

        result = _export_repo(
            repo, export_config, repos_dir, issues_dir, _make_progress(), 0
        )
        assert result.success is False
        assert result.error is not None

    def test_skip_issues_does_not_call_export_issues(
        self, mocker, repo, export_config, tmp_path
    ):
        export_config.skip_issues = True
        mocker.patch("gh_backup.exporter._clone_repo")
        mock_issues = mocker.patch("gh_backup.exporter._export_repo_issues")
        repos_dir = tmp_path / "repos"
        repos_dir.mkdir()
        issues_dir = tmp_path / "issues"
        issues_dir.mkdir()

        _export_repo(repo, export_config, repos_dir, issues_dir, _make_progress(), 0)
        mock_issues.assert_not_called()

    def test_issues_failure_does_not_fail_repo(
        self, mocker, repo, export_config, tmp_path
    ):
        """Issues export failure is logged as warning; repo still succeeds."""
        mocker.patch("gh_backup.exporter._clone_repo")
        mocker.patch(
            "gh_backup.exporter._export_repo_issues",
            side_effect=RuntimeError("API error"),
        )
        repos_dir = tmp_path / "repos"
        repos_dir.mkdir()
        issues_dir = tmp_path / "issues"
        issues_dir.mkdir()

        result = _export_repo(
            repo, export_config, repos_dir, issues_dir, _make_progress(), 0
        )
        assert result.success is True
        assert result.issues_count == 0

    def test_clone_path_set_on_success(self, mocker, repo, export_config, tmp_path):
        mocker.patch("gh_backup.exporter._clone_repo")
        mocker.patch("gh_backup.exporter._export_repo_issues", return_value=(0, 0))
        repos_dir = tmp_path / "repos"
        repos_dir.mkdir()
        issues_dir = tmp_path / "issues"
        issues_dir.mkdir()

        result = _export_repo(
            repo, export_config, repos_dir, issues_dir, _make_progress(), 0
        )
        assert result.clone_path == repos_dir / f"{repo.name}.git"


# ── run_export ────────────────────────────────────────────────────────────────


class TestRunExport:
    def test_returns_export_stats(self, mocker, export_config, two_repos, tmp_path):
        export_config.output_dir = tmp_path / "output"
        # list_repos is imported locally inside run_export(); mock at source
        mocker.patch("gh_backup.github.list_repos", return_value=two_repos)
        mocker.patch("gh_backup.exporter._clone_repo")
        mocker.patch("gh_backup.exporter._export_repo_issues", return_value=(2, 1))

        stats = run_export(export_config, _console())
        assert isinstance(stats, ExportStats)
        assert stats.repos_total == 2
        assert stats.repos_cloned == 2
        assert stats.repos_failed == 0

    def test_returns_early_when_no_repos_found(self, mocker, export_config, tmp_path):
        export_config.output_dir = tmp_path / "output"
        mocker.patch("gh_backup.github.list_repos", return_value=[])
        stats = run_export(export_config, _console())
        assert stats.repos_total == 0

    def test_counts_failed_repos_in_stats(
        self, mocker, export_config, two_repos, tmp_path
    ):
        export_config.output_dir = tmp_path / "output"
        mocker.patch("gh_backup.github.list_repos", return_value=two_repos)
        call_count = {"n": 0}

        def clone_side_effect(repo, dest, token):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise subprocess.CalledProcessError(128, "git")

        mocker.patch("tenacity.nap.time.sleep")
        mocker.patch("gh_backup.exporter._clone_repo", side_effect=clone_side_effect)
        mocker.patch("gh_backup.exporter._export_repo_issues", return_value=(0, 0))

        stats = run_export(export_config, _console())
        assert stats.repos_failed >= 1

    def test_calls_compress_when_compress_true(
        self, mocker, export_config, two_repos, tmp_path
    ):
        export_config.output_dir = tmp_path / "output"
        export_config.compress = True
        mocker.patch("gh_backup.github.list_repos", return_value=two_repos)
        mocker.patch("gh_backup.exporter._clone_repo")
        mocker.patch("gh_backup.exporter._export_repo_issues", return_value=(0, 0))

        # run_export computes the archive path dynamically; make the mock
        # create the file so the subsequent stat() call succeeds.
        def fake_compress(source_dir, output_path, **kwargs):
            output_path.write_bytes(b"fake archive content")
            return output_path

        mock_compress = mocker.patch(
            "gh_backup.exporter.compress_directory",
            side_effect=fake_compress,
        )

        run_export(export_config, _console())
        mock_compress.assert_called_once()

    def test_skips_compression_when_compress_false(
        self, mocker, export_config, two_repos, tmp_path
    ):
        export_config.output_dir = tmp_path / "output"
        export_config.compress = False
        mocker.patch("gh_backup.github.list_repos", return_value=two_repos)
        mocker.patch("gh_backup.exporter._clone_repo")
        mocker.patch("gh_backup.exporter._export_repo_issues", return_value=(0, 0))
        mock_compress = mocker.patch("gh_backup.exporter.compress_directory")

        run_export(export_config, _console())
        mock_compress.assert_not_called()

    def test_removes_dir_when_compress_and_not_keep_dir(
        self, mocker, export_config, two_repos, tmp_path
    ):
        export_config.output_dir = tmp_path / "output"
        export_config.compress = True
        export_config.keep_dir = False
        mocker.patch("gh_backup.github.list_repos", return_value=two_repos)
        mocker.patch("gh_backup.exporter._clone_repo")
        mocker.patch("gh_backup.exporter._export_repo_issues", return_value=(0, 0))

        def fake_compress(source_dir, output_path, **kwargs):
            output_path.write_bytes(b"fake archive content")
            return output_path

        mocker.patch("gh_backup.exporter.compress_directory", side_effect=fake_compress)
        mock_rmtree = mocker.patch("shutil.rmtree")

        run_export(export_config, _console())
        mock_rmtree.assert_called_once()

    def test_keeps_dir_when_keep_dir_true(
        self, mocker, export_config, two_repos, tmp_path
    ):
        export_config.output_dir = tmp_path / "output"
        export_config.compress = True
        export_config.keep_dir = True
        mocker.patch("gh_backup.github.list_repos", return_value=two_repos)
        mocker.patch("gh_backup.exporter._clone_repo")
        mocker.patch("gh_backup.exporter._export_repo_issues", return_value=(0, 0))

        def fake_compress(source_dir, output_path, **kwargs):
            output_path.write_bytes(b"fake archive content")
            return output_path

        mocker.patch("gh_backup.exporter.compress_directory", side_effect=fake_compress)
        mock_rmtree = mocker.patch("shutil.rmtree")

        run_export(export_config, _console())
        mock_rmtree.assert_not_called()

    def test_duration_seconds_is_non_negative(
        self, mocker, export_config, two_repos, tmp_path
    ):
        export_config.output_dir = tmp_path / "output"
        mocker.patch("gh_backup.github.list_repos", return_value=two_repos)
        mocker.patch("gh_backup.exporter._clone_repo")
        mocker.patch("gh_backup.exporter._export_repo_issues", return_value=(0, 0))

        stats = run_export(export_config, _console())
        assert stats.duration_seconds >= 0.0

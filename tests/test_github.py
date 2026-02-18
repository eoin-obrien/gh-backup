"""Tests for gh_backup/github.py."""

from __future__ import annotations

import json
import subprocess

import pytest

from gh_backup.github import (
    ExportStats,
    RepoInfo,
    _parse_paginated_json,
    _run_gh,
    fetch_issues,
    fetch_pulls,
    list_repos,
)
from tests.conftest import REPO_LIST_JSON, make_completed_process


# ── _run_gh ───────────────────────────────────────────────────────────────────


class TestRunGh:
    def test_passes_gh_prefix_and_args(self, mocker):
        mock_run = mocker.patch(
            "gh_backup.github.subprocess.run",
            return_value=make_completed_process(),
        )
        _run_gh("repo", "list", "myorg")
        assert mock_run.call_args[0][0] == ["gh", "repo", "list", "myorg"]

    def test_propagates_called_process_error(self, mocker):
        mocker.patch(
            "gh_backup.github.subprocess.run",
            side_effect=subprocess.CalledProcessError(1, "gh"),
        )
        with pytest.raises(subprocess.CalledProcessError):
            _run_gh("api", "/unknown")


# ── _parse_paginated_json ─────────────────────────────────────────────────────


class TestParsePaginatedJson:
    def test_single_valid_array(self):
        data = [{"id": 1}, {"id": 2}]
        assert _parse_paginated_json(json.dumps(data)) == data

    def test_empty_string_returns_empty_list(self):
        assert _parse_paginated_json("") == []

    def test_whitespace_only_returns_empty_list(self):
        assert _parse_paginated_json("   \n\t  ") == []

    def test_concatenated_two_arrays_are_merged(self):
        """gh --paginate produces concatenated arrays: [...][...] → merged."""
        page1 = json.dumps([{"id": 1}, {"id": 2}])
        page2 = json.dumps([{"id": 3}])
        result = _parse_paginated_json(f"{page1}\n{page2}")
        assert len(result) == 3
        assert result[0]["id"] == 1
        assert result[2]["id"] == 3

    def test_concatenated_three_pages(self):
        pages = [json.dumps([{"id": i}]) for i in range(3)]
        result = _parse_paginated_json("\n".join(pages))
        assert len(result) == 3

    def test_single_object_wrapped_in_list(self):
        result = _parse_paginated_json('{"id": 42}')
        assert result == [{"id": 42}]

    def test_extra_whitespace_between_pages(self):
        page1 = json.dumps([{"id": 1}])
        page2 = json.dumps([{"id": 2}])
        result = _parse_paginated_json(f"{page1}   \n\n   {page2}")
        assert len(result) == 2

    def test_stops_gracefully_at_invalid_json_after_valid(self):
        page1 = json.dumps([{"id": 1}])
        result = _parse_paginated_json(f"{page1}\n{{bad json}}")
        assert len(result) == 1

    def test_empty_array_returns_empty_list(self):
        assert _parse_paginated_json("[]") == []

    def test_two_empty_arrays_returns_empty_list(self):
        assert _parse_paginated_json("[]\n[]") == []

    @pytest.mark.parametrize(
        "n_pages,items_per_page",
        [(1, 100), (3, 100), (10, 100)],
    )
    def test_large_paginated_outputs(self, n_pages, items_per_page):
        """Handles realistic pagination scales without data loss."""
        all_items = [{"id": i} for i in range(n_pages * items_per_page)]
        pages = [
            json.dumps(all_items[i * items_per_page : (i + 1) * items_per_page])
            for i in range(n_pages)
        ]
        result = _parse_paginated_json("\n".join(pages))
        assert len(result) == n_pages * items_per_page


# ── list_repos ────────────────────────────────────────────────────────────────


class TestListRepos:
    def test_returns_repo_info_objects(self, mocker):
        mocker.patch(
            "gh_backup.github._run_gh",
            return_value=make_completed_process(stdout=REPO_LIST_JSON),
        )
        repos = list_repos("myorg")
        assert len(repos) == 2
        assert all(isinstance(r, RepoInfo) for r in repos)

    def test_maps_all_fields_correctly(self, mocker):
        mocker.patch(
            "gh_backup.github._run_gh",
            return_value=make_completed_process(stdout=REPO_LIST_JSON),
        )
        repos = list_repos("myorg")
        r = repos[0]
        assert r.name == "repo-a"
        assert r.url == "https://github.com/myorg/repo-a"
        assert r.ssh_url == "git@github.com:myorg/repo-a.git"
        assert r.is_private is False
        assert r.default_branch == "main"
        assert r.disk_usage_kb == 200

    def test_null_description_becomes_empty_string(self, mocker):
        mocker.patch(
            "gh_backup.github._run_gh",
            return_value=make_completed_process(stdout=REPO_LIST_JSON),
        )
        repos = list_repos("myorg")
        assert repos[1].description == ""

    def test_null_default_branch_ref_becomes_none(self, mocker):
        mocker.patch(
            "gh_backup.github._run_gh",
            return_value=make_completed_process(stdout=REPO_LIST_JSON),
        )
        repos = list_repos("myorg")
        assert repos[1].default_branch is None

    def test_null_disk_usage_becomes_zero(self, mocker):
        mocker.patch(
            "gh_backup.github._run_gh",
            return_value=make_completed_process(stdout=REPO_LIST_JSON),
        )
        repos = list_repos("myorg")
        assert repos[1].disk_usage_kb == 0

    def test_only_filter_keeps_matching_repos(self, mocker):
        mocker.patch(
            "gh_backup.github._run_gh",
            return_value=make_completed_process(stdout=REPO_LIST_JSON),
        )
        repos = list_repos("myorg", only=["repo-a"])
        assert len(repos) == 1
        assert repos[0].name == "repo-a"

    def test_only_filter_returns_empty_for_unknown_name(self, mocker):
        mocker.patch(
            "gh_backup.github._run_gh",
            return_value=make_completed_process(stdout=REPO_LIST_JSON),
        )
        assert list_repos("myorg", only=["does-not-exist"]) == []

    def test_passes_json_and_limit_args(self, mocker):
        mock_run = mocker.patch(
            "gh_backup.github._run_gh",
            return_value=make_completed_process(stdout="[]"),
        )
        list_repos("someorg")
        call_args = mock_run.call_args[0]
        assert "--json" in call_args
        assert "--limit" in call_args

    def test_empty_json_returns_empty_list(self, mocker):
        mocker.patch(
            "gh_backup.github._run_gh",
            return_value=make_completed_process(stdout="[]"),
        )
        assert list_repos("emptyorg") == []

    def test_propagates_called_process_error(self, mocker):
        mocker.patch(
            "gh_backup.github._run_gh",
            side_effect=subprocess.CalledProcessError(1, "gh"),
        )
        with pytest.raises(subprocess.CalledProcessError):
            list_repos("badorg")


# ── fetch_issues ──────────────────────────────────────────────────────────────


class TestFetchIssues:
    def test_returns_list_of_dicts(self, mocker):
        data = [{"id": 1, "title": "Bug"}, {"id": 2, "title": "Feature"}]
        mocker.patch(
            "gh_backup.github._run_gh",
            return_value=make_completed_process(stdout=json.dumps(data)),
        )
        assert fetch_issues("myorg", "my-repo") == data

    def test_calls_correct_endpoint(self, mocker):
        mock_run = mocker.patch(
            "gh_backup.github._run_gh",
            return_value=make_completed_process(stdout="[]"),
        )
        fetch_issues("myorg", "my-repo")
        args = mock_run.call_args[0]
        assert "/repos/myorg/my-repo/issues" in args

    def test_passes_paginate_flag(self, mocker):
        mock_run = mocker.patch(
            "gh_backup.github._run_gh",
            return_value=make_completed_process(stdout="[]"),
        )
        fetch_issues("myorg", "my-repo")
        assert "--paginate" in mock_run.call_args[0]

    def test_empty_response_returns_empty_list(self, mocker):
        mocker.patch(
            "gh_backup.github._run_gh",
            return_value=make_completed_process(stdout="[]"),
        )
        assert fetch_issues("myorg", "my-repo") == []

    def test_handles_two_page_concatenated_output(self, mocker):
        page1 = json.dumps([{"id": 1}])
        page2 = json.dumps([{"id": 2}])
        mocker.patch(
            "gh_backup.github._run_gh",
            return_value=make_completed_process(stdout=f"{page1}\n{page2}"),
        )
        result = fetch_issues("myorg", "my-repo")
        assert len(result) == 2


# ── fetch_pulls ───────────────────────────────────────────────────────────────


class TestFetchPulls:
    def test_returns_list_of_dicts(self, mocker):
        data = [{"id": 10, "title": "PR 1"}]
        mocker.patch(
            "gh_backup.github._run_gh",
            return_value=make_completed_process(stdout=json.dumps(data)),
        )
        assert fetch_pulls("myorg", "my-repo") == data

    def test_calls_correct_endpoint(self, mocker):
        mock_run = mocker.patch(
            "gh_backup.github._run_gh",
            return_value=make_completed_process(stdout="[]"),
        )
        fetch_pulls("myorg", "my-repo")
        args = mock_run.call_args[0]
        assert "/repos/myorg/my-repo/pulls" in args

    def test_passes_paginate_flag(self, mocker):
        mock_run = mocker.patch(
            "gh_backup.github._run_gh",
            return_value=make_completed_process(stdout="[]"),
        )
        fetch_pulls("myorg", "my-repo")
        assert "--paginate" in mock_run.call_args[0]

    def test_empty_response_returns_empty_list(self, mocker):
        mocker.patch(
            "gh_backup.github._run_gh",
            return_value=make_completed_process(stdout="[]"),
        )
        assert fetch_pulls("myorg", "my-repo") == []


# ── RepoInfo dataclass ────────────────────────────────────────────────────────


class TestRepoInfo:
    def test_is_frozen(self, repo):
        with pytest.raises((AttributeError, TypeError)):
            repo.name = "hacked"  # type: ignore[misc]

    def test_equality(self, make_repo):
        a = make_repo()
        b = make_repo()
        assert a == b


# ── ExportStats dataclass ─────────────────────────────────────────────────────


class TestExportStats:
    def test_defaults_to_zero(self):
        stats = ExportStats()
        assert stats.repos_total == 0
        assert stats.repos_cloned == 0
        assert stats.repos_failed == 0
        assert stats.issues_exported == 0
        assert stats.pulls_exported == 0
        assert stats.bytes_compressed == 0
        assert stats.duration_seconds == 0.0
        assert stats.failed_repos == []

    def test_failed_repos_not_shared_between_instances(self):
        """Each ExportStats instance has its own list (no mutable default sharing)."""
        a = ExportStats()
        b = ExportStats()
        a.failed_repos.append("x")
        assert b.failed_repos == []

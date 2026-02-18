"""Shared fixtures and test data for the gh-backup test suite."""

from __future__ import annotations

import json
import subprocess

import pytest

from gh_backup.auth import AccountType, AuthState
from gh_backup.compress import ArchiveFormat
from gh_backup.exporter import ExportConfig
from gh_backup.github import RepoInfo

# ── subprocess helpers ────────────────────────────────────────────────────────


def make_completed_process(
    stdout: str = "",
    stderr: str = "",
    returncode: int = 0,
) -> subprocess.CompletedProcess:
    """Factory for subprocess.CompletedProcess test doubles."""
    result = subprocess.CompletedProcess(args=[], returncode=returncode)
    result.stdout = stdout
    result.stderr = stderr
    return result


# ── raw test data strings ─────────────────────────────────────────────────────

GH_AUTH_STATUS_LOGGED_IN = """\
github.com
  Logged in to github.com account testuser (keyring)
  Active token: ghs_testtoken
  Token scopes: 'repo', 'read:org'
"""

REPO_LIST_JSON = json.dumps(
    [
        {
            "name": "repo-a",
            "url": "https://github.com/myorg/repo-a",
            "sshUrl": "git@github.com:myorg/repo-a.git",
            "isPrivate": False,
            "isFork": False,
            "isArchived": False,
            "description": "Repo A",
            "defaultBranchRef": {"name": "main"},
            "diskUsage": 200,
        },
        {
            "name": "repo-b",
            "url": "https://github.com/myorg/repo-b",
            "sshUrl": "git@github.com:myorg/repo-b.git",
            "isPrivate": True,
            "isFork": False,
            "isArchived": False,
            "description": None,
            "defaultBranchRef": None,
            "diskUsage": None,
        },
    ]
)


# ── auth fixtures ─────────────────────────────────────────────────────────────


@pytest.fixture
def auth_state_logged_in() -> AuthState:
    return AuthState(
        logged_in=True,
        account="testuser",
        hostname="github.com",
        token="ghs_testtoken",
        scopes=["repo", "read:org"],
    )


@pytest.fixture
def auth_state_logged_out() -> AuthState:
    return AuthState(
        logged_in=False,
        account=None,
        hostname="github.com",
        token=None,
        scopes=[],
    )


# ── repo fixtures ─────────────────────────────────────────────────────────────


@pytest.fixture
def make_repo():
    """Factory fixture: call make_repo(name='foo') to get a RepoInfo."""

    def _make(
        name: str = "test-repo",
        url: str = "https://github.com/myorg/test-repo",
        ssh_url: str = "git@github.com:myorg/test-repo.git",
        is_private: bool = False,
        is_fork: bool = False,
        is_archived: bool = False,
        description: str = "A test repository",
        default_branch: str | None = "main",
        disk_usage_kb: int = 100,
    ) -> RepoInfo:
        return RepoInfo(
            name=name,
            url=url,
            ssh_url=ssh_url,
            is_private=is_private,
            is_fork=is_fork,
            is_archived=is_archived,
            description=description,
            default_branch=default_branch,
            disk_usage_kb=disk_usage_kb,
        )

    return _make


@pytest.fixture
def repo(make_repo) -> RepoInfo:
    return make_repo()


@pytest.fixture
def two_repos(make_repo) -> list[RepoInfo]:
    return [
        make_repo(name="repo-a", disk_usage_kb=200),
        make_repo(name="repo-b", disk_usage_kb=300, is_private=True),
    ]


# ── export config fixture ─────────────────────────────────────────────────────


@pytest.fixture
def export_config(tmp_path) -> ExportConfig:
    return ExportConfig(
        org="myorg",
        output_dir=tmp_path / "output",
        workers=2,
        compress=False,
        fmt=ArchiveFormat.ZST,
        skip_issues=False,
        only_repos=[],
        token="ghs_testtoken",
        account_type=AccountType.ORG,
        keep_dir=False,
    )


# ── filesystem fixture ────────────────────────────────────────────────────────


@pytest.fixture
def source_dir(tmp_path):
    """A small directory tree suitable for compression tests."""
    src = tmp_path / "source"
    (src / "sub").mkdir(parents=True)
    (src / "file1.txt").write_text("hello")
    (src / "sub" / "file2.txt").write_text("world")
    (src / "empty_dir").mkdir()
    return src

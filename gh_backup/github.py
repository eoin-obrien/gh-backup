"""GitHub CLI wrappers for listing repos and fetching issues/PRs."""

from __future__ import annotations

import json
import logging
import subprocess
from dataclasses import dataclass, field

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class RepoInfo:
    name: str
    url: str
    ssh_url: str
    is_private: bool
    is_fork: bool
    is_archived: bool
    description: str
    default_branch: str | None
    disk_usage_kb: int


@dataclass
class ExportStats:
    repos_total: int = 0
    repos_cloned: int = 0
    repos_failed: int = 0
    issues_exported: int = 0
    pulls_exported: int = 0
    bytes_compressed: int = 0
    duration_seconds: float = 0.0
    failed_repos: list[str] = field(default_factory=list)


def _run_gh(*args: str, **kwargs) -> subprocess.CompletedProcess:
    """Run `gh` with the given args. Raises CalledProcessError on failure."""
    return subprocess.run(
        ["gh", *args],
        capture_output=True,
        text=True,
        check=True,
        **kwargs,
    )


def list_repos(org: str, only: list[str] | None = None) -> list[RepoInfo]:
    """List all repositories in the given organization.

    If `only` is provided, filter to repos matching those names.
    """
    log.debug("Listing repos for org: %s", org)
    result = _run_gh(
        "repo",
        "list",
        org,
        "--limit",
        "9999",
        "--json",
        "name,url,sshUrl,isPrivate,defaultBranchRef,diskUsage,description,isFork,isArchived",
    )
    raw: list[dict] = json.loads(result.stdout)

    repos = [
        RepoInfo(
            name=r["name"],
            url=r["url"],
            ssh_url=r.get("sshUrl", ""),
            is_private=r.get("isPrivate", False),
            is_fork=r.get("isFork", False),
            is_archived=r.get("isArchived", False),
            description=r.get("description") or "",
            default_branch=(r.get("defaultBranchRef") or {}).get("name"),
            disk_usage_kb=r.get("diskUsage") or 0,
        )
        for r in raw
    ]

    if only:
        only_set = set(only)
        repos = [r for r in repos if r.name in only_set]

    log.debug("Found %d repos", len(repos))
    return repos


def fetch_issues(org: str, repo: str) -> list[dict]:
    """Fetch all issues for a repo (all states), with pagination."""
    log.debug("Fetching issues for %s/%s", org, repo)
    result = _run_gh(
        "api",
        f"/repos/{org}/{repo}/issues",
        "--paginate",
        "--method",
        "GET",
        "-F",
        "state=all",
        "-F",
        "per_page=100",
    )
    # gh --paginate concatenates JSON arrays; parse the full output
    return _parse_paginated_json(result.stdout)


def fetch_pulls(org: str, repo: str) -> list[dict]:
    """Fetch all pull requests for a repo (all states), with pagination."""
    log.debug("Fetching pulls for %s/%s", org, repo)
    result = _run_gh(
        "api",
        f"/repos/{org}/{repo}/pulls",
        "--paginate",
        "--method",
        "GET",
        "-F",
        "state=all",
        "-F",
        "per_page=100",
    )
    return _parse_paginated_json(result.stdout)


def _parse_paginated_json(output: str) -> list[dict]:
    """Parse paginated gh output, which may be concatenated JSON arrays."""
    output = output.strip()
    if not output:
        return []

    # gh --paginate concatenates arrays: [...][...] → parse each separately
    # and merge, or if it's already valid JSON, parse directly.
    try:
        parsed = json.loads(output)
        if isinstance(parsed, list):
            return parsed
        return [parsed]
    except json.JSONDecodeError:
        # Try to handle concatenated arrays: "[...]\n[...]"
        items: list[dict] = []
        decoder = json.JSONDecoder()
        pos = 0
        while pos < len(output):
            # Skip whitespace
            while pos < len(output) and output[pos].isspace():
                pos += 1
            if pos >= len(output):
                break
            try:
                obj, end_pos = decoder.raw_decode(output, pos)
                if isinstance(obj, list):
                    items.extend(obj)
                else:
                    items.append(obj)
                pos = end_pos
            except json.JSONDecodeError:
                break
        return items

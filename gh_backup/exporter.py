"""Core export orchestration: directory layout, cloning, issues, compression."""

from __future__ import annotations

import json
import logging
import os
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from rich.console import Console
from rich.table import Column
from rich.progress import (
    BarColumn,
    Progress,
    SpinnerColumn,
    TaskID,
    TaskProgressColumn,
    TextColumn,
    TimeRemainingColumn,
)
from tenacity import (
    before_sleep_log,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from . import __version__
from .auth import AccountType
from .compress import ArchiveFormat, compress_directory, get_archive_suffix
from .github import ExportStats, RepoInfo, fetch_issues, fetch_pulls

log = logging.getLogger(__name__)


@dataclass
class ExportConfig:
    org: str
    output_dir: Path
    workers: int
    compress: bool
    fmt: ArchiveFormat
    skip_issues: bool
    only_repos: list[str]
    token: str
    account_type: AccountType = AccountType.ORG
    keep_dir: bool = False
    git_gc: bool = False


@dataclass
class RepoResult:
    repo: RepoInfo
    success: bool
    clone_path: Path | None = None
    issues_count: int = 0
    pulls_count: int = 0
    error: str | None = None


def create_export_dir(output_dir: Path, org: str) -> Path:
    """Create and return the timestamped export directory."""
    timestamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    export_dir = output_dir / f"{org}-{timestamp}"
    (export_dir / "repos").mkdir(parents=True, exist_ok=True)
    (export_dir / "issues").mkdir(parents=True, exist_ok=True)
    log.info("Export directory: %s", export_dir)
    return export_dir


def write_metadata(
    export_dir: Path,
    org: str,
    repos: list[RepoInfo],
    config: ExportConfig,
) -> None:
    metadata = {
        "org": org,
        "export_timestamp": datetime.now(UTC).isoformat(),
        "tool_version": __version__,
        "stats": {
            "total_repos": len(repos),
            "private_repos": sum(1 for r in repos if r.is_private),
            "public_repos": sum(1 for r in repos if not r.is_private),
            "fork_repos": sum(1 for r in repos if r.is_fork),
            "archived_repos": sum(1 for r in repos if r.is_archived),
        },
        "config": {
            "account_type": config.account_type,
            "workers": config.workers,
            "format": config.fmt,
            "skip_issues": config.skip_issues,
        },
        "repos": [
            {
                "name": r.name,
                "url": r.url,
                "is_private": r.is_private,
                "is_fork": r.is_fork,
                "is_archived": r.is_archived,
                "disk_usage_kb": r.disk_usage_kb,
                "default_branch": r.default_branch,
                "description": r.description,
            }
            for r in repos
        ],
    }
    (export_dir / "metadata.json").write_text(json.dumps(metadata, indent=2))


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=30),
    retry=retry_if_exception_type(subprocess.CalledProcessError),
    reraise=True,
    before_sleep=before_sleep_log(log, logging.WARNING),
)
def _clone_repo(repo: RepoInfo, dest: Path, token: str) -> None:
    """Mirror-clone a repo with full history into `dest`."""
    # Build authenticated HTTPS URL
    clone_url = repo.url.replace("https://", f"https://oauth2:{token}@")
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    subprocess.run(
        ["git", "clone", "--mirror", clone_url, str(dest)],
        check=True,
        capture_output=True,
        env=env,
    )


def _gc_repo(clone_path: Path) -> None:
    """Run git gc --aggressive --prune=now on a bare clone to shrink pack files."""
    subprocess.run(
        ["git", "-C", str(clone_path), "gc", "--aggressive", "--prune=now", "--quiet"],
        check=True,
        capture_output=True,
    )


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=4, max=60),
    retry=retry_if_exception_type(subprocess.CalledProcessError),
    reraise=True,
    before_sleep=before_sleep_log(log, logging.WARNING),
)
def _export_repo_issues(org: str, repo_name: str, issues_dir: Path) -> tuple[int, int]:
    """Fetch issues and PRs for a repo and write JSON files.

    Returns (issues_count, pulls_count).
    """
    repo_issues_dir = issues_dir / repo_name
    repo_issues_dir.mkdir(parents=True, exist_ok=True)

    issues = fetch_issues(org, repo_name)
    pulls = fetch_pulls(org, repo_name)

    (repo_issues_dir / "issues.json").write_text(json.dumps(issues, indent=2))
    (repo_issues_dir / "pulls.json").write_text(json.dumps(pulls, indent=2))

    return len(issues), len(pulls)


def _export_repo(
    repo: RepoInfo,
    config: ExportConfig,
    repos_dir: Path,
    issues_dir: Path,
    progress: Progress,
    overall_task: TaskID,
) -> RepoResult:
    """Export a single repo: clone + issues/PRs. Called from worker threads."""
    steps = 4 if config.git_gc else 3
    task = progress.add_task(f"[cyan]{repo.name}[/]", total=steps, visible=True)
    clone_path = repos_dir / f"{repo.name}.git"
    issues_count = 0
    pulls_count = 0

    try:
        # Clone
        progress.update(task, description=f"[cyan]clone:[/] {repo.name}")
        _clone_repo(repo, clone_path, config.token)
        progress.advance(task)

        # GC (optional)
        if config.git_gc:
            progress.update(task, description=f"[cyan]gc:[/] {repo.name}")
            try:
                _gc_repo(clone_path)
            except Exception as e:
                log.warning("git gc failed for %s: %s", repo.name, e)
            progress.advance(task)

        # Export issues/PRs
        if not config.skip_issues:
            progress.update(task, description=f"[cyan]issues:[/] {repo.name}")
            try:
                issues_count, pulls_count = _export_repo_issues(
                    config.org, repo.name, issues_dir
                )
            except Exception as e:
                log.warning("Issues export failed for %s: %s", repo.name, e)
        progress.advance(task)

        # Done
        progress.update(task, description=f"[green]done:[/] {repo.name}")
        progress.advance(task)
        progress.update(task, visible=False)
        progress.advance(overall_task)

        return RepoResult(
            repo=repo,
            success=True,
            clone_path=clone_path,
            issues_count=issues_count,
            pulls_count=pulls_count,
        )

    except Exception as e:
        progress.update(task, description=f"[red]failed:[/] {repo.name}", visible=False)
        progress.advance(overall_task)
        log.error("Export failed for %s: %s", repo.name, e)
        return RepoResult(repo=repo, success=False, error=str(e))


def run_export(config: ExportConfig, console: Console) -> ExportStats:
    """Orchestrate the full organization export."""
    from .github import list_repos

    start_time = time.monotonic()
    stats = ExportStats()

    # List repos
    console.print(f"[bold]Listing repositories for[/] [cyan]{config.org}[/]...")
    repos = list_repos(config.org, config.only_repos or None)
    if not repos:
        console.print("[yellow]No repositories found.[/]")
        return stats

    stats.repos_total = len(repos)
    console.print(f"Found [bold]{len(repos)}[/] repositories.")

    # Create export directory
    export_dir = create_export_dir(config.output_dir, config.org)
    repos_dir = export_dir / "repos"
    issues_dir = export_dir / "issues"

    write_metadata(export_dir, config.org, repos, config)

    # Calculate total disk usage for display
    total_kb = sum(r.disk_usage_kb for r in repos)
    console.print(
        f"Estimated size: [bold]{total_kb / 1024:.1f} MB[/] "
        f"(reported by GitHub, actual clone may differ)"
    )

    results: list[RepoResult] = []

    progress_columns = [
        SpinnerColumn(),
        TextColumn(
            "[progress.description]{task.description}", table_column=Column(ratio=1)
        ),
        BarColumn(bar_width=None, table_column=Column(ratio=1)),
        TaskProgressColumn(),
        TextColumn("•"),
        TimeRemainingColumn(),
    ]

    with Progress(
        *progress_columns, console=console, transient=False, expand=True
    ) as progress:
        overall_task = progress.add_task(
            f"[bold]Exporting {config.org}[/]", total=len(repos)
        )

        with ThreadPoolExecutor(max_workers=config.workers) as executor:
            futures = {
                executor.submit(
                    _export_repo,
                    repo,
                    config,
                    repos_dir,
                    issues_dir,
                    progress,
                    overall_task,
                ): repo
                for repo in repos
            }
            try:
                for future in as_completed(futures):
                    result = future.result()
                    results.append(result)
            except KeyboardInterrupt:
                console.print(
                    "\n[yellow]Interrupted — cancelling remaining downloads...[/]"
                )
                executor.shutdown(wait=False, cancel_futures=True)
                raise

    # Aggregate stats
    for result in results:
        if result.success:
            stats.repos_cloned += 1
            stats.issues_exported += result.issues_count
            stats.pulls_exported += result.pulls_count
        else:
            stats.repos_failed += 1
            stats.failed_repos.append(result.repo.name)

    # Compress
    if config.compress:
        suffix = get_archive_suffix(config.fmt)
        archive_path = config.output_dir / f"{export_dir.name}{suffix}"

        console.print(f"\nCompressing to [cyan]{archive_path.name}[/]...")
        source_size = sum(
            f.stat().st_size for f in export_dir.rglob("*") if f.is_file()
        )

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TaskProgressColumn(),
            console=console,
        ) as progress:
            compress_task = progress.add_task("Compressing...", total=source_size)

            def _update_progress(bytes_written: int) -> None:
                progress.update(compress_task, completed=bytes_written)

            compress_directory(
                source_dir=export_dir,
                output_path=archive_path,
                fmt=config.fmt,
                level=3,
                progress_callback=_update_progress,
            )

        stats.bytes_compressed = archive_path.stat().st_size

        if not config.keep_dir:
            import shutil

            shutil.rmtree(export_dir)
            console.print("[dim]Removed uncompressed directory.[/]")

        console.print(
            f"Archive: [bold green]{archive_path}[/] "
            f"([bold]{stats.bytes_compressed / 1_048_576:.1f} MB[/])"
        )
    else:
        console.print(f"\nExport saved to [bold green]{export_dir}[/]")

    stats.duration_seconds = time.monotonic() - start_time
    return stats

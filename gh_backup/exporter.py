"""Core export orchestration: directory layout, cloning, issues, compression."""

from __future__ import annotations

import json
import logging
import os
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path

from rich.console import Console
from rich.progress import (
    BarColumn,
    Progress,
    SpinnerColumn,
    TaskID,
    TaskProgressColumn,
    TextColumn,
    TimeRemainingColumn,
)
from rich.table import Column
from tenacity import (
    Retrying,
    before_sleep_log,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from . import __version__
from .auth import AccountType
from .compress import ArchiveFormat, compress_directory, get_archive_suffix
from .github import ExportStats, RepoInfo, fetch_issues, fetch_pulls

log = logging.getLogger(__name__)


class Visibility(StrEnum):
    ALL = "all"
    PUBLIC = "public"
    PRIVATE = "private"


class ExportCancelled(Exception):
    """Raised when the export is cancelled via stop_event."""


def _sleep_or_cancel(stop_event: threading.Event, seconds: float) -> None:
    """Sleep up to `seconds`, but wake immediately if stop_event is set."""
    if stop_event.wait(timeout=seconds):
        raise ExportCancelled()


def _log_before_sleep(stop_event: threading.Event, retry_state) -> None:
    """Log a retry warning, suppressed when the export is being cancelled."""
    if not stop_event.is_set():
        before_sleep_log(log, logging.WARNING)(retry_state)


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
    skip_forks: bool = False
    skip_archived: bool = False
    visibility: Visibility = Visibility.ALL
    dry_run: bool = False
    shallow: bool = False


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


def _filter_repos(repos: list[RepoInfo], config: ExportConfig) -> list[RepoInfo]:
    """Apply skip_forks, skip_archived, and visibility filters to a repo list."""
    if config.skip_forks:
        repos = [r for r in repos if not r.is_fork]
    if config.skip_archived:
        repos = [r for r in repos if not r.is_archived]
    if config.visibility == Visibility.PUBLIC:
        repos = [r for r in repos if not r.is_private]
    elif config.visibility == Visibility.PRIVATE:
        repos = [r for r in repos if r.is_private]
    return repos


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


def _redact_token(text: str, token: str) -> str:
    """Replace occurrences of `token` in `text` with ***."""
    return text.replace(token, "***") if token else text


def _clone_repo(
    repo: RepoInfo,
    dest: Path,
    token: str,
    stop_event: threading.Event | None = None,
    shallow: bool = False,
) -> None:
    """Mirror-clone a repo with full history into `dest`.

    Pass shallow=True to add --depth 1 (faster, smaller, no full history).
    """
    if stop_event is None:
        stop_event = threading.Event()
    clone_url = repo.url.replace("https://", f"https://oauth2:{token}@")
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    cmd = ["git", "clone", "--mirror"]
    if shallow:
        cmd += ["--depth", "1"]
    cmd += [clone_url, str(dest)]
    try:
        for attempt in Retrying(
            stop=stop_after_attempt(3),
            wait=wait_exponential(multiplier=1, min=2, max=30),
            retry=retry_if_exception_type(subprocess.CalledProcessError),
            reraise=True,
            before_sleep=lambda rs: _log_before_sleep(stop_event, rs),
            sleep=lambda s: _sleep_or_cancel(stop_event, s),
        ):
            with attempt:
                if stop_event.is_set():
                    raise ExportCancelled()
                subprocess.run(cmd, check=True, capture_output=True, env=env)
    except subprocess.CalledProcessError as e:
        # Remove any partial clone directory left behind.
        if dest.exists():
            import shutil

            shutil.rmtree(dest, ignore_errors=True)
        # Redact the token from any error output before re-raising.
        redacted = subprocess.CalledProcessError(e.returncode, e.cmd)
        redacted.stdout = _redact_token(e.stdout or "", token)
        redacted.stderr = _redact_token(e.stderr or "", token)
        raise redacted from None


def _gc_repo(clone_path: Path) -> None:
    """Run git gc --aggressive --prune=now on a bare clone to shrink pack files."""
    subprocess.run(
        ["git", "-C", str(clone_path), "gc", "--aggressive", "--prune=now", "--quiet"],
        check=True,
        capture_output=True,
    )


def _export_repo_issues(
    org: str,
    repo_name: str,
    issues_dir: Path,
    stop_event: threading.Event | None = None,
) -> tuple[int, int]:
    """Fetch issues and PRs for a repo and write JSON files.

    Returns (issues_count, pulls_count).
    """
    if stop_event is None:
        stop_event = threading.Event()
    repo_issues_dir = issues_dir / repo_name
    repo_issues_dir.mkdir(parents=True, exist_ok=True)
    issues: list[dict] = []
    pulls: list[dict] = []
    for attempt in Retrying(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=4, max=60),
        retry=retry_if_exception_type(subprocess.CalledProcessError),
        reraise=True,
        before_sleep=lambda rs: _log_before_sleep(stop_event, rs),
        sleep=lambda s: _sleep_or_cancel(stop_event, s),
    ):
        with attempt:
            if stop_event.is_set():
                raise ExportCancelled()
            issues = fetch_issues(org, repo_name)
            if stop_event.is_set():
                raise ExportCancelled()
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
    stop_event: threading.Event | None = None,
) -> RepoResult:
    """Export a single repo: clone + issues/PRs. Called from worker threads."""
    if stop_event is None:
        stop_event = threading.Event()
    steps = 4 if config.git_gc else 3
    task = progress.add_task(f"[cyan]{repo.name}[/]", total=steps, visible=True)
    clone_path = repos_dir / f"{repo.name}.git"
    issues_count = 0
    pulls_count = 0

    try:
        # Clone
        progress.update(task, description=f"[cyan]clone:[/] {repo.name}")
        _clone_repo(repo, clone_path, config.token, stop_event, shallow=config.shallow)
        progress.advance(task)

        # GC (optional)
        if config.git_gc:
            progress.update(task, description=f"[cyan]gc:[/] {repo.name}")
            if not stop_event.is_set():
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
                    config.org, repo.name, issues_dir, stop_event
                )
            except ExportCancelled:
                raise
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

    except ExportCancelled:
        progress.update(task, description=f"[yellow]cancelled:[/] {repo.name}", visible=False)
        progress.advance(overall_task)
        return RepoResult(repo=repo, success=False, error="Cancelled")
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
    repos = _filter_repos(repos, config)
    if not repos:
        console.print("[yellow]No repositories found.[/]")
        return stats

    stats.repos_total = len(repos)
    console.print(f"Found [bold]{len(repos)}[/] repositories.")

    # Dry-run: print what would be exported and stop.
    if config.dry_run:
        total_kb = sum(r.disk_usage_kb for r in repos)
        console.print(
            f"Estimated size: [bold]{total_kb / 1024:.1f} MB[/] "
            f"(reported by GitHub, actual clone may differ)"
        )
        console.print(
            "\n[bold cyan]Dry run — no files written.[/] Repositories that would be exported:\n"
        )
        for repo in repos:
            tags = " ".join(
                f"[dim]{t}[/]"
                for t in (
                    ["fork"]
                    if repo.is_fork
                    else []
                    + (["archived"] if repo.is_archived else [])
                    + (["private"] if repo.is_private else ["public"])
                )
            )
            console.print(f"  [cyan]{repo.name}[/] {tags}")
        return stats

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
    stop_event = threading.Event()

    progress_columns = [
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}", table_column=Column(ratio=1)),
        BarColumn(bar_width=None, table_column=Column(ratio=1)),
        TaskProgressColumn(),
        TextColumn("•"),
        TimeRemainingColumn(),
    ]

    with Progress(*progress_columns, console=console, transient=False, expand=True) as progress:
        overall_task = progress.add_task(f"[bold]Exporting {config.org}[/]", total=len(repos))

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
                    stop_event,
                ): repo
                for repo in repos
            }
            try:
                for future in as_completed(futures):
                    result = future.result()
                    results.append(result)
            except KeyboardInterrupt:
                stop_event.set()
                console.print("\n[yellow]Interrupted — cancelling remaining downloads...[/]")
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
        source_size = sum(f.stat().st_size for f in export_dir.rglob("*") if f.is_file())

        try:
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
        except KeyboardInterrupt:
            if archive_path.exists():
                archive_path.unlink()
            raise

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

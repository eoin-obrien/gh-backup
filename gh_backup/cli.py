"""CLI entry point for gh-backup."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.logging import RichHandler
from rich.panel import Panel
from rich.table import Table

from . import __version__
from .auth import AccountType
from .compress import ArchiveFormat
from .exporter import ExportConfig, run_export

APP_NAME = "gh-backup"

app = typer.Typer(
    name=APP_NAME,
    help=f"[bold]{APP_NAME}[/] — Backup a GitHub organization: repos, issues, and PRs.\n\n"
    "Clones all repositories with full git history and exports issues/PRs as JSON, "
    "then compresses everything into a [cyan].tar.zst[/] archive.",
    rich_markup_mode="rich",
    no_args_is_help=True,
    add_completion=False,
)

console = Console()


def _setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.WARNING
    logging.basicConfig(
        level=level,
        format="%(message)s",
        handlers=[RichHandler(console=Console(stderr=True), show_path=False)],
    )


@app.callback()
def main(
    version: Annotated[
        bool,
        typer.Option("--version", "-V", help="Show version and exit.", is_eager=True),
    ] = False,
) -> None:
    if version:
        console.print(f"{APP_NAME} [bold]{__version__}[/]")
        raise typer.Exit()


@app.command("auth")
def auth_command(
    verbose: Annotated[
        bool, typer.Option("--verbose", "-v", help="Enable debug logging.")
    ] = False,
) -> None:
    """Check GitHub CLI authentication status."""
    _setup_logging(verbose)
    from . import auth

    try:
        state = auth.check_auth()
    except RuntimeError as e:
        console.print(f"[bold red]Error:[/] {e}")
        raise typer.Exit(1)

    if state.logged_in:
        scopes_str = ", ".join(state.scopes) if state.scopes else "(unknown)"
        console.print(
            Panel(
                f"Account: [bold green]{state.account}[/]\n"
                f"Host:    [bold]{state.hostname}[/]\n"
                f"Scopes:  [dim]{scopes_str}[/]",
                title="[bold green] GitHub Authentication[/]",
                border_style="green",
            )
        )
    else:
        console.print(
            Panel(
                "Not logged in.\n\n"
                "Run [bold cyan]gh auth login[/] to authenticate, then try again.",
                title="[bold red] GitHub Authentication[/]",
                border_style="red",
            )
        )
        raise typer.Exit(1)


@app.command("export")
def export_command(
    org: Annotated[
        str, typer.Argument(help="GitHub organization or user name to export.")
    ],
    output: Annotated[
        Path,
        typer.Option("--output", "-o", help="Directory to write exports into."),
    ],
    workers: Annotated[
        int,
        typer.Option(
            "--workers",
            "-w",
            help="Number of parallel repo clone workers.",
            min=1,
            max=32,
        ),
    ] = 4,
    no_compress: Annotated[
        bool,
        typer.Option(
            "--no-compress", help="Skip compression; keep the raw export directory."
        ),
    ] = False,
    keep_dir: Annotated[
        bool,
        typer.Option(
            "--keep-dir", help="Keep the uncompressed directory after archiving."
        ),
    ] = False,
    fmt: Annotated[
        ArchiveFormat,
        typer.Option("--format", help="Archive format.", show_default=True),
    ] = ArchiveFormat.ZST,
    git_gc: Annotated[
        bool,
        typer.Option(
            "--gc",
            help="Run 'git gc --aggressive' on each clone before archiving to shrink pack files.",
        ),
    ] = False,
    skip_issues: Annotated[
        bool,
        typer.Option("--skip-issues", help="Skip issues and pull request export."),
    ] = False,
    repos: Annotated[
        list[str] | None,
        typer.Option("--repos", "-r", help="Only export this repo (repeatable)."),
    ] = None,
    account_type: Annotated[
        AccountType,
        typer.Option(
            "--type", "-t", help="GitHub account type: org or user.", show_default=True
        ),
    ] = AccountType.ORG,
    verbose: Annotated[
        bool,
        typer.Option("--verbose", "-v", help="Enable debug logging."),
    ] = False,
) -> None:
    """Export a GitHub organization's repositories, issues, and pull requests.

    Each export run is stored in a timestamped subdirectory under OUTPUT,
    then compressed into a [cyan].tar.zst[/] archive (best size/speed tradeoff).

    \b
    Examples:
      gh-backup export myorg --output /backups
      gh-backup export myorg --output /backups --workers 8
      gh-backup export myorg --output /backups --repos frontend --repos backend
      gh-backup export myorg --output /backups --skip-issues --no-compress
      gh-backup export myusername --output /backups --type user
    """
    _setup_logging(verbose)
    from . import auth

    console.print(f"[bold]{APP_NAME}[/] [dim]v{__version__}[/]\n")
    try:
        auth_state = auth.require_auth()
        token = auth.get_token()
    except RuntimeError as e:
        console.print(f"[bold red]Error:[/] {e}")
        raise typer.Exit(1)

    console.print(
        f"Authenticated as [bold green]{auth_state.account}[/] on {auth_state.hostname}"
    )

    account_label = "organization" if account_type == AccountType.ORG else "user"
    console.print(f"Checking access to {account_label} [bold cyan]{org}[/]...")

    if not auth.check_account_access(org, account_type):
        console.print(
            f"[bold red]Error:[/] Cannot access {account_label} '[cyan]{org}[/]'. "
            "Check the name and your permissions."
        )
        raise typer.Exit(1)

    config = ExportConfig(
        org=org,
        output_dir=output.resolve(),
        workers=workers,
        compress=not no_compress,
        fmt=fmt,
        skip_issues=skip_issues,
        only_repos=repos or [],
        token=token,
        account_type=account_type,
        keep_dir=keep_dir,
        git_gc=git_gc,
    )

    try:
        stats = run_export(config, console)
    except KeyboardInterrupt:
        console.print("\n[yellow]Export cancelled.[/]")
        raise typer.Exit(130)
    except Exception as e:
        console.print(f"\n[bold red]Export failed:[/] {e}")
        if verbose:
            import traceback

            traceback.print_exc()
        raise typer.Exit(1)

    _print_summary(stats, console)

    exit_code = 2 if stats.repos_failed > 0 else 0
    raise typer.Exit(exit_code)


def _print_summary(stats, console: Console) -> None:
    table = Table(
        title="Export Summary",
        show_header=True,
        header_style="bold cyan",
        border_style="dim",
    )
    table.add_column("Metric", style="bold")
    table.add_column("Value", justify="right")

    table.add_row("Repos exported", f"[green]{stats.repos_cloned}[/]")

    if stats.repos_failed:
        table.add_row(
            "Repos failed",
            f"[red]{stats.repos_failed}[/]",
        )
        table.add_row(
            "  Failed repos",
            f"[dim red]{', '.join(stats.failed_repos)}[/]",
        )

    table.add_row("Issues exported", str(stats.issues_exported))
    table.add_row("PRs exported", str(stats.pulls_exported))

    if stats.bytes_compressed:
        table.add_row("Archive size", f"{stats.bytes_compressed / 1_048_576:.1f} MB")

    mins, secs = divmod(int(stats.duration_seconds), 60)
    duration_str = f"{mins}m {secs}s" if mins else f"{secs}s"
    table.add_row("Duration", duration_str)

    console.print()
    console.print(table)

    if stats.repos_failed:
        console.print(
            f"\n[yellow]Warning:[/] {stats.repos_failed} repo(s) failed to clone. "
            "Re-run with [dim]--verbose[/] for details."
        )
    elif stats.repos_cloned > 0:
        console.print("\n[bold green]Export completed successfully.[/]")

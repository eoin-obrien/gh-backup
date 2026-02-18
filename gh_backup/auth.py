"""GitHub CLI authentication helpers."""

from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass, field
from enum import StrEnum

log = logging.getLogger(__name__)


class AccountType(StrEnum):
    ORG = "org"
    USER = "user"


@dataclass(frozen=True)
class AuthState:
    logged_in: bool
    account: str | None
    hostname: str
    token: str | None
    scopes: list[str] = field(default_factory=list)


def check_auth() -> AuthState:
    """Run `gh auth status` and return the current auth state.

    Never raises — always returns an AuthState regardless of login status.
    """
    try:
        result = subprocess.run(
            ["gh", "auth", "status"],
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        raise RuntimeError(
            "GitHub CLI (gh) not found. Install it from https://cli.github.com/"
        )

    if result.returncode != 0:
        return AuthState(
            logged_in=False, account=None, hostname="github.com", token=None
        )

    # gh auth status writes to stderr
    output = result.stderr or result.stdout
    account: str | None = None
    hostname = "github.com"
    scopes: list[str] = []

    for line in output.splitlines():
        line = line.strip()
        if "Logged in to" in line:
            # "Logged in to github.com account eoin-obrien (...)"
            parts = line.split()
            try:
                idx = parts.index("to") + 1
                hostname = parts[idx]
                account_idx = parts.index("account") + 1
                account = parts[account_idx].rstrip("()")
            except (ValueError, IndexError):
                pass
        elif "Token scopes:" in line or "oauth_token" in line.lower():
            # "Token scopes: 'repo', 'read:org', ..."
            scopes_part = line.split(":", 1)[-1]
            scopes = [
                s.strip().strip("'\"") for s in scopes_part.split(",") if s.strip()
            ]

    token: str | None = None
    try:
        token = get_token()
    except RuntimeError:
        pass

    return AuthState(
        logged_in=True,
        account=account,
        hostname=hostname,
        token=token,
        scopes=scopes,
    )


def require_auth() -> AuthState:
    """Check auth and raise typer.Exit(1) with instructions if not logged in."""
    import typer

    state = check_auth()
    if not state.logged_in:
        import rich.console

        console = rich.console.Console(stderr=True)
        console.print(
            "[bold red]Not authenticated with GitHub.[/] "
            "Run [bold cyan]gh auth login[/] to log in, then try again."
        )
        raise typer.Exit(1)
    return state


def get_token() -> str:
    """Return the current GitHub auth token via `gh auth token`."""
    try:
        result = subprocess.run(
            ["gh", "auth", "token"],
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout.strip()
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"Failed to get GitHub token: {e.stderr.strip()}") from e
    except FileNotFoundError:
        raise RuntimeError("GitHub CLI (gh) not found.")


def check_account_access(name: str, account_type: AccountType) -> bool:
    """Return True if the current user can access the given org or user account."""
    endpoint = f"/orgs/{name}" if account_type == AccountType.ORG else f"/users/{name}"
    try:
        subprocess.run(
            ["gh", "api", endpoint],
            capture_output=True,
            text=True,
            check=True,
        )
        return True
    except subprocess.CalledProcessError:
        return False

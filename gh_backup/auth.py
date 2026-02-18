"""GitHub CLI authentication helpers."""

from __future__ import annotations

import logging
import re
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
        raise RuntimeError("GitHub CLI (gh) not found. Install it from https://cli.github.com/")

    if result.returncode != 0:
        return AuthState(logged_in=False, account=None, hostname="github.com", token=None)

    # gh auth status writes to stderr
    output = result.stderr or result.stdout
    account: str | None = None
    hostname = "github.com"
    scopes: list[str] = []

    for line in output.splitlines():
        line = line.strip()
        # "✓ Logged in to github.com account eoin-obrien (...)"
        m = re.search(r"Logged in to (\S+) account (\S+)", line)
        if m:
            hostname = m.group(1)
            account = m.group(2).rstrip("()")
        # "- Token scopes: 'repo', 'read:org', ..."  OR oauth_token legacy format
        elif re.search(r"Token scopes:", line, re.IGNORECASE) or "oauth_token" in line.lower():
            scopes_part = line.split(":", 1)[-1]
            scopes = [s.strip().strip("'\"") for s in scopes_part.split(",") if s.strip()]

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


def warn_missing_scopes(state: AuthState) -> list[str]:
    """Return a list of warning messages for likely missing token scopes.

    Only warns when scopes are non-empty (i.e. we successfully parsed them),
    to avoid false positives with fine-grained tokens or parsing failures.
    """
    warnings: list[str] = []
    if not state.scopes:
        return warnings
    if "repo" not in state.scopes:
        warnings.append(
            "Token is missing the [bold]repo[/] scope — private repository clones will fail. "
            "Run [bold cyan]gh auth refresh -s repo[/] to add it."
        )
    if "read:org" not in state.scopes:
        warnings.append(
            "Token is missing the [bold]read:org[/] scope — organization repository listing "
            "may fail. Run [bold cyan]gh auth refresh -s read:org[/] to add it."
        )
    return warnings


def resolve_account_type(name: str) -> AccountType:
    """Detect whether `name` is a GitHub org or user by probing the API.

    Tries /orgs/{name} first; falls back to /users/{name}.
    Raises RuntimeError if neither endpoint succeeds.
    """
    for account_type, endpoint in [
        (AccountType.ORG, f"/orgs/{name}"),
        (AccountType.USER, f"/users/{name}"),
    ]:
        try:
            subprocess.run(
                ["gh", "api", endpoint],
                capture_output=True,
                text=True,
                check=True,
            )
            return account_type
        except subprocess.CalledProcessError:
            continue
    raise RuntimeError(
        f"'{name}' not found as an org or user. Check the name and your permissions."
    )

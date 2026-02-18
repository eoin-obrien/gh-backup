"""Tests for gh_backup/auth.py."""

from __future__ import annotations

import subprocess

import pytest
import typer

from gh_backup.auth import (
    AccountType,
    AuthState,
    check_auth,
    get_token,
    require_auth,
    resolve_account_type,
)
from tests.conftest import GH_AUTH_STATUS_LOGGED_IN, make_completed_process

# ── check_auth ────────────────────────────────────────────────────────────────


class TestCheckAuth:
    def test_returns_logged_in_state_when_authenticated(self, mocker):
        """Happy path: gh auth status succeeds, account/hostname/scopes/token parsed."""
        mocker.patch(
            "gh_backup.auth.subprocess.run",
            side_effect=[
                make_completed_process(stderr=GH_AUTH_STATUS_LOGGED_IN, returncode=0),
                make_completed_process(stdout="ghs_testtoken\n", returncode=0),
            ],
        )
        state = check_auth()
        assert state.logged_in is True
        assert state.account == "testuser"
        assert state.hostname == "github.com"
        assert "repo" in state.scopes
        assert state.token == "ghs_testtoken"

    def test_returns_not_logged_in_on_nonzero_returncode(self, mocker):
        """Non-zero returncode → AuthState(logged_in=False), never raises."""
        mocker.patch(
            "gh_backup.auth.subprocess.run",
            return_value=make_completed_process(returncode=1),
        )
        state = check_auth()
        assert state.logged_in is False
        assert state.account is None
        assert state.token is None

    def test_raises_runtime_error_when_gh_not_found(self, mocker):
        """FileNotFoundError from subprocess → RuntimeError with install hint."""
        mocker.patch(
            "gh_backup.auth.subprocess.run",
            side_effect=FileNotFoundError,
        )
        with pytest.raises(RuntimeError, match="not found"):
            check_auth()

    def test_token_none_when_get_token_fails(self, mocker):
        """Token fetch RuntimeError is caught — returns None token, still logged_in=True."""
        mocker.patch(
            "gh_backup.auth.subprocess.run",
            side_effect=[
                make_completed_process(stderr=GH_AUTH_STATUS_LOGGED_IN, returncode=0),
                subprocess.CalledProcessError(1, "gh", stderr="no token"),
            ],
        )
        state = check_auth()
        assert state.logged_in is True
        assert state.token is None

    @pytest.mark.parametrize(
        "stderr,expected_hostname,expected_account",
        [
            (
                "  Logged in to github.enterprise.com account corp-user (keyring)\n"
                "  Token scopes: 'repo'\n",
                "github.enterprise.com",
                "corp-user",
            ),
            (
                "  Logged in to github.com account alice (oauth_token)\n",
                "github.com",
                "alice",
            ),
        ],
    )
    def test_parses_various_hostname_and_account_formats(
        self, mocker, stderr, expected_hostname, expected_account
    ):
        mocker.patch(
            "gh_backup.auth.subprocess.run",
            side_effect=[
                make_completed_process(stderr=stderr, returncode=0),
                make_completed_process(stdout="tok\n", returncode=0),
            ],
        )
        state = check_auth()
        assert state.hostname == expected_hostname
        assert state.account == expected_account

    def test_scopes_parsed_and_stripped(self, mocker):
        """Scopes are split on commas and surrounding quotes removed."""
        stderr = "  Logged in to github.com account u (k)\n  Token scopes: 'repo', 'read:org'\n"
        mocker.patch(
            "gh_backup.auth.subprocess.run",
            side_effect=[
                make_completed_process(stderr=stderr, returncode=0),
                make_completed_process(stdout="tok\n", returncode=0),
            ],
        )
        state = check_auth()
        assert state.scopes == ["repo", "read:org"]

    def test_falls_back_to_stdout_when_stderr_empty(self, mocker):
        """Uses stdout output when stderr is falsy (code does `stderr or stdout`)."""
        mocker.patch(
            "gh_backup.auth.subprocess.run",
            side_effect=[
                make_completed_process(stdout=GH_AUTH_STATUS_LOGGED_IN, stderr="", returncode=0),
                make_completed_process(stdout="tok\n", returncode=0),
            ],
        )
        state = check_auth()
        assert state.logged_in is True
        assert state.account == "testuser"

    def test_malformed_output_returns_valid_state_without_account(self, mocker):
        """Completely unparseable output still returns a valid AuthState (no exception)."""
        mocker.patch(
            "gh_backup.auth.subprocess.run",
            side_effect=[
                make_completed_process(stderr="this is garbage\n", returncode=0),
                make_completed_process(stdout="tok\n", returncode=0),
            ],
        )
        state = check_auth()
        assert state.logged_in is True
        assert state.account is None


# ── get_token ─────────────────────────────────────────────────────────────────


class TestGetToken:
    def test_returns_stripped_token_on_success(self, mocker):
        mocker.patch(
            "gh_backup.auth.subprocess.run",
            return_value=make_completed_process(stdout="ghs_abc123\n"),
        )
        assert get_token() == "ghs_abc123"

    def test_raises_runtime_error_on_called_process_error(self, mocker):
        mocker.patch(
            "gh_backup.auth.subprocess.run",
            side_effect=subprocess.CalledProcessError(1, "gh", stderr="no credential"),
        )
        with pytest.raises(RuntimeError, match="Failed to get GitHub token"):
            get_token()

    def test_raises_runtime_error_when_gh_not_found(self, mocker):
        mocker.patch("gh_backup.auth.subprocess.run", side_effect=FileNotFoundError)
        with pytest.raises(RuntimeError, match="not found"):
            get_token()


# ── require_auth ──────────────────────────────────────────────────────────────


class TestRequireAuth:
    def test_returns_state_when_logged_in(self, mocker, auth_state_logged_in):
        mocker.patch("gh_backup.auth.check_auth", return_value=auth_state_logged_in)
        state = require_auth()
        assert state is auth_state_logged_in

    def test_raises_typer_exit_when_not_logged_in(self, mocker, auth_state_logged_out):
        mocker.patch("gh_backup.auth.check_auth", return_value=auth_state_logged_out)
        with pytest.raises(typer.Exit) as exc_info:
            require_auth()
        assert exc_info.value.exit_code == 1


# ── resolve_account_type ──────────────────────────────────────────────────────


class TestResolveAccountType:
    def test_returns_org_when_org_endpoint_succeeds(self, mocker):
        mocker.patch(
            "gh_backup.auth.subprocess.run",
            return_value=make_completed_process(returncode=0),
        )
        assert resolve_account_type("myorg") == AccountType.ORG

    def test_returns_user_when_org_fails_but_user_succeeds(self, mocker):
        mocker.patch(
            "gh_backup.auth.subprocess.run",
            side_effect=[
                subprocess.CalledProcessError(404, "gh"),
                make_completed_process(returncode=0),
            ],
        )
        assert resolve_account_type("myuser") == AccountType.USER

    def test_raises_when_both_endpoints_fail(self, mocker):
        mocker.patch(
            "gh_backup.auth.subprocess.run",
            side_effect=subprocess.CalledProcessError(404, "gh"),
        )
        with pytest.raises(RuntimeError, match="not found"):
            resolve_account_type("nonexistent")

    def test_tries_org_endpoint_first(self, mocker):
        calls = []

        def side_effect(cmd, **kwargs):
            calls.append(cmd)
            raise subprocess.CalledProcessError(404, "gh")

        mocker.patch("gh_backup.auth.subprocess.run", side_effect=side_effect)
        with pytest.raises(RuntimeError):
            resolve_account_type("name")
        assert any("/orgs/name" in c for c in calls[0])


# ── AuthState dataclass ───────────────────────────────────────────────────────


class TestAuthState:
    def test_is_frozen(self, auth_state_logged_in):
        """AuthState must be immutable (frozen dataclass)."""
        with pytest.raises((AttributeError, TypeError)):
            auth_state_logged_in.account = "hacker"  # type: ignore[misc]

    def test_equality(self):
        a = AuthState(True, "u", "github.com", "tok", ["repo"])
        b = AuthState(True, "u", "github.com", "tok", ["repo"])
        assert a == b

    def test_empty_scopes_default(self):
        """Scopes default to empty list when not provided."""
        state = AuthState(logged_in=False, account=None, hostname="github.com", token=None)
        assert state.scopes == []

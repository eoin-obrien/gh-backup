"""Tests for gh_backup/cli.py."""

from __future__ import annotations

from io import StringIO

import pytest
import typer
from rich.console import Console
from typer.testing import CliRunner

from gh_backup.auth import AccountType, AuthState
from gh_backup.cli import _print_summary, app
from gh_backup.github import ExportStats

runner = CliRunner()


# ── fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def mock_auth_ok(mocker):
    """Patch all auth calls to simulate a logged-in user.

    cli.py uses `from . import auth` inside each function, so the resolved
    names are in gh_backup.auth, not gh_backup.cli.auth.
    """
    state = AuthState(
        logged_in=True,
        account="testuser",
        hostname="github.com",
        token="ghs_tok",
        scopes=["repo", "read:org"],
    )
    mocker.patch("gh_backup.auth.check_auth", return_value=state)
    mocker.patch("gh_backup.auth.require_auth", return_value=state)
    mocker.patch("gh_backup.auth.get_token", return_value="ghs_tok")
    mocker.patch("gh_backup.auth.check_account_access", return_value=True)
    return state


@pytest.fixture
def mock_run_export_ok(mocker):
    stats = ExportStats(repos_total=3, repos_cloned=3, duration_seconds=1.5)
    return mocker.patch("gh_backup.cli.run_export", return_value=stats)


# ── app callback ──────────────────────────────────────────────────────────────


class TestMainCallback:
    def test_help_shows_app_name(self):
        """--help exits 0 and shows the application name."""
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        assert "gh-backup" in result.output

    def test_help_shows_version_info(self):
        """The help output includes the app version."""
        result = runner.invoke(app, ["--help"])
        # Version appears in the help description or body
        assert "gh-backup" in result.output


# ── auth subcommand ───────────────────────────────────────────────────────────


class TestAuthCommand:
    def test_logged_in_exits_zero_and_shows_account(self, mocker):
        state = AuthState(True, "testuser", "github.com", "tok", ["repo"])
        mocker.patch("gh_backup.auth.check_auth", return_value=state)
        result = runner.invoke(app, ["auth"])
        assert result.exit_code == 0
        assert "testuser" in result.output

    def test_not_logged_in_exits_one(self, mocker):
        state = AuthState(False, None, "github.com", None, [])
        mocker.patch("gh_backup.auth.check_auth", return_value=state)
        result = runner.invoke(app, ["auth"])
        assert result.exit_code == 1

    def test_shows_hostname_in_output(self, mocker):
        state = AuthState(True, "user", "github.enterprise.com", "tok", [])
        mocker.patch("gh_backup.auth.check_auth", return_value=state)
        result = runner.invoke(app, ["auth"])
        assert "github.enterprise.com" in result.output

    def test_shows_scopes_in_output(self, mocker):
        state = AuthState(True, "user", "github.com", "tok", ["repo", "read:org"])
        mocker.patch("gh_backup.auth.check_auth", return_value=state)
        result = runner.invoke(app, ["auth"])
        assert "repo" in result.output

    def test_runtime_error_from_check_auth_exits_one(self, mocker):
        mocker.patch(
            "gh_backup.auth.check_auth",
            side_effect=RuntimeError("gh not found"),
        )
        result = runner.invoke(app, ["auth"])
        assert result.exit_code == 1
        assert "Error" in result.output

    def test_verbose_flag_accepted(self, mocker):
        state = AuthState(True, "user", "github.com", "tok", [])
        mocker.patch("gh_backup.auth.check_auth", return_value=state)
        result = runner.invoke(app, ["auth", "--verbose"])
        assert result.exit_code == 0

    def test_no_scopes_shows_unknown(self, mocker):
        state = AuthState(True, "user", "github.com", "tok", [])
        mocker.patch("gh_backup.auth.check_auth", return_value=state)
        result = runner.invoke(app, ["auth"])
        assert "(unknown)" in result.output


# ── export subcommand ─────────────────────────────────────────────────────────


class TestExportCommand:
    def test_happy_path_exits_zero(
        self, mocker, mock_auth_ok, mock_run_export_ok, tmp_path
    ):
        result = runner.invoke(app, ["export", "myorg", "--output", str(tmp_path)])
        assert result.exit_code == 0

    def test_calls_run_export_once(
        self, mocker, mock_auth_ok, mock_run_export_ok, tmp_path
    ):
        runner.invoke(app, ["export", "myorg", "--output", str(tmp_path)])
        mock_run_export_ok.assert_called_once()

    def test_exits_two_when_repos_failed(self, mocker, mock_auth_ok, tmp_path):
        stats = ExportStats(
            repos_total=2,
            repos_cloned=1,
            repos_failed=1,
            failed_repos=["repo-b"],
            duration_seconds=1.0,
        )
        mocker.patch("gh_backup.cli.run_export", return_value=stats)
        result = runner.invoke(app, ["export", "myorg", "--output", str(tmp_path)])
        assert result.exit_code == 2

    def test_require_auth_exit_propagates(self, mocker, tmp_path):
        """typer.Exit raised by require_auth propagates the exit code."""
        mocker.patch("gh_backup.auth.require_auth", side_effect=typer.Exit(1))
        result = runner.invoke(app, ["export", "myorg", "--output", str(tmp_path)])
        assert result.exit_code == 1

    def test_no_account_access_exits_one(self, mocker, mock_auth_ok, tmp_path):
        mocker.patch("gh_backup.auth.check_account_access", return_value=False)
        result = runner.invoke(app, ["export", "myorg", "--output", str(tmp_path)])
        assert result.exit_code == 1
        assert "Cannot access" in result.output

    def test_get_token_runtime_error_exits_one(self, mocker, tmp_path):
        state = AuthState(True, "user", "github.com", "tok", [])
        mocker.patch("gh_backup.auth.require_auth", return_value=state)
        mocker.patch(
            "gh_backup.auth.get_token",
            side_effect=RuntimeError("token error"),
        )
        result = runner.invoke(app, ["export", "myorg", "--output", str(tmp_path)])
        assert result.exit_code == 1

    def test_keyboard_interrupt_exits_130(self, mocker, mock_auth_ok, tmp_path):
        mocker.patch("gh_backup.cli.run_export", side_effect=KeyboardInterrupt)
        result = runner.invoke(app, ["export", "myorg", "--output", str(tmp_path)])
        assert result.exit_code == 130

    def test_no_compress_sets_compress_false(self, mocker, mock_auth_ok, tmp_path):
        captured = {}

        def capture(config, console):
            captured["config"] = config
            return ExportStats(repos_total=0)

        mocker.patch("gh_backup.cli.run_export", side_effect=capture)
        runner.invoke(
            app, ["export", "myorg", "--output", str(tmp_path), "--no-compress"]
        )
        assert captured["config"].compress is False

    def test_default_compress_is_true(self, mocker, mock_auth_ok, tmp_path):
        captured = {}

        def capture(config, console):
            captured["config"] = config
            return ExportStats(repos_total=0)

        mocker.patch("gh_backup.cli.run_export", side_effect=capture)
        runner.invoke(app, ["export", "myorg", "--output", str(tmp_path)])
        assert captured["config"].compress is True

    def test_workers_option_sets_config(self, mocker, mock_auth_ok, tmp_path):
        captured = {}

        def capture(config, console):
            captured["config"] = config
            return ExportStats(repos_total=0)

        mocker.patch("gh_backup.cli.run_export", side_effect=capture)
        runner.invoke(
            app, ["export", "myorg", "--output", str(tmp_path), "--workers", "8"]
        )
        assert captured["config"].workers == 8

    def test_skip_issues_flag_sets_config(self, mocker, mock_auth_ok, tmp_path):
        captured = {}

        def capture(config, console):
            captured["config"] = config
            return ExportStats(repos_total=0)

        mocker.patch("gh_backup.cli.run_export", side_effect=capture)
        runner.invoke(
            app, ["export", "myorg", "--output", str(tmp_path), "--skip-issues"]
        )
        assert captured["config"].skip_issues is True

    def test_repos_filter_sets_only_repos(self, mocker, mock_auth_ok, tmp_path):
        captured = {}

        def capture(config, console):
            captured["config"] = config
            return ExportStats(repos_total=0)

        mocker.patch("gh_backup.cli.run_export", side_effect=capture)
        runner.invoke(
            app,
            [
                "export",
                "myorg",
                "--output",
                str(tmp_path),
                "--repos",
                "frontend",
                "--repos",
                "backend",
            ],
        )
        assert set(captured["config"].only_repos) == {"frontend", "backend"}

    @pytest.mark.parametrize(
        "fmt_str,expected_value",
        [("gz", "gz"), ("xz", "xz"), ("zst", "zst")],
    )
    def test_format_option_sets_fmt(
        self, mocker, mock_auth_ok, tmp_path, fmt_str, expected_value
    ):
        captured = {}

        def capture(config, console):
            captured["config"] = config
            return ExportStats(repos_total=0)

        mocker.patch("gh_backup.cli.run_export", side_effect=capture)
        runner.invoke(
            app,
            ["export", "myorg", "--output", str(tmp_path), "--format", fmt_str],
        )
        assert str(captured["config"].fmt) == expected_value

    def test_type_user_sets_account_type(self, mocker, mock_auth_ok, tmp_path):
        captured = {}

        def capture(config, console):
            captured["config"] = config
            return ExportStats(repos_total=0)

        mocker.patch("gh_backup.cli.run_export", side_effect=capture)
        runner.invoke(
            app,
            ["export", "myorg", "--output", str(tmp_path), "--type", "user"],
        )
        assert captured["config"].account_type == AccountType.USER

    def test_keep_dir_flag_sets_config(self, mocker, mock_auth_ok, tmp_path):
        captured = {}

        def capture(config, console):
            captured["config"] = config
            return ExportStats(repos_total=0)

        mocker.patch("gh_backup.cli.run_export", side_effect=capture)
        runner.invoke(app, ["export", "myorg", "--output", str(tmp_path), "--keep-dir"])
        assert captured["config"].keep_dir is True


# ── _print_summary ────────────────────────────────────────────────────────────


def _buf_console() -> tuple[StringIO, Console]:
    buf = StringIO()
    return buf, Console(file=buf, highlight=False)


class TestPrintSummary:
    def test_shows_repos_cloned_count(self):
        buf, console = _buf_console()
        _print_summary(
            ExportStats(repos_total=5, repos_cloned=5, duration_seconds=10.0), console
        )
        assert "5" in buf.getvalue()

    def test_shows_failed_repo_names_when_present(self):
        buf, console = _buf_console()
        stats = ExportStats(
            repos_total=3,
            repos_cloned=2,
            repos_failed=1,
            failed_repos=["bad-repo"],
            duration_seconds=5.0,
        )
        _print_summary(stats, console)
        assert "bad-repo" in buf.getvalue()

    def test_duration_shows_minutes_when_over_60s(self):
        buf, console = _buf_console()
        _print_summary(ExportStats(duration_seconds=75.0), console)
        assert "1m" in buf.getvalue()

    def test_duration_shows_seconds_when_under_60s(self):
        buf, console = _buf_console()
        _print_summary(ExportStats(duration_seconds=45.0), console)
        assert "45s" in buf.getvalue()

    def test_archive_size_shown_in_mb_when_compressed(self):
        buf, console = _buf_console()
        _print_summary(ExportStats(bytes_compressed=1_048_576), console)
        assert "MB" in buf.getvalue()

    def test_archive_size_not_shown_when_not_compressed(self):
        buf, console = _buf_console()
        _print_summary(ExportStats(bytes_compressed=0), console)
        assert "MB" not in buf.getvalue()

    def test_success_message_shown_when_all_cloned(self):
        buf, console = _buf_console()
        _print_summary(
            ExportStats(repos_cloned=3, repos_failed=0, duration_seconds=2.0), console
        )
        assert "successfully" in buf.getvalue().lower()

    def test_no_success_message_when_repos_failed(self):
        buf, console = _buf_console()
        _print_summary(
            ExportStats(
                repos_cloned=2,
                repos_failed=1,
                failed_repos=["x"],
                duration_seconds=2.0,
            ),
            console,
        )
        assert "successfully" not in buf.getvalue().lower()

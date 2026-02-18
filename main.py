"""Entry point shim — the real CLI lives in gh_backup.cli."""

from gh_backup.cli import app

if __name__ == "__main__":
    app()

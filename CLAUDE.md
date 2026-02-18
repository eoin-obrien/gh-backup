# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
make install      # uv sync --all-groups && pre-commit install
make test         # uv run pytest
make lint         # uv run ruff check . && uv run ruff format --check .
make lint-fix     # uv run ruff check --fix . && uv run ruff format .
make commit       # uv run cz commit (conventional commits via commitizen)
```

**Run a single test:**
```bash
uv run pytest tests/test_auth.py::TestResolveAccountType::test_returns_org_when_org_endpoint_succeeds -v
uv run pytest -k "test_no_compress" -v
```

## Architecture

The project is a CLI tool (`gh-backup`) that clones GitHub org/user repos and exports issues/PRs, then compresses everything into a `.tar.zst` archive. It delegates all GitHub operations to the `gh` CLI via subprocesses.

**Layer order:** `cli.py` → `exporter.py` → `github.py` + `auth.py` → subprocess (`gh`, `git`)

### Key modules

- **`auth.py`** — Wraps `gh auth status` / `gh auth token`. `resolve_account_type(name)` probes `/orgs/{name}` then `/users/{name}` to auto-detect ORG vs USER.
- **`github.py`** — Wraps `gh repo list` and `gh api --paginate`. `_parse_paginated_json()` handles concatenated JSON arrays that `gh --paginate` emits.
- **`exporter.py`** — Core orchestration. `run_export()` lists repos, creates a timestamped output dir, writes `metadata.json`, then fans out to a `ThreadPoolExecutor`. `_clone_repo()` uses `git clone --mirror` with tenacity retries and injects a token into the clone URL.
- **`compress.py`** — Streams the export directory into a tarball. ZST uses the `zstandard` library (multi-threaded); GZ/XZ use stdlib `tarfile`.
- **`cli.py`** — Typer app with two subcommands: `auth` (show status) and `export`. Uses lazy imports of submodules. Exit codes: 0 success, 1 error, 2 partial failure (some repos failed), 130 KeyboardInterrupt.

### Data flow for `export`

1. `require_auth()` validates `gh` auth state
2. `resolve_account_type(org)` detects ORG or USER
3. `list_repos()` fetches all repos (optionally filtered by `--repos`)
4. `create_export_dir()` makes `<output>/<org>-<timestamp>/`
5. `write_metadata()` writes `metadata.json`
6. ThreadPoolExecutor runs `_export_repo()` per repo (clone → optional GC → optional issues/PRs)
7. `compress_directory()` produces the archive; `--keep-dir` skips deletion of the staging dir

### Testing conventions

Tests live in `tests/` with a mirror of the source structure. `conftest.py` provides shared fixtures including `make_repo()`, `export_config`, and `make_completed_process()`. Subprocess calls are mocked via `pytest-mock`. The integration test (`tests/integration/test_export_flow.py`) runs the full pipeline end-to-end.

## Commit messages

Commits must follow Conventional Commits format (enforced by commitizen pre-commit hook). Use `make commit` or write messages like `feat:`, `fix:`, `chore:`, etc.

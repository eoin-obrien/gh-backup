# gh-backup

[![CI](https://github.com/eoin-obrien/gh-backup/actions/workflows/ci.yml/badge.svg)](https://github.com/eoin-obrien/gh-backup/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/gh-backup)](https://pypi.org/project/gh-backup/)
[![Python 3.13+](https://img.shields.io/badge/python-3.13+-blue.svg)](https://www.python.org/downloads/)
[![License: AGPL-3.0](https://img.shields.io/badge/license-AGPL--3.0-blue.svg)](LICENSE.md)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)
[![uv](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/uv/main/assets/badge/v0.json)](https://github.com/astral-sh/uv)
[![Conventional Commits](https://img.shields.io/badge/Conventional%20Commits-1.0.0-%23FE5196?logo=conventionalcommits&logoColor=white)](https://conventionalcommits.org)
[![pre-commit](https://img.shields.io/badge/pre--commit-enabled-brightgreen?logo=pre-commit)](https://github.com/pre-commit/pre-commit)
[![codecov](https://codecov.io/gh/eoin-obrien/gh-backup/branch/master/graph/badge.svg)](https://codecov.io/gh/eoin-obrien/gh-backup)

Backup a GitHub organization or user: repos, issues, and pull requests.

Clones all repositories with full git history and exports issues/PRs as JSON, then compresses everything into a `.tar.zst` archive.

## Requirements

- [GitHub CLI (`gh`)](https://cli.github.com/) — authenticated with a token that has `repo` and `read:org` scopes

## Installation

```bash
# uv (recommended)
uv tool install gh-backup

# pip
pip install gh-backup

# pipx
pipx install gh-backup
```

## Authentication

```bash
gh-backup auth
```

Checks that `gh` is authenticated and reports the active account and token scopes.

## Usage

### Export an organization

```bash
gh-backup export myorg --output /backups
```

### Export a user account

```bash
gh-backup export myusername --output /backups
```

Account type (org or user) is detected automatically.

### Options

| Option | Short | Description |
|---|---|---|
| `--output PATH` | `-o` | Directory to write exports into (required) |
| `--workers N` | `-w` | Parallel clone workers (default: 4, max: 32) |
| `--repos NAME` | `-r` | Only export specific repos (repeatable) |
| `--format` | | Archive format: `zst` (default), `gz`, or `xz` |
| `--no-compress` | | Keep raw export directory, skip archiving |
| `--keep-dir` | | Keep uncompressed directory after archiving |
| `--shallow` | | Shallow clone (`--depth 1`); faster but no full history |
| `--gc` | | Run `git gc --aggressive` on each clone to shrink pack files |
| `--dry-run` | | List repos that would be exported without writing anything |
| `--skip-forks` | | Exclude forked repositories |
| `--skip-archived` | | Exclude archived repositories |
| `--visibility` | | Only export repos with this visibility: `all` (default), `public`, or `private` |
| `--skip-issues` | | Skip issues and pull request export |
| `--verbose` | `-v` | Enable debug logging |

### Examples

```bash
# Export an org with more workers
gh-backup export myorg --output /backups --workers 8

# Export specific repos only
gh-backup export myorg --output /backups --repos frontend --repos backend

# Export a user account, skip issues, no compression
gh-backup export myusername --output /backups --skip-issues --no-compress

# Export with GZ compression instead of ZST
gh-backup export myorg --output /backups --format gz
```

Each run is saved to a timestamped subdirectory under the output directory, then compressed into a single archive.

## Output structure

The archive unpacks to:

```
<org>-<timestamp>/
├── metadata.json       # Export config and repo stats
├── repos/
│   ├── repo1.git       # Bare mirror clone (git clone --mirror)
│   └── ...
└── issues/
    ├── repo1/
    │   ├── issues.json
    │   └── pulls.json
    └── ...
```

## Exit codes

| Code | Meaning |
|---|---|
| `0` | Success |
| `1` | Export failed |
| `2` | Partial failure — some repos failed, others succeeded |
| `130` | Cancelled with Ctrl+C |

## Development

Requires [uv](https://docs.astral.sh/uv/).

```bash
make install    # Install dependencies and set up pre-commit hooks
make test       # Run the test suite
make lint       # Check linting and formatting (ruff)
make lint-fix   # Auto-fix linting and formatting issues
make commit     # Create a conventional commit (via commitizen)
```

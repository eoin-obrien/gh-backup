# gh-backup

Backup a GitHub organization or user: repos, issues, and pull requests.

Clones all repositories with full git history and exports issues/PRs as JSON, then compresses everything into a `.tar.zst` archive.

## Requirements

- [uv](https://docs.astral.sh/uv/)
- [GitHub CLI (`gh`)](https://cli.github.com/) — authenticated with a token that has `repo` and `read:org` scopes

## Setup

```bash
make install
```

This installs dependencies and sets up pre-commit hooks.

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
gh-backup export myusername --type user --output /backups
```

### Common options

| Option | Description |
|---|---|
| `--output PATH` | Directory to write exports into |
| `--workers N` | Parallel clone workers (default: 4) |
| `--repos NAME` | Only export specific repos (repeatable) |
| `--skip-issues` | Skip issues and pull request export |
| `--no-compress` | Keep raw export directory, skip archiving |
| `--keep-dir` | Keep uncompressed directory after archiving |
| `--format` | Archive format: `zst` (default) or `gz` |
| `--verbose` | Enable debug logging |

### Examples

```bash
# Export an org with more workers
gh-backup export myorg --output /backups --workers 8

# Export specific repos only
gh-backup export myorg --output /backups --repos frontend --repos backend

# Export a user, skip issues, no compression
gh-backup export myusername --output /backups --type user --skip-issues --no-compress
```

Each run is saved to a timestamped subdirectory under the output directory, then compressed into a single archive.

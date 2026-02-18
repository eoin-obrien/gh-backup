## 0.3.0 (2026-02-18)

### Feat

- add --verify flag to check archive integrity after compression
- add --shallow clone option
- add --dry-run mode
- add --skip-forks, --skip-archived, and --visibility filters
- warn on missing token scopes before export

### Fix

- remove partial clone directory on definitive failure
- use regex for robust gh auth status output parsing
- redact token from clone error messages
- read __version__ dynamically from package metadata

## 0.2.0 (2026-02-18)

### Feat

- clean cancellation on Ctrl+C with cooperative stop_event
- auto-detect account type instead of requiring --type flag
- expand progress to full width with stable column sizes
- add --gc flag to run git gc --aggressive before archiving

## 0.1.1 (2026-02-18)

## 0.1.0 (2026-02-18)

### Feat

- initial commit

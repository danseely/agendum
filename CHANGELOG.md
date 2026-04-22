## v0.5.1 (2026-04-22)

### Fix

- **syncer**: raise attention when review task flips to re-review requested

## v0.5.0 (2026-04-20)

### Feat

- **status**: introduce backlog + in-progress lifecycle for manual tasks (#42)

### Fix

- **syncer**: close orphaned review tasks when repo drops from fetched_repos (#40)

## v0.4.0 (2026-04-20)

### Feat

- **ui**: differentiate status colors per section

### Fix

- **ci**: dispatch homebrew tap from release workflow

## v0.3.0 (2026-04-19)

### Feat

- add isolated GitHub namespace switching

### Fix

- sync uv.lock package version
- configure git identity before tagging releases

## v0.2.0 (2026-04-18)

### Feat

- dispatch homebrew tap after published releases
- **docs**: add screenshot demo workflow

### Fix

- **ci**: attach validation check to release PRs
- budget DataTable column widths correctly
- **release**: harden release-next publish flow
- **ci**: trigger validation for release PRs
- detect re-review requests from timeline events

## v0.1.1 (2026-04-16)

### Feat

- add release discipline and packaging scaffolding
- add max retry limit for wake sync retries
- implement retry-with-backoff after wake from sleep
- replace monotonic-only sleep detection with wall-vs-monotonic drift
- **ui**: wrap title column instead of truncating
- **ui**: right-align link column in task table
- agendum — terminal dashboard for GitHub tasks

### Fix

- discard stale backoff timers after suspended state is cleared
- force-sync overrides suspended state, refresh status on retry exhaustion
- prevent periodic sync timer from forking wake-retry chain
- **sync**: handle null timestamps in review comparison
- **ui**: increase selected row contrast
- **ui**: restore cursor position after table refresh

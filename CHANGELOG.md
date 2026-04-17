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

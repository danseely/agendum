# Decisions

## 2026-04-25

- Decision: Treat issue `#51` as the canonical plan for the sync refactor and create repo-local planning memory before implementation.
- Reason: The issue explicitly replaces the prior direction from `#49` / `#50`, and this work is expected to span multiple sessions.
- Impact: Future sessions can recover scope, current slice, and drift from repo files instead of chat history.
- Plan change: yes

## 2026-04-25

- Decision: Start issue `#51` with phase 0, the local live benchmark harness, before any sync-path behavior changes.
- Reason: The issue requires a benchmark gate for every later sync-affecting PR, and the current repo has no checked-in harness or comparison helper.
- Impact: Subsequent slices can validate real API-use improvements against the current baseline instead of relying on intuition.
- Plan change: no

## 2026-04-25

- Decision: Instrument the benchmark by wrapping `agendum.gh._run_gh` inside the harness instead of adding benchmark-only branches to production sync code.
- Reason: phase 0 must measure the current hot path without changing sync behavior, and `gh._run_gh` is the narrowest stable interception point for call counts, payload bytes, and classification.
- Impact: The harness can observe real CLI usage while leaving `src/agendum/syncer.py` and `src/agendum/gh.py` behavior unchanged.
- Plan change: no

## 2026-04-25

- Decision: Store `gh_node_id` as a nullable indexed column and migrate it in `init_db()` instead of requiring a one-shot schema reset.
- Reason: phase 1 must support populated legacy DBs and mixed historical rows while preserving existing `gh_url` identity behavior.
- Impact: Future slices can reconcile tracked items by node id without breaking older rows that only have URLs.
- Plan change: no

## 2026-04-25

- Decision: Implement phase 2 discovery helpers with page-aware `gh api search/issues` queries rather than `gh search`.
- Reason: This slice needs explicit page control and pagination tests, which the `gh search` subcommands do not expose cleanly.
- Impact: The new helpers can dedupe predictably across pages and orgs while returning a small normalized skeleton for later hydration work.
- Plan change: no

## 2026-04-25

- Decision: Rename the issue-51 chunk shorthand in repo memory from `PR#` to `phase #`.
- Reason: The shorthand was being read as already-open GitHub pull requests, but these are planned implementation phases and only one real checkpoint PR is being opened now.
- Impact: Planning files and handoffs should now distinguish between issue phases and actual GitHub PRs more clearly.
- Plan change: no

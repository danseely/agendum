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

## 2026-04-25

- Decision: Land the phase-6 planner switch on the org-backed sync path first, while keeping explicit-repo configs on the legacy path temporarily.
- Reason: The live benchmark target is an org-backed workspace, and this keeps the hot-path cutover moving without destabilizing the heavily covered explicit-repo behavior in the same checkpoint.
- Impact: `src/agendum/syncer.py` now exercises open-only discovery plus targeted verification for org workspaces, but phase 6 is not complete until explicit-repo configs are moved off repo fanout and the live benchmark is rerun.
- Plan change: yes

## 2026-04-25

- Decision: Finish phase 6 for explicit-repo configs with repo-qualified `search/issues` discovery instead of keeping a permanent repo-only legacy branch.
- Reason: Explicit-repo workspaces still need the same open-only discovery and targeted missing-item verification model as org workspaces, and leaving repo fanout in place would contradict the issue-51 hot-path rules.
- Impact: `src/agendum/gh.py` now supports repo-scoped open discovery, and `src/agendum/syncer.py` no longer branches repo configs back to the legacy repo-fanout path.
- Plan change: no

## 2026-04-25

- Decision: Teach the live benchmark harness to classify phase-6 `gh api search/issues` discovery calls by lane before finalizing the PR benchmark summary.
- Reason: The initial phase-6 benchmark already showed the hot-path win, but lane attribution was misleading because the new open-search calls were falling through to `other`.
- Impact: `scripts/live_sync_bench.py` now reports authored, assigned, and review-requested open-discovery traffic under stable lane names, and the PR benchmark summary can describe the before/after mix accurately.
- Plan change: no

## 2026-04-25

- Decision: Treat phase 6 as complete and advance the active plan to phase 7 after the parity-fix benchmark rerun.
- Reason: The rerun still materially beats `main`, preserves the reduced hot-path call shape, and no longer shows the earlier `mulligan#1` semantic delta versus baseline.
- Impact: The active planning slice is now legacy hot-path cleanup and regression-budget hardening rather than more phase-6 parity work.
- Plan change: yes

## 2026-04-25

- Decision: Enforce the phase-7 benchmark budget with `scripts/compare_live_sync_bench.py --fail-on-regression` instead of relying on manual review of the printed delta report.
- Reason: Phase 7 is meant to stop repo fanout, per-review detail N+1, and unclassified `gh` traffic from creeping back in unnoticed; a failing compare mode makes that check repeatable.
- Impact: Future live before/after comparisons can fail fast when a candidate adds `repo_graphql`, `review_detail_graphql`, `other`, or a higher total `gh` call count.
- Plan change: no

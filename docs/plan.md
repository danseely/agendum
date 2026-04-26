# Plan

## Active goal

Deliver issue `#51` in the staged order from the issue body. `phase 0` through `phase 6` are now complete locally. The active next slice is `phase 7`: remove legacy hot-path code and tighten regression/budget assertions without changing the new steady-state sync semantics.

## Scope

- Remove old hot-path helpers no longer used by the switched planner path.
- Tighten regression/budget assertions so repo fanout and per-review detail N+1 cannot creep back in.
- Preserve the current phase-6 behavior and benchmark win while reducing leftover implementation drag.

## Constraints

- No new behavior scope.
- No reintroduction of repo fanout, per-review detail N+1, or org-wide terminal-history search lanes.
- Cleanup must preserve the current phase-6 semantics and benchmark shape.
- The live benchmark for this slice must still materially beat `main`.

## Invariants from issue 51

- Open-only broad discovery is the target steady-state hot path.
- Terminal verification must eventually be limited to tracked rows that disappear from open discovery.
- Repo fanout and per-review detail N+1 are still present in `main`; `phase 6` is the slice that must remove them from the hot path.
- Every later sync-affecting PR should use this harness for live before/after comparison.

## Non-goals for this slice

- No semantic redesign of statuses or reconciliation.
- No new sync-surface behavior beyond cleanup and hardening.

## Current drift check

- `phase 0` is complete in the repo and produced a local `adadaptedinc` benchmark baseline.
- `phase 1` is complete locally and stayed neutral versus the baseline.
- `phase 2` is complete locally and stayed neutral versus the baseline.
- `phase 3` is complete locally: `src/agendum/gh.py` now has lane-specific open-item hydration helpers with explicit batch sizing and tests, and the live benchmark stayed neutral versus the baseline.
- `phase 4` is complete locally: `src/agendum/gh.py` now has targeted missing-item verification helpers with node-id batching, URL fallback for legacy rows, and neutral live benchmark results.
- `phase 5` is complete locally: `src/agendum/syncer.py` now has a pure planner and normalized-model layer with fixture-backed parity tests, and the live benchmark stayed neutral versus the baseline.
- `src/agendum/gh.py` now exposes completeness-aware discovery, hydration, and missing-verification wrappers for both org and explicit-repo scopes, so `run_sync` can suppress closes when a lane or row is incomplete.
- `src/agendum/syncer.py` now routes both org-backed and explicit-repo configs through the planner-backed open-only flow.
- `src/agendum/syncer.py` now matches verified missing items by both `gh_node_id` and `gh_url`, which preserves phase-1 legacy-row compatibility when verification backfills a new node id.
- `src/agendum/syncer.py` no longer marks tasks unseen for metadata-only updates such as `gh_node_id` backfill.
- `scripts/live_sync_bench.py` now classifies the phase-6 `gh api search/issues` open-discovery calls into their real lane names instead of lumping them into `other`.
- The parity-fix `adadaptedinc` phase-6 rerun still materially beats `main` on wall time and `gh` call count.
- The parity-fix rerun no longer shows the earlier `mulligan#1` active-task delta versus `main`; cold and warm active-task counts now match the baseline.
- The planner path now preserves the main semantic constraints called out during review: out-of-scope existing authored/issue rows stay protected by planner fetched-scope gating, authored/issue labels still map into `tags`, and archived repos are suppressed on both org-backed and explicit-repo planner paths.
- No unapproved drift is currently known against the issue-51 phase-6 goals; the next work is planned phase-7 cleanup/hardening.

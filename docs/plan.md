# Plan

## Active goal

Deliver issue `#51` in the staged order from the issue body. `phase 0` through `phase 7` are now complete locally. The active next work is to refresh PR `#52`, rerun review on the realigned branch, and only return to code if review finds new drift.

## Scope

- Keep the phase-7 implementation aligned with the issue-51 constraints and benchmark shape.
- Use PR `#52` plus the live benchmark harness as the review surface for final issue-51 validation.
- Avoid new sync-surface work unless review uncovers a concrete problem.

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
- No new sync-surface behavior beyond the completed phase-7 cleanup/hardening work unless review requires it.

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
- `src/agendum/gh.py` no longer carries the old repo-fanout and per-review-detail hot-path helpers that the planner switch made dead.
- `src/agendum/syncer.py` no longer carries the legacy `_run_sync_once_legacy()` branch.
- `scripts/compare_live_sync_bench.py` now has a `--fail-on-regression` budget gate that fails if a candidate run reintroduces `repo_graphql`, `review_detail_graphql`, `other`, or a higher total `gh` call count.
- The phase-7 `adadaptedinc` rerun still materially beats `main`: cold `18.46s -> 6.24s`, warm `17.36s -> 6.36s`, and `12 -> 8` `gh` calls with active-task counts unchanged at `10`.
- No unapproved drift is currently known against the issue-51 implementation plan; the next work is PR refresh plus renewed review, not more planned implementation.

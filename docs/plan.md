# Plan

## Active goal

Deliver issue `#51` in the staged order from the issue body. `phase 0` through `phase 5` are complete locally. `phase 6` is functionally switched for both org-backed and explicit-repo configs, the live benchmark gate is complete, and the remaining work is PR cleanup plus review readiness.

## Scope

- Wire `run_sync` to the phase-2 through phase-5 groundwork.
- Use open-only discovery, lane-specific hydration, missing-row partitioning, targeted verification, normalization, and reconciliation.
- Make lane-specific close suppression explicit in production behavior.
- Preserve current task semantics while removing repo fanout and per-review detail N+1 from the hot path for both org-backed and explicit-repo workspaces.

## Constraints

- The production hot-path switch happens in this slice.
- No legacy helper cleanup beyond what is strictly required to switch.
- Partial-failure close suppression must remain safe by lane and by affected missing row.
- The live benchmark for this slice must materially beat `main`, not just stay neutral.

## Invariants from issue 51

- Open-only broad discovery is the target steady-state hot path.
- Terminal verification must eventually be limited to tracked rows that disappear from open discovery.
- Repo fanout and per-review detail N+1 are still present in `main`; `phase 6` is the slice that must remove them from the hot path.
- Every later sync-affecting PR should use this harness for live before/after comparison.

## Non-goals for this slice

- No legacy helper cleanup yet.

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
- The corrected `adadaptedinc` phase-6 rerun still materially beats `main` on wall time and `gh` call count while surfacing the same `mulligan#1` completeness delta.

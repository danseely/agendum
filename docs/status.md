# Status

## Current milestone

Issue `#51` / next up: phase 3 minimal hydration helpers.

## Done

- Loaded the issue-51 replacement plan from GitHub.
- Audited the current sync hot path in `src/agendum/syncer.py` and `src/agendum/gh.py`.
- Confirmed the repo still uses repo fanout plus per-review detail fetches.
- Established canonical planning files for this effort.
- Added `scripts/live_sync_bench.py` for isolated cold/warm live sync benchmarking.
- Added `scripts/compare_live_sync_bench.py` for before/after report comparison.
- Added `docs/live-sync-benchmark.md` with the required workflow and output fields.
- Added `tests/test_live_sync_bench.py` for call classification and report summary coverage.
- Ran a live one-run smoke benchmark against `adadaptedinc` and captured `/tmp/agendum-live-sync-bench-smoke.json`.
- Ran the full two-run local baseline against `adadaptedinc` and captured `/tmp/agendum-live-sync-bench-baseline.json`.
- Added nullable `gh_node_id` storage and safe legacy migration handling in `src/agendum/db.py`.
- Added bulk lookup by node id while preserving URL lookup behavior.
- Added DB tests covering empty create path, populated migration, URL-only history, and late node assignment.
- Ran the `phase 1` neutral benchmark and captured `/tmp/agendum-live-sync-bench-pr1.json`.
- Added page-aware open discovery helpers in `src/agendum/gh.py` for authored PRs, assigned issues, and review-requested PRs.
- Added `gh` tests covering minimal skeleton shape, page overlap, and cross-org dedupe.
- Ran the `phase 2` neutral benchmark and captured `/tmp/agendum-live-sync-bench-pr2.json`.

## In progress

- Checkpointing phases 0-2 in a real GitHub PR so work can safely resume in a later session.

## Blocked

- No repo-checked-in benchmark artifact exists; the baseline and subsequent slice runs live in local `/tmp` output only.

## Next

- Preserve or restage the local baseline numbers in the next sync-affecting PR description/comment.
- Use `scripts/compare_live_sync_bench.py` in the next sync-affecting PR.
- Start `phase 3` minimal hydration helpers when ready.

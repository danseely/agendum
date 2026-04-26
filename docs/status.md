# Status

## Current milestone

Issue `#51` / phase 6 production sync-path switch, benchmarked with corrected lane attribution and realigned to the plan, pending a refreshed review pass.

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
- Added lane-specific minimal hydration helpers in `src/agendum/gh.py` for authored PRs, review PRs, and issues.
- Kept the hydration queries lane-specific and limited to fields needed for current open-state derivation.
- Added `gh` tests covering hydration query shape, explicit batch sizing, order preservation, and wrong-type node filtering.
- Updated `scripts/live_sync_bench.py` so future live runs classify hydration GraphQL calls and extract GraphQL batch sizes.
- Ran the `phase 3` neutral benchmark and captured `/tmp/agendum-live-sync-bench-pr3.json`.
- Added targeted verification helpers in `src/agendum/gh.py` for missing authored PRs, issues, and review PRs.
- Batched node-id verification and kept URL-derived fallback limited to legacy rows lacking `gh_node_id`.
- Added `gh` tests covering verification query shape, explicit batching, and legacy URL fallback.
- Updated `scripts/live_sync_bench.py` so future live runs classify `verify_missing_*` GraphQL calls.
- Ran the `phase 4` neutral benchmark and captured `/tmp/agendum-live-sync-bench-pr4.json`.
- Added the pure planner and normalized-model layer in `src/agendum/syncer.py`.
- Defined explicit phase-5 types for open discovery coverage, open hydration bundles, missing verification requests, missing verification bundles, close suppression, and normalized incoming tasks.
- Added fixture-backed parity tests in `tests/test_syncer.py` for authored-heavy, review-heavy, issue-heavy, mixed-org, repo-only, and partial-failure planner worlds.
- Ran the `phase 5` neutral benchmark and captured `/tmp/agendum-live-sync-bench-pr5.json`.
- Added completeness-aware wrappers in `src/agendum/gh.py` for open discovery, open hydration, and missing-item verification.
- Switched the org-backed `run_sync` path in `src/agendum/syncer.py` to the planner-backed open-only pipeline.
- Started persisting `gh_node_id` updates during sync reconciliation instead of leaving active rows URL-only.
- Added org-path sync tests covering excluded repos, review-task closure without `fetched_repos`, and `gh_node_id` backfill.
- Added explicit-repo open-discovery helpers in `src/agendum/gh.py` so repo-config workspaces can use repo-qualified search instead of repo fanout.
- Switched explicit-repo configs in `src/agendum/syncer.py` to the planner-backed path and stopped branching back to the legacy repo-fanout flow.
- Added shared repo-path planner test helpers in `tests/syncer_test_helpers.py` and migrated the repo-config sync tests onto the new seam.
- Fixed verified-missing matching for legacy URL-only rows by indexing verification results by both `gh_node_id` and `gh_url`.
- Stopped metadata-only sync updates from marking tasks unseen.
- Ran the live phase-6 benchmark against `adadaptedinc` and captured `/tmp/agendum-live-sync-bench-pr52-phase6.json`.
- Posted the phase-6 benchmark comparison to PR `#52`.
- Fixed `scripts/live_sync_bench.py` classification for phase-6 `gh api search/issues` calls so authored, assigned, and review-requested open-discovery traffic is attributed to real lanes instead of `other`.
- Added benchmark-classification coverage in `tests/test_live_sync_bench.py` for the `api search/issues` query shapes used by phase 6.
- Reran the local phase-6 comparison after the classifier fix and confirmed the same hot-path win with corrected lane totals.
- Updated PR `#52` title/body so it describes the production switch instead of groundwork and includes the corrected benchmark summary.
- Added a superseding PR `#52` benchmark comment with the corrected lane attribution and current numbers.
- Pushed the full local phase-6 branch state to `origin/codex/issue-51-sync-foundation` in commit `e08115f`.
- Ran an independent review pass against `pr 52` and verified the reported findings locally.
- Restored planner-path fetched-scope parity for authored and issue rows by limiting missing verification to active repos and passing planner fetched scope into `diff_tasks()` in `src/agendum/syncer.py`.
- Added org-path regression coverage proving out-of-scope existing authored rows are preserved in `tests/test_syncer_edge_cases.py`.
- Reran `uv run pytest tests/test_live_sync_bench.py tests/test_gh.py tests/test_gh_edge_cases.py tests/test_syncer.py tests/test_syncer_edge_cases.py` after the scope fix.
- Restored planner-path label-to-`tags` propagation for authored PRs and issues in `src/agendum/gh.py` and `src/agendum/syncer.py`, with regression coverage in `tests/test_syncer_edge_cases.py`.
- Restored archived-repo suppression on the planner path by filtering org-backed hydrated items using `repository.isArchived` and batching explicit-repo archive-state lookup in `src/agendum/gh.py`.
- Added archive-state coverage in `tests/test_gh.py` plus org/repo planner regression coverage in `tests/test_syncer_edge_cases.py`.
- Reran `uv run pytest tests/test_live_sync_bench.py tests/test_gh.py tests/test_gh_edge_cases.py tests/test_syncer.py tests/test_syncer_edge_cases.py` after the label/archive fixes.

## In progress

- No code work is in progress; the branch is ready for a renewed review pass.

## Blocked

- No repo-checked-in benchmark artifact exists; the baseline and subsequent slice runs live in local `/tmp` output only.

## Next

- Re-run the review pass against the realigned phase-6 branch.
- Keep calling out `adadaptedinc/mulligan#1` as the only observed semantic delta versus `main`.
- Decide whether the parity fixes justify a fresh live benchmark rerun before pushing the next PR update.

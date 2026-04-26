# Status

## Current milestone

Issue `#51` implementation, the post-phase-7 parity fixes, and a follow-up title-clobber fix surfaced by a second-pass review are all landed on `origin/codex/issue-51-sync-foundation`. The live `adadaptedinc` benchmark gate passed against a fresh `main` baseline. PR `#52` is awaiting review response.

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
- Reran the live phase-6 benchmark after the parity fixes and captured `/tmp/agendum-live-sync-bench-pr52-phase6-parity.json`.
- Confirmed the parity branch still materially beats `main`: cold `18.46s -> 6.30s`, warm `17.36s -> 6.17s`, and `12 -> 8` `gh` calls.
- Confirmed the parity rerun no longer shows the earlier `mulligan#1` semantic delta; cold and warm active-task counts both match the baseline at `10`.
- Removed dead legacy hot-path helpers from `src/agendum/gh.py`, including the old repo-fanout and per-review-detail fetch paths.
- Removed `_run_sync_once_legacy()` from `src/agendum/syncer.py`.
- Removed the orphaned legacy `gh` edge-case tests that only exercised the deleted hot path.
- Added `--fail-on-regression` to `scripts/compare_live_sync_bench.py` so benchmark comparisons can fail fast on `repo_graphql`, `review_detail_graphql`, `other`, or total `gh` call-count regressions.
- Added benchmark-budget regression coverage in `tests/test_live_sync_bench.py`.
- Reran `uv run pytest tests/test_live_sync_bench.py tests/test_gh.py tests/test_gh_edge_cases.py tests/test_syncer.py tests/test_syncer_edge_cases.py` after the phase-7 cleanup.
- Reran the live benchmark for phase 7 and captured `/tmp/agendum-live-sync-bench-pr52-phase7.json`.
- Confirmed the phase-7 rerun still materially beats `main`: cold `18.46s -> 6.24s`, warm `17.36s -> 6.36s`, and `12 -> 8` `gh` calls, with no `repo_graphql`, `review_detail_graphql`, or `other` calls in the candidate.
- Confirmed `uv run python scripts/compare_live_sync_bench.py --fail-on-regression /tmp/agendum-live-sync-bench-baseline.json /tmp/agendum-live-sync-bench-pr52-phase7.json` exits cleanly.

- Extended `_planner_active_repos()` in `src/agendum/syncer.py` to add tracked-task repos whose owner is in `scoped_orgs`, restoring terminal verification for dormant in-scope repos on the org-backed path.
- Changed the explicit-repo archive filter in `_run_sync_once_planner()` to drop only repos confirmed archived (`is True`); repos missing from a partial archive-state response stay in scope.
- Updated `test_run_sync_org_path_preserves_out_of_scope_authored_tasks` so its `gh_repo` is in a foreign org (`other-org/other-repo`) — the existing safety semantic now matches the test name.
- Added `test_run_sync_org_path_verifies_tracked_authored_in_dormant_in_scope_repo` to lock in Fix 1 (dormant in-scope tracked task gets verified and closed when terminal).
- Added `test_run_sync_repo_path_keeps_unknown_archive_state_repo_in_scope` to lock in Fix 2 (partial archive lookup keeps healthy repos in scope and updates their tasks normally).
- Reran `uv run pytest tests/test_live_sync_bench.py tests/test_gh.py tests/test_gh_edge_cases.py tests/test_syncer.py tests/test_syncer_edge_cases.py` (114 passed) and `uv run pytest` (276 passed) on the parity-fix branch.
- Ran a second-pass review of PR `#52` against the parity-fix branch and found a title-clobber bug in the verified-terminal normalizers in `src/agendum/syncer.py`.
- Spawned an independent `crew:reviewer` agent to verify the title-clobber finding; agent reproduced it empirically across `_normalize_verified_authored_task`, `_normalize_verified_issue_task`, and `_normalize_verified_review_task`.
- Replaced `title=""` with `title=tracked.title or ""` in the three verified-terminal normalizers.
- Updated the two planner-test fixture expectations that pinned the buggy `title=""` output (`test_build_sync_plan_authored_heavy_world`, `test_build_sync_plan_partial_failure_world`).
- Added a `task["title"] == "Old PR"` assertion to `test_run_sync_marks_closed_authored_pr_closed`.
- Added `test_run_sync_marks_closed_assigned_issue_closed` and `test_run_sync_marks_dropped_review_request_done` covering the issue and review-PR terminal verification paths.
- Reran `uv run pytest tests/test_syncer.py tests/test_syncer_edge_cases.py` (53 passed) and `uv run pytest` (278 passed) on the title-fix branch.
- Regenerated the live `main` baseline against `43cc532` at `/tmp/agendum-live-sync-bench-baseline.json`.
- Captured the live title-fix candidate at `/tmp/agendum-live-sync-bench-pr52-titlefix.json`.
- Confirmed `uv run python scripts/compare_live_sync_bench.py --fail-on-regression /tmp/agendum-live-sync-bench-baseline.json /tmp/agendum-live-sync-bench-pr52-titlefix.json` exits cleanly.
- Pushed the title-fix commit `e68ee25` to `origin/codex/issue-51-sync-foundation`.

## In progress

- No code work is in progress; the title-fix commit is pushed and the PR is awaiting review response.

## Blocked

- No repo-checked-in benchmark artifact exists; the baseline and subsequent slice runs live in local `/tmp` output only.

## Next

- Wait for review response on PR `#52`. If reviewer requests changes, re-enter the workflow. If approved, merge.

# Handoff

## Current objective

Issue `#51` implementation is fully landed on `origin/codex/issue-51-sync-foundation`, including the post-phase-7 parity fixes and a follow-up title-clobber fix from a second-pass review. The live `adadaptedinc` benchmark gate passes against a fresh `main` baseline. PR `#52` is now awaiting review response.

## Branch

`codex/issue-51-sync-foundation`

## Completed

- Read issue `#51` and mapped its requirements to the current codebase.
- Confirmed the current hot path still uses repo fanout and per-review detail fetches.
- Created canonical planning memory in `docs/plan.md`, `docs/status.md`, and `docs/decisions.md`.
- Added `scripts/live_sync_bench.py` to benchmark cold and warm syncs in an isolated temp workspace.
- Added `scripts/compare_live_sync_bench.py` to print before/after deltas for wall time, `gh` calls, payload bytes, and classification totals.
- Added `docs/live-sync-benchmark.md` and `tests/test_live_sync_bench.py`.
- Ran a live smoke benchmark with `uv run python scripts/live_sync_bench.py --org adadaptedinc --runs 1 --output /tmp/agendum-live-sync-bench-smoke.json`.
- Ran the full local baseline with `uv run python scripts/live_sync_bench.py --org adadaptedinc --runs 2 --output /tmp/agendum-live-sync-bench-baseline.json`.
- Added `gh_node_id` to the DB schema and made `init_db()` backfill the column safely on legacy DBs.
- Added `find_tasks_by_gh_node_ids()` for bulk node-id lookup.
- Added DB tests for populated migration, URL-only historical rows, and rows that gain a node id later.
- Ran the `phase 1` neutral benchmark with `uv run python scripts/live_sync_bench.py --org adadaptedinc --runs 2 --output /tmp/agendum-live-sync-bench-pr1.json`.
- Added `search_open_authored_prs()`, `search_open_assigned_issues()`, and `search_open_review_requested_prs()` to `src/agendum/gh.py`.
- Kept the new discovery results minimal: `gh_node_id`, `number`, `title`, `url`, and `repository.nameWithOwner`.
- Added `gh` tests for pagination, overlap dedupe, and stable minimal discovery shape.
- Ran the `phase 2` neutral benchmark with `uv run python scripts/live_sync_bench.py --org adadaptedinc --runs 2 --output /tmp/agendum-live-sync-bench-pr2.json`.
- Added `hydrate_open_authored_prs()`, `hydrate_open_review_prs()`, and `hydrate_open_issues()` to `src/agendum/gh.py`.
- Kept the hydration queries lane-specific and limited to current open-state derivation fields instead of introducing a shared oversized PR fragment.
- Made hydration batch sizing explicit with a helper-level `batch_size` parameter and batching tests.
- Updated `scripts/live_sync_bench.py` so hydration GraphQL calls classify as `hydrate_*` and GraphQL node batch sizes are recorded.
- Ran the `phase 3` neutral benchmark with `uv run python scripts/live_sync_bench.py --org adadaptedinc --runs 2 --output /tmp/agendum-live-sync-bench-pr3.json`.
- Added `verify_missing_authored_prs()`, `verify_missing_issues()`, and `verify_missing_review_prs()` to `src/agendum/gh.py`.
- Batched node-id verification through lane-specific GraphQL queries and kept URL-derived fallback limited to legacy rows lacking `gh_node_id`.
- Kept the verification queries minimal: authored returns terminal PR state, issues return state plus assignment membership, and review PRs return state plus review-request membership.
- Updated `scripts/live_sync_bench.py` so verification GraphQL calls classify as `verify_missing_*`.
- Ran the `phase 4` neutral benchmark with `uv run python scripts/live_sync_bench.py --org adadaptedinc --runs 2 --output /tmp/agendum-live-sync-bench-pr4.json`.
- Added the phase-5 pure planner layer to `src/agendum/syncer.py`, including typed coverage, hydration, missing-verification, suppression, and normalized-task models.
- Added `build_sync_plan()` plus pure normalization and suppression helpers without switching the production `run_sync` path yet.
- Added fixture-backed parity tests in `tests/test_syncer.py` for authored-heavy, review-heavy, issue-heavy, mixed-org, repo-only, and partial-failure worlds.
- Ran the `phase 5` neutral benchmark with `uv run python scripts/live_sync_bench.py --org adadaptedinc --runs 2 --output /tmp/agendum-live-sync-bench-pr5.json`.
- Started phase 6 in `src/agendum/syncer.py` by adding explicit lane-wide and row-level close suppression support to `diff_tasks()`.
- Added `syncer` edge-case tests covering lane-wide and row-level close suppression behavior.
- Added completeness-aware `gh` wrappers for open discovery, hydration, and missing-item verification so the syncer can distinguish empty lanes from incomplete lanes.
- Switched the org-backed `run_sync` path in `src/agendum/syncer.py` to the planner-backed open-only flow and routed notifications/diff application through shared helpers.
- Updated sync reconciliation so active rows can pick up `gh_node_id` during creates and updates.
- Added org-path tests covering excluded-repo filtering, review-task closure without the legacy `fetched_repos` guard, and `gh_node_id` backfill.
- Added explicit-repo search helpers in `src/agendum/gh.py` using repo-qualified `search/issues` queries.
- Switched explicit-repo configs in `src/agendum/syncer.py` onto the same planner-backed path as org workspaces.
- Added `tests/syncer_test_helpers.py` and migrated the repo-config sync tests onto planner-path mocks instead of legacy repo GraphQL mocks.
- Fixed legacy URL-only verified-item matching and metadata-only unseen churn in `src/agendum/syncer.py`.
- Ran `uv run python scripts/live_sync_bench.py --org adadaptedinc --runs 2 --output /tmp/agendum-live-sync-bench-pr52-phase6.json`.
- Ran `uv run python scripts/compare_live_sync_bench.py /tmp/agendum-live-sync-bench-baseline.json /tmp/agendum-live-sync-bench-pr52-phase6.json`.
- Confirmed the `+1` active-task delta is `https://github.com/adadaptedinc/mulligan/pull/1`, which current `main` does not surface.
- Updated PR `#52` body to describe the current phase-6 state and added a benchmark comment with the before/after numbers plus the `mulligan#1` note.
- Fixed `scripts/live_sync_bench.py` so the new phase-6 `gh api search/issues` calls classify as `search_open_*` lanes instead of `other`.
- Added `tests/test_live_sync_bench.py` coverage for the `api search/issues` command shapes used by phase 6.
- Reran `uv run python scripts/compare_live_sync_bench.py /tmp/agendum-live-sync-bench-baseline.json /tmp/agendum-live-sync-bench-pr52-phase6.json` and confirmed the same hot-path win with corrected lane attribution.
- Updated PR `#52` title/body so the visible status now says the hot-path switch is complete and benchmarked.
- Added a superseding PR `#52` benchmark comment with the corrected lane attribution and current comparison numbers.
- Pushed the full local phase-6 branch state to `origin/codex/issue-51-sync-foundation` in commit `e08115f`.
- Ran an independent review pass on `pr 52` and verified the reported regressions against the current code.
- Restored planner-path fetched-scope parity for authored and issue rows in `src/agendum/syncer.py` by deriving planner active repos, filtering missing verification to that scope, and passing planner fetched scope into `diff_tasks()`.
- Added `test_run_sync_org_path_preserves_out_of_scope_authored_tasks()` in `tests/test_syncer_edge_cases.py`.
- Reran `uv run pytest tests/test_live_sync_bench.py tests/test_gh.py tests/test_gh_edge_cases.py tests/test_syncer.py tests/test_syncer_edge_cases.py` after the scope fix.
- Restored planner-path label-to-`tags` propagation for authored PRs and issues by adding `labels` back to the authored/issue hydration shapes in `src/agendum/gh.py` and serializing them in `src/agendum/syncer.py`.
- Restored planner-path archived-repo suppression by filtering org-backed hydrated items on `repository.isArchived` and batching explicit-repo archive-state lookup with `fetch_repo_archive_states_with_completeness()` in `src/agendum/gh.py`.
- Added regression coverage for label parity and archived-repo suppression in `tests/test_gh.py` and `tests/test_syncer_edge_cases.py`.
- Reran `uv run pytest tests/test_live_sync_bench.py tests/test_gh.py tests/test_gh_edge_cases.py tests/test_syncer.py tests/test_syncer_edge_cases.py` after the label/archive fixes.
- Ran `uv run python scripts/live_sync_bench.py --org adadaptedinc --runs 2 --output /tmp/agendum-live-sync-bench-pr52-phase6-parity.json`.
- Ran `uv run python scripts/compare_live_sync_bench.py /tmp/agendum-live-sync-bench-baseline.json /tmp/agendum-live-sync-bench-pr52-phase6-parity.json`.
- Confirmed the parity rerun still materially beats `main` and no longer shows the earlier `mulligan#1` active-task delta.
- Removed the dead repo-fanout and per-review-detail helpers from `src/agendum/gh.py`.
- Removed the dead `_run_sync_once_legacy()` branch from `src/agendum/syncer.py`.
- Removed legacy-only edge-case tests from `tests/test_gh_edge_cases.py` and updated `tests/test_gh.py` to stop importing removed helpers.
- Added `find_budget_regressions()` plus `--fail-on-regression` to `scripts/compare_live_sync_bench.py`.
- Added `tests/test_live_sync_bench.py` coverage for the phase-7 benchmark budget gate.
- Ran `uv run pytest tests/test_live_sync_bench.py tests/test_gh.py tests/test_gh_edge_cases.py tests/test_syncer.py tests/test_syncer_edge_cases.py` after the phase-7 cleanup and hardening changes.
- Ran `uv run python -m py_compile scripts/compare_live_sync_bench.py src/agendum/gh.py src/agendum/syncer.py tests/test_gh.py tests/test_gh_edge_cases.py tests/test_live_sync_bench.py`.
- Ran `uv run python scripts/live_sync_bench.py --org adadaptedinc --runs 2 --output /tmp/agendum-live-sync-bench-pr52-phase7.json`.
- Ran `uv run python scripts/compare_live_sync_bench.py --fail-on-regression /tmp/agendum-live-sync-bench-baseline.json /tmp/agendum-live-sync-bench-pr52-phase7.json`.
- Confirmed the phase-7 rerun still materially beats `main`: cold `18.46s -> 6.24s`, warm `17.36s -> 6.36s`, and `12 -> 8` `gh` calls with active-task counts unchanged at `10`.
- Ran a second-pass review of PR `#52` and identified a title-clobber bug: the three verified-terminal normalizers in `src/agendum/syncer.py` built `NormalizedIncomingTask(title="")`, which `as_dict()` emitted unconditionally and `diff_tasks` then wrote through to the DB on the `to_update` path.
- Spawned an independent `crew:reviewer` agent which reproduced the bug across all three normalizers and confirmed the test gap.
- Replaced `title=""` with `title=tracked.title or ""` in `_normalize_verified_authored_task`, `_normalize_verified_issue_task`, and `_normalize_verified_review_task`.
- Updated the planner-test fixture expectations in `test_build_sync_plan_authored_heavy_world` and `test_build_sync_plan_partial_failure_world` to use the tracked titles instead of `""`.
- Added a `task["title"]` assertion to `test_run_sync_marks_closed_authored_pr_closed` and added sibling regression tests `test_run_sync_marks_closed_assigned_issue_closed` and `test_run_sync_marks_dropped_review_request_done`.
- Regenerated the live `main` baseline against `43cc532` at `/tmp/agendum-live-sync-bench-baseline.json`.
- Captured the title-fix candidate at `/tmp/agendum-live-sync-bench-pr52-titlefix.json`.
- Confirmed `uv run python scripts/compare_live_sync_bench.py --fail-on-regression /tmp/agendum-live-sync-bench-baseline.json /tmp/agendum-live-sync-bench-pr52-titlefix.json` exits cleanly: cold `19.24s -> 6.55s`, warm `19.15s -> 6.47s`, `12 -> 8` `gh` calls, lane shape unchanged.
- Pushed the title-fix commit `e68ee25` to `origin/codex/issue-51-sync-foundation`.

## Validation

- `uv run pytest tests/test_live_sync_bench.py`
- `uv run python -m py_compile scripts/live_sync_bench.py scripts/compare_live_sync_bench.py`
- `uv run python scripts/live_sync_bench.py --help`
- `uv run python scripts/compare_live_sync_bench.py --help`
- `uv run python scripts/live_sync_bench.py --org adadaptedinc --runs 1 --output /tmp/agendum-live-sync-bench-smoke.json`
- `uv run python scripts/compare_live_sync_bench.py /tmp/agendum-live-sync-bench-smoke.json /tmp/agendum-live-sync-bench-smoke.json`
- `uv run pytest tests/test_live_sync_bench.py tests/test_release_workflow_docs.py`
- `uv run python scripts/live_sync_bench.py --org adadaptedinc --runs 2 --output /tmp/agendum-live-sync-bench-baseline.json`
- `uv run python scripts/compare_live_sync_bench.py /tmp/agendum-live-sync-bench-baseline.json /tmp/agendum-live-sync-bench-baseline.json`
- `uv run pytest tests/test_db.py tests/test_db_edge_cases.py`
- `uv run python -m py_compile src/agendum/db.py`
- `uv run python scripts/live_sync_bench.py --org adadaptedinc --runs 2 --output /tmp/agendum-live-sync-bench-pr1.json`
- `uv run python scripts/compare_live_sync_bench.py /tmp/agendum-live-sync-bench-baseline.json /tmp/agendum-live-sync-bench-pr1.json`
- `uv run pytest tests/test_gh.py tests/test_gh_edge_cases.py`
- `uv run python -m py_compile src/agendum/gh.py`
- `uv run python scripts/live_sync_bench.py --org adadaptedinc --runs 2 --output /tmp/agendum-live-sync-bench-pr2.json`
- `uv run python scripts/compare_live_sync_bench.py /tmp/agendum-live-sync-bench-baseline.json /tmp/agendum-live-sync-bench-pr2.json`
- `uv run pytest tests/test_gh.py tests/test_gh_edge_cases.py tests/test_live_sync_bench.py`
- `uv run python -m py_compile src/agendum/gh.py scripts/live_sync_bench.py`
- `uv run python scripts/live_sync_bench.py --org adadaptedinc --runs 2 --output /tmp/agendum-live-sync-bench-pr3.json`
- `uv run python scripts/compare_live_sync_bench.py /tmp/agendum-live-sync-bench-baseline.json /tmp/agendum-live-sync-bench-pr3.json`
- `uv run pytest tests/test_gh.py tests/test_gh_edge_cases.py tests/test_live_sync_bench.py`
- `uv run python -m py_compile src/agendum/gh.py scripts/live_sync_bench.py`
- `uv run python scripts/live_sync_bench.py --org adadaptedinc --runs 2 --output /tmp/agendum-live-sync-bench-pr4.json`
- `uv run python scripts/compare_live_sync_bench.py /tmp/agendum-live-sync-bench-baseline.json /tmp/agendum-live-sync-bench-pr4.json`
- `uv run pytest tests/test_syncer.py tests/test_syncer_edge_cases.py`
- `uv run python -m py_compile src/agendum/syncer.py tests/test_syncer.py`
- `uv run python scripts/live_sync_bench.py --org adadaptedinc --runs 2 --output /tmp/agendum-live-sync-bench-pr5.json`
- `uv run python scripts/compare_live_sync_bench.py /tmp/agendum-live-sync-bench-baseline.json /tmp/agendum-live-sync-bench-pr5.json`
- `uv run pytest tests/test_syncer.py tests/test_syncer_edge_cases.py`
- `uv run python -m py_compile src/agendum/syncer.py tests/test_syncer_edge_cases.py`
- `uv run pytest tests/test_gh.py tests/test_gh_edge_cases.py tests/test_syncer.py tests/test_syncer_edge_cases.py`
- `uv run python -m py_compile src/agendum/gh.py src/agendum/syncer.py tests/test_syncer_edge_cases.py`
- `uv run pytest tests/test_syncer.py tests/test_syncer_edge_cases.py`
- `uv run python -m py_compile src/agendum/gh.py src/agendum/syncer.py tests/syncer_test_helpers.py tests/test_syncer.py tests/test_syncer_edge_cases.py`
- `uv run python scripts/live_sync_bench.py --org adadaptedinc --runs 2 --output /tmp/agendum-live-sync-bench-pr52-phase6.json`
- `uv run python scripts/compare_live_sync_bench.py /tmp/agendum-live-sync-bench-baseline.json /tmp/agendum-live-sync-bench-pr52-phase6.json`
- `uv run pytest tests/test_live_sync_bench.py tests/test_gh.py tests/test_gh_edge_cases.py tests/test_syncer.py tests/test_syncer_edge_cases.py`
- `uv run python scripts/compare_live_sync_bench.py /tmp/agendum-live-sync-bench-baseline.json /tmp/agendum-live-sync-bench-pr52-phase6.json`
- `uv run pytest tests/test_syncer.py tests/test_syncer_edge_cases.py` (53 passed) on the title-fix branch
- `uv run pytest` (278 passed) on the title-fix branch
- `uv run python scripts/live_sync_bench.py --org adadaptedinc --runs 2 --output /tmp/agendum-live-sync-bench-baseline.json` (against `main` `43cc532`)
- `uv run python scripts/live_sync_bench.py --org adadaptedinc --runs 2 --output /tmp/agendum-live-sync-bench-pr52-titlefix.json`
- `uv run python scripts/compare_live_sync_bench.py --fail-on-regression /tmp/agendum-live-sync-bench-baseline.json /tmp/agendum-live-sync-bench-pr52-titlefix.json`

## Changed files

- `docs/plan.md`
- `docs/status.md`
- `docs/decisions.md`
- `docs/handoff.md`
- `docs/live-sync-benchmark.md`
- `src/agendum/db.py`
- `src/agendum/gh.py`
- `src/agendum/syncer.py`
- `scripts/live_sync_bench.py`
- `scripts/compare_live_sync_bench.py`
- `tests/syncer_test_helpers.py`
- `tests/test_db.py`
- `tests/test_db_edge_cases.py`
- `tests/test_gh.py`
- `tests/test_gh_edge_cases.py`
- `tests/test_live_sync_bench.py`
- `tests/test_syncer.py`
- `tests/test_syncer_edge_cases.py`

## Risks / blockers

- The harness must measure real `gh` usage without mutating the user's live workspace.
- The current code has no built-in sync metrics surface, so the harness needs to instrument `gh` calls directly.
- The benchmark outputs are local temp data, not checked-in artifacts.
- No outstanding code blockers: the title-fix commit is pushed, the live benchmark gate passes, and the test suite is green.

## Benchmark snapshot

- Baseline output: `/tmp/agendum-live-sync-bench-baseline.json`
- Cold average over 2 runs: `18.46s`, `12` `gh` calls, `291919` payload bytes, `10` changes, `10` active tasks.
- Warm average over 2 runs: `17.36s`, `12` `gh` calls, `291919` payload bytes, `0` changes, `10` active tasks.
- Per run:
  run 1 cold `19.57s`, warm `17.51s`
  run 2 cold `17.35s`, warm `17.21s`
- Call mix per phase: `1` user lookup, `1` open-authored search, `1` open-assigned-issues search, `2` review-requested searches, `4` repo GraphQL calls, `2` review-detail GraphQL calls, `1` notifications poll.
- `phase 1` comparison versus baseline: same `gh` calls, same payload bytes, same task counts and changes; wall time moved within expected live-run noise (`-6.3%` cold, `+1.1%` warm).
- `phase 2` comparison versus baseline: same `gh` calls, same payload bytes, same task counts and changes; wall time stayed within expected live-run noise (`-4.4%` cold, `+0.6%` warm).
- `phase 3` comparison versus baseline: same `gh` calls, same payload bytes, same task counts and changes; wall time stayed within expected live-run noise (`-5.2%` cold, `+1.0%` warm).
- `phase 4` comparison versus baseline: same `gh` calls, same payload bytes, same task counts and changes; wall time stayed within expected live-run noise (`-4.9%` cold, `-1.7%` warm).
- `phase 5` comparison versus baseline: same `gh` calls, same payload bytes, same task counts and changes; wall time stayed within expected live-run noise (`-7.3%` cold, `-2.5%` warm).
- Corrected `phase 6` comparison versus baseline: cold `6.42s` vs `18.46s` (`-65.2%`), warm `6.19s` vs `17.36s` (`-64.3%`), and `8` `gh` calls vs `12` (`-33.3%`) on both cold and warm runs.
- `phase 6` payload bytes increased to `335908` (`+15.1%`) because the new open-search plus hydration shape trades fewer calls for somewhat larger batched responses.
- The corrected call mix now shows `repo_graphql` and `review_detail_graphql` removed from the hot path, replaced by `search_open_*` plus `hydrate_open_*` lanes without any benchmark traffic falling into `other`.
- Phase-6 parity rerun versus baseline: cold `6.30s` vs `18.46s` (`-65.9%`), warm `6.17s` vs `17.36s` (`-64.4%`), `8` `gh` calls vs `12` (`-33.3%`), and active-task counts matched baseline at `10` on both cold and warm runs.
- Phase-6 parity payload bytes increased slightly again to `336314` (`+15.2%`), still within the same reduced-call batched-response tradeoff.
- Phase-7 rerun versus baseline: cold `6.24s` vs `18.46s` (`-66.2%`), warm `6.36s` vs `17.36s` (`-63.3%`), `8` `gh` calls vs `12` (`-33.3%`), and active-task counts still matched baseline at `10` on both cold and warm runs.
- The phase-7 candidate keeps `repo_graphql`, `review_detail_graphql`, and `other` at `0`, and `scripts/compare_live_sync_bench.py --fail-on-regression` passes against the baseline.
- Title-fix rerun versus a fresh `main` baseline (`43cc532`): cold `6.55s` vs `19.24s` (`-66.0%`), warm `6.47s` vs `19.15s` (`-66.2%`), `8` `gh` calls vs `12` (`-33.3%`), payload bytes `336314` vs `291919` (`+15.2%`), and active-task counts matched at `10` on both cold and warm runs. The title fix is pure normalization, so the lane shape is unchanged from the parity-fix run.

## Next actions

1. Wait for review response on PR `#52`. If reviewer requests changes, re-enter the workflow. If approved, merge.

## Drift from original plan

- The previously identified phase-6 semantic drift has been resolved in code and regression coverage, and the parity rerun confirms the benchmark gate still holds.
- Planning memory was missing from the repo and has now been added.
- The repo memory had stale “checkpoint PR” language after PR `#52` was opened; `docs/plan.md`, `docs/status.md`, and `docs/handoff.md` now point at the active post-phase-4 state instead.
- The post-phase-7 review surfaced two parity regressions (org-backed terminal verification scope, explicit-repo archive-state incompleteness). Both are now resolved in code with regression coverage and the live benchmark gate passes.
- A second-pass review surfaced a title-clobber bug in the verified-terminal normalizers (independent of the issue-51 hot-path goals). It is resolved in commit `e68ee25` with regression coverage and the live benchmark gate still passes. No further unapproved drift is open.

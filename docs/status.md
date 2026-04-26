# Status

## Current milestone

Issue `#51` implementation is fully landed on `origin/codex/issue-51-sync-foundation` (HEAD `426e4bb`). The branch is rebased onto `origin/main` (`43cc532`), the live `adadaptedinc` benchmark gate passes, and the full pytest suite passes. PR `#52` is awaiting merge approval.

## Done

- Phase 0: live benchmark harness (`scripts/live_sync_bench.py`, `scripts/compare_live_sync_bench.py`, `docs/live-sync-benchmark.md`, `tests/test_live_sync_bench.py`).
- Phase 1: `gh_node_id` column with safe migration in `src/agendum/db.py`, plus bulk node-id lookup and DB tests.
- Phase 2: page-aware open discovery helpers in `src/agendum/gh.py` for authored PRs, assigned issues, and review-requested PRs (org-scoped and repo-scoped).
- Phase 3: lane-specific minimal hydration helpers in `src/agendum/gh.py` with explicit batch sizing.
- Phase 4: targeted missing-item verification helpers in `src/agendum/gh.py` with node-id batching and URL fallback for legacy rows.
- Phase 5: pure planner and normalized-model layer in `src/agendum/syncer.py` with fixture-backed parity tests.
- Phase 6: `run_sync` switched to the planner-backed open-only pipeline for both org-backed and explicit-repo configs; legacy URL-only matching and metadata-only unseen churn fixed; lane attribution corrected in the bench harness.
- Phase 7: dead repo-fanout and per-review-detail helpers removed from `src/agendum/gh.py`; `_run_sync_once_legacy()` removed from `src/agendum/syncer.py`; `--fail-on-regression` budget gate added to `scripts/compare_live_sync_bench.py`.
- Post-phase-7 parity fixes: org-backed `_planner_active_repos` extends `active_repos` with tracked-task repos whose owner is in `scoped_orgs`; explicit-repo archive filter drops only `isArchived is True`; both with regression coverage.
- Title-clobber fix: verified-terminal normalizers in `src/agendum/syncer.py` now pass `tracked.title` through; planner-test fixture expectations and integration tests updated; sibling regression tests added for issue and review-PR terminal verification.
- Live benchmark history per phase recorded in `docs/handoff.md`; final gate at `426e4bb` versus `main` `43cc532` is cold `19.24s -> 6.55s`, warm `19.15s -> 6.47s`, `12 -> 8` `gh` calls, lane shape clean.
- Full pytest: 278 passed.
- Branch rebased onto `origin/main` (`43cc532`) with no conflicts; force-push-with-lease landed at `426e4bb`; CI re-queued and ran clean.
- Follow-up issue `#53` filed for the latent `compute_close_suppression` keying inconsistency surfaced during the third reviewer pass.

## In progress

- None. Implementation work is complete; PR is awaiting merge approval.

## Blocked

- None.

## Next

- Merge PR `#52` once approved. If reviewer requests changes, re-enter the workflow.
- Treat issue `#53` as a separate, low-priority follow-up.

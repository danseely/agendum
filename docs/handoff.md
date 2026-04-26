# Handoff

## Current objective

Issue `#51` implementation, the post-phase-7 parity fixes, and the second-pass title-clobber fix are landed on `origin/codex/issue-51-sync-foundation`. The branch is rebased onto `origin/main` (`43cc532`), the live `adadaptedinc` benchmark gate passes, and the full pytest suite passes. PR `#52` is awaiting merge approval.

## Branch

`codex/issue-51-sync-foundation` at `426e4bb` (post-rebase).

## Completed

- All seven issue-51 phases (`0` benchmark harness, `1` `gh_node_id` DB groundwork, `2` open-only discovery, `3` lane-specific hydration, `4` targeted missing-item verification, `5` pure planner, `6` `run_sync` switch on both org and explicit-repo paths, `7` legacy code removal plus benchmark budget gate).
- Post-phase-7 parity fixes: tracked-task repos in scoped orgs are added to `_planner_active_repos`, and the explicit-repo archive filter drops only `isArchived is True`. Regression tests `test_run_sync_org_path_verifies_tracked_authored_in_dormant_in_scope_repo` and `test_run_sync_repo_path_keeps_unknown_archive_state_repo_in_scope` lock in the fixes.
- Title-clobber fix: the three verified-terminal normalizers in `src/agendum/syncer.py` now pass `tracked.title` through. Two planner-test fixture expectations were updated; `test_run_sync_marks_closed_authored_pr_closed` gained a title assertion; sibling regression tests `test_run_sync_marks_closed_assigned_issue_closed` and `test_run_sync_marks_dropped_review_request_done` were added.
- Three independent reviewer passes against PR `#52`: the first found two parity drifts (now fixed), the second reproduced the title-clobber bug (now fixed), the third approved the branch with one latent observation that was filed as issue `#53` (out of scope here).
- Branch rebased onto `origin/main` (`43cc532`) with no conflicts. Production code, tests, scripts, and docs are byte-identical to the pre-rebase tip after replay; only the new workflow file from main was added on top. Force-push-with-lease landed at `426e4bb`; CI re-queued and ran clean.
- Follow-up issue `#53` filed for the latent `compute_close_suppression` keying inconsistency.

## Validation

Final gates at branch tip `426e4bb`:

- `uv run pytest` — 278 passed.
- `uv run python scripts/live_sync_bench.py --org adadaptedinc --runs 2 --output /tmp/agendum-live-sync-bench-baseline.json` (against `main` `43cc532`).
- `uv run python scripts/live_sync_bench.py --org adadaptedinc --runs 2 --output /tmp/agendum-live-sync-bench-pr52-titlefix.json`.
- `uv run python scripts/compare_live_sync_bench.py --fail-on-regression /tmp/agendum-live-sync-bench-baseline.json /tmp/agendum-live-sync-bench-pr52-titlefix.json` exits cleanly.

Per-phase neutral benchmarks and pytest invocations are not repeated here; they were captured at the time of each phase commit and the corresponding output files lived in `/tmp` during that work.

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

- The harness must measure real `gh` usage without mutating the user's live workspace; this is enforced by using a temp DB and copied auth.
- Benchmark output files are local temp data, not checked-in artifacts; reproducing the gate requires running the harness locally.
- No outstanding code blockers.

## Benchmark snapshot

Final gate at `426e4bb` versus `main` `43cc532`, 2 runs each:

- Cold: `19.24s -> 6.55s` (`-66.0%`).
- Warm: `19.15s -> 6.47s` (`-66.2%`).
- `gh` calls: `12 -> 8` (`-33.3%`).
- Payload bytes: `291919 -> 336314` (`+15.2%`, expected from fewer-but-batched responses).
- Active tasks: `10 -> 10` on both cold and warm runs.
- Candidate keeps `repo_graphql`, `review_detail_graphql`, and `other` at `0`.

The rebase did not change this snapshot; the title fix is pure normalization and the rebased commits are byte-identical at file level.

## Next actions

1. Merge PR `#52` once approved.
2. Treat issue `#53` (latent `compute_close_suppression` keying inconsistency) as a separate, low-priority follow-up.

## Drift from original plan

- Planning memory was missing from the repo at the start of this effort and was added.
- The post-phase-7 review surfaced two parity regressions; both are resolved with regression coverage.
- The second-pass review surfaced a title-clobber bug independent of the issue-51 hot-path goals; it is resolved with regression coverage.
- The third reviewer pass surfaced a latent keying inconsistency in `compute_close_suppression` with no observable wrong behavior today; it is filed as issue `#53` rather than fixed inline to keep this PR scoped.
- No further unapproved drift is open.

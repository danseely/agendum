# Handoff

## Current objective

Checkpoint phases 0-2 of issue `#51` in a real GitHub PR so the next session can resume with phase 3 minimal hydration helpers.

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

## Changed files

- `docs/plan.md`
- `docs/status.md`
- `docs/decisions.md`
- `docs/handoff.md`
- `docs/live-sync-benchmark.md`
- `src/agendum/db.py`
- `src/agendum/gh.py`
- `scripts/live_sync_bench.py`
- `scripts/compare_live_sync_bench.py`
- `tests/test_db.py`
- `tests/test_db_edge_cases.py`
- `tests/test_gh_edge_cases.py`
- `tests/test_live_sync_bench.py`

## Risks / blockers

- The harness must measure real `gh` usage without mutating the user's live workspace.
- The current code has no built-in sync metrics surface, so the harness needs to instrument `gh` calls directly.
- The benchmark outputs are local temp data, not checked-in artifacts.

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

## Next actions

1. Carry the baseline plus phase 1 and phase 2 neutral-comparison numbers into the PR description/comment.
2. Start issue `#51` phase 3 minimal hydration helpers.
3. Keep `src/agendum/gh.py` ownership narrow when the later hot-path slices begin.

## Drift from original plan

- No implementation drift from issue `#51`.
- Planning memory was missing from the repo and has now been added.

# Plan

## Active goal

Deliver issue `#51` in the staged order from the issue body. `phase 0` through `phase 7` are landed on `origin/codex/issue-51-sync-foundation`, the two post-phase-7 parity drifts are fixed, and a second-pass review uncovered a title-clobber bug in the verified-terminal normalizers which is also fixed and pushed. The live `adadaptedinc` benchmark gate passes against a fresh `main` baseline. Remaining work is review response on PR `#52`.

## Scope

- Fix org-backed terminal verification for tracked authored PRs and issues in repos that currently have zero open discovered items.
- Fix explicit-repo archived-repo filtering so archive-state incompleteness does not silently drop healthy repos from planner scope.
- Preserve the current hot-path call shape and benchmark win while restoring issue-51 semantic parity.

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
- No new sync-surface behavior beyond the review-driven parity fixes required to match issue `#51`.

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
- Both review-driven parity regressions are now resolved in code with regression coverage:
- Org-backed planner sync now derives `active_repos` from configured `scoped_orgs` membership of tracked tasks in addition to hydrated open items, so tracked authored/issue rows in dormant in-scope repos still flow through terminal verification.
- Explicit-repo planner sync now drops only repos confirmed archived (`isArchived` is `True`); repos missing from a partial archive-state response stay in scope so a flaky lookup cannot silently remove healthy repos from the planner.
- The focused unit suite (`tests/test_live_sync_bench.py tests/test_gh.py tests/test_gh_edge_cases.py tests/test_syncer.py tests/test_syncer_edge_cases.py`) and the full repo suite both pass on the parity-fix branch.
- A second-pass review surfaced a title-clobber bug: the three verified-terminal normalizers in `src/agendum/syncer.py` built `NormalizedIncomingTask(title="")`, which `as_dict()` emitted unconditionally and `diff_tasks` then wrote through to the DB on the `to_update` path, clobbering the tracked title on closed authored PRs, closed assigned issues, and dropped review requests. An independent reviewer agent reproduced the bug across all three normalizers.
- The fix passes `tracked.title` through the three normalizers, updates two planner-test fixture expectations that pinned the buggy `title=""` output, adds a title assertion to `test_run_sync_marks_closed_authored_pr_closed`, and adds two sibling regression tests covering the issue and review-PR terminal verification paths. Full pytest is `278 passed`.
- The live title-fix rerun against a fresh `main` baseline (`43cc532`) still materially beats `main` (cold `19.24s -> 6.55s`, warm `19.15s -> 6.47s`, `12 -> 8` `gh` calls), and `scripts/compare_live_sync_bench.py --fail-on-regression` exits cleanly. Lane shape is unchanged because the title fix is pure normalization.
- The title fix is pushed as commit `e68ee25` on `origin/codex/issue-51-sync-foundation`. PR `#52` is now awaiting review response.

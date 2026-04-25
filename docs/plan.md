# Plan

## Active goal

Deliver issue `#51` in the staged order from the issue body. `phase 0`, `phase 1`, and `phase 2` are complete locally; the next active slice is `phase 3`: add minimal hydration helpers without switching `run_sync` yet.

## Scope

- Add `hydrate_open_authored_prs(...)`.
- Add `hydrate_open_review_prs(...)`.
- Add `hydrate_open_issues(...)`.
- Keep hydration lane-specific and minimal.
- Make batch sizing explicit and tested.
- Leave current sync wiring alone.

## Constraints

- No `run_sync` switch in this slice.
- No missing-item verification in this slice.
- No oversized generic PR fragment in this slice.
- Hydration must stay lane-specific and minimal.
- The live benchmark for this slice should be neutral versus the `phase 0` baseline.

## Invariants from issue 51

- Open-only broad discovery is the target steady-state hot path.
- Terminal verification must eventually be limited to tracked rows that disappear from open discovery.
- Repo fanout and per-review detail N+1 are still present in `main`; later slices must remove them, but `phase 3` must not alter that hot path yet.
- Every later sync-affecting PR should use this harness for live before/after comparison.

## Non-goals for this slice

- No targeted missing-item verification yet.
- No reconciliation changes.
- No production hot-path switch yet.
- No legacy helper cleanup yet.

## Current drift check

- `phase 0` is complete in the repo and produced a local `adadaptedinc` benchmark baseline.
- `phase 1` is complete locally and stayed neutral versus the baseline.
- `phase 2` is complete locally and stayed neutral versus the baseline.
- The hot path still matches the pre-issue-51 baseline: repo fanout in `syncer.py`, repo GraphQL hydration in `gh.py`, and per-review PR detail fetches remain active.
- No unapproved drift from issue `#51` is present; progressing from `phase 2` to `phase 3` is the planned next step.

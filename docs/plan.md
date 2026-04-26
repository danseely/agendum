# Plan

## Active goal

Deliver issue `#51` via PR `#52`. All seven phases plus the post-phase-7 parity fixes and the second-pass title-clobber fix are landed. The branch is rebased onto current `origin/main` (`43cc532`), the live `adadaptedinc` benchmark gate passes, and the full pytest suite passes. PR `#52` is awaiting merge approval.

## Scope

- Switch the sync hot path to open-only discovery, lane-specific hydration, and targeted missing-item verification.
- Remove repo fanout and per-review detail N+1 from production code.
- Add the live benchmark harness and budget gate required by issue `#51`.
- Restore semantic parity for fetched-scope, label propagation, and archived-repo suppression.
- Preserve titles when verification closes a tracked row.

## Constraints

- No new behavior scope.
- No reintroduction of repo fanout, per-review detail N+1, or org-wide terminal-history search lanes.
- The live benchmark must still materially beat `main`.

## Invariants from issue 51

- Open-only broad discovery is the steady-state hot path.
- Terminal verification is limited to tracked rows that disappear from open discovery.
- Every sync-affecting PR uses the harness for live before/after comparison.
- The benchmark budget gate fails if a candidate reintroduces `repo_graphql`, `review_detail_graphql`, `other`, or a higher total `gh` call count.

## Non-goals

- No semantic redesign of statuses or reconciliation.
- No webhook or GitHub App redesign.
- No fix for the latent `compute_close_suppression` keying inconsistency in `src/agendum/syncer.py` (tracked separately as issue `#53`).

## Current drift check

- All seven phases are landed in the branch and verified by pytest plus the live benchmark gate.
- The two post-phase-7 parity drifts (org-backed terminal verification scope, explicit-repo archive-state incompleteness) are resolved with regression coverage.
- The second-pass title-clobber bug in the verified-terminal normalizers is resolved with regression coverage.
- The branch was rebased onto `origin/main` (`43cc532`); production code, tests, scripts, and docs are byte-identical to the pre-rebase tip after replay.
- The `compute_close_suppression` keying inconsistency surfaced by the third reviewer pass is documented in issue `#53` and intentionally out of scope here.
- No further unapproved drift is open.

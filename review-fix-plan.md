# PR 36 Follow-up Fix Plan

## 1. Repo-only review discovery

Make review-task cleanup aware that `orgs=[]` in a repo-only workspace does not mean review discovery was complete.

Safest short fix:

- mark review discovery incomplete for repo-only configs unless there is an actual repo-scoped review discovery path
- preserve existing `pr_review` rows instead of closing them on false completeness
- keep authored PR, issue, and manual task cleanup behavior unchanged

Regression test:

- repo-only workspace preserves existing `pr_review` rows after sync when no org-scoped review query ran

Likely touch points:

- `src/agendum/syncer.py`
- `tests/test_syncer_edge_cases.py`

## 2. Auth source ordering for namespace creation

Change namespace switching so recovery tries auth sources in this order:

1. target workspace auth
2. current workspace auth
3. default/global `gh` auth
4. interactive login

This keeps agendum-local authenticated state reusable even when `~/.config/gh` is stale or empty.

Regression test:

- creating a new namespace reuses current workspace auth when global `gh` auth is stale

Likely touch points:

- `src/agendum/app.py`
- `src/agendum/gh.py`
- `tests/test_app_interaction.py`
- `tests/test_gh.py`

## 3. Base-workspace return symmetry

Remove the asymmetry that skips auth recovery when returning to base.

Returning to base should go through the same recovery logic as entering a namespace, targeting the base workspace auth dir and falling back to interactive login only if recovery fails.

Regression test:

- returning to base recovers base auth instead of skipping auth handling

Likely touch points:

- `src/agendum/app.py`
- `tests/test_app_interaction.py`

## 4. Reauth semantics

Separate "recover if broken" from "refresh/reauth on demand."

`agendum reauth` should not short-circuit just because workspace auth is currently valid. It should either:

- refresh from the preferred upstream auth source if available, or
- force interactive reauth when requested or needed

This makes the command useful after account or token changes.

Regression test:

- `agendum reauth` refreshes even when workspace auth already exists

Likely touch points:

- `src/agendum/__main__.py`
- `src/agendum/gh.py`
- `tests/test_main.py`
- `tests/test_gh.py`

## 5. Validation sequence

After implementation:

1. run the focused auth and sync regression tests first
2. run full `uv run pytest`
3. do one final targeted review only against these four invariants, not another broad free-form pass

Suggested focused pass:

- `uv run pytest tests/test_syncer_edge_cases.py tests/test_app_interaction.py tests/test_main.py tests/test_gh.py`

Implementation guardrails:

- keep fixes minimal and local to the auth/sync paths above
- prefer adding small helpers over widening CLI or app behavior
- do not change unrelated namespace/config defaults while addressing these items

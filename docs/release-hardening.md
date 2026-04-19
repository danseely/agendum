# Release Hardening

This repository uses a rolling release PR model:

1. Merges to `main` can update `release/next`.
2. Merging `release/next` publishes the tagged GitHub release.
3. The first release still needs a bootstrap tag if the repository has no reachable release tag yet.

## Maintainer flow

The normal operator flow is:

1. Merge releasable work into `main`.
2. Review the rolling `release/next` PR created or updated by automation.
3. Merge `release/next` when ready to ship.
4. Confirm that `release.yml` publishes the GitHub tag and release.
5. Confirm that `release.yml` dispatches the release payload to `danseely/homebrew-tap`.
6. Review and merge the resulting tap PR after Homebrew CI passes.
7. Trigger the tap's separate `pr-pull` publish path if you want bottles/publish handled through the standard Homebrew flow.

The tap release PR is part of the release path now. The code repository release is not complete until the corresponding tap PR is green and merged.

## Required GitHub rulesets

Configure these in repository settings:

- Branch ruleset for `release/next`
  - exact branch name: `release/next`
  - protect it so only the release automation and approved maintainers can update or merge it
- Tag ruleset for `v*`
  - exact pattern: `v*`
  - protect release tags from direct edits or deletes outside the release workflow

The workflow conditions remain the final semantic guard. Rulesets reduce accidental writes, but `release.yml` still only publishes when the merged pull request head branch is exactly `release/next`.

## Workflow token permissions

The release workflow assumes `GITHUB_TOKEN` has:

- `contents: write` to create the annotated tag and publish the GitHub release

The release workflow also assumes a repository secret named `HOMEBREW_TAP_DISPATCH_TOKEN` exists so it can notify `danseely/homebrew-tap` after the release is published.

The rolling release PR workflow also needs repository write permissions for:

- `contents: write`
- `pull-requests: write`
- `actions: write` to dispatch validation on `release/next`

## Homebrew tap dispatch

After `release.yml` publishes a GitHub release, that same workflow fetches the release metadata and sends a `repository_dispatch` to `danseely/homebrew-tap`.

`.github/workflows/dispatch-homebrew-tap.yml` is the manual replay path. It accepts a release tag input and replays the same dispatch payload for an existing GitHub release.

Use a dedicated repo secret named `HOMEBREW_TAP_DISPATCH_TOKEN` for the dispatch call. Keep that token scoped only to the tap repo and the `repository_dispatch` API path it needs; do not reuse `GITHUB_TOKEN` for cross-repo automation.

The dispatch contract is:

- `event_type`: `agendum_release_published`
- `client_payload.source_repo`: `danseely/agendum`
- `client_payload.tag`: the raw release tag, for example `v0.1.0`
- `client_payload.version`: the SemVer string without the leading `v`, for example `0.1.0`
- `client_payload.release_url`: the GitHub release URL
- `client_payload.tarball_url`: the release tarball URL from GitHub
- `client_payload.published_at`: the release publication timestamp

Sequencing matters: the tap should treat `repository_dispatch` as the signal that the GitHub release already exists and can be consumed from the release URLs above. The tap should not assume source artifacts are available before the dispatch arrives.

Operational fallback: if dispatch fails, rerun `release.yml` if you want to replay the full publish handoff for that merge, or run `Dispatch Homebrew Tap` with the desired tag if you only want to replay the tap notification. If the workflow is unavailable, trigger the tap manually with the same `repository_dispatch` payload using `gh api` and the same token.

## Bootstrap flow

If `create-release-pr.yml` cannot find a reachable prior release tag, bootstrap the first release tag once. After that, the automation keeps `release/next` moving and the publish workflow handles the release on merge.

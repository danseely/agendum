# Release Hardening

This repository uses a rolling release PR model:

1. Merges to `main` can update `release/next`.
2. Merging `release/next` publishes the tagged GitHub release.
3. The first release still needs a bootstrap tag if the repository has no reachable release tag yet.

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

The rolling release PR workflow also needs repository write permissions for:

- `contents: write`
- `pull-requests: write`
- `actions: write` to dispatch validation on `release/next`

## Bootstrap flow

If `create-release-pr.yml` cannot find a reachable prior release tag, bootstrap the first release tag once. After that, the automation keeps `release/next` moving and the publish workflow handles the release on merge.

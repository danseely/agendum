# Contributing

## Development Setup

```bash
uv sync --dev
uv run pre-commit install --hook-type pre-commit --hook-type commit-msg
```

## Commit Policy

This repository uses Conventional Commits and SemVer.

Use commit subjects like:

- `feat: add homebrew packaging scaffolding`
- `fix: handle missing gh auth more clearly`
- `docs: document local hook installation`
- `test: cover first-run setup`

Use an optional scope when it adds clarity:

- `feat(cli): add --version output`
- `fix(sync): avoid duplicate wake retry timers`

Mark breaking changes with `!` in the subject or a `BREAKING CHANGE:` footer.

Examples:

- `feat!: redesign config schema`
- `feat(config)!: rename github.orgs to github.repos`

## Versioning

Release versioning follows SemVer:

- `fix:` increments patch
- `feat:` increments minor
- breaking changes increment major

## Pull Requests

PR titles should also follow Conventional Commits. This keeps squash merges aligned with release automation.

Examples:

- `feat: add conventional commit enforcement`
- `fix: support non-interactive version output`

## Releases

Releases are operator-triggered from GitHub Actions, not on every merge.

The release workflow:

- runs only from `main`
- bumps the version with `commitizen`
- updates `CHANGELOG.md`
- creates an annotated git tag
- builds `sdist` and wheel artifacts with `uv build`
- publishes a GitHub release

For the first release, provide an explicit `version` input because there is no prior tag history yet.

## Packaging Notes

`agendum self-check` exists as a deterministic non-interactive validation path for packaging and install verification. It should remain free of network and `gh auth` requirements.

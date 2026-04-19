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

Releases follow the rolling `release/next` PR model.

The release PR workflow:

- runs when a merged PR lands on `main`
- bumps the version with `commitizen`
- updates `CHANGELOG.md`
- keeps a single `release/next` branch updated in place
- triggers validation for the release PR

The publish workflow:

- runs only when the merged PR head branch is exactly `release/next`
- creates an annotated git tag
- builds `sdist` and wheel artifacts with `uv build`
- publishes a GitHub release
- dispatches the release payload to `danseely/homebrew-tap`

For the first release, bootstrap one reachable release tag if none exists yet, then the rolling release PR automation takes over.

See [docs/release-hardening.md](docs/release-hardening.md) for the required GitHub rulesets and token permissions.

## Packaging Notes

`agendum self-check` exists as a deterministic non-interactive validation path for packaging and install verification. It should remain free of network and `gh auth` requirements.

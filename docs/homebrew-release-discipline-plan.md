# Agendum Homebrew + Release Discipline Plan

## Goal

Make `agendum` installable via Homebrew from a dedicated tap while also standardizing release discipline in this repo around:

- Apache 2.0 licensing
- SemVer
- Conventional Commits
- local hook enforcement
- CI enforcement
- automated release tagging/changelog/release publication

This plan is staged so implementation can be handed off or executed incrementally without blocking on external repo setup until the main repo is ready.

## Current State

- Main app repo: `danseely/agendum`
- Planned tap repo: `danseely/homebrew-tap`
- Language/runtime: Python 3.11+
- Build backend: Hatchling
- Existing distribution artifacts: local `uv build` succeeds
- Current release automation: none
- Current license metadata: none
- Current commit discipline: mixed, not enforced
- Current Homebrew install path: none

## Non-Goals

- Publishing to PyPI in this phase
- Submitting to `homebrew/core`
- Shipping bottles before the formula is validated from source
- Designing a complex release train beyond what is needed for SemVer and Homebrew

## High-Level Strategy

1. Clean up this repo so it is releaseable.
2. Enforce commit and release discipline so future tags are trustworthy.
3. Add a small non-interactive CLI path for package validation.
4. Cut a tagged GitHub release from this repo.
5. Create and publish `danseely/homebrew-tap`.
6. Add the `agendum` formula pointing at the release tarball.
7. Validate install/test/audit locally, then enable bottles if desired.

## Phase 0: Planning and Branch Setup

### Goal

Create a concrete execution plan and isolate work on a feature branch.

### Tasks

- [x] Create branch `feat/homebrew-packaging-and-release-discipline`
- [x] Create this planning document
- [x] Confirm tap and license direction:
  - Apache 2.0
  - `danseely/homebrew-tap`
- [ ] Confirm release automation shape before wiring tagging and releases

### Deliverables

- Planning document in-repo
- Working branch for implementation

### Exit Criteria

- Repo contains a handoff-ready staged plan
- Implementation branch exists

## Phase 1: Foundation in the Main Repo

### Goal

Make the repository legally distributable and enforce contribution/release discipline.

### Tasks

- [x] Add `LICENSE` file with Apache License 2.0 text
- [x] Add `license = "Apache-2.0"` to `pyproject.toml`
- [x] Add development dependencies for release/commit tooling
- [x] Add `pre-commit` configuration
- [x] Add Conventional Commit enforcement via local hook(s)
- [x] Add repo documentation for commit/release workflow
- [x] Add CI workflow to validate:
  - tests
  - pre-commit checks
  - Conventional Commit policy for PR titles or commits

### Recommended Tooling

- `pre-commit` for local hook orchestration
- `commitizen` for Conventional Commit validation and SemVer bump support
- GitHub Actions for enforcement that cannot be bypassed locally

### Manual/User Steps

- Install hooks locally after tooling lands
- Ensure branch protection eventually requires the new checks

### Risks

- Local hooks alone are bypassable
- CI-only enforcement without local hooks creates contributor friction

### Exit Criteria

- Repo contains license and license metadata
- Local hooks can reject non-conventional commit messages
- CI fails on non-compliant PR metadata or commits

## Phase 2: Release Automation and Version Discipline

### Goal

Automate SemVer-based tagging and GitHub release publication from Conventional Commits.

### Tasks

- [x] Decide release automation shape:
  - manual operator-triggered release workflow
  - `commitizen` performs SemVer bumping, changelog, and tagging
- [x] Configure changelog generation
- [ ] Standardize where version is sourced from and updated
- [x] Add GitHub Actions workflow for release creation
- [x] Define manual release operator flow in docs

### Preferred Direction

Use a workflow that derives release bumps from Conventional Commits when manually triggered from `main`.

### Open Implementation Detail

Need to decide whether to keep duplicated version declarations or move to a single runtime version source.

### Manual/User Steps

- Approve GitHub Actions permissions for tagging/releases if needed

### Exit Criteria

- A mergeable change with `feat:` produces a minor release path
- A mergeable change with `fix:` produces a patch release path
- Breaking changes can trigger major release behavior

## Phase 3: Packaging Readiness in the Main Repo

### Goal

Make the installed application testable in a Homebrew formula without interactive setup.

### Tasks

- [x] Add `agendum --version`
- [x] Add a deterministic non-interactive validation path
- [ ] Verify installed code can exercise a lightweight storage/task round-trip
- [ ] Update README install docs to prepare for Homebrew
- [ ] Ensure runtime dependency on `gh` is clearly documented

### Preferred Formula Test Shape

Use installed Python to:

- initialize the app DB
- create a manual task
- read it back

Avoid:

- launching the TUI
- requiring network access
- requiring `gh auth login`

### Exit Criteria

- There is at least one reliable non-interactive verification path for formula testing

## Phase 4: GitHub Release Readiness

### Goal

Produce a stable tagged release artifact the formula can target.

### Tasks

- [ ] Validate `uv build`
- [ ] Decide first release version
- [ ] Create annotated Git tag
- [ ] Publish GitHub release with source tarball
- [ ] Confirm release archive URL and checksum workflow

### Manual/User Steps

- May require GitHub UI/API credentials or release approval

### Exit Criteria

- Stable release tarball exists on GitHub
- Version/taging semantics are documented and repeatable

## Phase 5: Tap Creation

### Goal

Create the external Homebrew tap repository and scaffold its CI.

### Tasks

- [ ] Run `brew tap-new danseely/homebrew-tap`
- [ ] Create/push GitHub repo `danseely/homebrew-tap`
- [ ] Keep generated tap CI unless there is a strong reason to diverge
- [ ] Document tap maintenance ownership

### Expected External Repo Structure

- `Formula/agendum.rb`
- generated GitHub Actions from `brew tap-new`
- optional bottle publishing configuration

### Manual/User Steps

- GitHub repo creation
- Push permissions / workflow permissions

### Exit Criteria

- Tap repo exists and is reachable by Homebrew

## Phase 6: Formula Implementation

### Goal

Publish a working formula for `agendum`.

### Tasks

- [ ] Generate starter formula from release tarball
- [ ] Set metadata:
  - `desc`
  - `homepage`
  - `url`
  - `sha256`
  - `license`
- [ ] Add:
  - `depends_on "python@3.x"`
  - `depends_on "gh"`
- [ ] Use `Language::Python::Virtualenv`
- [ ] Add Python resource blocks for transitive dependencies
- [ ] Add `test do` block using installed Python and local temp HOME
- [ ] Run local validation:
  - `brew install --build-from-source`
  - `brew test`
  - `brew audit --strict --online`

### Notes

- Do not make the formula depend on `uv`
- Do not test by launching the TUI
- Do not require `gh auth login` in the formula test

### Exit Criteria

- Formula installs from source successfully
- Formula test passes locally
- Audit passes or only has understood non-blocking output

## Phase 7: Bottles and UX Polish

### Goal

Improve install speed and publish polished installation docs.

### Tasks

- [ ] Enable bottle generation if desired
- [ ] Verify one-command install docs
- [ ] Update README with:
  - Homebrew install
  - fallback dev install via `uv`
  - `gh` requirement
  - MCP install notes

### Exit Criteria

- README defaults to Homebrew for end users
- Bottle strategy is documented

## Phase Ownership

### Main Repo (`danseely/agendum`)

- license and metadata
- commit/release discipline
- CLI packaging readiness
- release automation
- GitHub release creation

### Tap Repo (`danseely/homebrew-tap`)

- formula
- resource updates
- tap CI
- bottle workflows

## Manual Checkpoints

These are points where user help may be required:

1. GitHub Actions permissions for release/tag workflows
2. Creating or pushing the `homebrew-tap` repo
3. Publishing the first GitHub release if UI/API auth is needed
4. Verifying any branch protection settings on GitHub

## Implementation Order for This Branch

1. Phase 1: license + commit/release discipline scaffolding
2. Phase 2: release automation choice and implementation
3. Phase 3: non-interactive CLI/package validation path
4. Stop and review before touching external tap repo work

## Suggested First Deliverable on This Branch

A PR that includes:

- Apache 2.0 license
- `pyproject.toml` license metadata
- commit/release tooling configuration
- local hook config
- CI enforcement workflow(s)
- documentation for contributors

This keeps the first PR self-contained and reviewable before adding Homebrew-specific packaging code.

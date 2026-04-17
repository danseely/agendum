from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_release_workflow_only_publishes_from_release_next() -> None:
    release_workflow = (ROOT / ".github/workflows/release.yml").read_text()

    assert "github.event.pull_request.head.ref == 'release/next'" in release_workflow
    assert "startsWith(github.event.pull_request.head.ref, 'release/')" not in release_workflow


def test_create_release_pr_documents_bootstrap_path() -> None:
    create_release_pr = (ROOT / ".github/workflows/create-release-pr.yml").read_text()

    assert "release/next" in create_release_pr
    assert "Bootstrap the first release tag once" in create_release_pr


def test_release_hardening_doc_covers_rulesets_and_permissions() -> None:
    hardening_doc = (ROOT / "docs/release-hardening.md").read_text()

    assert "exact branch name: `release/next`" in hardening_doc
    assert "exact pattern: `v*`" in hardening_doc
    assert "`contents: write`" in hardening_doc
    assert "final semantic guard" in hardening_doc

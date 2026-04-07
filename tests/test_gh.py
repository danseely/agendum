import json
from agendum.gh import (
    derive_authored_pr_status,
    derive_review_pr_status,
    derive_issue_status,
    parse_author_first_name,
    extract_repo_short_name,
)


def test_authored_pr_draft() -> None:
    assert derive_authored_pr_status(is_draft=True, review_decision=None, state="OPEN") == "draft"


def test_authored_pr_open_no_reviewers() -> None:
    assert derive_authored_pr_status(is_draft=False, review_decision=None, state="OPEN", has_review_requests=False) == "open"


def test_authored_pr_awaiting_review() -> None:
    assert derive_authored_pr_status(is_draft=False, review_decision=None, state="OPEN", has_review_requests=True) == "awaiting review"


def test_authored_pr_changes_requested() -> None:
    assert derive_authored_pr_status(is_draft=False, review_decision="CHANGES_REQUESTED", state="OPEN") == "changes requested"


def test_authored_pr_approved() -> None:
    assert derive_authored_pr_status(is_draft=False, review_decision="APPROVED", state="OPEN") == "approved"


def test_authored_pr_merged() -> None:
    assert derive_authored_pr_status(is_draft=False, review_decision=None, state="MERGED") == "merged"


def test_authored_pr_closed() -> None:
    assert derive_authored_pr_status(is_draft=False, review_decision=None, state="CLOSED") == "closed"


def test_review_pr_requested() -> None:
    assert derive_review_pr_status(user_has_reviewed=False, new_commits_since_review=False) == "review requested"


def test_review_pr_reviewed() -> None:
    assert derive_review_pr_status(user_has_reviewed=True, new_commits_since_review=False) == "reviewed"


def test_review_pr_re_review() -> None:
    assert derive_review_pr_status(user_has_reviewed=True, new_commits_since_review=True) == "re-review requested"


def test_issue_open() -> None:
    assert derive_issue_status(state="OPEN", has_linked_pr=False) == "open"


def test_issue_in_progress() -> None:
    assert derive_issue_status(state="OPEN", has_linked_pr=True) == "in progress"


def test_issue_closed() -> None:
    assert derive_issue_status(state="CLOSED", has_linked_pr=False) == "closed"


def test_parse_author_first_name() -> None:
    assert parse_author_first_name("Example Reviewer") == "Example"
    assert parse_author_first_name("Reviewer") == "Reviewer"
    assert parse_author_first_name(None) is None
    assert parse_author_first_name("") is None


def test_extract_repo_short_name() -> None:
    assert extract_repo_short_name("example-org/example-repo") == "example-repo"
    assert extract_repo_short_name("org/repo") == "repo"

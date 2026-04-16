import json
import pytest

from agendum.gh import (
    derive_authored_pr_status,
    derive_review_pr_status,
    derive_issue_status,
    fetch_review_detail,
    parse_author_first_name,
    extract_repo_short_name,
)


def authored_pr_status_with_review_feedback(**overrides: object) -> str:
    kwargs = {
        "is_draft": False,
        "review_decision": None,
        "state": "OPEN",
        "has_review_requests": False,
        "latest_commit_time": None,
        "latest_comment_review_id": None,
        "latest_comment_review_time": None,
        "author_login": "author",
        "review_threads": [],
    }
    kwargs.update(overrides)
    return derive_authored_pr_status(**kwargs)


def test_authored_pr_draft() -> None:
    assert derive_authored_pr_status(is_draft=True, review_decision=None, state="OPEN") == "draft"


def test_authored_pr_open_no_reviewers() -> None:
    assert derive_authored_pr_status(is_draft=False, review_decision=None, state="OPEN", has_review_requests=False) == "open"


def test_authored_pr_awaiting_review() -> None:
    assert derive_authored_pr_status(is_draft=False, review_decision=None, state="OPEN", has_review_requests=True) == "awaiting review"


def test_authored_pr_changes_requested_overrides_review_received() -> None:
    assert authored_pr_status_with_review_feedback(
        review_decision="CHANGES_REQUESTED",
        latest_comment_review_id="review-1",
        latest_comment_review_time="2026-04-10T12:00:00Z",
        review_threads=[
            {
                "isResolved": False,
                "comments": {
                    "nodes": [
                        {
                            "createdAt": "2026-04-10T12:01:00Z",
                            "author": {"login": "reviewer"},
                            "pullRequestReview": {"id": "review-1"},
                        },
                    ],
                },
            },
        ],
    ) == "changes requested"


def test_authored_pr_approved() -> None:
    assert derive_authored_pr_status(is_draft=False, review_decision="APPROVED", state="OPEN") == "approved"


def test_authored_pr_merged() -> None:
    assert derive_authored_pr_status(is_draft=False, review_decision=None, state="MERGED") == "merged"


def test_authored_pr_closed() -> None:
    assert derive_authored_pr_status(is_draft=False, review_decision=None, state="CLOSED") == "closed"


def test_authored_pr_review_received_for_non_blocking_feedback() -> None:
    assert authored_pr_status_with_review_feedback(
        latest_comment_review_id="review-1",
        latest_comment_review_time="2026-04-10T12:00:00Z",
        review_threads=[
            {
                "isResolved": False,
                "comments": {
                    "nodes": [
                        {
                            "createdAt": "2026-04-10T12:01:00Z",
                            "author": {"login": "reviewer"},
                            "pullRequestReview": {"id": "review-1"},
                        },
                    ],
                },
            },
        ],
    ) == "review received"


def test_authored_pr_review_received_stays_active_with_sibling_thread_reply() -> None:
    assert authored_pr_status_with_review_feedback(
        qualifying_reviews=[
            {
                "id": "review-1",
                "submittedAt": "2026-04-10T12:00:00Z",
            },
        ],
        review_threads=[
            {
                "isResolved": False,
                "comments": {
                    "nodes": [
                        {
                            "createdAt": "2026-04-10T12:01:00Z",
                            "author": {"login": "reviewer"},
                            "pullRequestReview": {"id": "review-1"},
                        },
                        {
                            "createdAt": "2026-04-10T12:02:00Z",
                            "author": {"login": "author"},
                            "pullRequestReview": {"id": None},
                        },
                    ],
                },
            },
            {
                "isResolved": False,
                "comments": {
                    "nodes": [
                        {
                            "createdAt": "2026-04-10T12:03:00Z",
                            "author": {"login": "reviewer"},
                            "pullRequestReview": {"id": "review-1"},
                        },
                    ],
                },
            },
        ],
    ) == "review received"


def test_authored_pr_review_received_stays_active_for_older_unresolved_review() -> None:
    assert authored_pr_status_with_review_feedback(
        qualifying_reviews=[
            {
                "id": "review-old",
                "submittedAt": "2026-04-10T11:00:00Z",
            },
            {
                "id": "review-new",
                "submittedAt": "2026-04-10T12:00:00Z",
            },
        ],
        review_threads=[
            {
                "isResolved": False,
                "comments": {
                    "nodes": [
                        {
                            "createdAt": "2026-04-10T11:01:00Z",
                            "author": {"login": "reviewer"},
                            "pullRequestReview": {"id": "review-old"},
                        },
                    ],
                },
            },
            {
                "isResolved": True,
                "comments": {
                    "nodes": [
                        {
                            "createdAt": "2026-04-10T12:01:00Z",
                            "author": {"login": "reviewer"},
                            "pullRequestReview": {"id": "review-new"},
                        },
                    ],
                },
            },
        ],
    ) == "review received"


def test_authored_pr_review_received_clears_after_author_reply() -> None:
    assert authored_pr_status_with_review_feedback(
        latest_comment_review_id="review-1",
        latest_comment_review_time="2026-04-10T12:00:00Z",
        review_threads=[
            {
                "isResolved": False,
                "comments": {
                    "nodes": [
                        {
                            "createdAt": "2026-04-10T12:01:00Z",
                            "author": {"login": "reviewer"},
                            "pullRequestReview": {"id": "review-1"},
                        },
                        {
                            "createdAt": "2026-04-10T12:05:00Z",
                            "author": {"login": "author"},
                            "pullRequestReview": {"id": None},
                        },
                    ],
                },
            },
        ],
    ) == "open"


def test_authored_pr_review_received_clears_after_threads_resolved() -> None:
    assert authored_pr_status_with_review_feedback(
        latest_comment_review_id="review-1",
        latest_comment_review_time="2026-04-10T12:00:00Z",
        review_threads=[
            {
                "isResolved": True,
                "comments": {
                    "nodes": [
                        {
                            "createdAt": "2026-04-10T12:01:00Z",
                            "author": {"login": "reviewer"},
                            "pullRequestReview": {"id": "review-1"},
                        },
                    ],
                },
            },
        ],
    ) == "open"


def test_authored_pr_review_received_persists_after_push_when_feedback_threads_exist() -> None:
    assert authored_pr_status_with_review_feedback(
        latest_comment_review_id="review-1",
        latest_comment_review_time="2026-04-10T12:00:00Z",
        latest_commit_time="2026-04-10T12:05:00Z",
        review_threads=[
            {
                "isResolved": False,
                "comments": {
                    "nodes": [
                        {
                            "createdAt": "2026-04-10T12:01:00Z",
                            "author": {"login": "reviewer"},
                            "pullRequestReview": {"id": "review-1"},
                        },
                    ],
                },
            },
        ],
    ) == "review received"


def test_authored_pr_review_received_clears_after_push_without_feedback_threads() -> None:
    assert authored_pr_status_with_review_feedback(
        latest_comment_review_id="review-1",
        latest_comment_review_time="2026-04-10T12:00:00Z",
        latest_commit_time="2026-04-10T12:05:00Z",
    ) == "open"


def test_review_pr_requested() -> None:
    assert derive_review_pr_status(user_has_reviewed=False, new_commits_since_review=False) == "review requested"


def test_review_pr_reviewed() -> None:
    assert derive_review_pr_status(user_has_reviewed=True, new_commits_since_review=False) == "reviewed"


def test_review_pr_re_review() -> None:
    assert derive_review_pr_status(user_has_reviewed=True, new_commits_since_review=True) == "re-review requested"


def test_review_pr_re_review_via_explicit_rerequest() -> None:
    assert derive_review_pr_status(
        user_has_reviewed=True,
        new_commits_since_review=False,
        re_requested_after_review=True,
    ) == "re-review requested"


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


@pytest.mark.asyncio
async def test_fetch_review_detail_uses_valid_author_name_query(monkeypatch) -> None:
    calls: list[tuple[str, ...]] = []

    async def fake_run_gh(*args: str) -> str:
        calls.append(args)
        return json.dumps(
            {
                "data": {
                    "repository": {
                        "pullRequest": {
                            "author": {
                                "login": "reviewer",
                                "name": "Review Person",
                            },
                        },
                    },
                },
            }
        )

    from agendum import gh

    monkeypatch.setattr(gh, "_run_gh", fake_run_gh)

    result = await fetch_review_detail("example-org", "example-repo", 34, "current-user")

    assert result["data"]["repository"]["pullRequest"]["author"]["name"] == "Review Person"
    call = calls[0]
    query_arg = next(arg for arg in call if arg.startswith("query="))
    assert "$user" not in query_arg
    assert "-F" in call
    assert "user=current-user" not in call
    assert "... on User" in query_arg

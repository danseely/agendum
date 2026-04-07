import json

import pytest

from agendum.gh_review import (
    fetch_pr_reviews,
    get_pr_review_status,
    parse_github_pr_url,
    reviewer_matches,
)


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://github.com/org/repo/pull/123", ("org", "repo", 123)),
        ("https://github.com/org/repo/pull/123/", ("org", "repo", 123)),
        ("https://github.com/org/repo/issues/123", None),
        ("not-a-url", None),
    ],
)
def test_parse_github_pr_url(url: str, expected: tuple[str, str, int] | None) -> None:
    assert parse_github_pr_url(url) == expected


@pytest.mark.parametrize(
    ("query", "login", "name", "expected"),
    [
        ("alex", "alex-radaev", "Alex Radaev", True),
        ("Alex Radaev", "alex-radaev", "Alex Radaev", True),
        ("radaev", "alex-radaev", "Alex Radaev", True),
        ("sam", "alex-radaev", "Alex Radaev", False),
        ("", "alex-radaev", "Alex Radaev", False),
    ],
)
def test_reviewer_matches(query: str, login: str | None, name: str | None, expected: bool) -> None:
    assert reviewer_matches(query, login=login, name=name) is expected


@pytest.mark.asyncio
async def test_fetch_pr_reviews_parses_and_sorts_reviews(monkeypatch) -> None:
    calls: list[tuple[str, ...]] = []

    async def fake_run_gh(*args: str) -> str:
        calls.append(args)
        return json.dumps(
            {
                "data": {
                    "repository": {
                        "pullRequest": {
                            "reviews": {
                                "nodes": [
                                    {
                                        "state": "APPROVED",
                                        "submittedAt": "2026-04-07T20:55:00Z",
                                        "url": "https://github.com/org/repo/pull/123#review-2",
                                        "author": {"login": "alex-radaev", "name": "Alex Radaev"},
                                    },
                                    {
                                        "state": "COMMENTED",
                                        "submittedAt": "2026-04-07T20:15:00Z",
                                        "url": "https://github.com/org/repo/pull/123#review-1",
                                        "author": {"login": "sam", "name": "Sam Reviewer"},
                                    },
                                ],
                            },
                        },
                    },
                },
            }
        )

    from agendum import gh

    monkeypatch.setattr(gh, "_run_gh", fake_run_gh)

    reviews = await fetch_pr_reviews("org", "repo", 123)

    assert reviews[0]["state"] == "APPROVED"
    assert reviews[0]["login"] == "alex-radaev"
    assert reviews[0]["name"] == "Alex Radaev"
    assert reviews[1]["state"] == "COMMENTED"
    query_arg = next(arg for arg in calls[0] if arg.startswith("query="))
    assert "... on User" in query_arg
    assert "submittedAt" in query_arg
    assert "reviews(last: 100)" in query_arg


@pytest.mark.asyncio
async def test_get_pr_review_status_filters_by_reviewer(monkeypatch) -> None:
    async def fake_run_gh(*args: str) -> str:
        return json.dumps(
            {
                "data": {
                    "repository": {
                        "pullRequest": {
                            "reviews": {
                                "nodes": [
                                    {
                                        "state": "COMMENTED",
                                        "submittedAt": "2026-04-07T20:05:00Z",
                                        "url": "https://github.com/org/repo/pull/123#review-1",
                                        "author": {"login": "sam", "name": "Sam Reviewer"},
                                    },
                                    {
                                        "state": "APPROVED",
                                        "submittedAt": "2026-04-07T20:55:00Z",
                                        "url": "https://github.com/org/repo/pull/123#review-2",
                                        "author": {"login": "alex-radaev", "name": "Alex Radaev"},
                                    },
                                ],
                            },
                        },
                    },
                },
            }
        )

    from agendum import gh

    monkeypatch.setattr(gh, "_run_gh", fake_run_gh)

    status = await get_pr_review_status(
        url="https://github.com/org/repo/pull/123",
        reviewer="alex",
    )

    assert status["url"] == "https://github.com/org/repo/pull/123"
    assert status["reviewer"] == "alex"
    assert len(status["matches"]) == 1
    assert status["matches"][0]["login"] == "alex-radaev"
    assert status["latest_state"] == "APPROVED"


@pytest.mark.asyncio
async def test_get_pr_review_status_uses_latest_overall_state_when_no_reviewer(monkeypatch) -> None:
    async def fake_run_gh(*args: str) -> str:
        return json.dumps(
            {
                "data": {
                    "repository": {
                        "pullRequest": {
                            "reviews": {
                                "nodes": [
                                    {
                                        "state": "COMMENTED",
                                        "submittedAt": "2026-04-07T20:05:00Z",
                                        "url": "https://github.com/org/repo/pull/123#review-1",
                                        "author": {"login": "sam", "name": "Sam Reviewer"},
                                    },
                                    {
                                        "state": "CHANGES_REQUESTED",
                                        "submittedAt": "2026-04-07T21:05:00Z",
                                        "url": "https://github.com/org/repo/pull/123#review-2",
                                        "author": {"login": "alex-radaev", "name": "Alex Radaev"},
                                    },
                                ],
                            },
                        },
                    },
                },
            }
        )

    from agendum import gh

    monkeypatch.setattr(gh, "_run_gh", fake_run_gh)

    status = await get_pr_review_status(url="https://github.com/org/repo/pull/123")

    assert len(status["matches"]) == 2
    assert status["latest_state"] == "CHANGES_REQUESTED"

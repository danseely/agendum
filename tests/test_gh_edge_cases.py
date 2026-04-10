"""Tests for gh module functions that interact with the gh CLI."""

import json

import pytest

from agendum.gh import discover_repos, discover_review_prs, fetch_notifications, fetch_repo_data


async def test_discover_repos_aggregates_across_searches(monkeypatch) -> None:
    call_log = []

    async def fake_run_gh(*args: str) -> str:
        call_log.append(args)
        # Return different repos for each search type based on args
        if "--author" in args:
            return json.dumps([
                {"repository": {"nameWithOwner": "org/authored-repo"}},
            ])
        if "--assignee" in args:
            return json.dumps([
                {"repository": {"nameWithOwner": "org/assigned-repo"}},
            ])
        if "--review-requested" in args:
            return json.dumps([
                {"repository": {"nameWithOwner": "org/review-repo"}},
            ])
        return "[]"

    from agendum import gh
    monkeypatch.setattr(gh, "_run_gh", fake_run_gh)

    repos = await discover_repos(["org"], "user")

    assert repos == {"org/authored-repo", "org/assigned-repo", "org/review-repo"}


async def test_discover_repos_handles_empty_output(monkeypatch) -> None:
    async def fake_run_gh(*args: str) -> str:
        return ""

    from agendum import gh
    monkeypatch.setattr(gh, "_run_gh", fake_run_gh)

    repos = await discover_repos(["org"], "user")
    assert repos == set()


async def test_discover_review_prs_across_orgs(monkeypatch) -> None:
    async def fake_run_gh(*args: str) -> str:
        # The org name is passed via --owner
        owner_idx = args.index("--owner") + 1 if "--owner" in args else -1
        org = args[owner_idx] if owner_idx > 0 else "unknown"
        return json.dumps([
            {"number": 1, "title": f"PR from {org}", "url": f"https://github.com/{org}/repo/pull/1",
             "repository": {"nameWithOwner": f"{org}/repo"}, "author": {"login": "dev"}},
        ])

    from agendum import gh
    monkeypatch.setattr(gh, "_run_gh", fake_run_gh)

    prs = await discover_review_prs(["org-a", "org-b"], "reviewer")
    assert len(prs) == 2


async def test_fetch_notifications_empty_response(monkeypatch) -> None:
    async def fake_run_gh(*args: str) -> str:
        return ""

    from agendum import gh
    monkeypatch.setattr(gh, "_run_gh", fake_run_gh)

    assert await fetch_notifications("user") == []


async def test_fetch_notifications_invalid_json(monkeypatch) -> None:
    async def fake_run_gh(*args: str) -> str:
        return "not json"

    from agendum import gh
    monkeypatch.setattr(gh, "_run_gh", fake_run_gh)

    assert await fetch_notifications("user") == []


async def test_fetch_repo_data_invalid_json(monkeypatch) -> None:
    async def fake_run_gh(*args: str) -> str:
        return "not json"

    from agendum import gh
    monkeypatch.setattr(gh, "_run_gh", fake_run_gh)

    assert await fetch_repo_data("org", "repo", "user") == {}


async def test_fetch_repo_data_empty_response(monkeypatch) -> None:
    async def fake_run_gh(*args: str) -> str:
        return ""

    from agendum import gh
    monkeypatch.setattr(gh, "_run_gh", fake_run_gh)

    assert await fetch_repo_data("org", "repo", "user") == {}

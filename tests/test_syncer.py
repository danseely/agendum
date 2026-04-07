from pathlib import Path

import pytest

from agendum.config import AgendumConfig
from agendum.db import add_task, find_task_by_gh_url, get_active_tasks, init_db
from agendum.syncer import diff_tasks, run_sync


def test_diff_detects_new_task() -> None:
    existing: list[dict] = []
    incoming = [
        {"gh_url": "https://github.com/org/repo/pull/1", "title": "New PR", "source": "pr_authored", "status": "open"},
    ]
    result = diff_tasks(existing, incoming)
    assert len(result.to_create) == 1
    assert result.to_create[0]["title"] == "New PR"
    assert len(result.to_update) == 0
    assert len(result.to_close) == 0


def test_diff_detects_status_change() -> None:
    existing = [
        {"id": 1, "gh_url": "https://github.com/org/repo/pull/1", "status": "awaiting review", "title": "PR"},
    ]
    incoming = [
        {"gh_url": "https://github.com/org/repo/pull/1", "title": "PR", "source": "pr_authored", "status": "approved"},
    ]
    result = diff_tasks(existing, incoming)
    assert len(result.to_create) == 0
    assert len(result.to_update) == 1
    assert result.to_update[0]["id"] == 1
    assert result.to_update[0]["status"] == "approved"


def test_diff_detects_closed_task() -> None:
    existing = [
        {"id": 1, "gh_url": "https://github.com/org/repo/pull/1", "status": "open", "title": "PR", "source": "pr_authored"},
    ]
    incoming: list[dict] = []
    result = diff_tasks(existing, incoming)
    assert len(result.to_close) == 1
    assert result.to_close[0]["id"] == 1


def test_diff_ignores_manual_tasks() -> None:
    existing = [
        {"id": 1, "gh_url": None, "status": "active", "title": "Manual", "source": "manual"},
    ]
    incoming: list[dict] = []
    result = diff_tasks(existing, incoming)
    assert len(result.to_close) == 0


def test_diff_no_change() -> None:
    existing = [
        {"id": 1, "gh_url": "https://github.com/org/repo/pull/1", "status": "open", "title": "PR"},
    ]
    incoming = [
        {"gh_url": "https://github.com/org/repo/pull/1", "title": "PR", "source": "pr_authored", "status": "open"},
    ]
    result = diff_tasks(existing, incoming)
    assert len(result.to_create) == 0
    assert len(result.to_update) == 0
    assert len(result.to_close) == 0


def test_diff_title_change_without_status_change() -> None:
    existing = [
        {"id": 1, "gh_url": "https://github.com/org/repo/pull/1", "status": "open", "title": "Old title"},
    ]
    incoming = [
        {"gh_url": "https://github.com/org/repo/pull/1", "title": "New title", "source": "pr_authored", "status": "open"},
    ]
    result = diff_tasks(existing, incoming)
    assert len(result.to_update) == 1
    assert result.to_update[0]["title"] == "New title"


@pytest.mark.asyncio
async def test_run_sync_marks_closed_authored_pr_closed(tmp_db: Path, monkeypatch) -> None:
    init_db(tmp_db)
    url = "https://github.com/example-org/example-repo/pull/12"
    add_task(tmp_db, title="Old PR", source="pr_authored", status="open", gh_url=url)

    async def fake_get_gh_username() -> str:
        return "author"

    async def fake_fetch_repo_data(owner: str, name: str, gh_user: str) -> dict:
        return {
            "data": {
                "repository": {
                    "isArchived": False,
                    "openIssues": {"nodes": []},
                    "closedIssues": {"nodes": []},
                    "authoredPRs": {"nodes": []},
                    "mergedPRs": {"nodes": []},
                    "closedPRs": {
                        "nodes": [
                            {
                                "number": 12,
                                "url": url,
                                "state": "CLOSED",
                                "author": {"login": gh_user},
                            }
                        ],
                    },
                },
            },
        }

    async def fake_discover_review_prs(orgs: list[str], gh_user: str) -> list[dict]:
        return []

    async def fake_fetch_notifications(gh_user: str) -> list[dict]:
        return []

    from agendum import gh

    monkeypatch.setattr(gh, "get_gh_username", fake_get_gh_username)
    monkeypatch.setattr(gh, "fetch_repo_data", fake_fetch_repo_data)
    monkeypatch.setattr(gh, "discover_review_prs", fake_discover_review_prs)
    monkeypatch.setattr(gh, "fetch_notifications", fake_fetch_notifications)

    changes, attention, error = await run_sync(
        tmp_db,
        AgendumConfig(repos=["example-org/example-repo"]),
    )

    task = find_task_by_gh_url(tmp_db, url)
    assert changes == 1
    assert attention is False
    assert error is None
    assert task is not None
    assert task["status"] == "closed"


@pytest.mark.asyncio
async def test_run_sync_reports_missing_gh_auth(tmp_db: Path, monkeypatch) -> None:
    init_db(tmp_db)

    async def fake_get_gh_username() -> str:
        return ""

    from agendum import gh

    monkeypatch.setattr(gh, "get_gh_username", fake_get_gh_username)

    changes, attention, error = await run_sync(
        tmp_db,
        AgendumConfig(repos=["example-org/example-repo"]),
    )

    assert changes == 0
    assert attention is False
    assert error == "gh credentials expired"


@pytest.mark.asyncio
async def test_run_sync_creates_review_requested_pr_with_author_name(tmp_db: Path, monkeypatch) -> None:
    init_db(tmp_db)
    url = "https://github.com/example-org/example-repo/pull/34"

    async def fake_get_gh_username() -> str:
        return "reviewer"

    async def fake_fetch_repo_data(owner: str, name: str, gh_user: str) -> dict:
        return {
            "data": {
                "repository": {
                    "isArchived": False,
                    "openIssues": {"nodes": []},
                    "closedIssues": {"nodes": []},
                    "authoredPRs": {"nodes": []},
                    "mergedPRs": {"nodes": []},
                    "closedPRs": {"nodes": []},
                },
            },
        }

    async def fake_discover_review_prs(orgs: list[str], gh_user: str) -> list[dict]:
        return [
            {
                "number": 34,
                "title": "Fix telemetry attributes",
                "url": url,
                "repository": {"nameWithOwner": "example-org/example-repo"},
                "author": {"login": "author"},
            }
        ]

    async def fake_fetch_review_detail(owner: str, name: str, number: int, gh_user: str) -> dict:
        return {
            "data": {
                "repository": {
                    "pullRequest": {
                        "number": number,
                        "title": "Fix telemetry attributes",
                        "url": url,
                        "author": {"login": "author", "name": "Author Person"},
                        "commits": {"nodes": [{"commit": {"committedDate": "2026-04-07T20:00:00Z"}}]},
                        "reviews": {"nodes": []},
                    },
                },
            },
        }

    async def fake_fetch_notifications(gh_user: str) -> list[dict]:
        return []

    from agendum import gh

    monkeypatch.setattr(gh, "get_gh_username", fake_get_gh_username)
    monkeypatch.setattr(gh, "fetch_repo_data", fake_fetch_repo_data)
    monkeypatch.setattr(gh, "discover_review_prs", fake_discover_review_prs)
    monkeypatch.setattr(gh, "fetch_review_detail", fake_fetch_review_detail)
    monkeypatch.setattr(gh, "fetch_notifications", fake_fetch_notifications)

    changes, attention, error = await run_sync(
        tmp_db,
        AgendumConfig(repos=["example-org/example-repo"]),
    )

    tasks = get_active_tasks(tmp_db)
    assert changes == 1
    assert attention is True
    assert error is None
    assert len(tasks) == 1
    assert tasks[0]["source"] == "pr_review"
    assert tasks[0]["status"] == "review requested"
    assert tasks[0]["gh_url"] == url
    assert tasks[0]["gh_author"] == "author"
    assert tasks[0]["gh_author_name"] == "Author"

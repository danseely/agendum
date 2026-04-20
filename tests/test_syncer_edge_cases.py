"""Edge-case tests for the sync engine."""

from pathlib import Path

import pytest

from agendum.config import AgendumConfig
from agendum.db import add_task, find_task_by_gh_url, get_active_tasks, init_db, update_task
from agendum.syncer import diff_tasks, run_sync


# ── diff edge cases ──────────────────────────────────────────────────────


def test_diff_detects_author_name_change() -> None:
    existing = [
        {"id": 1, "gh_url": "https://github.com/org/repo/pull/1",
         "status": "review requested", "title": "PR",
         "gh_author_name": "Old Name"},
    ]
    incoming = [
        {"gh_url": "https://github.com/org/repo/pull/1",
         "title": "PR", "source": "pr_review", "status": "review requested",
         "gh_author_name": "New Name"},
    ]
    result = diff_tasks(existing, incoming)
    assert len(result.to_update) == 1
    assert result.to_update[0]["gh_author_name"] == "New Name"


def test_diff_detects_tag_change() -> None:
    existing = [
        {"id": 1, "gh_url": "https://github.com/org/repo/pull/1",
         "status": "open", "title": "PR", "tags": '["old"]'},
    ]
    incoming = [
        {"gh_url": "https://github.com/org/repo/pull/1",
         "title": "PR", "source": "pr_authored", "status": "open",
         "tags": '["new"]'},
    ]
    result = diff_tasks(existing, incoming)
    assert len(result.to_update) == 1
    assert result.to_update[0]["tags"] == '["new"]'


def test_diff_closes_review_task_when_repo_not_in_fetched_repos() -> None:
    """Review tasks must close via review_fetch_ok, regardless of fetched_repos.

    Once GitHub drops the user from --review-requested, the repo may no
    longer be in fetched_repos — but the review task must still close.
    Authored/issue tasks keep the fetched_repos guard.
    """
    existing = [
        {"id": 1, "source": "pr_review",
         "gh_url": "https://github.com/org/other-repo/pull/1",
         "gh_repo": "org/other-repo", "status": "review requested",
         "title": "Review PR"},
        {"id": 2, "source": "pr_authored",
         "gh_url": "https://github.com/org/other-repo/pull/2",
         "gh_repo": "org/other-repo", "status": "open", "title": "My PR"},
    ]
    result = diff_tasks(
        existing, incoming=[], fetched_repos=set(), review_fetch_ok=True,
    )
    closed_ids = {t["id"] for t in result.to_close}
    assert closed_ids == {1}


def test_diff_detects_project_change() -> None:
    existing = [
        {"id": 1, "gh_url": "https://github.com/org/repo/pull/1",
         "status": "open", "title": "PR", "project": "old-name"},
    ]
    incoming = [
        {"gh_url": "https://github.com/org/repo/pull/1",
         "title": "PR", "source": "pr_authored", "status": "open",
         "project": "new-name"},
    ]
    result = diff_tasks(existing, incoming)
    assert len(result.to_update) == 1
    assert result.to_update[0]["project"] == "new-name"


# ── run_sync edge cases ─────────────────────────────────────────────────


async def test_run_sync_skips_when_no_orgs_or_repos(tmp_db: Path) -> None:
    init_db(tmp_db)
    changes, attention, error = await run_sync(
        tmp_db, AgendumConfig(orgs=[], repos=[]),
    )
    assert changes == 0
    assert attention is False
    assert error is None


async def test_run_sync_notification_marks_seen_task_unseen(
    tmp_db: Path, monkeypatch,
) -> None:
    init_db(tmp_db)
    url = "https://github.com/org/repo/pull/5"
    task_id = add_task(tmp_db, title="Existing PR", source="pr_authored", status="open",
                       gh_url=url, gh_number=5, project="repo", gh_repo="org/repo")
    # Task is seen
    update_task(tmp_db, task_id, seen=1)

    async def fake_get_gh_username() -> str:
        return "author"

    async def fake_fetch_repo_data(owner, name, gh_user) -> dict:
        return {
            "data": {
                "repository": {
                    "isArchived": False,
                    "openIssues": {"nodes": []},
                    "closedIssues": {"nodes": []},
                    "authoredPRs": {
                        "nodes": [{
                            "number": 5, "url": url, "title": "Existing PR",
                            "state": "OPEN", "isDraft": False,
                            "reviewDecision": None,
                            "reviewRequests": {"totalCount": 0},
                            "labels": {"nodes": []},
                            "author": {"login": "author"},
                        }],
                    },
                    "mergedPRs": {"nodes": []},
                    "closedPRs": {"nodes": []},
                },
            },
        }

    async def fake_discover_review_prs(orgs, gh_user) -> tuple[list, bool]:
        return [], True

    async def fake_fetch_notifications(gh_user) -> list:
        return [{
            "reason": "comment",
            "subject": {
                "url": "https://api.github.com/repos/org/repo/pulls/5",
            },
        }]

    from agendum import gh
    monkeypatch.setattr(gh, "get_gh_username", fake_get_gh_username)
    monkeypatch.setattr(gh, "fetch_repo_data", fake_fetch_repo_data)
    monkeypatch.setattr(gh, "discover_review_prs", fake_discover_review_prs)
    monkeypatch.setattr(gh, "fetch_notifications", fake_fetch_notifications)

    changes, attention, error = await run_sync(
        tmp_db, AgendumConfig(repos=["org/repo"]),
    )

    task = find_task_by_gh_url(tmp_db, url)
    assert task["seen"] == 0
    assert attention is True
    assert error is None


async def test_run_sync_excludes_repos(tmp_db: Path, monkeypatch) -> None:
    init_db(tmp_db)

    fetch_calls = []

    async def fake_get_gh_username() -> str:
        return "author"

    async def fake_discover_repos(orgs, gh_user) -> set:
        return {"org/keep", "org/exclude-me"}

    async def fake_fetch_repo_data(owner, name, gh_user) -> dict:
        fetch_calls.append(f"{owner}/{name}")
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

    async def fake_discover_review_prs(orgs, gh_user) -> tuple[list, bool]:
        return [], True

    async def fake_fetch_notifications(gh_user) -> list:
        return []

    from agendum import gh
    monkeypatch.setattr(gh, "get_gh_username", fake_get_gh_username)
    monkeypatch.setattr(gh, "discover_repos", fake_discover_repos)
    monkeypatch.setattr(gh, "fetch_repo_data", fake_fetch_repo_data)
    monkeypatch.setattr(gh, "discover_review_prs", fake_discover_review_prs)
    monkeypatch.setattr(gh, "fetch_notifications", fake_fetch_notifications)

    await run_sync(
        tmp_db,
        AgendumConfig(orgs=["org"], exclude_repos=["org/exclude-me"]),
    )

    assert "org/keep" in fetch_calls
    assert "org/exclude-me" not in fetch_calls


async def test_run_sync_attention_on_status_change_to_approved(
    tmp_db: Path, monkeypatch,
) -> None:
    init_db(tmp_db)
    url = "https://github.com/org/repo/pull/10"
    add_task(tmp_db, title="My PR", source="pr_authored", status="awaiting review",
             gh_url=url)

    async def fake_get_gh_username() -> str:
        return "author"

    async def fake_fetch_repo_data(owner, name, gh_user) -> dict:
        return {
            "data": {
                "repository": {
                    "isArchived": False,
                    "openIssues": {"nodes": []},
                    "closedIssues": {"nodes": []},
                    "authoredPRs": {
                        "nodes": [{
                            "number": 10, "url": url, "title": "My PR",
                            "state": "OPEN", "isDraft": False,
                            "reviewDecision": "APPROVED",
                            "reviewRequests": {"totalCount": 1},
                            "labels": {"nodes": []},
                            "author": {"login": "author"},
                        }],
                    },
                    "mergedPRs": {"nodes": []},
                    "closedPRs": {"nodes": []},
                },
            },
        }

    async def fake_discover_review_prs(orgs, gh_user) -> tuple[list, bool]:
        return [], True

    async def fake_fetch_notifications(gh_user) -> list:
        return []

    from agendum import gh
    monkeypatch.setattr(gh, "get_gh_username", fake_get_gh_username)
    monkeypatch.setattr(gh, "fetch_repo_data", fake_fetch_repo_data)
    monkeypatch.setattr(gh, "discover_review_prs", fake_discover_review_prs)
    monkeypatch.setattr(gh, "fetch_notifications", fake_fetch_notifications)

    changes, attention, error = await run_sync(
        tmp_db, AgendumConfig(repos=["org/repo"]),
    )

    assert changes == 1
    assert attention is True
    task = find_task_by_gh_url(tmp_db, url)
    assert task["status"] == "approved"


async def test_run_sync_preserves_tasks_when_repo_fetch_fails(
    tmp_db: Path, monkeypatch,
) -> None:
    """Tasks should survive when their repo's API call fails."""
    init_db(tmp_db)
    url = "https://github.com/org/repo/pull/3"
    add_task(tmp_db, title="My PR", source="pr_authored", status="open",
             gh_url=url, gh_repo="org/repo")

    async def fake_get_gh_username() -> str:
        return "author"

    async def fake_fetch_repo_data(owner, name, gh_user) -> dict:
        # Simulate API failure — returns empty
        return {}

    async def fake_discover_review_prs(orgs, gh_user) -> tuple[list, bool]:
        return [], True

    async def fake_fetch_notifications(gh_user) -> list:
        return []

    from agendum import gh
    monkeypatch.setattr(gh, "get_gh_username", fake_get_gh_username)
    monkeypatch.setattr(gh, "fetch_repo_data", fake_fetch_repo_data)
    monkeypatch.setattr(gh, "discover_review_prs", fake_discover_review_prs)
    monkeypatch.setattr(gh, "fetch_notifications", fake_fetch_notifications)

    changes, attention, error = await run_sync(
        tmp_db, AgendumConfig(repos=["org/repo"]),
    )

    task = find_task_by_gh_url(tmp_db, url)
    assert task is not None
    assert task["status"] == "open", "Task should not be closed when repo fetch failed"


async def test_run_sync_preserves_review_tasks_when_review_fetch_fails(
    tmp_db: Path, monkeypatch,
) -> None:
    """Review tasks should survive when discover_review_prs fails."""
    init_db(tmp_db)
    url = "https://github.com/org/repo/pull/9"
    add_task(tmp_db, title="Review PR", source="pr_review", status="review requested",
             gh_url=url, gh_repo="org/repo")

    async def fake_get_gh_username() -> str:
        return "reviewer"

    async def fake_fetch_repo_data(owner, name, gh_user) -> dict:
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

    async def fake_discover_review_prs(orgs, gh_user) -> tuple[list, bool]:
        return [], False  # Simulate API failure

    async def fake_fetch_notifications(gh_user) -> list:
        return []

    from agendum import gh
    monkeypatch.setattr(gh, "get_gh_username", fake_get_gh_username)
    monkeypatch.setattr(gh, "fetch_repo_data", fake_fetch_repo_data)
    monkeypatch.setattr(gh, "discover_review_prs", fake_discover_review_prs)
    monkeypatch.setattr(gh, "fetch_notifications", fake_fetch_notifications)

    changes, attention, error = await run_sync(
        tmp_db, AgendumConfig(repos=["org/repo"]),
    )

    task = find_task_by_gh_url(tmp_db, url)
    assert task is not None
    assert task["status"] == "review requested", "Review task should not be closed when review fetch failed"


async def test_run_sync_repo_only_preserves_review_tasks_without_review_discovery(
    tmp_db: Path,
    monkeypatch,
) -> None:
    init_db(tmp_db)
    url = "https://github.com/org/repo/pull/11"
    add_task(
        tmp_db,
        title="Review PR",
        source="pr_review",
        status="review requested",
        gh_url=url,
        gh_repo="org/repo",
    )

    async def fake_get_gh_username() -> str:
        return "reviewer"

    async def fake_fetch_repo_data(owner, name, gh_user) -> dict:
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

    async def fake_discover_review_prs(orgs, gh_user) -> tuple[list, bool]:
        assert orgs == []
        return [], True

    async def fake_fetch_notifications(gh_user) -> list:
        return []

    from agendum import gh

    monkeypatch.setattr(gh, "get_gh_username", fake_get_gh_username)
    monkeypatch.setattr(gh, "fetch_repo_data", fake_fetch_repo_data)
    monkeypatch.setattr(gh, "discover_review_prs", fake_discover_review_prs)
    monkeypatch.setattr(gh, "fetch_notifications", fake_fetch_notifications)

    changes, attention, error = await run_sync(tmp_db, AgendumConfig(repos=["org/repo"]))

    assert changes == 0
    assert attention is False
    assert error is None
    task = find_task_by_gh_url(tmp_db, url)
    assert task is not None
    assert task["status"] == "review requested"


async def test_run_sync_closes_review_pr_as_done(
    tmp_db: Path, monkeypatch,
) -> None:
    """When a review PR disappears from incoming, it should be marked 'done'."""
    init_db(tmp_db)
    url = "https://github.com/org/repo/pull/8"
    add_task(tmp_db, title="Review PR", source="pr_review", status="review requested",
             gh_url=url, gh_repo="org/repo")

    async def fake_get_gh_username() -> str:
        return "reviewer"

    async def fake_fetch_repo_data(owner, name, gh_user) -> dict:
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

    async def fake_discover_review_prs(orgs, gh_user) -> tuple[list, bool]:
        return [], True

    async def fake_fetch_notifications(gh_user) -> list:
        return []

    from agendum import gh
    monkeypatch.setattr(gh, "get_gh_username", fake_get_gh_username)
    monkeypatch.setattr(gh, "fetch_repo_data", fake_fetch_repo_data)
    monkeypatch.setattr(gh, "discover_review_prs", fake_discover_review_prs)
    monkeypatch.setattr(gh, "fetch_notifications", fake_fetch_notifications)

    changes, attention, error = await run_sync(
        tmp_db, AgendumConfig(orgs=["org"], repos=["org/repo"]),
    )

    task = find_task_by_gh_url(tmp_db, url)
    assert task["status"] == "done"


async def test_run_sync_closes_review_pr_when_repo_not_in_fetched_repos(
    tmp_db: Path, monkeypatch,
) -> None:
    """Review task closes even when the repo drops out of discover_repos.

    Once the user reviews, GitHub removes them from --review-requested, so
    the repo is no longer surfaced by discover_repos and never makes it
    into fetched_repos. The review task must still close.
    """
    init_db(tmp_db)
    url = "https://github.com/org/other-repo/pull/100"
    add_task(tmp_db, title="Review PR", source="pr_review",
             status="review requested",
             gh_url=url, gh_repo="org/other-repo")

    async def fake_get_gh_username() -> str:
        return "reviewer"

    async def fake_discover_repos(orgs, gh_user) -> set:
        return set()

    async def fake_discover_review_prs(orgs, gh_user) -> tuple[list, bool]:
        return [], True

    async def fake_fetch_notifications(gh_user) -> list:
        return []

    from agendum import gh
    monkeypatch.setattr(gh, "get_gh_username", fake_get_gh_username)
    monkeypatch.setattr(gh, "discover_repos", fake_discover_repos)
    monkeypatch.setattr(gh, "discover_review_prs", fake_discover_review_prs)
    monkeypatch.setattr(gh, "fetch_notifications", fake_fetch_notifications)

    changes, attention, error = await run_sync(tmp_db, AgendumConfig(orgs=["org"]))

    task = find_task_by_gh_url(tmp_db, url)
    assert task["status"] == "done"

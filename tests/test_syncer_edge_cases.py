"""Edge-case tests for the sync engine."""

from pathlib import Path

import pytest

from agendum.config import AgendumConfig
from agendum.db import add_task, find_task_by_gh_url, get_active_tasks, init_db, update_task
from agendum.syncer import diff_tasks, run_sync


def make_empty_repo_payload() -> dict:
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

    async def fake_search_authored_prs(orgs, gh_user) -> tuple[list, bool]:
        assert orgs == ["org"]
        return [{"id": "PR_5", "repository": {"nameWithOwner": "org/repo"}}], True

    async def fake_search_merged_authored_prs(orgs, gh_user) -> tuple[list, bool]:
        return [], True

    async def fake_search_closed_authored_prs(orgs, gh_user) -> tuple[list, bool]:
        return [], True

    async def fake_search_assigned_issues(orgs, gh_user) -> tuple[list, bool]:
        return [], True

    async def fake_search_closed_issues(orgs, gh_user) -> tuple[list, bool]:
        return [], True

    async def fake_search_review_requested_prs(orgs, gh_user) -> tuple[list, bool]:
        return [], True

    async def fake_hydrate_pull_requests(node_ids) -> tuple[list, bool]:
        assert node_ids == ["PR_5"]
        return [{
            "id": "PR_5",
            "number": 5,
            "url": url,
            "title": "Existing PR",
            "state": "OPEN",
            "isDraft": False,
            "reviewDecision": None,
            "reviewRequests": {"totalCount": 0},
            "labels": {"nodes": []},
            "author": {"login": "author"},
            "repository": {"nameWithOwner": "org/repo"},
            "commits": {"nodes": []},
            "reviews": {"nodes": []},
            "reviewThreads": {"nodes": []},
        }], True

    async def fake_hydrate_issues(node_ids) -> tuple[list, bool]:
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
    monkeypatch.setattr(gh, "search_authored_prs", fake_search_authored_prs)
    monkeypatch.setattr(gh, "search_merged_authored_prs", fake_search_merged_authored_prs)
    monkeypatch.setattr(gh, "search_closed_authored_prs", fake_search_closed_authored_prs)
    monkeypatch.setattr(gh, "search_assigned_issues", fake_search_assigned_issues)
    monkeypatch.setattr(gh, "search_closed_issues", fake_search_closed_issues)
    monkeypatch.setattr(gh, "search_review_requested_prs", fake_search_review_requested_prs)
    monkeypatch.setattr(gh, "hydrate_pull_requests", fake_hydrate_pull_requests)
    monkeypatch.setattr(gh, "hydrate_issues", fake_hydrate_issues)
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

    hydrate_calls: list[tuple[str, ...]] = []

    async def fake_get_gh_username() -> str:
        return "author"

    async def fake_search_authored_prs(orgs, gh_user) -> tuple[list, bool]:
        return [
            {"id": "PR_KEEP", "repository": {"nameWithOwner": "org/keep"}},
            {"id": "PR_SKIP", "repository": {"nameWithOwner": "org/exclude-me"}},
        ], True

    async def fake_search_merged_authored_prs(orgs, gh_user) -> tuple[list, bool]:
        return [], True

    async def fake_search_closed_authored_prs(orgs, gh_user) -> tuple[list, bool]:
        return [], True

    async def fake_search_assigned_issues(orgs, gh_user) -> tuple[list, bool]:
        return [], True

    async def fake_search_closed_issues(orgs, gh_user) -> tuple[list, bool]:
        return [], True

    async def fake_search_review_requested_prs(orgs, gh_user) -> tuple[list, bool]:
        return [], True

    async def fake_hydrate_pull_requests(node_ids: list[str]) -> tuple[list[dict], bool]:
        hydrate_calls.append(tuple(node_ids))
        return [
            {
                "id": "PR_KEEP",
                "number": 1,
                "title": "Keep PR",
                "url": "https://github.com/org/keep/pull/1",
                "state": "OPEN",
                "isDraft": False,
                "reviewDecision": None,
                "repository": {"nameWithOwner": "org/keep"},
                "labels": {"nodes": []},
                "reviewRequests": {"totalCount": 0},
                "commits": {"nodes": []},
                "reviews": {"nodes": []},
                "reviewThreads": {"nodes": []},
                "author": {"login": "author"},
            }
        ], True

    async def fake_hydrate_issues(node_ids: list[str]) -> tuple[list[dict], bool]:
        return [], True

    async def fake_fetch_notifications(gh_user) -> list:
        return []

    from agendum import gh
    monkeypatch.setattr(gh, "get_gh_username", fake_get_gh_username)
    monkeypatch.setattr(gh, "search_authored_prs", fake_search_authored_prs)
    monkeypatch.setattr(gh, "search_merged_authored_prs", fake_search_merged_authored_prs)
    monkeypatch.setattr(gh, "search_closed_authored_prs", fake_search_closed_authored_prs)
    monkeypatch.setattr(gh, "search_assigned_issues", fake_search_assigned_issues)
    monkeypatch.setattr(gh, "search_closed_issues", fake_search_closed_issues)
    monkeypatch.setattr(gh, "search_review_requested_prs", fake_search_review_requested_prs)
    monkeypatch.setattr(gh, "hydrate_pull_requests", fake_hydrate_pull_requests)
    monkeypatch.setattr(gh, "hydrate_issues", fake_hydrate_issues)
    monkeypatch.setattr(gh, "fetch_notifications", fake_fetch_notifications)

    await run_sync(
        tmp_db,
        AgendumConfig(orgs=["org"], exclude_repos=["org/exclude-me"]),
    )

    assert hydrate_calls == [("PR_KEEP",)]
    assert find_task_by_gh_url(tmp_db, "https://github.com/org/keep/pull/1") is not None
    assert find_task_by_gh_url(tmp_db, "https://github.com/org/exclude-me/pull/1") is None


async def test_run_sync_repo_only_excludes_repos(tmp_db: Path, monkeypatch) -> None:
    init_db(tmp_db)

    hydrate_calls: list[tuple[str, ...]] = []

    async def fake_get_gh_username() -> str:
        return "author"

    async def fake_search_authored_prs(orgs, gh_user) -> tuple[list, bool]:
        assert orgs == ["org"]
        return [
            {"id": "PR_KEEP", "repository": {"nameWithOwner": "org/keep"}},
            {"id": "PR_SKIP", "repository": {"nameWithOwner": "org/exclude-me"}},
        ], True

    async def fake_search_merged_authored_prs(orgs, gh_user) -> tuple[list, bool]:
        return [], True

    async def fake_search_closed_authored_prs(orgs, gh_user) -> tuple[list, bool]:
        return [], True

    async def fake_search_assigned_issues(orgs, gh_user) -> tuple[list, bool]:
        return [], True

    async def fake_search_closed_issues(orgs, gh_user) -> tuple[list, bool]:
        return [], True

    async def fake_search_review_requested_prs(orgs, gh_user) -> tuple[list, bool]:
        return [], True

    async def fake_hydrate_pull_requests(node_ids: list[str]) -> tuple[list[dict], bool]:
        hydrate_calls.append(tuple(node_ids))
        return [
            {
                "id": "PR_KEEP",
                "number": 1,
                "title": "Keep PR",
                "url": "https://github.com/org/keep/pull/1",
                "state": "OPEN",
                "isDraft": False,
                "reviewDecision": None,
                "repository": {"nameWithOwner": "org/keep"},
                "labels": {"nodes": []},
                "reviewRequests": {"totalCount": 0},
                "commits": {"nodes": []},
                "reviews": {"nodes": []},
                "reviewThreads": {"nodes": []},
                "author": {"login": "author"},
            }
        ], True

    async def fake_hydrate_issues(node_ids: list[str]) -> tuple[list[dict], bool]:
        return [], True

    async def fake_fetch_notifications(gh_user) -> list:
        return []

    from agendum import gh
    monkeypatch.setattr(gh, "get_gh_username", fake_get_gh_username)
    monkeypatch.setattr(gh, "search_authored_prs", fake_search_authored_prs)
    monkeypatch.setattr(gh, "search_merged_authored_prs", fake_search_merged_authored_prs)
    monkeypatch.setattr(gh, "search_closed_authored_prs", fake_search_closed_authored_prs)
    monkeypatch.setattr(gh, "search_assigned_issues", fake_search_assigned_issues)
    monkeypatch.setattr(gh, "search_closed_issues", fake_search_closed_issues)
    monkeypatch.setattr(gh, "search_review_requested_prs", fake_search_review_requested_prs)
    monkeypatch.setattr(gh, "hydrate_pull_requests", fake_hydrate_pull_requests)
    monkeypatch.setattr(gh, "hydrate_issues", fake_hydrate_issues)
    monkeypatch.setattr(gh, "fetch_notifications", fake_fetch_notifications)

    await run_sync(
        tmp_db,
        AgendumConfig(
            repos=["org/keep", "org/exclude-me"],
            exclude_repos=["org/exclude-me"],
        ),
    )

    assert hydrate_calls == [("PR_KEEP",)]
    assert find_task_by_gh_url(tmp_db, "https://github.com/org/keep/pull/1") is not None
    assert find_task_by_gh_url(tmp_db, "https://github.com/org/exclude-me/pull/1") is None


async def test_run_sync_attention_on_status_change_to_approved(
    tmp_db: Path, monkeypatch,
) -> None:
    init_db(tmp_db)
    url = "https://github.com/org/repo/pull/10"
    add_task(tmp_db, title="My PR", source="pr_authored", status="awaiting review",
             gh_url=url)

    async def fake_get_gh_username() -> str:
        return "author"

    async def fake_search_authored_prs(orgs, gh_user) -> tuple[list, bool]:
        assert orgs == ["org"]
        return [{"id": "PR_10", "repository": {"nameWithOwner": "org/repo"}}], True

    async def fake_search_merged_authored_prs(orgs, gh_user) -> tuple[list, bool]:
        return [], True

    async def fake_search_closed_authored_prs(orgs, gh_user) -> tuple[list, bool]:
        return [], True

    async def fake_search_assigned_issues(orgs, gh_user) -> tuple[list, bool]:
        return [], True

    async def fake_search_closed_issues(orgs, gh_user) -> tuple[list, bool]:
        return [], True

    async def fake_search_review_requested_prs(orgs, gh_user) -> tuple[list, bool]:
        return [], True

    async def fake_hydrate_pull_requests(node_ids) -> tuple[list, bool]:
        assert node_ids == ["PR_10"]
        return [{
            "id": "PR_10",
            "number": 10,
            "url": url,
            "title": "My PR",
            "state": "OPEN",
            "isDraft": False,
            "reviewDecision": "APPROVED",
            "reviewRequests": {"totalCount": 1},
            "labels": {"nodes": []},
            "author": {"login": "author"},
            "repository": {"nameWithOwner": "org/repo"},
            "commits": {"nodes": []},
            "reviews": {"nodes": []},
            "reviewThreads": {"nodes": []},
        }], True

    async def fake_hydrate_issues(node_ids) -> tuple[list, bool]:
        return [], True

    async def fake_fetch_notifications(gh_user) -> list:
        return []

    from agendum import gh
    monkeypatch.setattr(gh, "get_gh_username", fake_get_gh_username)
    monkeypatch.setattr(gh, "search_authored_prs", fake_search_authored_prs)
    monkeypatch.setattr(gh, "search_merged_authored_prs", fake_search_merged_authored_prs)
    monkeypatch.setattr(gh, "search_closed_authored_prs", fake_search_closed_authored_prs)
    monkeypatch.setattr(gh, "search_assigned_issues", fake_search_assigned_issues)
    monkeypatch.setattr(gh, "search_closed_issues", fake_search_closed_issues)
    monkeypatch.setattr(gh, "search_review_requested_prs", fake_search_review_requested_prs)
    monkeypatch.setattr(gh, "hydrate_pull_requests", fake_hydrate_pull_requests)
    monkeypatch.setattr(gh, "hydrate_issues", fake_hydrate_issues)
    monkeypatch.setattr(gh, "fetch_notifications", fake_fetch_notifications)

    changes, attention, error = await run_sync(
        tmp_db, AgendumConfig(repos=["org/repo"]),
    )

    assert changes == 1
    assert attention is True
    task = find_task_by_gh_url(tmp_db, url)
    assert task["status"] == "approved"


@pytest.mark.parametrize(
    ("source", "url", "repo"),
    [
        ("pr_authored", "https://github.com/org/authored-repo/pull/7",
         "org/authored-repo"),
        ("issue", "https://github.com/org/issue-repo/issues/8",
         "org/issue-repo"),
    ],
)
async def test_run_sync_org_sync_incomplete_slice_keeps_existing_task(
    tmp_db: Path,
    monkeypatch,
    source: str,
    url: str,
    repo: str,
) -> None:
    init_db(tmp_db)
    add_task(tmp_db, title="Existing task", source=source, status="open",
             gh_url=url, gh_repo=repo)

    calls = {
        "search_authored_prs": 0,
        "search_assigned_issues": 0,
        "hydrate_pull_requests": 0,
        "hydrate_issues": 0,
        "discover_repos": 0,
        "fetch_repo_data": 0,
    }

    async def fake_get_gh_username() -> str:
        return "dan"

    async def fake_search_authored_prs(orgs, gh_user) -> tuple[list, bool]:
        calls["search_authored_prs"] += 1
        if source == "pr_authored":
            return [], False
        return [], True

    async def fake_search_merged_authored_prs(orgs, gh_user) -> tuple[list, bool]:
        return [], True

    async def fake_search_closed_authored_prs(orgs, gh_user) -> tuple[list, bool]:
        return [], True

    async def fake_search_assigned_issues(orgs, gh_user) -> tuple[list, bool]:
        calls["search_assigned_issues"] += 1
        if source == "issue":
            return [{"id": "ISSUE_1", "repository": {"nameWithOwner": repo}}], True
        return [], True

    async def fake_search_closed_issues(orgs, gh_user) -> tuple[list, bool]:
        return [], True

    async def fake_search_review_requested_prs(orgs, gh_user) -> tuple[list, bool]:
        return [], True

    async def fake_hydrate_pull_requests(node_ids) -> tuple[list, bool]:
        calls["hydrate_pull_requests"] += 1
        return [], False

    async def fake_hydrate_issues(node_ids) -> tuple[list, bool]:
        calls["hydrate_issues"] += 1
        return [], False

    async def fake_discover_repos(orgs, gh_user) -> set:
        calls["discover_repos"] += 1
        return {repo}

    async def fake_fetch_repo_data(owner, name, gh_user) -> dict:
        calls["fetch_repo_data"] += 1
        return make_empty_repo_payload()

    async def fake_discover_review_prs(orgs, gh_user) -> tuple[list, bool]:
        return [], True

    async def fake_fetch_notifications(gh_user) -> list:
        return []

    from agendum import gh

    monkeypatch.setattr(gh, "get_gh_username", fake_get_gh_username)
    monkeypatch.setattr(gh, "search_authored_prs", fake_search_authored_prs)
    monkeypatch.setattr(gh, "search_merged_authored_prs", fake_search_merged_authored_prs)
    monkeypatch.setattr(gh, "search_closed_authored_prs", fake_search_closed_authored_prs)
    monkeypatch.setattr(gh, "search_assigned_issues", fake_search_assigned_issues)
    monkeypatch.setattr(gh, "search_closed_issues", fake_search_closed_issues)
    monkeypatch.setattr(gh, "search_review_requested_prs", fake_search_review_requested_prs)
    monkeypatch.setattr(gh, "hydrate_pull_requests", fake_hydrate_pull_requests)
    monkeypatch.setattr(gh, "hydrate_issues", fake_hydrate_issues)
    monkeypatch.setattr(gh, "discover_repos", fake_discover_repos)
    monkeypatch.setattr(gh, "fetch_repo_data", fake_fetch_repo_data)
    monkeypatch.setattr(gh, "discover_review_prs", fake_discover_review_prs)
    monkeypatch.setattr(gh, "fetch_notifications", fake_fetch_notifications)

    changes, attention, error = await run_sync(tmp_db, AgendumConfig(orgs=["org"]))

    assert changes == 0
    assert attention is False
    assert error is None
    assert calls["search_authored_prs"] == 1
    assert calls["search_assigned_issues"] == 1
    if source == "issue":
        assert calls["hydrate_issues"] == 1
    assert calls["discover_repos"] == 0
    assert calls["fetch_repo_data"] == 0

    task = find_task_by_gh_url(tmp_db, url)
    assert task is not None
    assert task["status"] == "open"


async def test_run_sync_preserves_tasks_when_repo_search_is_incomplete(
    tmp_db: Path, monkeypatch,
) -> None:
    """Tasks should survive when repo-scoped search-first discovery is incomplete."""
    init_db(tmp_db)
    url = "https://github.com/org/repo/pull/3"
    add_task(tmp_db, title="My PR", source="pr_authored", status="open",
             gh_url=url, gh_repo="org/repo")

    async def fake_get_gh_username() -> str:
        return "author"

    async def fake_search_authored_prs(orgs, gh_user) -> tuple[list, bool]:
        assert orgs == ["org"]
        return [], False

    async def fake_search_merged_authored_prs(orgs, gh_user) -> tuple[list, bool]:
        return [], True

    async def fake_search_closed_authored_prs(orgs, gh_user) -> tuple[list, bool]:
        return [], True

    async def fake_search_assigned_issues(orgs, gh_user) -> tuple[list, bool]:
        return [], True

    async def fake_search_closed_issues(orgs, gh_user) -> tuple[list, bool]:
        return [], True

    async def fake_search_review_requested_prs(orgs, gh_user) -> tuple[list, bool]:
        return [], True

    async def fake_hydrate_pull_requests(node_ids) -> tuple[list, bool]:
        return [], True

    async def fake_hydrate_issues(node_ids) -> tuple[list, bool]:
        return [], True

    async def fake_fetch_notifications(gh_user) -> list:
        return []

    from agendum import gh
    monkeypatch.setattr(gh, "get_gh_username", fake_get_gh_username)
    monkeypatch.setattr(gh, "search_authored_prs", fake_search_authored_prs)
    monkeypatch.setattr(gh, "search_merged_authored_prs", fake_search_merged_authored_prs)
    monkeypatch.setattr(gh, "search_closed_authored_prs", fake_search_closed_authored_prs)
    monkeypatch.setattr(gh, "search_assigned_issues", fake_search_assigned_issues)
    monkeypatch.setattr(gh, "search_closed_issues", fake_search_closed_issues)
    monkeypatch.setattr(gh, "search_review_requested_prs", fake_search_review_requested_prs)
    monkeypatch.setattr(gh, "hydrate_pull_requests", fake_hydrate_pull_requests)
    monkeypatch.setattr(gh, "hydrate_issues", fake_hydrate_issues)
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
    """Review tasks should survive when review search-first discovery fails."""
    init_db(tmp_db)
    url = "https://github.com/org/repo/pull/9"
    add_task(tmp_db, title="Review PR", source="pr_review", status="review requested",
             gh_url=url, gh_repo="org/repo")

    async def fake_get_gh_username() -> str:
        return "reviewer"

    async def fake_search_authored_prs(orgs, gh_user) -> tuple[list, bool]:
        return [], True

    async def fake_search_merged_authored_prs(orgs, gh_user) -> tuple[list, bool]:
        return [], True

    async def fake_search_closed_authored_prs(orgs, gh_user) -> tuple[list, bool]:
        return [], True

    async def fake_search_assigned_issues(orgs, gh_user) -> tuple[list, bool]:
        return [], True

    async def fake_search_closed_issues(orgs, gh_user) -> tuple[list, bool]:
        return [], True

    async def fake_search_review_requested_prs(orgs, gh_user) -> tuple[list, bool]:
        return [], False  # Simulate API failure

    async def fake_hydrate_pull_requests(node_ids) -> tuple[list, bool]:
        return [], True

    async def fake_hydrate_issues(node_ids) -> tuple[list, bool]:
        return [], True

    async def fake_fetch_notifications(gh_user) -> list:
        return []

    from agendum import gh
    monkeypatch.setattr(gh, "get_gh_username", fake_get_gh_username)
    monkeypatch.setattr(gh, "search_authored_prs", fake_search_authored_prs)
    monkeypatch.setattr(gh, "search_merged_authored_prs", fake_search_merged_authored_prs)
    monkeypatch.setattr(gh, "search_closed_authored_prs", fake_search_closed_authored_prs)
    monkeypatch.setattr(gh, "search_assigned_issues", fake_search_assigned_issues)
    monkeypatch.setattr(gh, "search_closed_issues", fake_search_closed_issues)
    monkeypatch.setattr(gh, "search_review_requested_prs", fake_search_review_requested_prs)
    monkeypatch.setattr(gh, "hydrate_pull_requests", fake_hydrate_pull_requests)
    monkeypatch.setattr(gh, "hydrate_issues", fake_hydrate_issues)
    monkeypatch.setattr(gh, "fetch_notifications", fake_fetch_notifications)

    changes, attention, error = await run_sync(
        tmp_db, AgendumConfig(repos=["org/repo"]),
    )

    task = find_task_by_gh_url(tmp_db, url)
    assert task is not None
    assert task["status"] == "review requested", "Review task should not be closed when review fetch failed"


async def test_run_sync_preserves_review_tasks_when_review_detail_fetch_fails(
    tmp_db: Path,
    monkeypatch,
) -> None:
    init_db(tmp_db)
    url = "https://github.com/org/repo/pull/12"
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

    async def fake_search_authored_prs(orgs, gh_user) -> tuple[list, bool]:
        return [], True

    async def fake_search_merged_authored_prs(orgs, gh_user) -> tuple[list, bool]:
        return [], True

    async def fake_search_closed_authored_prs(orgs, gh_user) -> tuple[list, bool]:
        return [], True

    async def fake_search_assigned_issues(orgs, gh_user) -> tuple[list, bool]:
        return [], True

    async def fake_search_closed_issues(orgs, gh_user) -> tuple[list, bool]:
        return [], True

    async def fake_search_review_requested_prs(orgs, gh_user) -> tuple[list, bool]:
        return [
            {
                "id": "PR_12",
                "number": 12,
                "title": "Review PR",
                "url": url,
                "repository": {"nameWithOwner": "org/repo"},
                "author": {"login": "author"},
            }
        ], True

    async def fake_hydrate_pull_requests(node_ids) -> tuple[list, bool]:
        assert node_ids == ["PR_12"]
        return [], False

    async def fake_hydrate_issues(node_ids) -> tuple[list, bool]:
        return [], True

    async def fake_fetch_notifications(gh_user) -> list:
        return []

    from agendum import gh

    monkeypatch.setattr(gh, "get_gh_username", fake_get_gh_username)
    monkeypatch.setattr(gh, "search_authored_prs", fake_search_authored_prs)
    monkeypatch.setattr(gh, "search_merged_authored_prs", fake_search_merged_authored_prs)
    monkeypatch.setattr(gh, "search_closed_authored_prs", fake_search_closed_authored_prs)
    monkeypatch.setattr(gh, "search_assigned_issues", fake_search_assigned_issues)
    monkeypatch.setattr(gh, "search_closed_issues", fake_search_closed_issues)
    monkeypatch.setattr(gh, "search_review_requested_prs", fake_search_review_requested_prs)
    monkeypatch.setattr(gh, "hydrate_pull_requests", fake_hydrate_pull_requests)
    monkeypatch.setattr(gh, "hydrate_issues", fake_hydrate_issues)
    monkeypatch.setattr(gh, "fetch_notifications", fake_fetch_notifications)

    changes, attention, error = await run_sync(
        tmp_db,
        AgendumConfig(orgs=["org"], repos=["org/repo"]),
    )

    assert changes == 0
    assert attention is False
    assert error is None
    task = find_task_by_gh_url(tmp_db, url)
    assert task is not None
    assert task["status"] == "review requested"


async def test_run_sync_repo_only_uses_search_first_path(
    tmp_db: Path,
    monkeypatch,
) -> None:
    init_db(tmp_db)

    legacy_calls = {
        "fetch_repo_data": 0,
        "discover_review_prs": 0,
    }
    search_calls = {
        "search_authored_prs": 0,
        "search_merged_authored_prs": 0,
        "search_closed_authored_prs": 0,
        "search_assigned_issues": 0,
        "search_closed_issues": 0,
        "search_review_requested_prs": 0,
        "hydrate_pull_requests": 0,
        "hydrate_issues": 0,
    }
    authored_url = "https://github.com/org/repo/pull/1"
    issue_url = "https://github.com/org/repo/issues/2"

    async def fake_get_gh_username() -> str:
        return "dan"

    async def fake_search_authored_prs(orgs, gh_user) -> tuple[list, bool]:
        search_calls["search_authored_prs"] += 1
        assert orgs == ["org"]
        return [{"id": "PR_1", "repository": {"nameWithOwner": "org/repo"}}], True

    async def fake_search_merged_authored_prs(orgs, gh_user) -> tuple[list, bool]:
        search_calls["search_merged_authored_prs"] += 1
        assert orgs == ["org"]
        return [], True

    async def fake_search_closed_authored_prs(orgs, gh_user) -> tuple[list, bool]:
        search_calls["search_closed_authored_prs"] += 1
        assert orgs == ["org"]
        return [], True

    async def fake_search_assigned_issues(orgs, gh_user) -> tuple[list, bool]:
        search_calls["search_assigned_issues"] += 1
        assert orgs == ["org"]
        return [{"id": "ISSUE_2", "repository": {"nameWithOwner": "org/repo"}}], True

    async def fake_search_closed_issues(orgs, gh_user) -> tuple[list, bool]:
        search_calls["search_closed_issues"] += 1
        assert orgs == ["org"]
        return [], True

    async def fake_search_review_requested_prs(orgs, gh_user) -> tuple[list, bool]:
        search_calls["search_review_requested_prs"] += 1
        assert orgs == ["org"]
        return [], True

    async def fake_hydrate_pull_requests(node_ids) -> tuple[list, bool]:
        search_calls["hydrate_pull_requests"] += 1
        assert node_ids == ["PR_1"]
        return [{
            "id": "PR_1",
            "number": 1,
            "title": "Repo PR",
            "url": authored_url,
            "state": "OPEN",
            "isDraft": False,
            "reviewDecision": None,
            "reviewRequests": {"totalCount": 0},
            "labels": {"nodes": []},
            "author": {"login": "dan"},
            "repository": {"nameWithOwner": "org/repo"},
            "commits": {"nodes": []},
            "reviews": {"nodes": []},
            "reviewThreads": {"nodes": []},
        }], True

    async def fake_hydrate_issues(node_ids) -> tuple[list, bool]:
        search_calls["hydrate_issues"] += 1
        assert node_ids == ["ISSUE_2"]
        return [{
            "id": "ISSUE_2",
            "number": 2,
            "title": "Repo issue",
            "url": issue_url,
            "state": "OPEN",
            "labels": {"nodes": []},
            "timelineItems": {"nodes": []},
            "repository": {"nameWithOwner": "org/repo"},
        }], True

    async def fake_discover_review_prs(orgs, gh_user) -> tuple[list, bool]:
        legacy_calls["discover_review_prs"] += 1
        return [], True

    async def fake_fetch_repo_data(owner, name, gh_user) -> dict:
        legacy_calls["fetch_repo_data"] += 1
        return {}

    async def fake_fetch_notifications(gh_user) -> list:
        return []

    from agendum import gh

    monkeypatch.setattr(gh, "get_gh_username", fake_get_gh_username)
    monkeypatch.setattr(gh, "search_authored_prs", fake_search_authored_prs)
    monkeypatch.setattr(gh, "search_merged_authored_prs", fake_search_merged_authored_prs)
    monkeypatch.setattr(gh, "search_closed_authored_prs", fake_search_closed_authored_prs)
    monkeypatch.setattr(gh, "search_assigned_issues", fake_search_assigned_issues)
    monkeypatch.setattr(gh, "search_closed_issues", fake_search_closed_issues)
    monkeypatch.setattr(gh, "search_review_requested_prs", fake_search_review_requested_prs)
    monkeypatch.setattr(gh, "hydrate_pull_requests", fake_hydrate_pull_requests)
    monkeypatch.setattr(gh, "hydrate_issues", fake_hydrate_issues)
    monkeypatch.setattr(gh, "fetch_repo_data", fake_fetch_repo_data)
    monkeypatch.setattr(gh, "discover_review_prs", fake_discover_review_prs)
    monkeypatch.setattr(gh, "fetch_notifications", fake_fetch_notifications)

    changes, attention, error = await run_sync(tmp_db, AgendumConfig(repos=["org/repo"]))

    assert changes == 2
    assert attention is False
    assert error is None
    assert legacy_calls == {
        "fetch_repo_data": 0,
        "discover_review_prs": 0,
    }
    assert search_calls == {
        "search_authored_prs": 1,
        "search_merged_authored_prs": 1,
        "search_closed_authored_prs": 1,
        "search_assigned_issues": 1,
        "search_closed_issues": 1,
        "search_review_requested_prs": 1,
        "hydrate_pull_requests": 1,
        "hydrate_issues": 1,
    }
    assert find_task_by_gh_url(tmp_db, authored_url)["source"] == "pr_authored"
    assert find_task_by_gh_url(tmp_db, issue_url)["source"] == "issue"


async def test_run_sync_repo_only_preserves_review_tasks_on_incomplete_review_search(
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

    async def fake_search_authored_prs(orgs, gh_user) -> tuple[list, bool]:
        assert orgs == ["org"]
        return [], True

    async def fake_search_merged_authored_prs(orgs, gh_user) -> tuple[list, bool]:
        assert orgs == ["org"]
        return [], True

    async def fake_search_closed_authored_prs(orgs, gh_user) -> tuple[list, bool]:
        assert orgs == ["org"]
        return [], True

    async def fake_search_assigned_issues(orgs, gh_user) -> tuple[list, bool]:
        assert orgs == ["org"]
        return [], True

    async def fake_search_closed_issues(orgs, gh_user) -> tuple[list, bool]:
        assert orgs == ["org"]
        return [], True

    async def fake_search_review_requested_prs(orgs, gh_user) -> tuple[list, bool]:
        assert orgs == ["org"]
        return [], False

    async def fake_hydrate_pull_requests(node_ids) -> tuple[list, bool]:
        return [], True

    async def fake_hydrate_issues(node_ids) -> tuple[list, bool]:
        return [], True

    async def fake_fetch_notifications(gh_user) -> list:
        return []

    from agendum import gh

    monkeypatch.setattr(gh, "get_gh_username", fake_get_gh_username)
    monkeypatch.setattr(gh, "search_authored_prs", fake_search_authored_prs)
    monkeypatch.setattr(gh, "search_merged_authored_prs", fake_search_merged_authored_prs)
    monkeypatch.setattr(gh, "search_closed_authored_prs", fake_search_closed_authored_prs)
    monkeypatch.setattr(gh, "search_assigned_issues", fake_search_assigned_issues)
    monkeypatch.setattr(gh, "search_closed_issues", fake_search_closed_issues)
    monkeypatch.setattr(gh, "search_review_requested_prs", fake_search_review_requested_prs)
    monkeypatch.setattr(gh, "hydrate_pull_requests", fake_hydrate_pull_requests)
    monkeypatch.setattr(gh, "hydrate_issues", fake_hydrate_issues)
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

    async def fake_search_authored_prs(orgs, gh_user) -> tuple[list, bool]:
        return [], True

    async def fake_search_merged_authored_prs(orgs, gh_user) -> tuple[list, bool]:
        return [], True

    async def fake_search_closed_authored_prs(orgs, gh_user) -> tuple[list, bool]:
        return [], True

    async def fake_search_assigned_issues(orgs, gh_user) -> tuple[list, bool]:
        return [], True

    async def fake_search_closed_issues(orgs, gh_user) -> tuple[list, bool]:
        return [], True

    async def fake_search_review_requested_prs(orgs, gh_user) -> tuple[list, bool]:
        return [], True

    async def fake_hydrate_pull_requests(node_ids: list[str]) -> tuple[list[dict], bool]:
        return [], True

    async def fake_hydrate_issues(node_ids: list[str]) -> tuple[list[dict], bool]:
        return [], True

    async def fake_fetch_notifications(gh_user) -> list:
        return []

    from agendum import gh
    monkeypatch.setattr(gh, "get_gh_username", fake_get_gh_username)
    monkeypatch.setattr(gh, "search_authored_prs", fake_search_authored_prs)
    monkeypatch.setattr(gh, "search_merged_authored_prs", fake_search_merged_authored_prs)
    monkeypatch.setattr(gh, "search_closed_authored_prs", fake_search_closed_authored_prs)
    monkeypatch.setattr(gh, "search_assigned_issues", fake_search_assigned_issues)
    monkeypatch.setattr(gh, "search_closed_issues", fake_search_closed_issues)
    monkeypatch.setattr(gh, "search_review_requested_prs", fake_search_review_requested_prs)
    monkeypatch.setattr(gh, "hydrate_pull_requests", fake_hydrate_pull_requests)
    monkeypatch.setattr(gh, "hydrate_issues", fake_hydrate_issues)
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

    async def fake_search_authored_prs(orgs, gh_user) -> tuple[list, bool]:
        return [], True

    async def fake_search_merged_authored_prs(orgs, gh_user) -> tuple[list, bool]:
        return [], True

    async def fake_search_closed_authored_prs(orgs, gh_user) -> tuple[list, bool]:
        return [], True

    async def fake_search_assigned_issues(orgs, gh_user) -> tuple[list, bool]:
        return [], True

    async def fake_search_closed_issues(orgs, gh_user) -> tuple[list, bool]:
        return [], True

    async def fake_search_review_requested_prs(orgs, gh_user) -> tuple[list, bool]:
        return [], True

    async def fake_hydrate_pull_requests(node_ids: list[str]) -> tuple[list[dict], bool]:
        return [], True

    async def fake_hydrate_issues(node_ids: list[str]) -> tuple[list[dict], bool]:
        return [], True

    async def fake_fetch_notifications(gh_user) -> list:
        return []

    from agendum import gh
    monkeypatch.setattr(gh, "get_gh_username", fake_get_gh_username)
    monkeypatch.setattr(gh, "search_authored_prs", fake_search_authored_prs)
    monkeypatch.setattr(gh, "search_merged_authored_prs", fake_search_merged_authored_prs)
    monkeypatch.setattr(gh, "search_closed_authored_prs", fake_search_closed_authored_prs)
    monkeypatch.setattr(gh, "search_assigned_issues", fake_search_assigned_issues)
    monkeypatch.setattr(gh, "search_closed_issues", fake_search_closed_issues)
    monkeypatch.setattr(gh, "search_review_requested_prs", fake_search_review_requested_prs)
    monkeypatch.setattr(gh, "hydrate_pull_requests", fake_hydrate_pull_requests)
    monkeypatch.setattr(gh, "hydrate_issues", fake_hydrate_issues)
    monkeypatch.setattr(gh, "fetch_notifications", fake_fetch_notifications)

    changes, attention, error = await run_sync(tmp_db, AgendumConfig(orgs=["org"]))

    task = find_task_by_gh_url(tmp_db, url)
    assert task["status"] == "done"

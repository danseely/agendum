import json
from pathlib import Path

import pytest

from agendum.config import AgendumConfig
from agendum.db import add_task, find_task_by_gh_url, get_active_tasks, init_db
from agendum.syncer import diff_tasks, run_sync


def make_review(
    *,
    author: str,
    submitted_at: str,
    state: str,
    review_id: str = "review-1",
    body: str = "feedback",
) -> dict:
    return {
        "id": review_id,
        "author": {"login": author},
        "submittedAt": submitted_at,
        "state": state,
        "body": body,
    }


def make_thread(*, is_resolved: bool, comments: list[dict]) -> dict:
    return {
        "isResolved": is_resolved,
        "comments": {"nodes": comments},
    }


def make_thread_comment(
    *,
    author: str,
    created_at: str,
    review_id: str | None = None,
) -> dict:
    return {
        "author": {"login": author},
        "createdAt": created_at,
        "pullRequestReview": {"id": review_id} if review_id is not None else None,
    }


def make_authored_pr(
    *,
    gh_user: str,
    url: str,
    review_decision: str | None = None,
    last_commit_at: str = "2026-04-07T20:00:00Z",
    reviews: list[dict] | None = None,
    review_threads: list[dict] | None = None,
) -> dict:
    return {
        "number": 12,
        "title": "Improve sync status handling",
        "url": url,
        "state": "OPEN",
        "isDraft": False,
        "author": {"login": gh_user},
        "reviewDecision": review_decision,
        "reviewRequests": {"totalCount": 0},
        "labels": {"nodes": []},
        "commits": {"nodes": [{"commit": {"committedDate": last_commit_at}}]},
        "reviews": {"nodes": reviews or []},
        "reviewThreads": {"nodes": review_threads or []},
    }


def authored_repo_payload(*, authored_prs: list[dict]) -> dict:
    return {
        "data": {
            "repository": {
                "isArchived": False,
                "openIssues": {"nodes": []},
                "closedIssues": {"nodes": []},
                "authoredPRs": {"nodes": authored_prs},
                "mergedPRs": {"nodes": []},
                "closedPRs": {"nodes": []},
            },
        },
    }


def install_repo_only_search_first_mocks(
    monkeypatch,
    *,
    repos: list[str],
    gh_user: str,
    authored_open: list[dict] | None = None,
    authored_merged: list[dict] | None = None,
    authored_closed: list[dict] | None = None,
    issue_open: list[dict] | None = None,
    issue_closed: list[dict] | None = None,
    review_open: list[dict] | None = None,
    hydrated_pull_requests: list[dict] | None = None,
    hydrated_issues: list[dict] | None = None,
    notifications: list[dict] | None = None,
) -> None:
    from agendum import gh

    owner_scope = list(dict.fromkeys(repo.split("/", 1)[0] for repo in repos))
    pull_requests_by_id = {
        item["id"]: item for item in (hydrated_pull_requests or []) if item.get("id")
    }
    issues_by_id = {
        item["id"]: item for item in (hydrated_issues or []) if item.get("id")
    }

    async def fake_get_gh_username() -> str:
        return gh_user

    async def fake_search_authored_prs(orgs: list[str], seen_user: str) -> tuple[list[dict], bool]:
        assert orgs == owner_scope
        assert seen_user == gh_user
        return list(authored_open or []), True

    async def fake_search_merged_authored_prs(
        orgs: list[str], seen_user: str,
    ) -> tuple[list[dict], bool]:
        assert orgs == owner_scope
        assert seen_user == gh_user
        return list(authored_merged or []), True

    async def fake_search_closed_authored_prs(
        orgs: list[str], seen_user: str,
    ) -> tuple[list[dict], bool]:
        assert orgs == owner_scope
        assert seen_user == gh_user
        return list(authored_closed or []), True

    async def fake_search_assigned_issues(orgs: list[str], seen_user: str) -> tuple[list[dict], bool]:
        assert orgs == owner_scope
        assert seen_user == gh_user
        return list(issue_open or []), True

    async def fake_search_closed_issues(orgs: list[str], seen_user: str) -> tuple[list[dict], bool]:
        assert orgs == owner_scope
        assert seen_user == gh_user
        return list(issue_closed or []), True

    async def fake_search_review_requested_prs(
        orgs: list[str], seen_user: str,
    ) -> tuple[list[dict], bool]:
        assert orgs == owner_scope
        assert seen_user == gh_user
        return list(review_open or []), True

    async def fake_hydrate_pull_requests(node_ids: list[str]) -> tuple[list[dict], bool]:
        return [pull_requests_by_id[node_id] for node_id in node_ids if node_id in pull_requests_by_id], True

    async def fake_hydrate_issues(node_ids: list[str]) -> tuple[list[dict], bool]:
        return [issues_by_id[node_id] for node_id in node_ids if node_id in issues_by_id], True

    async def fake_fetch_notifications(seen_user: str) -> list[dict]:
        assert seen_user == gh_user
        return list(notifications or [])

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
        {"id": 1, "gh_url": None, "status": "backlog", "title": "Manual", "source": "manual"},
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
    repo = "example-org/example-repo"
    url = "https://github.com/example-org/example-repo/pull/12"
    add_task(tmp_db, title="Old PR", source="pr_authored", status="open", gh_url=url)

    install_repo_only_search_first_mocks(
        monkeypatch,
        repos=[repo],
        gh_user="author",
        authored_closed=[
            {
                "id": "PR_12",
                "number": 12,
                "title": "Old PR",
                "url": url,
                "state": "CLOSED",
                "repository": {"nameWithOwner": repo},
                "labels": {"nodes": []},
            }
        ],
    )

    changes, attention, error = await run_sync(
        tmp_db,
        AgendumConfig(repos=[repo]),
    )

    task = find_task_by_gh_url(tmp_db, url)
    assert changes == 1
    assert attention is False
    assert error is None
    assert task is not None
    assert task["status"] == "closed"


@pytest.mark.asyncio
async def test_run_sync_revives_terminal_task(tmp_db: Path, monkeypatch) -> None:
    """A previously-closed PR that reappears as open should be updated, not re-inserted."""
    init_db(tmp_db)
    repo = "example-org/example-repo"
    url = "https://github.com/example-org/example-repo/pull/42"
    add_task(tmp_db, title="Old PR", source="pr_authored", status="closed", gh_url=url, gh_number=42)

    install_repo_only_search_first_mocks(
        monkeypatch,
        repos=[repo],
        gh_user="author",
        authored_open=[{"id": "PR_42", "repository": {"nameWithOwner": repo}}],
        hydrated_pull_requests=[
            {
                **make_authored_pr(gh_user="author", url=url),
                "id": "PR_42",
                "number": 42,
                "title": "Reopened PR",
                "repository": {"nameWithOwner": repo},
                "timelineItems": {"nodes": []},
            }
        ],
    )

    changes, attention, error = await run_sync(
        tmp_db,
        AgendumConfig(repos=[repo]),
    )

    task = find_task_by_gh_url(tmp_db, url)
    assert error is None
    assert changes == 1
    assert task is not None
    assert task["status"] == "open"
    assert task["title"] == "Reopened PR"


@pytest.mark.asyncio
async def test_run_sync_org_sync_uses_search_first_helpers(tmp_db: Path, monkeypatch) -> None:
    init_db(tmp_db)

    pr_hydrate_calls: list[tuple[str, ...]] = []
    issue_hydrate_calls: list[tuple[str, ...]] = []
    legacy_calls = {
        "discover_repos": 0,
        "fetch_repo_data": 0,
        "discover_review_prs": 0,
        "fetch_review_detail": 0,
    }

    authored_url = "https://github.com/org/authored-repo/pull/1"
    issue_url = "https://github.com/org/issue-repo/issues/2"
    review_url = "https://github.com/org/review-repo/pull/3"

    authored_pr = {
        **make_authored_pr(gh_user="dan", url=authored_url),
        "id": "PR_AUTH_1",
        "number": 1,
        "title": "Authored PR",
        "repository": {"nameWithOwner": "org/authored-repo"},
        "labels": {"nodes": [{"name": "bug"}]},
        "timelineItems": {"nodes": []},
    }
    review_pr = {
        **make_authored_pr(gh_user="alice", url=review_url),
        "id": "PR_REVIEW_1",
        "number": 3,
        "title": "Review PR",
        "repository": {"nameWithOwner": "org/review-repo"},
        "author": {"login": "alice", "name": "Alice Example"},
        "reviewRequests": {"totalCount": 1},
        "timelineItems": {"nodes": []},
    }
    issue = {
        "id": "ISSUE_1",
        "number": 2,
        "title": "Assigned issue",
        "url": issue_url,
        "state": "OPEN",
        "repository": {"nameWithOwner": "org/issue-repo"},
        "labels": {"nodes": [{"name": "help wanted"}]},
        "timelineItems": {"nodes": []},
    }

    async def fake_get_gh_username() -> str:
        return "dan"

    async def fake_search_authored_prs(orgs: list[str], gh_user: str) -> tuple[list[dict], bool]:
        assert orgs == ["org"]
        assert gh_user == "dan"
        return [{"id": "PR_AUTH_1", "repository": {"nameWithOwner": "org/authored-repo"}}], True

    async def fake_search_merged_authored_prs(
        orgs: list[str], gh_user: str,
    ) -> tuple[list[dict], bool]:
        assert orgs == ["org"]
        assert gh_user == "dan"
        return [], True

    async def fake_search_closed_authored_prs(
        orgs: list[str], gh_user: str,
    ) -> tuple[list[dict], bool]:
        assert orgs == ["org"]
        assert gh_user == "dan"
        return [], True

    async def fake_search_assigned_issues(orgs: list[str], gh_user: str) -> tuple[list[dict], bool]:
        assert orgs == ["org"]
        assert gh_user == "dan"
        return [{"id": "ISSUE_1", "repository": {"nameWithOwner": "org/issue-repo"}}], True

    async def fake_search_closed_issues(orgs: list[str], gh_user: str) -> tuple[list[dict], bool]:
        assert orgs == ["org"]
        assert gh_user == "dan"
        return [], True

    async def fake_search_review_requested_prs(
        orgs: list[str], gh_user: str,
    ) -> tuple[list[dict], bool]:
        assert orgs == ["org"]
        assert gh_user == "dan"
        return [{"id": "PR_REVIEW_1", "repository": {"nameWithOwner": "org/review-repo"}}], True

    async def fake_hydrate_pull_requests(node_ids: list[str]) -> tuple[list[dict], bool]:
        pr_hydrate_calls.append(tuple(node_ids))
        by_id = {
            "PR_AUTH_1": authored_pr,
            "PR_REVIEW_1": review_pr,
        }
        return [by_id[node_id] for node_id in node_ids], True

    async def fake_hydrate_issues(node_ids: list[str]) -> tuple[list[dict], bool]:
        issue_hydrate_calls.append(tuple(node_ids))
        return [issue for node_id in node_ids if node_id == "ISSUE_1"], True

    async def fake_discover_repos(orgs: list[str], gh_user: str) -> set[str]:
        legacy_calls["discover_repos"] += 1
        return {"org/legacy"}

    async def fake_fetch_repo_data(owner: str, name: str, gh_user: str) -> dict:
        legacy_calls["fetch_repo_data"] += 1
        return {}

    async def fake_discover_review_prs(orgs: list[str], gh_user: str) -> tuple[list[dict], bool]:
        legacy_calls["discover_review_prs"] += 1
        return [], True

    async def fake_fetch_review_detail(
        owner: str, name: str, number: int, gh_user: str,
    ) -> dict:
        legacy_calls["fetch_review_detail"] += 1
        return {}

    async def fake_fetch_notifications(gh_user: str) -> list[dict]:
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
    monkeypatch.setattr(gh, "fetch_review_detail", fake_fetch_review_detail)
    monkeypatch.setattr(gh, "fetch_notifications", fake_fetch_notifications)

    changes, attention, error = await run_sync(tmp_db, AgendumConfig(orgs=["org"]))

    assert changes == 3
    assert attention is True
    assert error is None
    assert {node_id for call in pr_hydrate_calls for node_id in call} == {
        "PR_AUTH_1",
        "PR_REVIEW_1",
    }
    assert issue_hydrate_calls == [("ISSUE_1",)]
    assert legacy_calls == {
        "discover_repos": 0,
        "fetch_repo_data": 0,
        "discover_review_prs": 0,
        "fetch_review_detail": 0,
    }

    authored_task = find_task_by_gh_url(tmp_db, authored_url)
    assert authored_task is not None
    assert authored_task["source"] == "pr_authored"
    assert authored_task["status"] == "open"
    assert authored_task["project"] == "authored-repo"

    issue_task = find_task_by_gh_url(tmp_db, issue_url)
    assert issue_task is not None
    assert issue_task["source"] == "issue"
    assert issue_task["status"] == "open"
    assert issue_task["project"] == "issue-repo"

    review_task = find_task_by_gh_url(tmp_db, review_url)
    assert review_task is not None
    assert review_task["source"] == "pr_review"
    assert review_task["status"] == "review requested"
    assert review_task["gh_author"] == "alice"
    assert review_task["gh_author_name"] == "Alice"
    assert review_task["project"] == "review-repo"


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
async def test_run_sync_keeps_workspace_gh_config_dir_when_global_changes(
    tmp_db: Path,
    monkeypatch,
) -> None:
    init_db(tmp_db)

    from agendum import gh

    workspace_gh_dir = tmp_db.parent / "gh"
    switched_gh_dir = tmp_db.parent / "switched" / "gh"
    observed_gh_dirs: list[Path | None] = []
    switched = False

    async def fake_run_gh(*args: str) -> str:
        nonlocal switched
        observed_gh_dirs.append(gh.get_gh_config_dir())
        if not switched:
            gh.set_gh_config_dir(switched_gh_dir)
            switched = True

        if args == ("api", "user", "--jq", ".login"):
            return "author\n"
        if args[:2] == ("api", "graphql"):
            return json.dumps({
                "data": {
                    "search": {
                        "pageInfo": {"hasNextPage": False, "endCursor": None},
                        "nodes": [],
                    },
                },
            })
        if args[:2] == ("api", "notifications"):
            return "[]"
        raise AssertionError(f"Unexpected gh call: {args}")

    monkeypatch.setattr(gh, "_run_gh", fake_run_gh)

    gh.set_gh_config_dir(workspace_gh_dir)
    try:
        changes, attention, error = await run_sync(
            tmp_db,
            AgendumConfig(repos=["example-org/example-repo"]),
        )
    finally:
        gh.set_gh_config_dir(None)

    assert changes == 0
    assert attention is False
    assert error is None
    assert observed_gh_dirs
    assert all(path == workspace_gh_dir for path in observed_gh_dirs)


@pytest.mark.asyncio
async def test_run_sync_creates_review_requested_pr_with_author_name(tmp_db: Path, monkeypatch) -> None:
    init_db(tmp_db)
    repo = "example-org/example-repo"
    url = "https://github.com/example-org/example-repo/pull/34"

    install_repo_only_search_first_mocks(
        monkeypatch,
        repos=[repo],
        gh_user="reviewer",
        review_open=[{"id": "PR_34", "repository": {"nameWithOwner": repo}}],
        hydrated_pull_requests=[
            {
                **make_authored_pr(gh_user="author", url=url),
                "id": "PR_34",
                "number": 34,
                "title": "Fix telemetry attributes",
                "repository": {"nameWithOwner": repo},
                "author": {"login": "author", "name": "Author Person"},
                "reviewRequests": {"totalCount": 1},
                "timelineItems": {"nodes": []},
            }
        ],
    )

    changes, attention, error = await run_sync(
        tmp_db,
        AgendumConfig(repos=[repo]),
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


@pytest.mark.asyncio
async def test_run_sync_flips_reviewed_back_to_re_review_when_re_requested(tmp_db: Path, monkeypatch) -> None:
    """After user reviews, if the author re-requests them (even without new commits),
    the status should flip from 'reviewed' back to 're-review requested'."""
    init_db(tmp_db)
    repo = "example-org/example-repo"
    url = "https://github.com/example-org/example-repo/pull/77"

    add_task(
        tmp_db,
        title="Fix something",
        source="pr_review",
        status="reviewed",
        project="example-repo",
        gh_repo="example-org/example-repo",
        gh_url=url,
        gh_number=77,
        gh_author="author",
        gh_author_name="Author",
        tags='["review"]',
    )

    install_repo_only_search_first_mocks(
        monkeypatch,
        repos=[repo],
        gh_user="reviewer",
        review_open=[{"id": "PR_77", "repository": {"nameWithOwner": repo}}],
        hydrated_pull_requests=[
            {
                **make_authored_pr(
                    gh_user="author",
                    url=url,
                    last_commit_at="2026-04-05T09:00:00Z",
                    reviews=[
                        {
                            "author": {"login": "reviewer"},
                            "submittedAt": "2026-04-06T10:00:00Z",
                            "state": "CHANGES_REQUESTED",
                        }
                    ],
                ),
                "id": "PR_77",
                "number": 77,
                "title": "Fix something",
                "repository": {"nameWithOwner": repo},
                "author": {"login": "author", "name": "Author Person"},
                "reviewRequests": {"totalCount": 1},
                "timelineItems": {
                    "nodes": [
                        {
                            "createdAt": "2026-04-07T11:00:00Z",
                            "requestedReviewer": {"login": "reviewer"},
                        }
                    ]
                },
            }
        ],
    )

    changes, attention, error = await run_sync(
        tmp_db,
        AgendumConfig(repos=[repo]),
    )

    tasks = get_active_tasks(tmp_db)
    assert error is None
    assert changes >= 1
    assert len(tasks) == 1
    assert tasks[0]["status"] == "re-review requested"
    assert attention is True


@pytest.mark.asyncio
async def test_run_sync_resurrects_done_review_task_when_re_requested(
    tmp_db: Path, monkeypatch,
) -> None:
    """A review task that was auto-closed to 'done' must be resurrected and
    flagged for attention when the user is re-requested as a reviewer."""
    init_db(tmp_db)
    repo = "example-org/example-repo"
    url = "https://github.com/example-org/example-repo/pull/88"

    add_task(
        tmp_db,
        title="Fix something",
        source="pr_review",
        status="done",
        project="example-repo",
        gh_repo="example-org/example-repo",
        gh_url=url,
        gh_number=88,
        gh_author="author",
        gh_author_name="Author",
        tags='["review"]',
    )

    install_repo_only_search_first_mocks(
        monkeypatch,
        repos=[repo],
        gh_user="reviewer",
        review_open=[{"id": "PR_88", "repository": {"nameWithOwner": repo}}],
        hydrated_pull_requests=[
            {
                **make_authored_pr(
                    gh_user="author",
                    url=url,
                    last_commit_at="2026-04-05T09:00:00Z",
                    reviews=[
                        {
                            "author": {"login": "reviewer"},
                            "submittedAt": "2026-04-06T10:00:00Z",
                            "state": "COMMENTED",
                        }
                    ],
                ),
                "id": "PR_88",
                "number": 88,
                "title": "Fix something",
                "repository": {"nameWithOwner": repo},
                "author": {"login": "author", "name": "Author Person"},
                "reviewRequests": {"totalCount": 1},
                "timelineItems": {
                    "nodes": [
                        {
                            "createdAt": "2026-04-07T11:00:00Z",
                            "requestedReviewer": {"login": "reviewer"},
                        }
                    ]
                },
            }
        ],
    )

    changes, attention, error = await run_sync(
        tmp_db,
        AgendumConfig(repos=[repo]),
    )

    assert error is None
    assert changes >= 1
    assert attention is True

    active = get_active_tasks(tmp_db)
    assert len(active) == 1
    task = active[0]
    assert task["gh_url"] == url
    assert task["status"] == "re-review requested"
    assert task["seen"] == 0


@pytest.mark.asyncio
async def test_run_sync_resurrects_done_review_task_without_prior_review(
    tmp_db: Path, monkeypatch,
) -> None:
    """If a done review task is re-requested but the user never actually
    reviewed (e.g. task was closed for a different reason), resurrect it
    as 'review requested' and flag attention."""
    init_db(tmp_db)
    repo = "example-org/example-repo"
    url = "https://github.com/example-org/example-repo/pull/99"

    add_task(
        tmp_db,
        title="Fresh ask",
        source="pr_review",
        status="done",
        project="example-repo",
        gh_repo="example-org/example-repo",
        gh_url=url,
        gh_number=99,
        gh_author="author",
        gh_author_name="Author",
        tags='["review"]',
    )

    install_repo_only_search_first_mocks(
        monkeypatch,
        repos=[repo],
        gh_user="reviewer",
        review_open=[{"id": "PR_99", "repository": {"nameWithOwner": repo}}],
        hydrated_pull_requests=[
            {
                **make_authored_pr(
                    gh_user="author",
                    url=url,
                    last_commit_at="2026-04-05T09:00:00Z",
                    reviews=[],
                ),
                "id": "PR_99",
                "number": 99,
                "title": "Fresh ask",
                "repository": {"nameWithOwner": repo},
                "author": {"login": "author", "name": "Author Person"},
                "reviewRequests": {"totalCount": 1},
                "timelineItems": {"nodes": []},
            }
        ],
    )

    changes, attention, error = await run_sync(
        tmp_db,
        AgendumConfig(repos=[repo]),
    )

    assert error is None
    assert changes >= 1
    assert attention is True

    active = get_active_tasks(tmp_db)
    assert len(active) == 1
    assert active[0]["status"] == "review requested"
    assert active[0]["seen"] == 0


@pytest.mark.asyncio
async def test_run_sync_sets_authored_pr_to_review_received_for_non_blocking_feedback(tmp_db: Path, monkeypatch) -> None:
    init_db(tmp_db)
    repo = "example-org/example-repo"
    url = "https://github.com/example-org/example-repo/pull/12"
    add_task(tmp_db, title="Improve sync status handling", source="pr_authored", status="open", gh_url=url)

    install_repo_only_search_first_mocks(
        monkeypatch,
        repos=[repo],
        gh_user="author",
        authored_open=[{"id": "PR_12", "repository": {"nameWithOwner": repo}}],
        hydrated_pull_requests=[
            {
                **make_authored_pr(
                    gh_user="author",
                    url=url,
                    reviews=[
                        make_review(
                            author="reviewer",
                            submitted_at="2026-04-10T12:00:00Z",
                            state="COMMENTED",
                        ),
                    ],
                    review_threads=[
                        make_thread(
                            is_resolved=False,
                            comments=[
                                make_thread_comment(
                                    author="reviewer",
                                    created_at="2026-04-10T12:01:00Z",
                                    review_id="review-1",
                                ),
                            ],
                        ),
                    ],
                ),
                "id": "PR_12",
                "repository": {"nameWithOwner": repo},
                "timelineItems": {"nodes": []},
            }
        ],
    )

    changes, attention, error = await run_sync(
        tmp_db,
        AgendumConfig(repos=[repo]),
    )

    task = find_task_by_gh_url(tmp_db, url)
    assert changes == 1
    assert error is None
    assert task is not None
    assert task["status"] == "review received"
    assert task["seen"] == 0


@pytest.mark.asyncio
async def test_run_sync_keeps_changes_requested_for_blocking_review(tmp_db: Path, monkeypatch) -> None:
    init_db(tmp_db)
    repo = "example-org/example-repo"
    url = "https://github.com/example-org/example-repo/pull/12"
    add_task(tmp_db, title="Improve sync status handling", source="pr_authored", status="open", gh_url=url)

    install_repo_only_search_first_mocks(
        monkeypatch,
        repos=[repo],
        gh_user="author",
        authored_open=[{"id": "PR_12", "repository": {"nameWithOwner": repo}}],
        hydrated_pull_requests=[
            {
                **make_authored_pr(
                    gh_user="author",
                    url=url,
                    review_decision="CHANGES_REQUESTED",
                    reviews=[
                        make_review(
                            author="reviewer",
                            submitted_at="2026-04-10T12:00:00Z",
                            state="CHANGES_REQUESTED",
                        ),
                    ],
                    review_threads=[
                        make_thread(
                            is_resolved=False,
                            comments=[
                                make_thread_comment(
                                    author="reviewer",
                                    created_at="2026-04-10T12:01:00Z",
                                    review_id="review-1",
                                ),
                            ],
                        ),
                    ],
                ),
                "id": "PR_12",
                "repository": {"nameWithOwner": repo},
                "timelineItems": {"nodes": []},
            }
        ],
    )

    changes, attention, error = await run_sync(
        tmp_db,
        AgendumConfig(repos=[repo]),
    )

    task = find_task_by_gh_url(tmp_db, url)
    assert changes == 1
    assert error is None
    assert task is not None
    assert task["status"] == "changes requested"


@pytest.mark.asyncio
async def test_run_sync_clears_review_received_after_author_reply(tmp_db: Path, monkeypatch) -> None:
    init_db(tmp_db)
    repo = "example-org/example-repo"
    url = "https://github.com/example-org/example-repo/pull/12"
    add_task(tmp_db, title="Improve sync status handling", source="pr_authored", status="review received", gh_url=url)

    install_repo_only_search_first_mocks(
        monkeypatch,
        repos=[repo],
        gh_user="author",
        authored_open=[{"id": "PR_12", "repository": {"nameWithOwner": repo}}],
        hydrated_pull_requests=[
            {
                **make_authored_pr(
                    gh_user="author",
                    url=url,
                    reviews=[
                        make_review(
                            author="reviewer",
                            submitted_at="2026-04-10T12:00:00Z",
                            state="COMMENTED",
                        ),
                    ],
                    review_threads=[
                        make_thread(
                            is_resolved=False,
                            comments=[
                                make_thread_comment(
                                    author="reviewer",
                                    created_at="2026-04-10T12:01:00Z",
                                    review_id="review-1",
                                ),
                                make_thread_comment(author="author", created_at="2026-04-10T12:05:00Z"),
                            ],
                        ),
                    ],
                ),
                "id": "PR_12",
                "repository": {"nameWithOwner": repo},
                "timelineItems": {"nodes": []},
            }
        ],
    )

    changes, attention, error = await run_sync(
        tmp_db,
        AgendumConfig(repos=[repo]),
    )

    task = find_task_by_gh_url(tmp_db, url)
    assert changes == 1
    assert error is None
    assert task is not None
    assert task["status"] == "open"


@pytest.mark.asyncio
async def test_run_sync_keeps_review_received_with_sibling_thread_reply(tmp_db: Path, monkeypatch) -> None:
    init_db(tmp_db)
    repo = "example-org/example-repo"
    url = "https://github.com/example-org/example-repo/pull/12"
    add_task(tmp_db, title="Improve sync status handling", source="pr_authored", status="review received", gh_url=url)

    install_repo_only_search_first_mocks(
        monkeypatch,
        repos=[repo],
        gh_user="author",
        authored_open=[{"id": "PR_12", "repository": {"nameWithOwner": repo}}],
        hydrated_pull_requests=[
            {
                **make_authored_pr(
                    gh_user="author",
                    url=url,
                    reviews=[
                        make_review(
                            author="reviewer",
                            submitted_at="2026-04-10T12:00:00Z",
                            state="COMMENTED",
                        ),
                    ],
                    review_threads=[
                        make_thread(
                            is_resolved=False,
                            comments=[
                                make_thread_comment(
                                    author="reviewer",
                                    created_at="2026-04-10T12:01:00Z",
                                    review_id="review-1",
                                ),
                                make_thread_comment(author="author", created_at="2026-04-10T12:05:00Z"),
                            ],
                        ),
                        make_thread(
                            is_resolved=False,
                            comments=[
                                make_thread_comment(
                                    author="reviewer",
                                    created_at="2026-04-10T12:02:00Z",
                                    review_id="review-1",
                                ),
                            ],
                        ),
                    ],
                ),
                "id": "PR_12",
                "repository": {"nameWithOwner": repo},
                "timelineItems": {"nodes": []},
            }
        ],
    )

    changes, attention, error = await run_sync(
        tmp_db,
        AgendumConfig(repos=[repo]),
    )

    task = find_task_by_gh_url(tmp_db, url)
    assert changes == 1
    assert attention is False
    assert error is None
    assert task is not None
    assert task["status"] == "review received"


@pytest.mark.asyncio
async def test_run_sync_keeps_review_received_when_older_review_is_unresolved(tmp_db: Path, monkeypatch) -> None:
    init_db(tmp_db)
    repo = "example-org/example-repo"
    url = "https://github.com/example-org/example-repo/pull/12"
    add_task(tmp_db, title="Improve sync status handling", source="pr_authored", status="review received", gh_url=url)

    install_repo_only_search_first_mocks(
        monkeypatch,
        repos=[repo],
        gh_user="author",
        authored_open=[{"id": "PR_12", "repository": {"nameWithOwner": repo}}],
        hydrated_pull_requests=[
            {
                **make_authored_pr(
                    gh_user="author",
                    url=url,
                    reviews=[
                        make_review(
                            author="reviewer",
                            submitted_at="2026-04-10T11:00:00Z",
                            state="COMMENTED",
                            review_id="review-old",
                        ),
                        make_review(
                            author="reviewer",
                            submitted_at="2026-04-10T12:00:00Z",
                            state="COMMENTED",
                            review_id="review-new",
                        ),
                    ],
                    review_threads=[
                        make_thread(
                            is_resolved=False,
                            comments=[
                                make_thread_comment(
                                    author="reviewer",
                                    created_at="2026-04-10T11:01:00Z",
                                    review_id="review-old",
                                ),
                            ],
                        ),
                        make_thread(
                            is_resolved=True,
                            comments=[
                                make_thread_comment(
                                    author="reviewer",
                                    created_at="2026-04-10T12:01:00Z",
                                    review_id="review-new",
                                ),
                            ],
                        ),
                    ],
                ),
                "id": "PR_12",
                "repository": {"nameWithOwner": repo},
                "timelineItems": {"nodes": []},
            }
        ],
    )

    changes, attention, error = await run_sync(
        tmp_db,
        AgendumConfig(repos=[repo]),
    )

    task = find_task_by_gh_url(tmp_db, url)
    assert changes == 1
    assert attention is False
    assert error is None
    assert task is not None
    assert task["status"] == "review received"


@pytest.mark.asyncio
async def test_run_sync_clears_review_received_after_all_threads_resolved(tmp_db: Path, monkeypatch) -> None:
    init_db(tmp_db)
    repo = "example-org/example-repo"
    url = "https://github.com/example-org/example-repo/pull/12"
    add_task(tmp_db, title="Improve sync status handling", source="pr_authored", status="review received", gh_url=url)

    install_repo_only_search_first_mocks(
        monkeypatch,
        repos=[repo],
        gh_user="author",
        authored_open=[{"id": "PR_12", "repository": {"nameWithOwner": repo}}],
        hydrated_pull_requests=[
            {
                **make_authored_pr(
                    gh_user="author",
                    url=url,
                    reviews=[
                        make_review(
                            author="reviewer",
                            submitted_at="2026-04-10T12:00:00Z",
                            state="COMMENTED",
                        ),
                    ],
                    review_threads=[
                        make_thread(
                            is_resolved=True,
                            comments=[
                                make_thread_comment(
                                    author="reviewer",
                                    created_at="2026-04-10T12:01:00Z",
                                    review_id="review-1",
                                ),
                            ],
                        ),
                    ],
                ),
                "id": "PR_12",
                "repository": {"nameWithOwner": repo},
                "timelineItems": {"nodes": []},
            }
        ],
    )

    changes, attention, error = await run_sync(
        tmp_db,
        AgendumConfig(repos=[repo]),
    )

    task = find_task_by_gh_url(tmp_db, url)
    assert changes == 1
    assert error is None
    assert task is not None
    assert task["status"] == "open"


@pytest.mark.asyncio
async def test_run_sync_keeps_review_received_after_push_when_feedback_threads_exist(tmp_db: Path, monkeypatch) -> None:
    init_db(tmp_db)
    repo = "example-org/example-repo"
    url = "https://github.com/example-org/example-repo/pull/12"
    add_task(tmp_db, title="Improve sync status handling", source="pr_authored", status="review received", gh_url=url)

    install_repo_only_search_first_mocks(
        monkeypatch,
        repos=[repo],
        gh_user="author",
        authored_open=[{"id": "PR_12", "repository": {"nameWithOwner": repo}}],
        hydrated_pull_requests=[
            {
                **make_authored_pr(
                    gh_user="author",
                    url=url,
                    last_commit_at="2026-04-10T12:05:00Z",
                    reviews=[
                        make_review(
                            author="reviewer",
                            submitted_at="2026-04-10T12:00:00Z",
                            state="COMMENTED",
                        ),
                    ],
                    review_threads=[
                        make_thread(
                            is_resolved=False,
                            comments=[
                                make_thread_comment(
                                    author="reviewer",
                                    created_at="2026-04-10T12:01:00Z",
                                    review_id="review-1",
                                ),
                            ],
                        ),
                    ],
                ),
                "id": "PR_12",
                "repository": {"nameWithOwner": repo},
                "timelineItems": {"nodes": []},
            }
        ],
    )

    changes, attention, error = await run_sync(
        tmp_db,
        AgendumConfig(repos=[repo]),
    )

    task = find_task_by_gh_url(tmp_db, url)
    assert changes == 1
    assert error is None
    assert task is not None
    assert task["status"] == "review received"


@pytest.mark.asyncio
async def test_run_sync_clears_review_received_after_push_without_feedback_threads(tmp_db: Path, monkeypatch) -> None:
    init_db(tmp_db)
    repo = "example-org/example-repo"
    url = "https://github.com/example-org/example-repo/pull/12"
    add_task(tmp_db, title="Improve sync status handling", source="pr_authored", status="review received", gh_url=url)

    install_repo_only_search_first_mocks(
        monkeypatch,
        repos=[repo],
        gh_user="author",
        authored_open=[{"id": "PR_12", "repository": {"nameWithOwner": repo}}],
        hydrated_pull_requests=[
            {
                **make_authored_pr(
                    gh_user="author",
                    url=url,
                    last_commit_at="2026-04-10T12:05:00Z",
                    reviews=[
                        make_review(
                            author="reviewer",
                            submitted_at="2026-04-10T12:00:00Z",
                            state="COMMENTED",
                        ),
                    ],
                    review_threads=[],
                ),
                "id": "PR_12",
                "repository": {"nameWithOwner": repo},
                "timelineItems": {"nodes": []},
            }
        ],
    )

    changes, attention, error = await run_sync(
        tmp_db,
        AgendumConfig(repos=[repo]),
    )

    task = find_task_by_gh_url(tmp_db, url)
    assert changes == 1
    assert error is None
    assert task is not None
    assert task["status"] == "open"

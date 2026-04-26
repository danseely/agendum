"""Edge-case tests for the sync engine."""

from pathlib import Path

import pytest

from agendum.config import AgendumConfig
from agendum.db import add_task, find_task_by_gh_url, get_active_tasks, init_db, update_task
from agendum.syncer import CloseSuppression, diff_tasks, run_sync
from tests.syncer_test_helpers import (
    install_repo_planner_mocks,
    make_open_authored_hydrated_pr,
    make_open_issue_hydrated,
)


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


def test_diff_respects_lane_wide_close_suppression() -> None:
    existing = [
        {
            "id": 1,
            "source": "pr_authored",
            "gh_url": "https://github.com/org/repo/pull/1",
            "status": "open",
            "title": "PR",
        },
        {
            "id": 2,
            "source": "issue",
            "gh_url": "https://github.com/org/repo/issues/2",
            "status": "open",
            "title": "Issue",
        },
    ]

    result = diff_tasks(
        existing,
        incoming=[],
        close_suppression=CloseSuppression(authored=True),
    )

    assert [task["id"] for task in result.to_close] == [2]


def test_diff_respects_row_level_close_suppression() -> None:
    existing = [
        {
            "id": 1,
            "source": "pr_review",
            "gh_url": "https://github.com/org/repo/pull/1",
            "status": "review requested",
            "title": "Review PR",
        },
        {
            "id": 2,
            "source": "pr_review",
            "gh_url": "https://github.com/org/repo/pull/2",
            "status": "review requested",
            "title": "Review PR 2",
        },
    ]

    result = diff_tasks(
        existing,
        incoming=[],
        close_suppression=CloseSuppression(
            review_urls=frozenset({"https://github.com/org/repo/pull/2"})
        ),
    )

    assert [task["id"] for task in result.to_close] == [1]


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

    install_repo_planner_mocks(
        monkeypatch,
        gh_user="author",
        authored_prs=[
            make_open_authored_hydrated_pr(
                gh_user="author",
                url=url,
                repo_full="org/repo",
                gh_node_id="PR_node_5",
                number=5,
                title="Existing PR",
            )
        ],
        notifications=[
            {
                "reason": "comment",
                "subject": {
                    "url": "https://api.github.com/repos/org/repo/pulls/5",
                },
            }
        ],
        expected_repos=["org/repo"],
    )

    changes, attention, error = await run_sync(
        tmp_db, AgendumConfig(repos=["org/repo"]),
    )

    task = find_task_by_gh_url(tmp_db, url)
    assert task["seen"] == 0
    assert attention is True
    assert error is None


async def test_run_sync_excludes_repos(tmp_db: Path, monkeypatch) -> None:
    init_db(tmp_db)

    hydrated_repos: list[str] = []

    async def fake_get_gh_username() -> str:
        return "author"

    async def fake_search_open_authored_prs_with_completeness(orgs, gh_user) -> tuple[list, bool]:
        return [
            {
                "gh_node_id": "PR_keep",
                "number": 1,
                "title": "Keep me",
                "url": "https://github.com/org/keep/pull/1",
                "repository": {"nameWithOwner": "org/keep"},
            },
            {
                "gh_node_id": "PR_exclude",
                "number": 2,
                "title": "Skip me",
                "url": "https://github.com/org/exclude-me/pull/2",
                "repository": {"nameWithOwner": "org/exclude-me"},
            },
        ], True

    async def fake_empty_search(orgs, gh_user) -> tuple[list, bool]:
        return [], True

    async def fake_hydrate_open_authored_prs_with_completeness(prs, *, batch_size=50) -> tuple[list, bool]:
        hydrated_repos.extend(
            pr["repository"]["nameWithOwner"]
            for pr in prs
        )
        return [], True

    async def fake_empty_hydrate(items, *, batch_size=50) -> tuple[list, bool]:
        return [], True

    async def fake_fetch_notifications(gh_user) -> list:
        return []

    from agendum import gh
    monkeypatch.setattr(gh, "get_gh_username", fake_get_gh_username)
    monkeypatch.setattr(
        gh,
        "search_open_authored_prs_with_completeness",
        fake_search_open_authored_prs_with_completeness,
    )
    monkeypatch.setattr(gh, "search_open_assigned_issues_with_completeness", fake_empty_search)
    monkeypatch.setattr(gh, "search_open_review_requested_prs_with_completeness", fake_empty_search)
    monkeypatch.setattr(
        gh,
        "hydrate_open_authored_prs_with_completeness",
        fake_hydrate_open_authored_prs_with_completeness,
    )
    monkeypatch.setattr(gh, "hydrate_open_issues_with_completeness", fake_empty_hydrate)
    monkeypatch.setattr(gh, "hydrate_open_review_prs_with_completeness", fake_empty_hydrate)
    monkeypatch.setattr(gh, "fetch_notifications", fake_fetch_notifications)

    await run_sync(
        tmp_db,
        AgendumConfig(orgs=["org"], exclude_repos=["org/exclude-me"]),
    )

    assert hydrated_repos == ["org/keep"]


async def test_run_sync_attention_on_status_change_to_approved(
    tmp_db: Path, monkeypatch,
) -> None:
    init_db(tmp_db)
    url = "https://github.com/org/repo/pull/10"
    add_task(tmp_db, title="My PR", source="pr_authored", status="awaiting review",
             gh_url=url)

    install_repo_planner_mocks(
        monkeypatch,
        gh_user="author",
        authored_prs=[
            make_open_authored_hydrated_pr(
                gh_user="author",
                url=url,
                repo_full="org/repo",
                gh_node_id="PR_node_10",
                number=10,
                title="My PR",
                review_decision="APPROVED",
                review_requests_total=1,
            )
        ],
        expected_repos=["org/repo"],
    )

    changes, attention, error = await run_sync(
        tmp_db, AgendumConfig(repos=["org/repo"]),
    )

    assert changes == 1
    assert attention is True
    task = find_task_by_gh_url(tmp_db, url)
    assert task["status"] == "approved"


async def test_run_sync_org_path_backfills_node_id(
    tmp_db: Path,
    monkeypatch,
) -> None:
    init_db(tmp_db)
    url = "https://github.com/org/repo/pull/15"
    add_task(
        tmp_db,
        title="Needs approval",
        source="pr_authored",
        status="awaiting review",
        gh_url=url,
        gh_repo="org/repo",
        gh_number=15,
    )

    async def fake_get_gh_username() -> str:
        return "author"

    async def fake_search_open_authored_prs_with_completeness(orgs, gh_user) -> tuple[list, bool]:
        return [
            {
                "gh_node_id": "PR_node_15",
                "number": 15,
                "title": "Needs approval",
                "url": url,
                "repository": {"nameWithOwner": "org/repo"},
            }
        ], True

    async def fake_empty_search(orgs, gh_user) -> tuple[list, bool]:
        return [], True

    async def fake_hydrate_open_authored_prs_with_completeness(prs, *, batch_size=50) -> tuple[list, bool]:
        return [
            {
                "gh_node_id": "PR_node_15",
                "number": 15,
                "title": "Needs approval",
                "url": url,
                "repository": {"nameWithOwner": "org/repo"},
                "state": "OPEN",
                "isDraft": False,
                "reviewDecision": "APPROVED",
                "author": {"login": "author"},
                "reviewRequests": {"totalCount": 0},
                "commits": {"nodes": []},
                "reviews": {"nodes": []},
                "reviewThreads": {"nodes": []},
            }
        ], True

    async def fake_empty_hydrate(items, *, batch_size=50) -> tuple[list, bool]:
        return [], True

    async def fake_empty_verify_authored(prs, *, batch_size=50) -> tuple[list, bool]:
        return [], True

    async def fake_empty_verify_issues(issues, *, gh_user, batch_size=50) -> tuple[list, bool]:
        return [], True

    async def fake_empty_verify_review(prs, *, gh_user, batch_size=50) -> tuple[list, bool]:
        return [], True

    async def fake_fetch_notifications(gh_user) -> list:
        return []

    from agendum import gh

    monkeypatch.setattr(gh, "get_gh_username", fake_get_gh_username)
    monkeypatch.setattr(
        gh,
        "search_open_authored_prs_with_completeness",
        fake_search_open_authored_prs_with_completeness,
    )
    monkeypatch.setattr(gh, "search_open_assigned_issues_with_completeness", fake_empty_search)
    monkeypatch.setattr(gh, "search_open_review_requested_prs_with_completeness", fake_empty_search)
    monkeypatch.setattr(
        gh,
        "hydrate_open_authored_prs_with_completeness",
        fake_hydrate_open_authored_prs_with_completeness,
    )
    monkeypatch.setattr(gh, "hydrate_open_issues_with_completeness", fake_empty_hydrate)
    monkeypatch.setattr(gh, "hydrate_open_review_prs_with_completeness", fake_empty_hydrate)
    monkeypatch.setattr(gh, "verify_missing_authored_prs_with_completeness", fake_empty_verify_authored)
    monkeypatch.setattr(gh, "verify_missing_issues_with_completeness", fake_empty_verify_issues)
    monkeypatch.setattr(gh, "verify_missing_review_prs_with_completeness", fake_empty_verify_review)
    monkeypatch.setattr(gh, "fetch_notifications", fake_fetch_notifications)

    changes, attention, error = await run_sync(tmp_db, AgendumConfig(orgs=["org"]))

    task = find_task_by_gh_url(tmp_db, url)
    assert changes == 1
    assert attention is True
    assert error is None
    assert task is not None
    assert task["status"] == "approved"
    assert task["gh_node_id"] == "PR_node_15"


async def test_run_sync_org_path_preserves_out_of_scope_authored_tasks(
    tmp_db: Path,
    monkeypatch,
) -> None:
    init_db(tmp_db)
    url = "https://github.com/other-org/other-repo/pull/20"
    add_task(
        tmp_db,
        title="Out of scope authored PR",
        source="pr_authored",
        status="open",
        gh_url=url,
        gh_repo="other-org/other-repo",
        gh_number=20,
    )

    async def fake_get_gh_username() -> str:
        return "author"

    async def fake_empty_search(orgs, gh_user) -> tuple[list, bool]:
        return [], True

    async def fake_empty_hydrate(items, *, batch_size=50) -> tuple[list, bool]:
        return [], True

    async def fake_verify_missing_authored(prs, *, batch_size=50) -> tuple[list, bool]:
        assert prs == []
        return [], True

    async def fake_empty_verify_issues(issues, *, gh_user, batch_size=50) -> tuple[list, bool]:
        return [], True

    async def fake_empty_verify_review(prs, *, gh_user, batch_size=50) -> tuple[list, bool]:
        return [], True

    async def fake_fetch_notifications(gh_user) -> list:
        return []

    from agendum import gh

    monkeypatch.setattr(gh, "get_gh_username", fake_get_gh_username)
    monkeypatch.setattr(gh, "search_open_authored_prs_with_completeness", fake_empty_search)
    monkeypatch.setattr(gh, "search_open_assigned_issues_with_completeness", fake_empty_search)
    monkeypatch.setattr(gh, "search_open_review_requested_prs_with_completeness", fake_empty_search)
    monkeypatch.setattr(gh, "hydrate_open_authored_prs_with_completeness", fake_empty_hydrate)
    monkeypatch.setattr(gh, "hydrate_open_issues_with_completeness", fake_empty_hydrate)
    monkeypatch.setattr(gh, "hydrate_open_review_prs_with_completeness", fake_empty_hydrate)
    monkeypatch.setattr(gh, "verify_missing_authored_prs_with_completeness", fake_verify_missing_authored)
    monkeypatch.setattr(gh, "verify_missing_issues_with_completeness", fake_empty_verify_issues)
    monkeypatch.setattr(gh, "verify_missing_review_prs_with_completeness", fake_empty_verify_review)
    monkeypatch.setattr(gh, "fetch_notifications", fake_fetch_notifications)

    changes, attention, error = await run_sync(tmp_db, AgendumConfig(orgs=["org"]))

    task = find_task_by_gh_url(tmp_db, url)
    assert changes == 0
    assert attention is False
    assert error is None
    assert task is not None
    assert task["status"] == "open"


async def test_run_sync_org_path_verifies_tracked_authored_in_dormant_in_scope_repo(
    tmp_db: Path,
    monkeypatch,
) -> None:
    """Tracked authored PRs in dormant in-scope repos must still be verified.

    The repo is part of a configured org but currently has zero open
    discovered items, so org-wide search returns nothing for its lane. The
    tracked task must still flow into terminal verification or it stays
    open forever.
    """
    init_db(tmp_db)
    url = "https://github.com/org/dormant-repo/pull/77"
    add_task(
        tmp_db,
        title="Dormant authored PR",
        source="pr_authored",
        status="open",
        gh_url=url,
        gh_repo="org/dormant-repo",
        gh_node_id="PR_node_77",
        gh_number=77,
    )

    captured_authored_verify_calls: list[list[dict]] = []

    async def fake_get_gh_username() -> str:
        return "author"

    async def fake_empty_search(orgs, gh_user) -> tuple[list, bool]:
        return [], True

    async def fake_empty_hydrate(items, *, batch_size=50) -> tuple[list, bool]:
        return [], True

    async def fake_verify_missing_authored(prs, *, batch_size=50) -> tuple[list, bool]:
        captured_authored_verify_calls.append(prs)
        return [
            {
                "gh_node_id": "PR_node_77",
                "gh_url": url,
                "state": "MERGED",
                "is_assigned_to_user": None,
                "is_review_requested": None,
            }
        ], True

    async def fake_empty_verify_issues(issues, *, gh_user, batch_size=50) -> tuple[list, bool]:
        return [], True

    async def fake_empty_verify_review(prs, *, gh_user, batch_size=50) -> tuple[list, bool]:
        return [], True

    async def fake_fetch_notifications(gh_user) -> list:
        return []

    from agendum import gh

    monkeypatch.setattr(gh, "get_gh_username", fake_get_gh_username)
    monkeypatch.setattr(gh, "search_open_authored_prs_with_completeness", fake_empty_search)
    monkeypatch.setattr(gh, "search_open_assigned_issues_with_completeness", fake_empty_search)
    monkeypatch.setattr(gh, "search_open_review_requested_prs_with_completeness", fake_empty_search)
    monkeypatch.setattr(gh, "hydrate_open_authored_prs_with_completeness", fake_empty_hydrate)
    monkeypatch.setattr(gh, "hydrate_open_issues_with_completeness", fake_empty_hydrate)
    monkeypatch.setattr(gh, "hydrate_open_review_prs_with_completeness", fake_empty_hydrate)
    monkeypatch.setattr(gh, "verify_missing_authored_prs_with_completeness", fake_verify_missing_authored)
    monkeypatch.setattr(gh, "verify_missing_issues_with_completeness", fake_empty_verify_issues)
    monkeypatch.setattr(gh, "verify_missing_review_prs_with_completeness", fake_empty_verify_review)
    monkeypatch.setattr(gh, "fetch_notifications", fake_fetch_notifications)

    changes, attention, error = await run_sync(tmp_db, AgendumConfig(orgs=["org"]))

    assert error is None
    assert changes == 1
    assert attention is False
    assert len(captured_authored_verify_calls) == 1
    assert [item["gh_url"] for item in captured_authored_verify_calls[0]] == [url]
    task = find_task_by_gh_url(tmp_db, url)
    assert task is not None
    assert task["status"] == "merged"


async def test_run_sync_authored_pr_preserves_label_tags(
    tmp_db: Path,
    monkeypatch,
) -> None:
    init_db(tmp_db)
    url = "https://github.com/org/repo/pull/30"
    add_task(
        tmp_db,
        title="Tagged authored PR",
        source="pr_authored",
        status="open",
        gh_url=url,
        gh_repo="org/repo",
        gh_number=30,
    )

    install_repo_planner_mocks(
        monkeypatch,
        gh_user="author",
        authored_prs=[
            make_open_authored_hydrated_pr(
                gh_user="author",
                url=url,
                repo_full="org/repo",
                gh_node_id="PR_node_30",
                number=30,
                title="Tagged authored PR",
                labels=["bug", "ops"],
            )
        ],
        expected_repos=["org/repo"],
    )

    changes, attention, error = await run_sync(tmp_db, AgendumConfig(repos=["org/repo"]))

    task = find_task_by_gh_url(tmp_db, url)
    assert changes == 1
    assert attention is False
    assert error is None
    assert task is not None
    assert task["tags"] == '["bug", "ops"]'


async def test_run_sync_issue_preserves_label_tags(
    tmp_db: Path,
    monkeypatch,
) -> None:
    init_db(tmp_db)
    url = "https://github.com/org/repo/issues/31"
    add_task(
        tmp_db,
        title="Tagged issue",
        source="issue",
        status="open",
        gh_url=url,
        gh_repo="org/repo",
        gh_number=31,
    )

    install_repo_planner_mocks(
        monkeypatch,
        gh_user="author",
        issues=[
            make_open_issue_hydrated(
                url=url,
                repo_full="org/repo",
                gh_node_id="I_node_31",
                number=31,
                title="Tagged issue",
                labels=["backend"],
            )
        ],
        expected_repos=["org/repo"],
    )

    changes, attention, error = await run_sync(tmp_db, AgendumConfig(repos=["org/repo"]))

    task = find_task_by_gh_url(tmp_db, url)
    assert changes == 1
    assert attention is False
    assert error is None
    assert task is not None
    assert task["tags"] == '["backend"]'


async def test_run_sync_org_path_skips_archived_repo_items(
    tmp_db: Path,
    monkeypatch,
) -> None:
    init_db(tmp_db)

    async def fake_get_gh_username() -> str:
        return "author"

    async def fake_search_open_authored_prs_with_completeness(orgs, gh_user) -> tuple[list, bool]:
        return [
            {
                "gh_node_id": "PR_archived",
                "number": 40,
                "title": "Archived PR",
                "url": "https://github.com/org/archived-repo/pull/40",
                "repository": {"nameWithOwner": "org/archived-repo"},
            }
        ], True

    async def fake_empty_search(orgs, gh_user) -> tuple[list, bool]:
        return [], True

    async def fake_hydrate_open_authored_prs_with_completeness(prs, *, batch_size=50) -> tuple[list, bool]:
        return [
            {
                "gh_node_id": "PR_archived",
                "number": 40,
                "title": "Archived PR",
                "url": "https://github.com/org/archived-repo/pull/40",
                "repository": {
                    "nameWithOwner": "org/archived-repo",
                    "isArchived": True,
                },
                "state": "OPEN",
                "isDraft": False,
                "reviewDecision": None,
                "author": {"login": "author"},
                "labels": {"nodes": []},
                "reviewRequests": {"totalCount": 0},
                "commits": {"nodes": []},
                "reviews": {"nodes": []},
                "reviewThreads": {"nodes": []},
            }
        ], True

    async def fake_empty_hydrate(items, *, batch_size=50) -> tuple[list, bool]:
        return [], True

    async def fake_empty_verify_authored(prs, *, batch_size=50) -> tuple[list, bool]:
        assert prs == []
        return [], True

    async def fake_empty_verify_issues(issues, *, gh_user, batch_size=50) -> tuple[list, bool]:
        return [], True

    async def fake_empty_verify_review(prs, *, gh_user, batch_size=50) -> tuple[list, bool]:
        return [], True

    async def fake_fetch_notifications(gh_user) -> list:
        return []

    from agendum import gh

    monkeypatch.setattr(gh, "get_gh_username", fake_get_gh_username)
    monkeypatch.setattr(
        gh,
        "search_open_authored_prs_with_completeness",
        fake_search_open_authored_prs_with_completeness,
    )
    monkeypatch.setattr(gh, "search_open_assigned_issues_with_completeness", fake_empty_search)
    monkeypatch.setattr(gh, "search_open_review_requested_prs_with_completeness", fake_empty_search)
    monkeypatch.setattr(
        gh,
        "hydrate_open_authored_prs_with_completeness",
        fake_hydrate_open_authored_prs_with_completeness,
    )
    monkeypatch.setattr(gh, "hydrate_open_issues_with_completeness", fake_empty_hydrate)
    monkeypatch.setattr(gh, "hydrate_open_review_prs_with_completeness", fake_empty_hydrate)
    monkeypatch.setattr(gh, "verify_missing_authored_prs_with_completeness", fake_empty_verify_authored)
    monkeypatch.setattr(gh, "verify_missing_issues_with_completeness", fake_empty_verify_issues)
    monkeypatch.setattr(gh, "verify_missing_review_prs_with_completeness", fake_empty_verify_review)
    monkeypatch.setattr(gh, "fetch_notifications", fake_fetch_notifications)

    changes, attention, error = await run_sync(tmp_db, AgendumConfig(orgs=["org"]))

    assert changes == 0
    assert attention is False
    assert error is None
    assert get_active_tasks(tmp_db) == []


async def test_run_sync_repo_path_preserves_tasks_for_archived_repos(
    tmp_db: Path,
    monkeypatch,
) -> None:
    init_db(tmp_db)
    url = "https://github.com/org/repo/pull/41"
    add_task(
        tmp_db,
        title="Archived repo task",
        source="pr_authored",
        status="open",
        gh_url=url,
        gh_repo="org/repo",
        gh_number=41,
    )

    install_repo_planner_mocks(
        monkeypatch,
        gh_user="author",
        expected_repos=["org/repo"],
        archived_repos={"org/repo"},
    )

    changes, attention, error = await run_sync(tmp_db, AgendumConfig(repos=["org/repo"]))

    task = find_task_by_gh_url(tmp_db, url)
    assert changes == 0
    assert attention is False
    assert error is None
    assert task is not None
    assert task["status"] == "open"


async def test_run_sync_repo_path_keeps_unknown_archive_state_repo_in_scope(
    tmp_db: Path,
    monkeypatch,
) -> None:
    """Partial archive lookup must not silently drop healthy repos.

    Scoped repo ``org/healthy-repo`` is missing from the archive-state
    response (e.g. a flaky GraphQL batch), but it is not actually archived.
    The planner must keep it in scope so its open authored PR is hydrated
    and the existing tracked task is updated rather than left stale.
    """
    init_db(tmp_db)
    url = "https://github.com/org/healthy-repo/pull/42"
    add_task(
        tmp_db,
        title="Healthy repo authored PR",
        source="pr_authored",
        status="awaiting review",
        gh_url=url,
        gh_repo="org/healthy-repo",
        gh_node_id="PR_node_42",
        gh_number=42,
    )

    install_repo_planner_mocks(
        monkeypatch,
        gh_user="author",
        authored_prs=[
            make_open_authored_hydrated_pr(
                gh_user="author",
                url=url,
                repo_full="org/healthy-repo",
                gh_node_id="PR_node_42",
                number=42,
                title="Healthy repo authored PR",
                review_decision="APPROVED",
            )
        ],
        expected_repos=["org/healthy-repo"],
    )

    from agendum import gh

    async def fake_fetch_repo_archive_states_partial(repos, *, batch_size=20):
        # Simulate a partial response: the repo is missing entirely from
        # the lookup. A naive ``state is False`` filter would drop it
        # silently; the fix must keep unknown repos in scope.
        return {}, False

    monkeypatch.setattr(
        gh,
        "fetch_repo_archive_states_with_completeness",
        fake_fetch_repo_archive_states_partial,
    )

    changes, attention, error = await run_sync(
        tmp_db, AgendumConfig(repos=["org/healthy-repo"]),
    )

    assert error is None
    assert changes == 1
    assert attention is True
    task = find_task_by_gh_url(tmp_db, url)
    assert task is not None
    assert task["status"] == "approved"


async def test_run_sync_preserves_tasks_when_repo_fetch_fails(
    tmp_db: Path, monkeypatch,
) -> None:
    """Tasks should survive when their repo's API call fails."""
    init_db(tmp_db)
    url = "https://github.com/org/repo/pull/3"
    add_task(tmp_db, title="My PR", source="pr_authored", status="open",
             gh_url=url, gh_repo="org/repo")

    install_repo_planner_mocks(
        monkeypatch,
        gh_user="author",
        authored_search_complete=False,
        expected_repos=["org/repo"],
    )

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

    install_repo_planner_mocks(
        monkeypatch,
        gh_user="reviewer",
        review_search_complete=False,
        expected_repos=["org/repo"],
    )

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

    install_repo_planner_mocks(
        monkeypatch,
        gh_user="reviewer",
        review_search_complete=False,
        expected_repos=["org/repo"],
    )

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

    install_repo_planner_mocks(
        monkeypatch,
        gh_user="reviewer",
        verified_review_prs=[
            {
                "gh_node_id": "PR_node_8",
                "gh_url": url,
                "state": "OPEN",
                "is_review_requested": False,
            }
        ],
        expected_repos=["org/repo"],
    )

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

    async def fake_empty_search(orgs, gh_user) -> tuple[list, bool]:
        return [], True

    async def fake_empty_hydrate(items, *, batch_size=50) -> tuple[list, bool]:
        return [], True

    async def fake_verify_missing_review_prs_with_completeness(
        prs, *, gh_user, batch_size=50,
    ) -> tuple[list, bool]:
        assert prs == [
            {
                "task_id": 1,
                "source": "pr_review",
                "gh_repo": "org/other-repo",
                "gh_url": url,
                "gh_node_id": None,
                "gh_number": None,
                "title": "Review PR",
            }
        ]
        return [
            {
                "gh_node_id": "PR_100",
                "gh_url": url,
                "state": "OPEN",
                "is_review_requested": False,
            }
        ], True

    async def fake_empty_verify_authored(prs, *, batch_size=50) -> tuple[list, bool]:
        return [], True

    async def fake_empty_verify_issues(issues, *, gh_user, batch_size=50) -> tuple[list, bool]:
        return [], True

    async def fake_fetch_notifications(gh_user) -> list:
        return []

    from agendum import gh
    monkeypatch.setattr(gh, "get_gh_username", fake_get_gh_username)
    monkeypatch.setattr(gh, "search_open_authored_prs_with_completeness", fake_empty_search)
    monkeypatch.setattr(gh, "search_open_assigned_issues_with_completeness", fake_empty_search)
    monkeypatch.setattr(gh, "search_open_review_requested_prs_with_completeness", fake_empty_search)
    monkeypatch.setattr(gh, "hydrate_open_authored_prs_with_completeness", fake_empty_hydrate)
    monkeypatch.setattr(gh, "hydrate_open_issues_with_completeness", fake_empty_hydrate)
    monkeypatch.setattr(gh, "hydrate_open_review_prs_with_completeness", fake_empty_hydrate)
    monkeypatch.setattr(
        gh,
        "verify_missing_review_prs_with_completeness",
        fake_verify_missing_review_prs_with_completeness,
    )
    monkeypatch.setattr(gh, "verify_missing_authored_prs_with_completeness", fake_empty_verify_authored)
    monkeypatch.setattr(gh, "verify_missing_issues_with_completeness", fake_empty_verify_issues)
    monkeypatch.setattr(gh, "fetch_notifications", fake_fetch_notifications)

    changes, attention, error = await run_sync(tmp_db, AgendumConfig(orgs=["org"]))

    task = find_task_by_gh_url(tmp_db, url)
    assert task["status"] == "done"

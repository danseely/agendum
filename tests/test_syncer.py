import json
from pathlib import Path

import pytest

from agendum.config import AgendumConfig
from agendum.db import add_task, find_task_by_gh_url, get_active_tasks, init_db
from agendum.syncer import (
    CloseSuppression,
    MissingVerificationBundle,
    MissingVerificationRequest,
    NormalizedIncomingTask,
    OpenDiscoveryCoverage,
    OpenHydrationBundle,
    VerifiedMissingItem,
    build_sync_plan,
    diff_tasks,
    run_sync,
)
from tests.syncer_test_helpers import (
    install_repo_planner_mocks,
    make_open_authored_hydrated_pr,
    make_open_review_hydrated_pr,
)


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


@pytest.fixture
def authored_heavy_world() -> dict:
    return {
        "existing": [
            {
                "id": 1,
                "source": "pr_authored",
                "gh_repo": "org/repo-a",
                "gh_url": "https://github.com/org/repo-a/pull/1",
                "gh_node_id": "PR_1",
                "gh_number": 1,
                "title": "First PR",
            },
            {
                "id": 2,
                "source": "pr_authored",
                "gh_repo": "org/repo-b",
                "gh_url": "https://github.com/org/repo-b/pull/2",
                "gh_node_id": "PR_2",
                "gh_number": 2,
                "title": "Second PR",
            },
        ],
        "open_hydration": OpenHydrationBundle(
            authored_prs=[
                {
                    "gh_node_id": "PR_1",
                    "number": 1,
                    "title": "First PR",
                    "url": "https://github.com/org/repo-a/pull/1",
                    "repository": {"nameWithOwner": "org/repo-a"},
                    "state": "OPEN",
                    "isDraft": False,
                    "reviewDecision": "APPROVED",
                    "author": {"login": "author"},
                    "reviewRequests": {"totalCount": 0},
                    "commits": {"nodes": []},
                    "reviews": {"nodes": []},
                    "reviewThreads": {"nodes": []},
                },
                {
                    "gh_node_id": "PR_3",
                    "number": 3,
                    "title": "Third PR",
                    "url": "https://github.com/org/repo-c/pull/3",
                    "repository": {"nameWithOwner": "org/repo-c"},
                    "state": "OPEN",
                    "isDraft": True,
                    "reviewDecision": None,
                    "author": {"login": "author"},
                    "reviewRequests": {"totalCount": 0},
                    "commits": {"nodes": []},
                    "reviews": {"nodes": []},
                    "reviewThreads": {"nodes": []},
                },
            ]
        ),
        "verification": MissingVerificationBundle(
            authored_prs=[
                VerifiedMissingItem(
                    gh_node_id="PR_2",
                    gh_url="https://github.com/org/repo-b/pull/2",
                    state="MERGED",
                ),
            ]
        ),
    }


@pytest.fixture
def review_heavy_world() -> dict:
    return {
        "existing": [
            {
                "id": 10,
                "source": "pr_review",
                "gh_repo": "org/repo-r",
                "gh_url": "https://github.com/org/repo-r/pull/10",
                "gh_node_id": "PR_R1",
                "gh_number": 10,
                "title": "Reviewed once",
            },
            {
                "id": 11,
                "source": "pr_review",
                "gh_repo": "org/repo-r",
                "gh_url": "https://github.com/org/repo-r/pull/11",
                "gh_node_id": "PR_R2",
                "gh_number": 11,
                "title": "Missing review",
            },
        ],
        "open_hydration": OpenHydrationBundle(
            review_prs=[
                {
                    "gh_node_id": "PR_R1",
                    "number": 10,
                    "title": "Reviewed once",
                    "url": "https://github.com/org/repo-r/pull/10",
                    "repository": {"nameWithOwner": "org/repo-r"},
                    "author": {"login": "author", "name": "Author Person"},
                    "commits": {"nodes": [{"commit": {"committedDate": "2026-04-10T10:00:00Z"}}]},
                    "reviews": {
                        "nodes": [
                            {
                                "author": {"login": "reviewer"},
                                "submittedAt": "2026-04-09T10:00:00Z",
                                "state": "COMMENTED",
                            }
                        ]
                    },
                    "timelineItems": {"nodes": []},
                }
            ]
        ),
        "verification": MissingVerificationBundle(
            review_prs=[
                VerifiedMissingItem(
                    gh_node_id="PR_R2",
                    gh_url="https://github.com/org/repo-r/pull/11",
                    state="OPEN",
                    is_review_requested=False,
                )
            ]
        ),
    }


@pytest.fixture
def issue_heavy_world() -> dict:
    return {
        "existing": [
            {
                "id": 21,
                "source": "issue",
                "gh_repo": "org/repo-i",
                "gh_url": "https://github.com/org/repo-i/issues/21",
                "gh_node_id": "I_21",
                "gh_number": 21,
                "title": "Assigned task",
            },
            {
                "id": 22,
                "source": "issue",
                "gh_repo": "org/repo-i",
                "gh_url": "https://github.com/org/repo-i/issues/22",
                "gh_node_id": "I_22",
                "gh_number": 22,
                "title": "Dropped assignment",
            },
        ],
        "open_hydration": OpenHydrationBundle(
            issues=[
                {
                    "gh_node_id": "I_21",
                    "number": 21,
                    "title": "Assigned task",
                    "url": "https://github.com/org/repo-i/issues/21",
                    "repository": {"nameWithOwner": "org/repo-i"},
                    "state": "OPEN",
                    "timelineItems": {"nodes": [{"subject": {"url": "https://github.com/org/repo-i/pull/5"}}]},
                }
            ]
        ),
        "verification": MissingVerificationBundle(
            issues=[
                VerifiedMissingItem(
                    gh_node_id="I_22",
                    gh_url="https://github.com/org/repo-i/issues/22",
                    state="OPEN",
                    is_assigned_to_user=False,
                )
            ]
        ),
    }


@pytest.fixture
def mixed_org_world() -> dict:
    return {
        "existing": [],
        "open_hydration": OpenHydrationBundle(
            authored_prs=[
                {
                    "gh_node_id": "PR_M1",
                    "number": 31,
                    "title": "Org A PR",
                    "url": "https://github.com/org-a/repo-a/pull/31",
                    "repository": {"nameWithOwner": "org-a/repo-a"},
                    "state": "OPEN",
                    "isDraft": False,
                    "reviewDecision": None,
                    "author": {"login": "author"},
                    "reviewRequests": {"totalCount": 1},
                    "commits": {"nodes": []},
                    "reviews": {"nodes": []},
                    "reviewThreads": {"nodes": []},
                }
            ],
            issues=[
                {
                    "gh_node_id": "I_M2",
                    "number": 32,
                    "title": "Org B issue",
                    "url": "https://github.com/org-b/repo-b/issues/32",
                    "repository": {"nameWithOwner": "org-b/repo-b"},
                    "state": "OPEN",
                    "timelineItems": {"nodes": []},
                }
            ],
            review_prs=[
                {
                    "gh_node_id": "PR_M3",
                    "number": 33,
                    "title": "Org C review",
                    "url": "https://github.com/org-c/repo-c/pull/33",
                    "repository": {"nameWithOwner": "org-c/repo-c"},
                    "author": {"login": "peer", "name": "Peer Reviewer"},
                    "commits": {"nodes": []},
                    "reviews": {"nodes": []},
                    "timelineItems": {"nodes": []},
                }
            ],
        ),
    }


@pytest.fixture
def repo_only_world() -> dict:
    return {
        "existing": [
            {
                "id": 40,
                "source": "pr_review",
                "gh_repo": "org/repo-only",
                "gh_url": "https://github.com/org/repo-only/pull/40",
                "gh_node_id": "PR_REPO_ONLY",
                "gh_number": 40,
                "title": "Needs review",
            }
        ],
        "open_hydration": OpenHydrationBundle(),
        "coverage": OpenDiscoveryCoverage(review_complete=False),
    }


@pytest.fixture
def partial_failure_world() -> dict:
    return {
        "existing": [
            {
                "id": 51,
                "source": "pr_authored",
                "gh_repo": "org/repo-p",
                "gh_url": "https://github.com/org/repo-p/pull/51",
                "gh_node_id": "PR_P1",
                "gh_number": 51,
                "title": "Merged one",
            },
            {
                "id": 52,
                "source": "pr_authored",
                "gh_repo": "org/repo-p",
                "gh_url": "https://github.com/org/repo-p/pull/52",
                "gh_node_id": "PR_P2",
                "gh_number": 52,
                "title": "Unverified one",
            },
        ],
        "open_hydration": OpenHydrationBundle(),
        "verification": MissingVerificationBundle(
            authored_prs=[
                VerifiedMissingItem(
                    gh_node_id="PR_P1",
                    gh_url="https://github.com/org/repo-p/pull/51",
                    state="MERGED",
                )
            ],
            authored_complete=False,
        ),
    }


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


def test_build_sync_plan_authored_heavy_world(authored_heavy_world: dict) -> None:
    plan = build_sync_plan(
        authored_heavy_world["existing"],
        authored_heavy_world["open_hydration"],
        gh_user="author",
        verification=authored_heavy_world["verification"],
    )

    assert [item.gh_url for item in plan.missing_verification_request.authored_prs] == [
        "https://github.com/org/repo-b/pull/2"
    ]
    assert plan.close_suppression == CloseSuppression()
    assert plan.normalized_incoming_tasks == [
        NormalizedIncomingTask(
            title="First PR",
            source="pr_authored",
            status="approved",
            project="repo-a",
            gh_repo="org/repo-a",
            gh_url="https://github.com/org/repo-a/pull/1",
            gh_node_id="PR_1",
            gh_number=1,
        ),
        NormalizedIncomingTask(
            title="Third PR",
            source="pr_authored",
            status="draft",
            project="repo-c",
            gh_repo="org/repo-c",
            gh_url="https://github.com/org/repo-c/pull/3",
            gh_node_id="PR_3",
            gh_number=3,
        ),
        NormalizedIncomingTask(
            title="Second PR",
            source="pr_authored",
            status="merged",
            project="repo-b",
            gh_repo="org/repo-b",
            gh_url="https://github.com/org/repo-b/pull/2",
            gh_node_id="PR_2",
            gh_number=2,
        ),
    ]


def test_build_sync_plan_review_heavy_world(review_heavy_world: dict) -> None:
    plan = build_sync_plan(
        review_heavy_world["existing"],
        review_heavy_world["open_hydration"],
        gh_user="reviewer",
        verification=review_heavy_world["verification"],
    )

    assert [item.gh_url for item in plan.missing_verification_request.review_prs] == [
        "https://github.com/org/repo-r/pull/11"
    ]
    assert plan.close_suppression == CloseSuppression()
    assert [item.status for item in plan.normalized_incoming_tasks] == ["re-review requested", "done"]
    assert plan.normalized_incoming_tasks[0].gh_author_name == "Author"


def test_build_sync_plan_issue_heavy_world(issue_heavy_world: dict) -> None:
    plan = build_sync_plan(
        issue_heavy_world["existing"],
        issue_heavy_world["open_hydration"],
        gh_user="author",
        verification=issue_heavy_world["verification"],
    )

    assert [item.gh_url for item in plan.missing_verification_request.issues] == [
        "https://github.com/org/repo-i/issues/22"
    ]
    assert [item.status for item in plan.normalized_incoming_tasks] == ["in progress", "closed"]


def test_build_sync_plan_mixed_org_world(mixed_org_world: dict) -> None:
    plan = build_sync_plan(
        mixed_org_world["existing"],
        mixed_org_world["open_hydration"],
        gh_user="reviewer",
    )

    assert plan.missing_verification_request == MissingVerificationRequest()
    assert [item.project for item in plan.normalized_incoming_tasks] == [
        "repo-a",
        "repo-b",
        "repo-c",
    ]
    assert [item.status for item in plan.normalized_incoming_tasks] == [
        "awaiting review",
        "open",
        "review requested",
    ]


def test_build_sync_plan_repo_only_world(repo_only_world: dict) -> None:
    plan = build_sync_plan(
        repo_only_world["existing"],
        repo_only_world["open_hydration"],
        gh_user="reviewer",
        coverage=repo_only_world["coverage"],
    )

    assert plan.missing_verification_request.review_prs == []
    assert plan.close_suppression.review is True
    assert plan.close_suppression.review_urls == frozenset()


def test_build_sync_plan_partial_failure_world(partial_failure_world: dict) -> None:
    plan = build_sync_plan(
        partial_failure_world["existing"],
        partial_failure_world["open_hydration"],
        gh_user="author",
        verification=partial_failure_world["verification"],
    )

    assert [item.gh_url for item in plan.missing_verification_request.authored_prs] == [
        "https://github.com/org/repo-p/pull/51",
        "https://github.com/org/repo-p/pull/52",
    ]
    assert plan.close_suppression.authored is False
    assert plan.close_suppression.authored_urls == frozenset(
        {"https://github.com/org/repo-p/pull/52"}
    )
    assert plan.normalized_incoming_tasks[-1] == NormalizedIncomingTask(
        title="Merged one",
        source="pr_authored",
        status="merged",
        project="repo-p",
        gh_repo="org/repo-p",
        gh_url="https://github.com/org/repo-p/pull/51",
        gh_node_id="PR_P1",
        gh_number=51,
    )


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
    url = "https://github.com/example-org/example-repo/pull/12"
    add_task(tmp_db, title="Old PR", source="pr_authored", status="open", gh_url=url)
    install_repo_planner_mocks(
        monkeypatch,
        gh_user="author",
        verified_authored=[
            {
                "gh_node_id": "PR_node_12",
                "gh_url": url,
                "state": "CLOSED",
            }
        ],
        expected_repos=["example-org/example-repo"],
    )

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
    assert task["title"] == "Old PR"


@pytest.mark.asyncio
async def test_run_sync_marks_closed_assigned_issue_closed(tmp_db: Path, monkeypatch) -> None:
    init_db(tmp_db)
    url = "https://github.com/example-org/example-repo/issues/7"
    add_task(tmp_db, title="Old issue", source="issue", status="open", gh_url=url)
    install_repo_planner_mocks(
        monkeypatch,
        gh_user="author",
        verified_issues=[
            {
                "gh_node_id": "I_node_7",
                "gh_url": url,
                "state": "CLOSED",
                "is_assigned_to_user": False,
            }
        ],
        expected_repos=["example-org/example-repo"],
    )

    changes, attention, error = await run_sync(
        tmp_db,
        AgendumConfig(repos=["example-org/example-repo"]),
    )

    task = find_task_by_gh_url(tmp_db, url)
    assert changes == 1
    assert error is None
    assert task is not None
    assert task["status"] == "closed"
    assert task["title"] == "Old issue"


@pytest.mark.asyncio
async def test_run_sync_marks_dropped_review_request_done(tmp_db: Path, monkeypatch) -> None:
    init_db(tmp_db)
    url = "https://github.com/example-org/example-repo/pull/9"
    add_task(tmp_db, title="Old review PR", source="pr_review", status="review requested", gh_url=url)
    install_repo_planner_mocks(
        monkeypatch,
        gh_user="reviewer",
        verified_review_prs=[
            {
                "gh_node_id": "PR_node_9",
                "gh_url": url,
                "state": "OPEN",
                "is_review_requested": False,
            }
        ],
        expected_repos=["example-org/example-repo"],
    )

    changes, attention, error = await run_sync(
        tmp_db,
        AgendumConfig(repos=["example-org/example-repo"]),
    )

    task = find_task_by_gh_url(tmp_db, url)
    assert changes == 1
    assert error is None
    assert task is not None
    assert task["status"] == "done"
    assert task["title"] == "Old review PR"


@pytest.mark.asyncio
async def test_run_sync_revives_terminal_task(tmp_db: Path, monkeypatch) -> None:
    """A previously-closed PR that reappears as open should be updated, not re-inserted."""
    init_db(tmp_db)
    url = "https://github.com/example-org/example-repo/pull/42"
    add_task(tmp_db, title="Old PR", source="pr_authored", status="closed", gh_url=url, gh_number=42)

    install_repo_planner_mocks(
        monkeypatch,
        gh_user="author",
        authored_prs=[
            make_open_authored_hydrated_pr(
                gh_user="author",
                url=url,
                gh_node_id="PR_node_42",
                number=42,
                title="Reopened PR",
            )
        ],
        expected_repos=["example-org/example-repo"],
    )

    changes, attention, error = await run_sync(
        tmp_db,
        AgendumConfig(repos=["example-org/example-repo"]),
    )

    task = find_task_by_gh_url(tmp_db, url)
    assert error is None
    assert changes == 1
    assert task is not None
    assert task["status"] == "open"
    assert task["title"] == "Reopened PR"


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
        if args[:2] == ("api", "search/issues"):
            return json.dumps({"items": []})
        if args[:2] == ("api", "graphql"):
            return json.dumps(
                {
                    "data": {
                        "repo_0": {
                            "nameWithOwner": "example-org/example-repo",
                            "isArchived": False,
                        }
                    }
                }
            )
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
    assert observed_gh_dirs == [
        workspace_gh_dir,
        workspace_gh_dir,
        workspace_gh_dir,
        workspace_gh_dir,
        workspace_gh_dir,
        workspace_gh_dir,
    ]


@pytest.mark.asyncio
async def test_run_sync_creates_review_requested_pr_with_author_name(tmp_db: Path, monkeypatch) -> None:
    init_db(tmp_db)
    url = "https://github.com/example-org/example-repo/pull/34"
    install_repo_planner_mocks(
        monkeypatch,
        gh_user="reviewer",
        review_prs=[
            make_open_review_hydrated_pr(
                url=url,
                gh_node_id="PR_review_34",
                number=34,
                title="Fix telemetry attributes",
            )
        ],
        expected_repos=["example-org/example-repo"],
    )

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


@pytest.mark.asyncio
async def test_run_sync_flips_reviewed_back_to_re_review_when_re_requested(tmp_db: Path, monkeypatch) -> None:
    """After user reviews, if the author re-requests them (even without new commits),
    the status should flip from 'reviewed' back to 're-review requested'."""
    init_db(tmp_db)
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

    install_repo_planner_mocks(
        monkeypatch,
        gh_user="reviewer",
        review_prs=[
            make_open_review_hydrated_pr(
                url=url,
                gh_node_id="PR_review_77",
                number=77,
                title="Fix something",
                last_commit_at="2026-04-05T09:00:00Z",
                reviews=[
                    {
                        "author": {"login": "reviewer"},
                        "submittedAt": "2026-04-06T10:00:00Z",
                        "state": "CHANGES_REQUESTED",
                    }
                ],
                timeline_items=[
                    {
                        "createdAt": "2026-04-07T11:00:00Z",
                        "requestedReviewer": {"login": "reviewer"},
                    }
                ],
            )
        ],
        expected_repos=["example-org/example-repo"],
    )

    changes, attention, error = await run_sync(
        tmp_db,
        AgendumConfig(repos=["example-org/example-repo"]),
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

    install_repo_planner_mocks(
        monkeypatch,
        gh_user="reviewer",
        review_prs=[
            make_open_review_hydrated_pr(
                url=url,
                gh_node_id="PR_review_88",
                number=88,
                title="Fix something",
                last_commit_at="2026-04-05T09:00:00Z",
                reviews=[
                    {
                        "author": {"login": "reviewer"},
                        "submittedAt": "2026-04-06T10:00:00Z",
                        "state": "COMMENTED",
                    }
                ],
                timeline_items=[
                    {
                        "createdAt": "2026-04-07T11:00:00Z",
                        "requestedReviewer": {"login": "reviewer"},
                    }
                ],
            )
        ],
        expected_repos=["example-org/example-repo"],
    )

    changes, attention, error = await run_sync(
        tmp_db,
        AgendumConfig(repos=["example-org/example-repo"]),
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

    install_repo_planner_mocks(
        monkeypatch,
        gh_user="reviewer",
        review_prs=[
            make_open_review_hydrated_pr(
                url=url,
                gh_node_id="PR_review_99",
                number=99,
                title="Fresh ask",
                last_commit_at="2026-04-05T09:00:00Z",
                reviews=[],
                timeline_items=[],
            )
        ],
        expected_repos=["example-org/example-repo"],
    )

    changes, attention, error = await run_sync(
        tmp_db,
        AgendumConfig(repos=["example-org/example-repo"]),
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
    url = "https://github.com/example-org/example-repo/pull/12"
    add_task(tmp_db, title="Improve sync status handling", source="pr_authored", status="open", gh_url=url)
    install_repo_planner_mocks(
        monkeypatch,
        gh_user="author",
        authored_prs=[
            make_open_authored_hydrated_pr(
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
            )
        ],
        expected_repos=["example-org/example-repo"],
    )

    changes, attention, error = await run_sync(
        tmp_db,
        AgendumConfig(repos=["example-org/example-repo"]),
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
    url = "https://github.com/example-org/example-repo/pull/12"
    add_task(tmp_db, title="Improve sync status handling", source="pr_authored", status="open", gh_url=url)

    install_repo_planner_mocks(
        monkeypatch,
        gh_user="author",
        authored_prs=[
            make_open_authored_hydrated_pr(
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
            )
        ],
        expected_repos=["example-org/example-repo"],
    )

    changes, attention, error = await run_sync(
        tmp_db,
        AgendumConfig(repos=["example-org/example-repo"]),
    )

    task = find_task_by_gh_url(tmp_db, url)
    assert changes == 1
    assert error is None
    assert task is not None
    assert task["status"] == "changes requested"


@pytest.mark.asyncio
async def test_run_sync_clears_review_received_after_author_reply(tmp_db: Path, monkeypatch) -> None:
    init_db(tmp_db)
    url = "https://github.com/example-org/example-repo/pull/12"
    add_task(tmp_db, title="Improve sync status handling", source="pr_authored", status="review received", gh_url=url)

    install_repo_planner_mocks(
        monkeypatch,
        gh_user="author",
        authored_prs=[
            make_open_authored_hydrated_pr(
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
            )
        ],
        expected_repos=["example-org/example-repo"],
    )

    changes, attention, error = await run_sync(
        tmp_db,
        AgendumConfig(repos=["example-org/example-repo"]),
    )

    task = find_task_by_gh_url(tmp_db, url)
    assert changes == 1
    assert error is None
    assert task is not None
    assert task["status"] == "open"


@pytest.mark.asyncio
async def test_run_sync_keeps_review_received_with_sibling_thread_reply(tmp_db: Path, monkeypatch) -> None:
    init_db(tmp_db)
    url = "https://github.com/example-org/example-repo/pull/12"
    add_task(tmp_db, title="Improve sync status handling", source="pr_authored", status="review received", gh_url=url)

    install_repo_planner_mocks(
        monkeypatch,
        gh_user="author",
        authored_prs=[
            make_open_authored_hydrated_pr(
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
            )
        ],
        expected_repos=["example-org/example-repo"],
    )

    changes, attention, error = await run_sync(
        tmp_db,
        AgendumConfig(repos=["example-org/example-repo"]),
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
    url = "https://github.com/example-org/example-repo/pull/12"
    add_task(tmp_db, title="Improve sync status handling", source="pr_authored", status="review received", gh_url=url)

    install_repo_planner_mocks(
        monkeypatch,
        gh_user="author",
        authored_prs=[
            make_open_authored_hydrated_pr(
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
            )
        ],
        expected_repos=["example-org/example-repo"],
    )

    changes, attention, error = await run_sync(
        tmp_db,
        AgendumConfig(repos=["example-org/example-repo"]),
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
    url = "https://github.com/example-org/example-repo/pull/12"
    add_task(tmp_db, title="Improve sync status handling", source="pr_authored", status="review received", gh_url=url)

    install_repo_planner_mocks(
        monkeypatch,
        gh_user="author",
        authored_prs=[
            make_open_authored_hydrated_pr(
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
            )
        ],
        expected_repos=["example-org/example-repo"],
    )

    changes, attention, error = await run_sync(
        tmp_db,
        AgendumConfig(repos=["example-org/example-repo"]),
    )

    task = find_task_by_gh_url(tmp_db, url)
    assert changes == 1
    assert error is None
    assert task is not None
    assert task["status"] == "open"


@pytest.mark.asyncio
async def test_run_sync_keeps_review_received_after_push_when_feedback_threads_exist(tmp_db: Path, monkeypatch) -> None:
    init_db(tmp_db)
    url = "https://github.com/example-org/example-repo/pull/12"
    add_task(tmp_db, title="Improve sync status handling", source="pr_authored", status="review received", gh_url=url)

    install_repo_planner_mocks(
        monkeypatch,
        gh_user="author",
        authored_prs=[
            make_open_authored_hydrated_pr(
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
            )
        ],
        expected_repos=["example-org/example-repo"],
    )

    changes, attention, error = await run_sync(
        tmp_db,
        AgendumConfig(repos=["example-org/example-repo"]),
    )

    task = find_task_by_gh_url(tmp_db, url)
    assert changes == 1
    assert error is None
    assert task is not None
    assert task["status"] == "review received"


@pytest.mark.asyncio
async def test_run_sync_clears_review_received_after_push_without_feedback_threads(tmp_db: Path, monkeypatch) -> None:
    init_db(tmp_db)
    url = "https://github.com/example-org/example-repo/pull/12"
    add_task(tmp_db, title="Improve sync status handling", source="pr_authored", status="review received", gh_url=url)

    install_repo_planner_mocks(
        monkeypatch,
        gh_user="author",
        authored_prs=[
            make_open_authored_hydrated_pr(
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
            )
        ],
        expected_repos=["example-org/example-repo"],
    )

    changes, attention, error = await run_sync(
        tmp_db,
        AgendumConfig(repos=["example-org/example-repo"]),
    )

    task = find_task_by_gh_url(tmp_db, url)
    assert changes == 1
    assert error is None
    assert task is not None
    assert task["status"] == "open"

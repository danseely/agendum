from __future__ import annotations

from typing import Any


def search_skeletons(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "gh_node_id": item.get("gh_node_id"),
            "number": item["number"],
            "title": item["title"],
            "url": item["url"],
            "repository": item["repository"],
        }
        for item in items
    ]


def make_open_authored_hydrated_pr(
    *,
    gh_user: str,
    url: str,
    repo_full: str = "example-org/example-repo",
    gh_node_id: str = "PR_node_12",
    number: int = 12,
    title: str = "Improve sync status handling",
    state: str = "OPEN",
    is_draft: bool = False,
    review_decision: str | None = None,
    review_requests_total: int = 0,
    labels: list[str] | None = None,
    last_commit_at: str = "2026-04-07T20:00:00Z",
    reviews: list[dict[str, Any]] | None = None,
    review_threads: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "gh_node_id": gh_node_id,
        "number": number,
        "title": title,
        "url": url,
        "repository": {"nameWithOwner": repo_full},
        "state": state,
        "isDraft": is_draft,
        "author": {"login": gh_user},
        "reviewDecision": review_decision,
        "reviewRequests": {"totalCount": review_requests_total},
        "labels": {"nodes": [{"name": label} for label in (labels or [])]},
        "commits": {"nodes": [{"commit": {"committedDate": last_commit_at}}]},
        "reviews": {"nodes": reviews or []},
        "reviewThreads": {"nodes": review_threads or []},
    }


def make_open_review_hydrated_pr(
    *,
    url: str,
    repo_full: str = "example-org/example-repo",
    gh_node_id: str = "PR_review_node",
    number: int,
    title: str,
    author_login: str = "author",
    author_name: str = "Author Person",
    last_commit_at: str = "2026-04-07T20:00:00Z",
    reviews: list[dict[str, Any]] | None = None,
    timeline_items: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "gh_node_id": gh_node_id,
        "number": number,
        "title": title,
        "url": url,
        "repository": {"nameWithOwner": repo_full},
        "author": {"login": author_login, "name": author_name},
        "commits": {"nodes": [{"commit": {"committedDate": last_commit_at}}]},
        "reviews": {"nodes": reviews or []},
        "timelineItems": {"nodes": timeline_items or []},
    }


def make_open_issue_hydrated(
    *,
    url: str,
    repo_full: str = "example-org/example-repo",
    gh_node_id: str = "I_node_1",
    number: int,
    title: str,
    state: str = "OPEN",
    labels: list[str] | None = None,
    timeline_items: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "gh_node_id": gh_node_id,
        "number": number,
        "title": title,
        "url": url,
        "repository": {"nameWithOwner": repo_full},
        "state": state,
        "labels": {"nodes": [{"name": label} for label in (labels or [])]},
        "timelineItems": {"nodes": timeline_items or []},
    }


def install_repo_planner_mocks(
    monkeypatch,
    *,
    gh_user: str,
    authored_prs: list[dict[str, Any]] | None = None,
    issues: list[dict[str, Any]] | None = None,
    review_prs: list[dict[str, Any]] | None = None,
    verified_authored: list[dict[str, Any]] | None = None,
    verified_issues: list[dict[str, Any]] | None = None,
    verified_review_prs: list[dict[str, Any]] | None = None,
    authored_search_complete: bool = True,
    issues_search_complete: bool = True,
    review_search_complete: bool = True,
    authored_hydrate_complete: bool = True,
    issues_hydrate_complete: bool = True,
    review_hydrate_complete: bool = True,
    authored_verify_complete: bool = True,
    issues_verify_complete: bool = True,
    review_verify_complete: bool = True,
    notifications: list[dict[str, Any]] | None = None,
    expected_repos: list[str] | None = None,
    archived_repos: set[str] | None = None,
    archive_states_complete: bool = True,
) -> None:
    authored_prs = authored_prs or []
    issues = issues or []
    review_prs = review_prs or []
    verified_authored = verified_authored or []
    verified_issues = verified_issues or []
    verified_review_prs = verified_review_prs or []
    notifications = notifications or []
    archived_repos = archived_repos or set()

    from agendum import gh

    async def fake_get_gh_username() -> str:
        return gh_user

    async def fake_search_open_authored_prs_for_repos_with_completeness(repos, gh_user_value):
        if expected_repos is not None:
            assert repos == expected_repos
        assert gh_user_value == gh_user
        return search_skeletons(authored_prs), authored_search_complete

    async def fake_search_open_assigned_issues_for_repos_with_completeness(repos, gh_user_value):
        if expected_repos is not None:
            assert repos == expected_repos
        assert gh_user_value == gh_user
        return search_skeletons(issues), issues_search_complete

    async def fake_search_open_review_requested_prs_for_repos_with_completeness(
        repos,
        gh_user_value,
    ):
        if expected_repos is not None:
            assert repos == expected_repos
        assert gh_user_value == gh_user
        return search_skeletons(review_prs), review_search_complete

    async def fake_hydrate_open_authored_prs_with_completeness(prs, *, batch_size=50):
        return authored_prs, authored_hydrate_complete

    async def fake_hydrate_open_issues_with_completeness(items, *, batch_size=50):
        return issues, issues_hydrate_complete

    async def fake_hydrate_open_review_prs_with_completeness(prs, *, batch_size=50):
        return review_prs, review_hydrate_complete

    async def fake_verify_missing_authored_prs_with_completeness(prs, *, batch_size=50):
        return verified_authored, authored_verify_complete

    async def fake_verify_missing_issues_with_completeness(items, *, gh_user, batch_size=50):
        return verified_issues, issues_verify_complete

    async def fake_verify_missing_review_prs_with_completeness(prs, *, gh_user, batch_size=50):
        return verified_review_prs, review_verify_complete

    async def fake_fetch_notifications(gh_user_value):
        assert gh_user_value == gh_user
        return notifications

    async def fake_fetch_repo_archive_states_with_completeness(repos, *, batch_size=20):
        if expected_repos is not None:
            assert repos == expected_repos
        return {repo: repo in archived_repos for repo in repos}, archive_states_complete

    monkeypatch.setattr(gh, "get_gh_username", fake_get_gh_username)
    monkeypatch.setattr(
        gh,
        "search_open_authored_prs_for_repos_with_completeness",
        fake_search_open_authored_prs_for_repos_with_completeness,
    )
    monkeypatch.setattr(
        gh,
        "search_open_assigned_issues_for_repos_with_completeness",
        fake_search_open_assigned_issues_for_repos_with_completeness,
    )
    monkeypatch.setattr(
        gh,
        "search_open_review_requested_prs_for_repos_with_completeness",
        fake_search_open_review_requested_prs_for_repos_with_completeness,
    )
    monkeypatch.setattr(
        gh,
        "hydrate_open_authored_prs_with_completeness",
        fake_hydrate_open_authored_prs_with_completeness,
    )
    monkeypatch.setattr(
        gh,
        "hydrate_open_issues_with_completeness",
        fake_hydrate_open_issues_with_completeness,
    )
    monkeypatch.setattr(
        gh,
        "hydrate_open_review_prs_with_completeness",
        fake_hydrate_open_review_prs_with_completeness,
    )
    monkeypatch.setattr(
        gh,
        "verify_missing_authored_prs_with_completeness",
        fake_verify_missing_authored_prs_with_completeness,
    )
    monkeypatch.setattr(
        gh,
        "verify_missing_issues_with_completeness",
        fake_verify_missing_issues_with_completeness,
    )
    monkeypatch.setattr(
        gh,
        "verify_missing_review_prs_with_completeness",
        fake_verify_missing_review_prs_with_completeness,
    )
    monkeypatch.setattr(
        gh,
        "fetch_repo_archive_states_with_completeness",
        fake_fetch_repo_archive_states_with_completeness,
    )
    monkeypatch.setattr(gh, "fetch_notifications", fake_fetch_notifications)

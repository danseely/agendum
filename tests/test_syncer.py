import json
import logging
from pathlib import Path
import re

import pytest

from agendum.config import AgendumConfig
from agendum.db import add_task, find_task_by_gh_url, get_active_tasks, get_sync_state, init_db
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

    async def fake_fetch_notifications(
        seen_user: str,
        *,
        since: str | None = None,
    ) -> tuple[list[dict], bool]:
        assert seen_user == gh_user
        del since
        return list(notifications or []), True

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


def make_search_payload(
    nodes: list[dict],
    *,
    has_next_page: bool = False,
    end_cursor: str | None = None,
) -> dict:
    return {
        "data": {
            "search": {
                "nodes": nodes,
                "pageInfo": {
                    "hasNextPage": has_next_page,
                    "endCursor": end_cursor,
                },
            },
        },
    }


def _graphql_query_arg(args: tuple[str, ...]) -> str:
    return next(arg.split("=", 1)[1] for arg in args if arg.startswith("query="))


def _graphql_form_arg(args: tuple[str, ...], name: str) -> str | None:
    for index, arg in enumerate(args):
        if arg == "-F" and index + 1 < len(args) and args[index + 1].startswith(f"{name}="):
            return args[index + 1].split("=", 1)[1]
    return None


def install_search_first_cli_fixture(
    monkeypatch,
    *,
    gh_user: str,
    search_pages: dict[tuple[str, str | None], dict],
    hydrated_nodes: dict[str, dict],
    notifications: list[dict] | None = None,
) -> None:
    class FakeProcess:
        returncode = 0

        def __init__(self, stdout: bytes):
            self.stdout = stdout

        async def communicate(self):
            return self.stdout, b""

    async def fake_create_subprocess_exec(*args: str, **kwargs):
        del kwargs
        gh_args = args[1:]

        if gh_args == ("api", "user", "--jq", ".login"):
            return FakeProcess(f"{gh_user}\n".encode())

        if gh_args[:2] == ("api", "notifications"):
            return FakeProcess(json.dumps(notifications or []).encode())

        if gh_args[:2] == ("api", "graphql"):
            query = _graphql_query_arg(gh_args)
            if "search(type: ISSUE" in query:
                search_query = _graphql_form_arg(gh_args, "query")
                after = _graphql_form_arg(gh_args, "after")
                payload = search_pages.get((search_query or "", after))
                if payload is None:
                    raise AssertionError(f"Unexpected search query: {search_query!r} after={after!r}")
                return FakeProcess(json.dumps(payload).encode())

            node_ids = re.findall(r'node\(id:\s*"([^"]+)"\)', query)
            if node_ids:
                payload = {
                    "data": {
                        f"n{index}": hydrated_nodes.get(node_id)
                        for index, node_id in enumerate(node_ids)
                    },
                }
                return FakeProcess(json.dumps(payload).encode())

        raise AssertionError(f"Unexpected gh call: {gh_args}")

    monkeypatch.setattr("agendum.gh.asyncio.create_subprocess_exec", fake_create_subprocess_exec)


def parse_github_api_usage(caplog) -> dict[str, int]:
    pattern = re.compile(
        r"GitHub API usage: total=(?P<total>\d+) graphql=(?P<graphql>\d+) "
        r"search=(?P<search>\d+) hydrate=(?P<hydrate>\d+) notifications=(?P<notifications>\d+) "
        r"rest=(?P<rest>\d+) bytes=(?P<bytes>\d+)"
    )
    for record in reversed(caplog.records):
        match = pattern.search(record.message)
        if match:
            return {key: int(value) for key, value in match.groupdict().items()}
    raise AssertionError("GitHub API usage log line not found")


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

    async def fake_fetch_notifications(
        gh_user: str,
        *,
        since: str | None = None,
    ) -> tuple[list[dict], bool]:
        del gh_user, since
        return [], True

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

    assert changes == 3
    assert attention is True
    assert error is None
    assert {node_id for call in pr_hydrate_calls for node_id in call} == {
        "PR_AUTH_1",
        "PR_REVIEW_1",
    }
    assert issue_hydrate_calls == [("ISSUE_1",)]

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
    caplog,
) -> None:
    init_db(tmp_db)

    from agendum import gh

    workspace_gh_dir = tmp_db.parent / "gh"
    switched_gh_dir = tmp_db.parent / "switched" / "gh"
    observed_gh_dirs: list[Path | None] = []
    switched = False

    class FakeProcess:
        returncode = 0

        def __init__(self, stdout: bytes):
            self.stdout = stdout

        async def communicate(self):
            return self.stdout, b""

    async def fake_create_subprocess_exec(*args: str, **kwargs):
        nonlocal switched
        observed_gh_dirs.append(gh.get_gh_config_dir())
        if not switched:
            gh.set_gh_config_dir(switched_gh_dir)
            switched = True

        gh_args = args[1:]
        if gh_args == ("api", "user", "--jq", ".login"):
            return FakeProcess(b"author\n")
        if gh_args[:2] == ("api", "graphql"):
            return FakeProcess(json.dumps({
                "data": {
                    "search": {
                        "pageInfo": {"hasNextPage": False, "endCursor": None},
                        "nodes": [],
                    },
                },
            }).encode())
        if gh_args[:2] == ("api", "notifications"):
            return FakeProcess(b"[]")
        raise AssertionError(f"Unexpected gh call: {gh_args}")

    monkeypatch.setattr("agendum.gh.asyncio.create_subprocess_exec", fake_create_subprocess_exec)
    caplog.set_level(logging.INFO)

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
    assert any(
        "GitHub API usage: total=8 graphql=6 search=6 hydrate=0 notifications=1 rest=2"
        in record.message
        for record in caplog.records
    )


@pytest.mark.asyncio
async def test_run_sync_mixed_workspace_captures_api_budget_and_expected_tasks(
    tmp_db: Path,
    monkeypatch,
    caplog,
) -> None:
    init_db(tmp_db)
    caplog.set_level(logging.INFO)

    authored_one_url = "https://github.com/example-org/authored-repo/pull/1"
    authored_two_url = "https://github.com/example-org/authored-repo/pull/2"
    authored_three_url = "https://github.com/example-org/authored-repo/pull/3"
    issue_url = "https://github.com/example-org/issue-repo/issues/11"
    review_url = "https://github.com/example-org/review-repo/pull/7"

    install_search_first_cli_fixture(
        monkeypatch,
        gh_user="dan",
        search_pages={
            ("org:example-org is:open is:pr author:dan", None): make_search_payload(
                [
                    {"id": "PR_AUTH_1", "repository": {"nameWithOwner": "example-org/authored-repo"}},
                    {"id": "PR_AUTH_2", "repository": {"nameWithOwner": "example-org/authored-repo"}},
                ],
                has_next_page=True,
                end_cursor="AUTH_CURSOR",
            ),
            ("org:example-org is:open is:pr author:dan", "AUTH_CURSOR"): make_search_payload(
                [
                    {"id": "PR_AUTH_2", "repository": {"nameWithOwner": "example-org/authored-repo"}},
                    {"id": "PR_AUTH_3", "repository": {"nameWithOwner": "example-org/authored-repo"}},
                ],
            ),
            ("org:example-org is:merged is:pr author:dan", None): make_search_payload([]),
            ("org:example-org is:closed -is:merged is:pr author:dan", None): make_search_payload([]),
            ("org:example-org is:open is:issue assignee:dan", None): make_search_payload(
                [{"id": "ISSUE_1", "repository": {"nameWithOwner": "example-org/issue-repo"}}],
            ),
            ("org:example-org is:closed is:issue assignee:dan", None): make_search_payload([]),
            ("org:example-org is:open is:pr review-requested:dan", None): make_search_payload(
                [{"id": "PR_REVIEW_1", "repository": {"nameWithOwner": "example-org/review-repo"}}],
            ),
        },
        hydrated_nodes={
            "PR_AUTH_1": {
                **make_authored_pr(gh_user="dan", url=authored_one_url),
                "id": "PR_AUTH_1",
                "number": 1,
                "title": "Authored One",
                "repository": {"nameWithOwner": "example-org/authored-repo"},
                "timelineItems": {"nodes": []},
            },
            "PR_AUTH_2": {
                **make_authored_pr(gh_user="dan", url=authored_two_url),
                "id": "PR_AUTH_2",
                "number": 2,
                "title": "Authored Two",
                "repository": {"nameWithOwner": "example-org/authored-repo"},
                "reviewRequests": {"totalCount": 1},
                "timelineItems": {"nodes": []},
            },
            "PR_AUTH_3": {
                **make_authored_pr(gh_user="dan", url=authored_three_url, review_decision="APPROVED"),
                "id": "PR_AUTH_3",
                "number": 3,
                "title": "Authored Three",
                "repository": {"nameWithOwner": "example-org/authored-repo"},
                "timelineItems": {"nodes": []},
            },
            "ISSUE_1": {
                "id": "ISSUE_1",
                "number": 11,
                "title": "Assigned issue",
                "url": issue_url,
                "state": "OPEN",
                "repository": {"nameWithOwner": "example-org/issue-repo"},
                "labels": {"nodes": [{"name": "help wanted"}]},
                "timelineItems": {
                    "nodes": [
                        {
                            "subject": {
                                "url": "https://github.com/example-org/issue-repo/pull/99",
                            },
                        },
                    ],
                },
            },
            "PR_REVIEW_1": {
                **make_authored_pr(gh_user="alice", url=review_url),
                "id": "PR_REVIEW_1",
                "number": 7,
                "title": "Review me",
                "repository": {"nameWithOwner": "example-org/review-repo"},
                "author": {"login": "alice", "name": "Alice Example"},
                "timelineItems": {"nodes": []},
            },
        },
    )

    changes, attention, error = await run_sync(tmp_db, AgendumConfig(orgs=["example-org"]))

    tasks = {task["gh_url"]: task for task in get_active_tasks(tmp_db)}
    api_usage = parse_github_api_usage(caplog)

    assert changes == 5
    assert attention is True
    assert error is None
    assert set(tasks) == {
        authored_one_url,
        authored_two_url,
        authored_three_url,
        issue_url,
        review_url,
    }
    assert tasks[authored_one_url]["status"] == "open"
    assert tasks[authored_two_url]["status"] == "awaiting review"
    assert tasks[authored_three_url]["status"] == "approved"
    assert tasks[issue_url]["status"] == "in progress"
    assert tasks[review_url]["status"] == "review requested"
    assert tasks[review_url]["gh_author_name"] == "Alice"
    assert api_usage["total"] <= 12
    assert api_usage["graphql"] <= 10
    assert api_usage["search"] <= 7
    assert api_usage["hydrate"] <= 3
    assert api_usage["notifications"] == 1
    assert api_usage["rest"] == 2
    assert api_usage["bytes"] > 0


@pytest.mark.asyncio
async def test_run_sync_handles_page_two_item_once_with_duplicate_across_pages(
    tmp_db: Path,
    monkeypatch,
) -> None:
    init_db(tmp_db)

    first_url = "https://github.com/example-org/example-repo/pull/1"
    second_url = "https://github.com/example-org/example-repo/pull/2"

    install_search_first_cli_fixture(
        monkeypatch,
        gh_user="dan",
        search_pages={
            ("org:example-org is:open is:pr author:dan", None): make_search_payload(
                [{"id": "PR_1", "repository": {"nameWithOwner": "example-org/example-repo"}}],
                has_next_page=True,
                end_cursor="NEXT_PAGE",
            ),
            ("org:example-org is:open is:pr author:dan", "NEXT_PAGE"): make_search_payload(
                [
                    {"id": "PR_1", "repository": {"nameWithOwner": "example-org/example-repo"}},
                    {"id": "PR_2", "repository": {"nameWithOwner": "example-org/example-repo"}},
                ],
            ),
            ("org:example-org is:merged is:pr author:dan", None): make_search_payload([]),
            ("org:example-org is:closed -is:merged is:pr author:dan", None): make_search_payload([]),
            ("org:example-org is:open is:issue assignee:dan", None): make_search_payload([]),
            ("org:example-org is:closed is:issue assignee:dan", None): make_search_payload([]),
            ("org:example-org is:open is:pr review-requested:dan", None): make_search_payload([]),
        },
        hydrated_nodes={
            "PR_1": {
                **make_authored_pr(gh_user="dan", url=first_url),
                "id": "PR_1",
                "number": 1,
                "title": "First PR",
                "repository": {"nameWithOwner": "example-org/example-repo"},
                "timelineItems": {"nodes": []},
            },
            "PR_2": {
                **make_authored_pr(gh_user="dan", url=second_url),
                "id": "PR_2",
                "number": 2,
                "title": "Second PR",
                "repository": {"nameWithOwner": "example-org/example-repo"},
                "timelineItems": {"nodes": []},
            },
        },
    )

    changes, attention, error = await run_sync(tmp_db, AgendumConfig(orgs=["example-org"]))

    tasks = {task["gh_url"]: task for task in get_active_tasks(tmp_db)}
    assert changes == 2
    assert attention is False
    assert error is None
    assert set(tasks) == {first_url, second_url}
    assert tasks[second_url]["title"] == "Second PR"


@pytest.mark.asyncio
async def test_run_sync_persists_incremental_notification_cursor(
    tmp_db: Path,
    monkeypatch,
) -> None:
    init_db(tmp_db)
    repo = "example-org/example-repo"
    observed_since: list[str | None] = []

    install_repo_only_search_first_mocks(
        monkeypatch,
        repos=[repo],
        gh_user="reviewer",
    )

    from agendum import gh

    async def fake_fetch_notifications(
        gh_user: str,
        *,
        since: str | None = None,
    ) -> tuple[list[dict], bool]:
        assert gh_user == "reviewer"
        observed_since.append(since)
        return [], True

    monkeypatch.setattr(gh, "fetch_notifications", fake_fetch_notifications)

    first_changes, _, first_error = await run_sync(tmp_db, AgendumConfig(repos=[repo]))
    first_cursor = get_sync_state(tmp_db, "github_notifications_since")

    second_changes, _, second_error = await run_sync(tmp_db, AgendumConfig(repos=[repo]))

    assert first_changes == 0
    assert second_changes == 0
    assert first_error is None
    assert second_error is None
    assert first_cursor is not None
    assert observed_since == [None, first_cursor]


@pytest.mark.asyncio
async def test_run_sync_preserves_notification_cursor_when_fetch_fails(
    tmp_db: Path,
    monkeypatch,
) -> None:
    init_db(tmp_db)
    repo = "example-org/example-repo"
    original_cursor = "2026-04-24T12:00:00+00:00"

    install_repo_only_search_first_mocks(
        monkeypatch,
        repos=[repo],
        gh_user="reviewer",
    )
    from agendum import gh
    from agendum.db import set_sync_state

    set_sync_state(tmp_db, "github_notifications_since", original_cursor)

    async def fake_fetch_notifications(
        gh_user: str,
        *,
        since: str | None = None,
    ) -> tuple[list[dict], bool]:
        assert gh_user == "reviewer"
        assert since == original_cursor
        return [], False

    monkeypatch.setattr(gh, "fetch_notifications", fake_fetch_notifications)

    changes, attention, error = await run_sync(tmp_db, AgendumConfig(repos=[repo]))

    assert changes == 0
    assert attention is False
    assert error is None
    assert get_sync_state(tmp_db, "github_notifications_since") == original_cursor


@pytest.mark.asyncio
async def test_run_sync_replays_notifications_after_processing_failure(
    tmp_db: Path,
    monkeypatch,
) -> None:
    init_db(tmp_db)
    repo = "example-org/example-repo"
    url = "https://github.com/example-org/example-repo/pull/12"
    original_cursor = "2026-04-24T12:00:00+00:00"
    task_id = add_task(
        tmp_db,
        title="Existing PR",
        source="pr_authored",
        status="open",
        gh_url=url,
        gh_number=12,
        project="example-repo",
        gh_repo=repo,
    )

    from agendum import gh, syncer as syncer_module
    from agendum.db import set_sync_state, update_task as db_update_task

    db_update_task(tmp_db, task_id, seen=1)
    set_sync_state(tmp_db, "github_notifications_since", original_cursor)

    notifications = [
        {
            "reason": "comment",
            "subject": {
                "url": "https://api.github.com/repos/example-org/example-repo/pulls/12",
            },
        }
    ]

    install_repo_only_search_first_mocks(
        monkeypatch,
        repos=[repo],
        gh_user="reviewer",
        notifications=notifications,
    )

    def failing_update_task(db_path: Path, task_id: int, **kwargs) -> None:
        if kwargs.get("seen") == 0:
            raise RuntimeError("notification update failed")
        db_update_task(db_path, task_id, **kwargs)

    monkeypatch.setattr(syncer_module, "update_task", failing_update_task)

    with pytest.raises(RuntimeError, match="notification update failed"):
        await run_sync(tmp_db, AgendumConfig(repos=[repo]))

    task = find_task_by_gh_url(tmp_db, url)
    assert task is not None
    assert task["seen"] == 1
    assert get_sync_state(tmp_db, "github_notifications_since") == original_cursor

    monkeypatch.setattr(syncer_module, "update_task", db_update_task)

    changes, attention, error = await run_sync(tmp_db, AgendumConfig(repos=[repo]))

    task = find_task_by_gh_url(tmp_db, url)
    replay_cursor = get_sync_state(tmp_db, "github_notifications_since")
    assert changes == 1
    assert attention is True
    assert error is None
    assert task is not None
    assert task["seen"] == 0
    assert replay_cursor is not None
    assert replay_cursor != original_cursor


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

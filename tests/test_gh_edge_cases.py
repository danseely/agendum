"""Tests for gh module functions that interact with the gh CLI."""

import json

import pytest

from agendum.gh import (
    discover_repos,
    discover_review_prs,
    fetch_notifications,
    fetch_repo_data,
    hydrate_open_authored_prs,
    hydrate_open_issues,
    search_open_assigned_issues,
    search_open_assigned_issues_for_repos,
    search_open_authored_prs,
    search_open_authored_prs_for_repos,
    search_open_review_requested_prs,
    search_open_review_requested_prs_for_repos,
    verify_missing_authored_prs,
    verify_missing_issues,
    verify_missing_review_prs,
)


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


def _query_from_args(args: tuple[str, ...]) -> str:
    index = args.index("-f") + 1
    return args[index].split("q=", 1)[1]


def _page_from_args(args: tuple[str, ...]) -> int:
    index = args.index("-F")
    while index < len(args):
        value = args[index + 1]
        if value.startswith("page="):
            return int(value.split("=", 1)[1])
        index = args.index("-F", index + 2) if "-F" in args[index + 2:] else len(args)
    return 1


async def test_search_open_authored_prs_dedupes_across_pages_and_orgs(monkeypatch) -> None:
    responses = {
        ("is:open is:pr author:user org:org-a", 1): {
            "items": [
                {
                    "node_id": "PR_node_1",
                    "number": 1,
                    "title": "First authored PR",
                    "html_url": "https://github.com/org-a/repo-a/pull/1",
                    "repository_url": "https://api.github.com/repos/org-a/repo-a",
                },
                {
                    "node_id": "PR_node_2",
                    "number": 2,
                    "title": "Second authored PR",
                    "html_url": "https://github.com/org-a/repo-b/pull/2",
                    "repository_url": "https://api.github.com/repos/org-a/repo-b",
                },
            ],
        },
        ("is:open is:pr author:user org:org-a", 2): {
            "items": [
                {
                    "node_id": "PR_node_2",
                    "number": 2,
                    "title": "Second authored PR",
                    "html_url": "https://github.com/org-a/repo-b/pull/2",
                    "repository_url": "https://api.github.com/repos/org-a/repo-b",
                },
            ],
        },
        ("is:open is:pr author:user org:org-b", 1): {
            "items": [
                {
                    "node_id": "PR_node_3",
                    "number": 3,
                    "title": "Third authored PR",
                    "html_url": "https://github.com/org-b/repo-c/pull/3",
                    "repository_url": "https://api.github.com/repos/org-b/repo-c",
                },
                {
                    "node_id": "PR_node_1",
                    "number": 1,
                    "title": "First authored PR",
                    "html_url": "https://github.com/org-a/repo-a/pull/1",
                    "repository_url": "https://api.github.com/repos/org-a/repo-a",
                },
            ],
        },
    }

    async def fake_run_gh(*args: str) -> str:
        query = _query_from_args(args)
        page = _page_from_args(args)
        return json.dumps(responses.get((query, page), {"items": []}))

    from agendum import gh

    monkeypatch.setattr(gh, "_SEARCH_PAGE_SIZE", 2)
    monkeypatch.setattr(gh, "_run_gh", fake_run_gh)

    prs = await search_open_authored_prs(["org-a", "org-b"], "user")

    assert [item["number"] for item in prs] == [1, 2, 3]
    assert prs[0]["repository"]["nameWithOwner"] == "org-a/repo-a"
    assert all(set(item) == {"gh_node_id", "number", "title", "url", "repository"} for item in prs)


async def test_search_open_assigned_issues_returns_minimal_stable_shape(monkeypatch) -> None:
    async def fake_run_gh(*args: str) -> str:
        assert _query_from_args(args) == "is:open is:issue assignee:user org:org"
        return json.dumps(
            {
                "items": [
                    {
                        "node_id": "I_node_1",
                        "number": 11,
                        "title": "Assigned issue",
                        "html_url": "https://github.com/org/repo/issues/11",
                        "repository_url": "https://api.github.com/repos/org/repo",
                        "body": "ignored",
                        "user": {"login": "someone"},
                    }
                ]
            }
        )

    from agendum import gh

    monkeypatch.setattr(gh, "_run_gh", fake_run_gh)

    issues = await search_open_assigned_issues(["org"], "user")

    assert issues == [
        {
            "gh_node_id": "I_node_1",
            "number": 11,
            "title": "Assigned issue",
            "url": "https://github.com/org/repo/issues/11",
            "repository": {"nameWithOwner": "org/repo"},
        }
    ]


async def test_search_open_review_requested_prs_dedupes_overlap(monkeypatch) -> None:
    async def fake_run_gh(*args: str) -> str:
        query = _query_from_args(args)
        if query == "is:open is:pr review-requested:reviewer org:org-a":
            return json.dumps(
                {
                    "items": [
                        {
                            "node_id": "PR_review_1",
                            "number": 21,
                            "title": "Review me",
                            "html_url": "https://github.com/org-a/repo/pull/21",
                            "repository_url": "https://api.github.com/repos/org-a/repo",
                        }
                    ]
                }
            )
        if query == "is:open is:pr review-requested:reviewer org:org-b":
            return json.dumps(
                {
                    "items": [
                        {
                            "node_id": "PR_review_1",
                            "number": 21,
                            "title": "Review me",
                            "html_url": "https://github.com/org-a/repo/pull/21",
                            "repository_url": "https://api.github.com/repos/org-a/repo",
                        },
                        {
                            "node_id": "PR_review_2",
                            "number": 22,
                            "title": "Also review me",
                            "html_url": "https://github.com/org-b/repo/pull/22",
                            "repository_url": "https://api.github.com/repos/org-b/repo",
                        },
                    ]
                }
            )
        return json.dumps({"items": []})

    from agendum import gh

    monkeypatch.setattr(gh, "_run_gh", fake_run_gh)

    prs = await search_open_review_requested_prs(["org-a", "org-b"], "reviewer")

    assert [item["gh_node_id"] for item in prs] == ["PR_review_1", "PR_review_2"]


async def test_search_open_authored_prs_for_repos_chunks_and_dedupes(monkeypatch) -> None:
    responses = {
        (
            "is:open is:pr author:user repo:org-a/repo-1 repo:org-a/repo-2",
            1,
        ): {
            "items": [
                {
                    "node_id": "PR_node_1",
                    "number": 1,
                    "title": "Repo one PR",
                    "html_url": "https://github.com/org-a/repo-1/pull/1",
                    "repository_url": "https://api.github.com/repos/org-a/repo-1",
                },
                {
                    "node_id": "PR_node_2",
                    "number": 2,
                    "title": "Repo two PR",
                    "html_url": "https://github.com/org-a/repo-2/pull/2",
                    "repository_url": "https://api.github.com/repos/org-a/repo-2",
                },
            ],
        },
        (
            "is:open is:pr author:user repo:org-b/repo-3 repo:org-b/repo-4",
            1,
        ): {
            "items": [
                {
                    "node_id": "PR_node_2",
                    "number": 2,
                    "title": "Repo two PR",
                    "html_url": "https://github.com/org-a/repo-2/pull/2",
                    "repository_url": "https://api.github.com/repos/org-a/repo-2",
                },
                {
                    "node_id": "PR_node_3",
                    "number": 3,
                    "title": "Repo three PR",
                    "html_url": "https://github.com/org-b/repo-3/pull/3",
                    "repository_url": "https://api.github.com/repos/org-b/repo-3",
                },
            ],
        },
    }

    async def fake_run_gh(*args: str) -> str:
        query = _query_from_args(args)
        page = _page_from_args(args)
        return json.dumps(responses.get((query, page), {"items": []}))

    from agendum import gh

    monkeypatch.setattr(gh, "_run_gh", fake_run_gh)
    monkeypatch.setattr(gh, "_SEARCH_REPO_CHUNK_SIZE", 2)

    prs = await search_open_authored_prs_for_repos(
        ["org-a/repo-1", "org-a/repo-2", "org-b/repo-3", "org-b/repo-4"],
        "user",
    )

    assert [item["gh_node_id"] for item in prs] == ["PR_node_1", "PR_node_2", "PR_node_3"]


async def test_search_open_review_requested_prs_for_repos_uses_repo_qualifiers(monkeypatch) -> None:
    async def fake_run_gh(*args: str) -> str:
        assert _query_from_args(args) == (
            "is:open is:pr review-requested:reviewer "
            "repo:org/repo-1 repo:org/repo-2"
        )
        return json.dumps(
            {
                "items": [
                    {
                        "node_id": "PR_review_1",
                        "number": 41,
                        "title": "Review me",
                        "html_url": "https://github.com/org/repo-1/pull/41",
                        "repository_url": "https://api.github.com/repos/org/repo-1",
                    }
                ]
            }
        )

    from agendum import gh

    monkeypatch.setattr(gh, "_run_gh", fake_run_gh)

    prs = await search_open_review_requested_prs_for_repos(
        ["org/repo-1", "org/repo-2"],
        "reviewer",
    )

    assert prs == [
        {
            "gh_node_id": "PR_review_1",
            "number": 41,
            "title": "Review me",
            "url": "https://github.com/org/repo-1/pull/41",
            "repository": {"nameWithOwner": "org/repo-1"},
        }
    ]


async def test_hydrate_open_authored_prs_batches_explicitly_and_preserves_order(monkeypatch) -> None:
    calls: list[list[str]] = []

    async def fake_run_gh(*args: str) -> str:
        query_arg = next(arg for arg in args if arg.startswith("query="))
        ids_literal = query_arg.split("nodes(ids: ", 1)[1].split(")", 1)[0]
        node_ids = json.loads(ids_literal)
        calls.append(node_ids)
        return json.dumps(
            {
                "data": {
                    "nodes": [
                        {
                            "__typename": "PullRequest",
                            "id": node_id,
                            "number": index,
                            "title": f"PR {index}",
                            "url": f"https://github.com/org/repo/pull/{index}",
                            "state": "OPEN",
                            "isDraft": False,
                            "reviewDecision": None,
                            "repository": {
                                "nameWithOwner": "org/repo",
                                "isArchived": False,
                            },
                            "author": {"login": "author"},
                            "reviewRequests": {"totalCount": 0},
                            "commits": {"nodes": []},
                            "reviews": {"nodes": []},
                            "reviewThreads": {"nodes": []},
                        }
                        for index, node_id in enumerate(node_ids, start=1)
                    ]
                }
            }
        )

    from agendum import gh

    monkeypatch.setattr(gh, "_run_gh", fake_run_gh)

    hydrated = await hydrate_open_authored_prs(
        [
            {"gh_node_id": "PR_1"},
            {"gh_node_id": "PR_2"},
            {"gh_node_id": "PR_2"},
            {"gh_node_id": "PR_3"},
            {"gh_node_id": "PR_4"},
            {"gh_node_id": "PR_5"},
        ],
        batch_size=2,
    )

    assert calls == [["PR_1", "PR_2"], ["PR_3", "PR_4"], ["PR_5"]]
    assert [item["gh_node_id"] for item in hydrated] == ["PR_1", "PR_2", "PR_3", "PR_4", "PR_5"]


async def test_hydrate_open_issues_skips_non_issue_nodes(monkeypatch) -> None:
    async def fake_run_gh(*args: str) -> str:
        return json.dumps(
            {
                "data": {
                    "nodes": [
                        {
                            "__typename": "Issue",
                            "id": "I_1",
                            "number": 1,
                            "title": "Tracked issue",
                            "url": "https://github.com/org/repo/issues/1",
                            "state": "OPEN",
                            "repository": {
                                "nameWithOwner": "org/repo",
                                "isArchived": False,
                            },
                            "timelineItems": {"nodes": []},
                        },
                        {
                            "__typename": "PullRequest",
                            "id": "PR_2",
                            "number": 2,
                            "title": "Wrong type",
                            "url": "https://github.com/org/repo/pull/2",
                            "repository": {"nameWithOwner": "org/repo"},
                        },
                    ]
                }
            }
        )

    from agendum import gh

    monkeypatch.setattr(gh, "_run_gh", fake_run_gh)

    hydrated = await hydrate_open_issues(
        [
            {"gh_node_id": "I_1"},
            {"gh_node_id": "PR_2"},
        ]
    )

    assert hydrated == [
        {
            "gh_node_id": "I_1",
            "number": 1,
            "title": "Tracked issue",
            "url": "https://github.com/org/repo/issues/1",
            "repository": {"nameWithOwner": "org/repo", "isArchived": False},
            "state": "OPEN",
            "labels": {"nodes": []},
            "timelineItems": {"nodes": []},
        }
    ]


async def test_verify_missing_authored_prs_batches_node_ids_explicitly(monkeypatch) -> None:
    calls: list[list[str]] = []

    async def fake_run_gh(*args: str) -> str:
        query_arg = next(arg for arg in args if arg.startswith("query="))
        ids_literal = query_arg.split("nodes(ids: ", 1)[1].split(")", 1)[0]
        node_ids = json.loads(ids_literal)
        calls.append(node_ids)
        return json.dumps(
            {
                "data": {
                    "nodes": [
                        {
                            "__typename": "PullRequest",
                            "id": node_id,
                            "url": f"https://github.com/org/repo/pull/{index}",
                            "state": "CLOSED",
                        }
                        for index, node_id in enumerate(node_ids, start=1)
                    ]
                }
            }
        )

    from agendum import gh

    monkeypatch.setattr(gh, "_run_gh", fake_run_gh)

    verified = await verify_missing_authored_prs(
        [
            {"gh_node_id": "PR_1"},
            {"gh_node_id": "PR_2"},
            {"gh_node_id": "PR_2"},
            {"gh_node_id": "PR_3"},
            {"gh_node_id": "PR_4"},
        ],
        batch_size=2,
    )

    assert calls == [["PR_1", "PR_2"], ["PR_3", "PR_4"]]
    assert [item["gh_node_id"] for item in verified] == ["PR_1", "PR_2", "PR_3", "PR_4"]


async def test_verify_missing_issues_uses_url_fallback_for_legacy_rows(monkeypatch) -> None:
    calls: list[tuple[str, ...]] = []

    async def fake_run_gh(*args: str) -> str:
        calls.append(args)
        return json.dumps(
            {
                "data": {
                    "repository": {
                        "issue": {
                            "id": "I_legacy",
                            "url": "https://github.com/org/repo/issues/7",
                            "state": "OPEN",
                            "assignees": {"nodes": [{"login": "current-user"}]},
                        }
                    }
                }
            }
        )

    from agendum import gh

    monkeypatch.setattr(gh, "_run_gh", fake_run_gh)

    verified = await verify_missing_issues(
        [{"gh_url": "https://github.com/org/repo/issues/7"}],
        gh_user="current-user",
    )

    assert verified == [
        {
            "gh_node_id": "I_legacy",
            "gh_url": "https://github.com/org/repo/issues/7",
            "state": "OPEN",
            "is_assigned_to_user": True,
        }
    ]
    query_arg = next(arg for arg in calls[0] if arg.startswith("query="))
    assert "VerifyMissingIssueByUrl" in query_arg
    assert "issue(number: 7)" in query_arg


async def test_verify_missing_review_prs_uses_url_fallback_for_legacy_rows(monkeypatch) -> None:
    calls: list[tuple[str, ...]] = []

    async def fake_run_gh(*args: str) -> str:
        calls.append(args)
        return json.dumps(
            {
                "data": {
                    "repository": {
                        "pullRequest": {
                            "id": "PR_legacy",
                            "url": "https://github.com/org/repo/pull/9",
                            "state": "OPEN",
                            "reviewRequests": {
                                "nodes": [
                                    {"requestedReviewer": {"login": "current-user"}},
                                ]
                            },
                        }
                    }
                }
            }
        )

    from agendum import gh

    monkeypatch.setattr(gh, "_run_gh", fake_run_gh)

    verified = await verify_missing_review_prs(
        [{"gh_url": "https://github.com/org/repo/pull/9"}],
        gh_user="current-user",
    )

    assert verified == [
        {
            "gh_node_id": "PR_legacy",
            "gh_url": "https://github.com/org/repo/pull/9",
            "state": "OPEN",
            "is_review_requested": True,
        }
    ]
    query_arg = next(arg for arg in calls[0] if arg.startswith("query="))
    assert "VerifyMissingReviewPRByUrl" in query_arg
    assert "pullRequest(number: 9)" in query_arg

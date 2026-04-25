"""Tests for gh module functions that interact with the gh CLI."""

import json
from typing import Any

import pytest

from agendum.gh import (
    fetch_notifications,
    hydrate_issues,
    hydrate_pull_requests,
    search_assigned_issues,
    search_authored_prs,
    search_review_requested_prs,
)


async def test_fetch_notifications_empty_response(monkeypatch) -> None:
    async def fake_run_gh(*args: str) -> str:
        return ""

    from agendum import gh
    monkeypatch.setattr(gh, "_run_gh", fake_run_gh)

    assert await fetch_notifications("user") == ([], False)


async def test_fetch_notifications_invalid_json(monkeypatch) -> None:
    async def fake_run_gh(*args: str) -> str:
        return "not json"

    from agendum import gh
    monkeypatch.setattr(gh, "_run_gh", fake_run_gh)

    assert await fetch_notifications("user") == ([], False)


async def test_fetch_notifications_forwards_since(monkeypatch) -> None:
    call_log: list[tuple[str, ...]] = []

    async def fake_run_gh(*args: str) -> str:
        call_log.append(args)
        return "[]"

    from agendum import gh
    monkeypatch.setattr(gh, "_run_gh", fake_run_gh)

    notifications, ok = await fetch_notifications(
        "user",
        since="2026-04-24T12:00:00+00:00",
    )

    assert notifications == []
    assert ok is True
    assert call_log == [
        (
            "api",
            "notifications",
            "--method",
            "GET",
            "-f",
            "all=false",
            "-f",
            "since=2026-04-24T12:00:00+00:00",
        ),
    ]


def _search_payload(
    nodes: list[dict[str, Any]],
    *,
    has_next_page: bool = False,
    end_cursor: str | None = None,
) -> str:
    return json.dumps(
        {
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
    )


def _query_value(args: tuple[str, ...], name: str) -> str | None:
    for index, arg in enumerate(args):
        if arg == "-F" and index + 1 < len(args) and args[index + 1].startswith(f"{name}="):
            return args[index + 1]
    return None


async def test_search_authored_prs_paginates_and_dedupes(monkeypatch) -> None:
    calls: list[tuple[str, ...]] = []

    async def fake_run_gh(*args: str) -> str:
        calls.append(args)
        search_query_value = _query_value(args, "searchQuery")
        after_value = _query_value(args, "after")

        if search_query_value == "searchQuery=org:org-a is:open is:pr author:user" and after_value is None:
            return _search_payload(
                [{"id": "PR_1", "title": "one"}, {"id": "PR_2", "title": "two"}],
                has_next_page=True,
                end_cursor="cursor-a",
            )
        if search_query_value == "searchQuery=org:org-a is:open is:pr author:user" and after_value == "after=cursor-a":
            return _search_payload(
                [{"id": "PR_2", "title": "dup"}, {"id": "PR_3", "title": "three"}]
            )
        if search_query_value == "searchQuery=org:org-b is:open is:pr author:user":
            return _search_payload(
                [{"id": "PR_3", "title": "dup"}, {"id": "PR_4", "title": "four"}]
            )
        raise AssertionError(f"Unexpected gh call: {args}")

    from agendum import gh

    monkeypatch.setattr(gh, "_run_gh", fake_run_gh)

    items, ok = await search_authored_prs(["org-a", "org-b"], "user", page_size=2)

    assert ok is True
    assert [item["id"] for item in items] == ["PR_1", "PR_2", "PR_3", "PR_4"]
    query_arg = next(arg for arg in calls[0] if arg.startswith("query="))
    assert "search(type: ISSUE" in query_arg
    assert _query_value(calls[0], "searchQuery") == "searchQuery=org:org-a is:open is:pr author:user"


async def test_search_assigned_issues_marks_invalid_page_incomplete(monkeypatch) -> None:
    async def fake_run_gh(*args: str) -> str:
        search_query_value = _query_value(args, "searchQuery")
        if search_query_value == "searchQuery=org:org-a is:open is:issue assignee:user":
            return _search_payload([{"id": "ISSUE_1", "title": "one"}])
        if search_query_value == "searchQuery=org:org-b is:open is:issue assignee:user":
            return "not json"
        raise AssertionError(f"Unexpected gh call: {args}")

    from agendum import gh

    monkeypatch.setattr(gh, "_run_gh", fake_run_gh)

    items, ok = await search_assigned_issues(["org-a", "org-b"], "user")

    assert ok is False
    assert [item["id"] for item in items] == ["ISSUE_1"]


async def test_search_review_requested_prs_marks_empty_page_incomplete(monkeypatch) -> None:
    async def fake_run_gh(*args: str) -> str:
        return ""

    from agendum import gh

    monkeypatch.setattr(gh, "_run_gh", fake_run_gh)

    items, ok = await search_review_requested_prs(["org"], "reviewer")

    assert items == []
    assert ok is False


@pytest.mark.parametrize(
    ("func", "node_ids"),
    [
        (hydrate_pull_requests, ["PR_1", "PR_2"]),
        (hydrate_issues, ["ISSUE_1", "ISSUE_2"]),
    ],
)
async def test_hydration_primitives_return_not_ok_on_invalid_json(
    monkeypatch,
    func,
    node_ids: list[str],
) -> None:
    async def fake_run_gh(*args: str) -> str:
        return "not json"

    from agendum import gh

    monkeypatch.setattr(gh, "_run_gh", fake_run_gh)

    items, ok = await func(node_ids, batch_size=1)

    assert items == []
    assert ok is False


async def test_hydrate_pull_requests_batches_and_skips_null_nodes(monkeypatch) -> None:
    calls: list[tuple[str, ...]] = []

    async def fake_run_gh(*args: str) -> str:
        calls.append(args)
        query_arg = next(arg for arg in args if arg.startswith("query="))
        if "PR_1" in query_arg:
            return json.dumps(
                {
                    "data": {
                        "n0": {"id": "PR_1", "title": "one"},
                        "n1": None,
                    },
                }
            )
        return json.dumps({"data": {"n0": {"id": "PR_3", "title": "three"}}})

    from agendum import gh

    monkeypatch.setattr(gh, "_run_gh", fake_run_gh)

    items, ok = await hydrate_pull_requests(["PR_1", "PR_2", "PR_3"], batch_size=2)

    assert ok is True
    assert [item["id"] for item in items] == ["PR_1", "PR_3"]
    assert len(calls) == 2


async def test_hydrate_issues_returns_not_ok_on_failed_batch_after_partial_success(monkeypatch) -> None:
    calls = 0

    async def fake_run_gh(*args: str) -> str:
        nonlocal calls
        calls += 1
        if calls == 1:
            return json.dumps({"data": {"n0": {"id": "ISSUE_1", "number": 1}}})
        return ""

    from agendum import gh

    monkeypatch.setattr(gh, "_run_gh", fake_run_gh)

    items, ok = await hydrate_issues(["ISSUE_1", "ISSUE_2"], batch_size=1)

    assert [item["id"] for item in items] == ["ISSUE_1"]
    assert ok is False

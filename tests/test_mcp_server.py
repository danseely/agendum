from types import SimpleNamespace

import pytest

from agendum import mcp_server


def test_list_tasks_delegates_to_task_api(monkeypatch) -> None:
    calls = []

    def fake_task_api() -> SimpleNamespace:
        return SimpleNamespace(
            list_tasks=lambda db_path, **kwargs: calls.append((db_path, kwargs)) or [{"id": 1}],
        )

    monkeypatch.setattr(mcp_server, "_task_api", fake_task_api)

    result = mcp_server.list_tasks(source="pr_review", status="review requested", limit=25)

    assert result == [{"id": 1}]
    assert calls[0][1] == {
        "source": "pr_review",
        "status": "review requested",
        "project": None,
        "include_seen": True,
        "limit": 25,
    }


def test_search_tasks_requires_query(monkeypatch) -> None:
    with pytest.raises(ValueError, match="query must not be empty"):
        mcp_server.search_tasks("")


def test_search_tasks_delegates_to_task_api(monkeypatch) -> None:
    calls = []

    def fake_task_api() -> SimpleNamespace:
        return SimpleNamespace(
            search_tasks=lambda db_path, **kwargs: calls.append((db_path, kwargs)) or [{"id": 2}],
        )

    monkeypatch.setattr(mcp_server, "_task_api", fake_task_api)

    result = mcp_server.search_tasks("api", source="pr_authored", limit=10)

    assert result == [{"id": 2}]
    assert calls[0][1] == {
        "query": "api",
        "source": "pr_authored",
        "status": None,
        "project": None,
        "limit": 10,
    }


def test_get_task_delegates_to_task_api(monkeypatch) -> None:
    calls = []

    def fake_task_api() -> SimpleNamespace:
        return SimpleNamespace(
            get_task=lambda db_path, task_id: calls.append((db_path, task_id)) or {"id": task_id},
        )

    monkeypatch.setattr(mcp_server, "_task_api", fake_task_api)

    assert mcp_server.get_task(42) == {"id": 42}
    assert calls[0][1] == 42


def test_create_task_delegates_to_task_api(monkeypatch) -> None:
    calls = []

    def fake_task_api() -> SimpleNamespace:
        return SimpleNamespace(
            create_manual_task=lambda db_path, **kwargs: calls.append((db_path, kwargs)) or {"id": 9},
        )

    monkeypatch.setattr(mcp_server, "_task_api", fake_task_api)

    result = mcp_server.create_task("  follow up  ", project="api", tags=["triage"])

    assert result == {"id": 9}
    assert calls[0][1] == {
        "title": "follow up",
        "project": "api",
        "tags": ["triage"],
    }


@pytest.mark.asyncio
async def test_get_pr_review_status_uses_task_id(monkeypatch) -> None:
    task_calls = []
    review_calls = []

    def fake_task_api() -> SimpleNamespace:
        return SimpleNamespace(
            get_task=lambda db_path, task_id: task_calls.append((db_path, task_id)) or {"gh_url": "https://github.com/acme/repo/pull/12"},
        )

    async def fake_get_pr_review_status(*, url, reviewer=None):
        review_calls.append((url, reviewer))
        return {"url": url}

    def fake_gh_review() -> SimpleNamespace:
        return SimpleNamespace(
            get_pr_review_status=fake_get_pr_review_status,
        )

    monkeypatch.setattr(mcp_server, "_task_api", fake_task_api)
    monkeypatch.setattr(mcp_server, "_gh_review", fake_gh_review)

    result = await mcp_server.get_pr_review_status(task_id=12, reviewer="alex")

    assert result == {"url": "https://github.com/acme/repo/pull/12"}
    assert task_calls[0][1] == 12
    assert review_calls[0] == ("https://github.com/acme/repo/pull/12", "alex")


@pytest.mark.asyncio
async def test_get_pr_review_status_requires_task_id_or_url() -> None:
    with pytest.raises(ValueError, match="provide either task_id or url"):
        await mcp_server.get_pr_review_status()


def test_main_runs_stdio(monkeypatch) -> None:
    calls = []
    monkeypatch.setattr(mcp_server.mcp, "run", lambda transport="stdio": calls.append(transport))

    mcp_server.main()

    assert calls == ["stdio"]


def test_main_initializes_database_before_serving_tools(monkeypatch, tmp_path) -> None:
    db_path = tmp_path / ".agendum" / "agendum.db"
    served_results = []

    def fake_run(transport="stdio"):
        served_results.append(mcp_server.list_tasks())

    monkeypatch.setattr(mcp_server, "DB_PATH", db_path)
    monkeypatch.setattr(mcp_server.mcp, "run", fake_run)

    mcp_server.main()

    assert served_results == [[]]
    assert db_path.exists()

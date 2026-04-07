"""MCP server exposing agendum task and review actions."""

from __future__ import annotations

import importlib
from types import ModuleType
from typing import Any

from agendum.config import DB_PATH
from agendum.db import init_db

try:
    from mcp.server.fastmcp import FastMCP
except ModuleNotFoundError:  # pragma: no cover - fallback for local test envs
    class FastMCP:  # type: ignore[no-redef]
        def __init__(self, name: str) -> None:
            self.name = name

        def tool(self, name: str | None = None):
            def decorator(func):
                return func

            return decorator

        def run(self, transport: str = "stdio") -> None:
            raise RuntimeError("mcp package is required to run agendum-mcp")


mcp = FastMCP("agendum")


def _task_api() -> ModuleType:
    return importlib.import_module("agendum.task_api")


def _gh_review() -> ModuleType:
    return importlib.import_module("agendum.gh_review")


def _initialize_storage() -> None:
    init_db(DB_PATH)


def _task_or_error(task_id: int) -> dict[str, Any]:
    task = _task_api().get_task(DB_PATH, task_id)
    if task is None:
        raise ValueError(f"task {task_id} not found")
    return task


def _resolve_pr_url(*, task_id: int | None, url: str | None) -> str:
    if url:
        return url
    if task_id is None:
        raise ValueError("provide either task_id or url")
    task = _task_or_error(task_id)
    pr_url = task.get("gh_url")
    if not pr_url:
        raise ValueError(f"task {task_id} does not have a GitHub PR URL")
    return pr_url


def _create_task(
    title: str,
    *,
    project: str | None = None,
    tags: list[str] | None = None,
) -> dict[str, Any]:
    if not title or not title.strip():
        raise ValueError("title must not be empty")
    return _task_api().create_manual_task(
        DB_PATH,
        title=title.strip(),
        project=project,
        tags=tags,
    )


def _list_tasks(
    *,
    source: str | None = None,
    status: str | None = None,
    project: str | None = None,
    include_seen: bool = True,
    limit: int = 50,
) -> list[dict[str, Any]]:
    if limit < 1 or limit > 200:
        raise ValueError("limit must be between 1 and 200")
    return _task_api().list_tasks(
        DB_PATH,
        source=source,
        status=status,
        project=project,
        include_seen=include_seen,
        limit=limit,
    )


def _search_tasks(
    query: str,
    *,
    source: str | None = None,
    status: str | None = None,
    project: str | None = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    if not query or not query.strip():
        raise ValueError("query must not be empty")
    if limit < 1 or limit > 200:
        raise ValueError("limit must be between 1 and 200")
    return _task_api().search_tasks(
        DB_PATH,
        query=query,
        source=source,
        status=status,
        project=project,
        limit=limit,
    )


def _get_task(task_id: int) -> dict[str, Any] | None:
    return _task_api().get_task(DB_PATH, task_id)


async def _get_pr_review_status(
    *,
    task_id: int | None = None,
    url: str | None = None,
    reviewer: str | None = None,
) -> dict[str, Any]:
    pr_url = _resolve_pr_url(task_id=task_id, url=url)
    return await _gh_review().get_pr_review_status(url=pr_url, reviewer=reviewer)


@mcp.tool()
def list_tasks(
    source: str | None = None,
    status: str | None = None,
    project: str | None = None,
    include_seen: bool = True,
    limit: int = 50,
) -> list[dict[str, Any]]:
    return _list_tasks(
        source=source,
        status=status,
        project=project,
        include_seen=include_seen,
        limit=limit,
    )


@mcp.tool()
def search_tasks(
    query: str,
    source: str | None = None,
    status: str | None = None,
    project: str | None = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    return _search_tasks(
        query,
        source=source,
        status=status,
        project=project,
        limit=limit,
    )


@mcp.tool()
def get_task(task_id: int) -> dict[str, Any] | None:
    return _get_task(task_id)


@mcp.tool()
def create_task(
    title: str,
    project: str | None = None,
    tags: list[str] | None = None,
) -> dict[str, Any]:
    return _create_task(title, project=project, tags=tags)


@mcp.tool()
async def get_pr_review_status(
    task_id: int | None = None,
    url: str | None = None,
    reviewer: str | None = None,
) -> dict[str, Any]:
    return await _get_pr_review_status(task_id=task_id, url=url, reviewer=reviewer)


def main() -> None:
    _initialize_storage()
    mcp.run(transport="stdio")

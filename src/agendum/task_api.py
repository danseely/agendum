"""Helpers for reading and writing agendum tasks outside the TUI."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from agendum.db import add_task, get_active_tasks

_MAX_LIMIT = 200


def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _validate_limit(limit: int) -> int:
    if not isinstance(limit, int):
        raise TypeError("limit must be an integer")
    if limit <= 0:
        raise ValueError("limit must be greater than zero")
    if limit > _MAX_LIMIT:
        raise ValueError(f"limit must be <= {_MAX_LIMIT}")
    return limit


def _normalize_tags(tags: object) -> list[str]:
    if tags is None:
        return []
    if isinstance(tags, list):
        return [str(tag) for tag in tags]
    if isinstance(tags, str):
        try:
            loaded = json.loads(tags)
        except json.JSONDecodeError:
            return [tags]
        if isinstance(loaded, list):
            return [str(tag) for tag in loaded]
        return [str(loaded)]
    return [str(tags)]


def _normalize_task(task: dict) -> dict:
    normalized = dict(task)
    normalized["tags"] = _normalize_tags(normalized.get("tags"))
    return normalized


def _task_haystack(task: dict) -> str:
    parts: list[str] = []
    for key in ("title", "project", "gh_repo", "gh_url", "gh_author", "gh_author_name"):
        value = task.get(key)
        if value:
            parts.append(str(value))
    tags = task.get("tags")
    if tags:
        if isinstance(tags, list):
            parts.extend(str(tag) for tag in tags)
        else:
            parts.append(str(tags))
    return " ".join(parts).casefold()


def _apply_filters(
    tasks: list[dict],
    *,
    source: str | None = None,
    status: str | None = None,
    project: str | None = None,
    include_seen: bool = True,
) -> list[dict]:
    filtered: list[dict] = []
    for task in tasks:
        if source is not None and task.get("source") != source:
            continue
        if status is not None and task.get("status") != status:
            continue
        if project is not None and task.get("project") != project:
            continue
        if not include_seen and task.get("seen", 1):
            continue
        filtered.append(_normalize_task(task))
    return filtered


def list_tasks(
    db_path: Path,
    *,
    source: str | None = None,
    status: str | None = None,
    project: str | None = None,
    include_seen: bool = True,
    limit: int = 50,
) -> list[dict]:
    limit = _validate_limit(limit)
    tasks = _apply_filters(
        get_active_tasks(db_path),
        source=source,
        status=status,
        project=project,
        include_seen=include_seen,
    )
    return tasks[:limit]


def search_tasks(
    db_path: Path,
    *,
    query: str,
    source: str | None = None,
    status: str | None = None,
    project: str | None = None,
    limit: int = 20,
) -> list[dict]:
    limit = _validate_limit(limit)
    tokens = [token.casefold() for token in query.split() if token.strip()]
    if not tokens:
        raise ValueError("query must not be empty")

    candidates = _apply_filters(
        get_active_tasks(db_path),
        source=source,
        status=status,
        project=project,
        include_seen=True,
    )
    matches: list[dict] = []
    for task in candidates:
        haystack = _task_haystack(task)
        if all(token in haystack for token in tokens):
            matches.append(task)
        if len(matches) >= limit:
            break
    return matches


def get_task(db_path: Path, task_id: int) -> dict | None:
    if task_id <= 0:
        raise ValueError("task_id must be greater than zero")
    conn = _connect(db_path)
    row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    conn.close()
    return _normalize_task(dict(row)) if row else None


def create_manual_task(
    db_path: Path,
    *,
    title: str,
    project: str | None = None,
    tags: list[str] | None = None,
) -> dict:
    title = title.strip()
    if not title:
        raise ValueError("title must not be empty")

    tags_json = json.dumps(tags) if tags else None
    task_id = add_task(
        db_path,
        title=title,
        source="manual",
        status="backlog",
        project=project,
        tags=tags_json,
    )
    task = get_task(db_path, task_id)
    if task is None:
        raise RuntimeError("created task could not be loaded")
    return task

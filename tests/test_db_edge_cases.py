"""Edge-case tests for the database layer."""

import sqlite3
from pathlib import Path

import pytest

from agendum.db import (
    add_task,
    find_task_by_gh_url,
    find_tasks_by_gh_node_ids,
    get_active_tasks,
    init_db,
    mark_all_seen,
    update_task,
)


def test_mark_all_seen(tmp_db: Path) -> None:
    init_db(tmp_db)
    id1 = add_task(tmp_db, title="Unseen 1", source="manual", status="backlog")
    id2 = add_task(tmp_db, title="Unseen 2", source="manual", status="backlog")
    update_task(tmp_db, id1, seen=0)
    update_task(tmp_db, id2, seen=0)

    mark_all_seen(tmp_db)

    tasks = get_active_tasks(tmp_db)
    assert all(t["seen"] == 1 for t in tasks)


def test_mark_all_seen_sets_last_seen_at(tmp_db: Path) -> None:
    init_db(tmp_db)
    task_id = add_task(tmp_db, title="Check timestamp", source="manual", status="backlog")
    update_task(tmp_db, task_id, seen=0)

    mark_all_seen(tmp_db)

    tasks = get_active_tasks(tmp_db)
    assert tasks[0]["last_seen_at"] is not None


def test_mark_all_seen_noop_when_all_seen(tmp_db: Path) -> None:
    init_db(tmp_db)
    add_task(tmp_db, title="Already seen", source="manual", status="backlog")
    # Should not raise
    mark_all_seen(tmp_db)
    tasks = get_active_tasks(tmp_db)
    assert tasks[0]["seen"] == 1


def test_update_task_rejects_invalid_columns(tmp_db: Path) -> None:
    init_db(tmp_db)
    task_id = add_task(tmp_db, title="Bad update", source="manual", status="backlog")
    with pytest.raises(ValueError, match="Invalid column names"):
        update_task(tmp_db, task_id, evil_column="drop table")


def test_update_task_noop_with_no_fields(tmp_db: Path) -> None:
    init_db(tmp_db)
    task_id = add_task(tmp_db, title="Noop", source="manual", status="backlog")
    # Should not raise or execute SQL
    update_task(tmp_db, task_id)
    tasks = get_active_tasks(tmp_db)
    assert tasks[0]["title"] == "Noop"


def test_get_active_tasks_ordering(tmp_db: Path) -> None:
    """Unseen tasks sort before seen tasks within the same source."""
    init_db(tmp_db)
    id1 = add_task(tmp_db, title="Seen PR", source="pr_authored", status="open",
                   gh_url="https://github.com/org/repo/pull/1")
    id2 = add_task(tmp_db, title="Unseen PR", source="pr_authored", status="open",
                   gh_url="https://github.com/org/repo/pull/2")
    update_task(tmp_db, id2, seen=0)

    tasks = get_active_tasks(tmp_db)
    assert tasks[0]["title"] == "Unseen PR"
    assert tasks[1]["title"] == "Seen PR"


def test_url_only_historical_rows_still_work_without_gh_node_id(tmp_db: Path) -> None:
    init_db(tmp_db)
    url = "https://github.com/org/repo/pull/33"
    add_task(
        tmp_db,
        title="Historical URL-only PR",
        source="pr_authored",
        status="open",
        gh_url=url,
    )

    task = find_task_by_gh_url(tmp_db, url)
    by_node_id = find_tasks_by_gh_node_ids(tmp_db, ["PR_kwDO_missing"])

    assert task is not None
    assert task["gh_node_id"] is None
    assert by_node_id == {}


def test_rows_can_gain_gh_node_id_after_initial_insert(tmp_db: Path) -> None:
    init_db(tmp_db)
    url = "https://github.com/org/repo/issues/21"
    task_id = add_task(
        tmp_db,
        title="Legacy issue",
        source="issue",
        status="open",
        gh_url=url,
    )

    update_task(tmp_db, task_id, gh_node_id="I_kwDOExample21")

    task_by_url = find_task_by_gh_url(tmp_db, url)
    tasks_by_node = find_tasks_by_gh_node_ids(tmp_db, ["I_kwDOExample21"])

    assert task_by_url is not None
    assert task_by_url["gh_node_id"] == "I_kwDOExample21"
    assert tasks_by_node["I_kwDOExample21"]["id"] == task_id


def test_init_db_backfills_gh_node_id_column_idempotently_on_legacy_db(tmp_db: Path) -> None:
    conn = sqlite3.connect(tmp_db)
    conn.execute(
        """CREATE TABLE tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            source TEXT NOT NULL,
            status TEXT NOT NULL,
            gh_url TEXT UNIQUE,
            last_changed_at TEXT
        )"""
    )
    conn.commit()
    conn.close()

    init_db(tmp_db)
    init_db(tmp_db)

    conn = sqlite3.connect(tmp_db)
    conn.row_factory = sqlite3.Row
    columns = {
        row["name"]
        for row in conn.execute("PRAGMA table_info(tasks)").fetchall()
    }
    indexes = {
        row["name"]
        for row in conn.execute("PRAGMA index_list(tasks)").fetchall()
    }
    conn.close()

    assert "gh_node_id" in columns
    assert "idx_tasks_gh_node_id" in indexes

"""Edge-case tests for the database layer."""

from pathlib import Path

import pytest

from agendum.db import add_task, get_active_tasks, init_db, mark_all_seen, update_task


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

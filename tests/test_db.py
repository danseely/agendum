import sqlite3
from pathlib import Path
from agendum.db import init_db, add_task, get_active_tasks, update_task, remove_task


def test_init_db_creates_tables(tmp_db: Path) -> None:
    init_db(tmp_db)
    conn = sqlite3.connect(tmp_db)
    cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = {row[0] for row in cursor.fetchall()}
    conn.close()
    assert "tasks" in tables


def test_init_db_is_idempotent(tmp_db: Path) -> None:
    init_db(tmp_db)
    init_db(tmp_db)  # Should not raise


def test_add_manual_task(tmp_db: Path) -> None:
    init_db(tmp_db)
    task_id = add_task(tmp_db, title="Write docs", source="manual", status="backlog")
    assert isinstance(task_id, int)
    tasks = get_active_tasks(tmp_db)
    assert len(tasks) == 1
    assert tasks[0]["title"] == "Write docs"
    assert tasks[0]["source"] == "manual"
    assert tasks[0]["status"] == "backlog"
    assert tasks[0]["seen"] == 1


def test_add_github_task(tmp_db: Path) -> None:
    init_db(tmp_db)
    task_id = add_task(
        tmp_db,
        title="fix: heartbeat failures",
        source="pr_authored",
        status="awaiting review",
        project="example-repo",
        gh_repo="example-org/example-repo",
        gh_url="https://github.com/example-org/example-repo/pull/412",
        gh_number=412,
        tags='["bugfix"]',
    )
    tasks = get_active_tasks(tmp_db)
    assert len(tasks) == 1
    assert tasks[0]["gh_number"] == 412
    assert tasks[0]["project"] == "example-repo"


def test_update_task(tmp_db: Path) -> None:
    init_db(tmp_db)
    task_id = add_task(tmp_db, title="Test", source="manual", status="backlog")
    update_task(tmp_db, task_id, status="done", seen=0)
    conn = sqlite3.connect(tmp_db)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT status, seen FROM tasks WHERE id = ?", (task_id,)).fetchone()
    conn.close()
    assert row["status"] == "done"
    assert row["seen"] == 0


def test_get_active_tasks_excludes_terminal(tmp_db: Path) -> None:
    init_db(tmp_db)
    add_task(tmp_db, title="Open", source="manual", status="backlog")
    add_task(tmp_db, title="Done", source="manual", status="done")
    add_task(tmp_db, title="Merged", source="pr_authored", status="merged")
    add_task(tmp_db, title="Closed", source="issue", status="closed")
    tasks = get_active_tasks(tmp_db)
    assert len(tasks) == 1
    assert tasks[0]["title"] == "Open"


def test_remove_task(tmp_db: Path) -> None:
    init_db(tmp_db)
    task_id = add_task(tmp_db, title="Delete me", source="manual", status="backlog")
    remove_task(tmp_db, task_id)
    tasks = get_active_tasks(tmp_db)
    assert len(tasks) == 0


def test_init_db_migrates_active_to_backlog(tmp_db: Path) -> None:
    init_db(tmp_db)
    conn = sqlite3.connect(tmp_db)
    conn.execute(
        """INSERT INTO tasks (title, source, status, last_changed_at)
           VALUES (?, ?, ?, datetime('now'))""",
        ("Legacy", "manual", "active"),
    )
    conn.commit()
    conn.close()

    init_db(tmp_db)

    tasks = get_active_tasks(tmp_db)
    assert len(tasks) == 1
    assert tasks[0]["status"] == "backlog"


def test_gh_url_unique_constraint(tmp_db: Path) -> None:
    init_db(tmp_db)
    url = "https://github.com/org/repo/pull/1"
    add_task(tmp_db, title="PR 1", source="pr_authored", status="open", gh_url=url)
    import pytest
    with pytest.raises(sqlite3.IntegrityError):
        add_task(tmp_db, title="PR 1 dup", source="pr_authored", status="open", gh_url=url)


def test_find_task_by_gh_url(tmp_db: Path) -> None:
    from agendum.db import find_task_by_gh_url
    init_db(tmp_db)
    url = "https://github.com/org/repo/pull/1"
    add_task(tmp_db, title="PR 1", source="pr_authored", status="open", gh_url=url)
    task = find_task_by_gh_url(tmp_db, url)
    assert task is not None
    assert task["title"] == "PR 1"
    assert find_task_by_gh_url(tmp_db, "https://nonexistent") is None

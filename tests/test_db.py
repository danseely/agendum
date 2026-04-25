import sqlite3
from pathlib import Path
from agendum.db import (
    add_task,
    find_task_by_gh_url,
    find_tasks_by_gh_node_ids,
    get_active_tasks,
    init_db,
    remove_task,
    update_task,
)


LEGACY_SCHEMA = """
CREATE TABLE tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    source TEXT NOT NULL,
    status TEXT NOT NULL,
    project TEXT,
    gh_repo TEXT,
    gh_url TEXT UNIQUE,
    gh_number INTEGER,
    gh_author TEXT,
    gh_author_name TEXT,
    tags TEXT,
    seen INTEGER DEFAULT 1,
    last_changed_at TEXT,
    last_seen_at TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);
"""


def test_init_db_creates_tables(tmp_db: Path) -> None:
    init_db(tmp_db)
    conn = sqlite3.connect(tmp_db)
    conn.row_factory = sqlite3.Row
    cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = {row[0] for row in cursor.fetchall()}
    columns = {
        row["name"]
        for row in conn.execute("PRAGMA table_info(tasks)").fetchall()
    }
    conn.close()
    assert "tasks" in tables
    assert "gh_node_id" in columns


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
        gh_node_id="PR_kwDOExample412",
        gh_number=412,
        tags='["bugfix"]',
    )
    tasks = get_active_tasks(tmp_db)
    assert len(tasks) == 1
    assert tasks[0]["gh_number"] == 412
    assert tasks[0]["project"] == "example-repo"
    assert tasks[0]["gh_node_id"] == "PR_kwDOExample412"


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
    init_db(tmp_db)
    url = "https://github.com/org/repo/pull/1"
    add_task(tmp_db, title="PR 1", source="pr_authored", status="open", gh_url=url)
    task = find_task_by_gh_url(tmp_db, url)
    assert task is not None
    assert task["title"] == "PR 1"
    assert find_task_by_gh_url(tmp_db, "https://nonexistent") is None


def test_init_db_migrates_populated_db_to_add_gh_node_id(tmp_db: Path) -> None:
    conn = sqlite3.connect(tmp_db)
    conn.executescript(LEGACY_SCHEMA)
    conn.execute(
        """INSERT INTO tasks (title, source, status, gh_url, last_changed_at)
           VALUES (?, ?, ?, ?, datetime('now'))""",
        ("Legacy PR", "pr_authored", "open", "https://github.com/org/repo/pull/7"),
    )
    conn.commit()
    conn.close()

    init_db(tmp_db)

    conn = sqlite3.connect(tmp_db)
    conn.row_factory = sqlite3.Row
    columns = {
        row["name"]
        for row in conn.execute("PRAGMA table_info(tasks)").fetchall()
    }
    row = conn.execute(
        "SELECT title, gh_url, gh_node_id FROM tasks WHERE gh_url = ?",
        ("https://github.com/org/repo/pull/7",),
    ).fetchone()
    conn.close()

    assert "gh_node_id" in columns
    assert row is not None
    assert row["title"] == "Legacy PR"
    assert row["gh_node_id"] is None


def test_find_tasks_by_gh_node_ids_returns_matches(tmp_db: Path) -> None:
    init_db(tmp_db)
    add_task(
        tmp_db,
        title="Tracked PR",
        source="pr_authored",
        status="open",
        gh_url="https://github.com/org/repo/pull/9",
        gh_node_id="PR_kwDOExample9",
    )
    add_task(
        tmp_db,
        title="Tracked issue",
        source="issue",
        status="open",
        gh_url="https://github.com/org/repo/issues/4",
        gh_node_id="I_kwDOExample4",
    )

    tasks = find_tasks_by_gh_node_ids(
        tmp_db,
        ["PR_kwDOExample9", "I_kwDOExample4", "missing"],
    )

    assert set(tasks) == {"PR_kwDOExample9", "I_kwDOExample4"}
    assert tasks["PR_kwDOExample9"]["title"] == "Tracked PR"
    assert tasks["I_kwDOExample4"]["source"] == "issue"

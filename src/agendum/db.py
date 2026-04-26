import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

TERMINAL_STATUSES = {"merged", "closed", "done"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    source TEXT NOT NULL,
    status TEXT NOT NULL,
    project TEXT,
    gh_repo TEXT,
    gh_url TEXT UNIQUE,
    gh_node_id TEXT,
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


def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _task_columns(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute("PRAGMA table_info(tasks)").fetchall()
    return {row["name"] for row in rows}


def _ensure_task_column(
    conn: sqlite3.Connection,
    *,
    name: str,
    definition: str,
) -> None:
    if name in _task_columns(conn):
        return
    conn.execute(f"ALTER TABLE tasks ADD COLUMN {name} {definition}")


def _ensure_indexes(conn: sqlite3.Connection) -> None:
    conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_source ON tasks(source)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status)")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_tasks_gh_url ON tasks(gh_url) "
        "WHERE gh_url IS NOT NULL"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_tasks_gh_node_id ON tasks(gh_node_id) "
        "WHERE gh_node_id IS NOT NULL"
    )


def init_db(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    conn = _connect(db_path)
    conn.executescript(SCHEMA)
    _ensure_task_column(conn, name="gh_node_id", definition="TEXT")
    _ensure_indexes(conn)
    # Migrate legacy manual-task status "active" → "backlog".
    conn.execute("UPDATE tasks SET status='backlog' WHERE status='active'")
    conn.commit()
    conn.close()
    os.chmod(db_path, 0o600)


def add_task(
    db_path: Path,
    *,
    title: str,
    source: str,
    status: str,
    project: str | None = None,
    gh_repo: str | None = None,
    gh_url: str | None = None,
    gh_node_id: str | None = None,
    gh_number: int | None = None,
    gh_author: str | None = None,
    gh_author_name: str | None = None,
    tags: str | None = None,
) -> int:
    now = datetime.now(timezone.utc).isoformat()
    conn = _connect(db_path)
    cursor = conn.execute(
        """INSERT INTO tasks
           (title, source, status, project, gh_repo, gh_url, gh_node_id, gh_number,
            gh_author, gh_author_name, tags, last_changed_at, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (title, source, status, project, gh_repo, gh_url, gh_node_id, gh_number,
         gh_author, gh_author_name, tags, now, now, now),
    )
    conn.commit()
    task_id = cursor.lastrowid
    conn.close()
    return task_id


def get_active_tasks(db_path: Path) -> list[dict]:
    conn = _connect(db_path)
    rows = conn.execute(
        """SELECT * FROM tasks
           WHERE status NOT IN (?, ?, ?)
           ORDER BY
               CASE source
                   WHEN 'pr_authored' THEN 1
                   WHEN 'pr_review' THEN 2
                   WHEN 'issue' THEN 3
                   WHEN 'manual' THEN 4
               END,
               seen ASC,
               updated_at DESC""",
        tuple(TERMINAL_STATUSES),
    ).fetchall()
    conn.close()
    return [dict(row) for row in rows]


_VALID_COLUMNS = {
    "title", "source", "status", "project", "gh_repo", "gh_url", "gh_node_id", "gh_number",
    "gh_author", "gh_author_name", "tags", "seen", "last_changed_at",
    "last_seen_at", "updated_at",
}


def update_task(db_path: Path, task_id: int, **fields) -> None:
    if not fields:
        return
    bad_keys = set(fields) - _VALID_COLUMNS - {"updated_at"}
    if bad_keys:
        raise ValueError(f"Invalid column names: {bad_keys}")
    now = datetime.now(timezone.utc).isoformat()
    fields["updated_at"] = now
    set_clause = ", ".join(f"{k} = ?" for k in fields)
    values = list(fields.values()) + [task_id]
    conn = _connect(db_path)
    conn.execute(f"UPDATE tasks SET {set_clause} WHERE id = ?", values)
    conn.commit()
    conn.close()


def remove_task(db_path: Path, task_id: int) -> None:
    conn = _connect(db_path)
    conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
    conn.commit()
    conn.close()


def find_task_by_gh_url(db_path: Path, gh_url: str) -> dict | None:
    conn = _connect(db_path)
    row = conn.execute(
        "SELECT * FROM tasks WHERE gh_url = ?", (gh_url,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def find_tasks_by_gh_node_ids(db_path: Path, gh_node_ids: list[str]) -> dict[str, dict]:
    if not gh_node_ids:
        return {}
    placeholders = ", ".join("?" for _ in gh_node_ids)
    conn = _connect(db_path)
    rows = conn.execute(
        f"SELECT * FROM tasks WHERE gh_node_id IN ({placeholders})",
        gh_node_ids,
    ).fetchall()
    conn.close()
    return {
        row["gh_node_id"]: dict(row)
        for row in rows
        if row["gh_node_id"] is not None
    }


def mark_all_seen(db_path: Path) -> None:
    now = datetime.now(timezone.utc).isoformat()
    conn = _connect(db_path)
    conn.execute(
        "UPDATE tasks SET seen = 1, last_seen_at = ? WHERE seen = 0",
        (now,),
    )
    conn.commit()
    conn.close()

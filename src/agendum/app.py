"""Agendum — main Textual application."""

from __future__ import annotations

import atexit
import logging
import sys
import textwrap
import time
import webbrowser
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)

from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.widgets import DataTable, Footer, Input, Static
from textual.widgets._data_table import ColumnKey
from textual.worker import Worker, WorkerState

from agendum.config import AgendumConfig, DB_PATH, ensure_config
from agendum.db import (
    add_task,
    get_active_tasks,
    init_db,
    mark_all_seen,
    remove_task,
    update_task,
)
from agendum.syncer import run_sync
from agendum.widgets import (
    ActionModal,
    SECTION_ORDER,
    build_table_rows,
    format_link,
    styled_status,
)


class AgendumTable(DataTable):
    """DataTable with vim-style j/k navigation and header skipping."""

    BINDINGS = [
        Binding("j", "cursor_down", "Down", show=False),
        Binding("k", "cursor_up", "Up", show=False),
        Binding("q", "app.quit", "Quit"),
        Binding("r", "app.force_sync", "Sync"),
        Binding("c", "app.create_task", "Create"),
    ]

    _skipping = False

    def action_cursor_down(self) -> None:
        old_row = self.cursor_row
        super().action_cursor_down()
        if self.cursor_row == old_row and self.row_count > 0:
            # At the bottom — wrap to top
            self.move_cursor(row=0)
        if not self._skipping:
            self._skipping = True
            try:
                app = self.app
                if hasattr(app, "_skip_headers"):
                    app._skip_headers(direction=1)
            finally:
                self._skipping = False

    def action_cursor_up(self) -> None:
        old_row = self.cursor_row
        super().action_cursor_up()
        if self.cursor_row == old_row and self.row_count > 0:
            # At the top — wrap to bottom
            self.move_cursor(row=self.row_count - 1)
        if not self._skipping:
            self._skipping = True
            try:
                app = self.app
                if hasattr(app, "_skip_headers"):
                    app._skip_headers(direction=-1)
            finally:
                self._skipping = False


class AgendumApp(App):
    """Terminal dashboard for GitHub tasks."""

    TITLE = "agendum"

    CSS = """
    Screen {
        background: #0f0f1a;
        scrollbar-size: 0 0;
        overflow: hidden;
    }
    #status-bar {
        dock: top;
        height: 1;
        background: #1a1a2e;
        color: #8888aa;
    }
    DataTable {
        scrollbar-size: 0 0;
    }
    DataTable > .datatable--cursor {
        background: #363660;
    }
    #create-input {
        dock: bottom;
        height: 3;
        border: tall #444;
        display: none;
    }
    #create-input.visible {
        display: block;
    }
    """

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("r", "force_sync", "Sync"),
        Binding("c", "create_task", "Create", show=True),
        Binding("escape", "cancel_input", "Cancel", show=False),
    ]

    def __init__(
        self,
        *,
        db_path: Path | None = None,
        config: AgendumConfig | None = None,
    ) -> None:
        super().__init__()
        self._db_path = db_path or DB_PATH
        self._config = config  # resolved in on_mount if None
        self._task_rows: list[dict | None] = []  # None = section header
        self._last_sync: datetime | None = None
        self._sync_in_progress = False
        self._sync_error: str | None = None
        self._sync_spinner_frame = 0
        self._app_focused = True
        self._modal_task: dict | None = None
        self._last_sync_mono: float = time.monotonic()
        self._last_sync_wall: float = time.time()
        self._suspended = False
        self._wake_retry_count: int = 0

    @property
    def db_path(self) -> Path:
        return self._db_path

    # ── compose ──────────────────────────────────────────────────────

    def compose(self) -> ComposeResult:
        yield Static("agendum", id="status-bar")
        yield AgendumTable(cursor_type="row")
        yield Input(placeholder="type to create a new task…", id="create-input")
        yield Footer()

    # ── lifecycle ────────────────────────────────────────────────────

    # Fixed column widths: dot, status, author, repo, link
    _COL_DOT = 2
    _COL_STATUS = 18
    _COL_AUTHOR = 12
    _COL_REPO = 20
    _COL_LINK = 12
    _COL_FIXED = _COL_DOT + _COL_STATUS + _COL_AUTHOR + _COL_REPO + _COL_LINK
    # DataTable adds 1-char padding per column boundary (6 columns = ~7 gutters)
    _COL_GUTTERS = 12

    def _title_width(self) -> int:
        """Compute the title column width from available terminal width."""
        w = self.size.width - self._COL_FIXED - self._COL_GUTTERS
        return max(w, 10)

    async def on_mount(self) -> None:
        if self._config is None:
            self._config = ensure_config()

        init_db(self._db_path)

        table = self.query_one(DataTable)
        table.add_column("", width=self._COL_DOT, key="dot")
        table.add_column("status", width=self._COL_STATUS, key="status")
        table.add_column("title", width=self._title_width(), key="title")
        table.add_column("author", width=self._COL_AUTHOR, key="author")
        table.add_column("repo", width=self._COL_REPO, key="repo")
        table.add_column("link", width=self._COL_LINK, key="link")
        table.focus()

        self.refresh_table()
        self._enable_focus_reporting()
        # Run initial sync immediately, then on interval
        self._start_sync()
        self.set_interval(self._config.sync_interval, self._start_sync)
        self.set_interval(0.25, self._tick_initial_sync_spinner)
        self.set_interval(10, self._update_status_bar)

    def on_resize(self) -> None:
        """Recompute title column width when terminal is resized."""
        table = self.query_one(DataTable)
        if not table.columns:
            return
        title_key = ColumnKey("title")
        if title_key in table.columns:
            table.columns[title_key].width = self._title_width()
        self.refresh_table()

    # ── table rendering ──────────────────────────────────────────────

    def refresh_table(self) -> None:
        table = self.query_one(DataTable)
        saved_row = table.cursor_row
        table.clear()
        self._task_rows.clear()

        tasks = get_active_tasks(self._db_path)
        sections = build_table_rows(tasks)

        for label, section_tasks in sections:
            section_info = SECTION_ORDER.get(
                section_tasks[0].get("source", "manual"),
                ("ISSUES & MANUAL", "#60a5fa"),
            )
            colour = section_info[1]
            # Section header row
            table.add_row(
                Text(f"── {label} ", style=f"bold {colour}"),
                "",
                "",
                "",
                "",
                "",
            )
            self._task_rows.append(None)

            title_w = self._title_width()
            for task in section_tasks:
                seen = task.get("seen", 1)
                dot = Text("●", style="#f87171") if not seen else Text(" ")
                status_text = styled_status(task.get("status", ""))
                title = task.get("title", "")
                title_lines = textwrap.wrap(title, width=title_w) or [title]
                title = "\n".join(title_lines)
                author = task.get("gh_author_name") or task.get("gh_author") or ""
                if len(author) > self._COL_AUTHOR - 1:
                    author = author[: self._COL_AUTHOR - 2] + "…"
                repo = task.get("project") or task.get("gh_repo") or ""
                if len(repo) > self._COL_REPO - 1:
                    repo = repo[: self._COL_REPO - 2] + "…"
                link = format_link(
                    task.get("source", ""),
                    task.get("gh_number"),
                    task.get("gh_url"),
                )
                table.add_row(dot, status_text, title, author, repo, link, height=len(title_lines))
                self._task_rows.append(task)

        if saved_row > 0 and table.row_count > 0:
            table.move_cursor(row=min(saved_row, table.row_count - 1))

        self._update_status_bar()

    def _update_status_bar(self) -> None:
        """Update the status bar with current sync state."""
        tasks = get_active_tasks(self.db_path)
        unseen = sum(1 for t in tasks if not t["seen"])
        total = len(tasks)
        unseen_str = f" — {unseen} new" if unseen else ""
        sync_status = self._format_sync_status()
        self.query_one("#status-bar", Static).update(
            f"agendum — {sync_status} — {total} tasks{unseen_str}"
        )

    def _format_sync_status(self) -> str:
        if self._suspended:
            return "💤 sync suspended (waking up…)"
        if self._sync_error:
            return f"🟡 sync status ({self._sync_error})"
        if self._last_sync:
            return "🟢 sync status"
        if self._sync_in_progress:
            frame = "|/-\\"[self._sync_spinner_frame % 4]
            return f"initial sync starting {frame}"
        return "initial sync pending"

    def _tick_initial_sync_spinner(self) -> None:
        if not self._sync_in_progress or self._last_sync is not None or self._sync_error:
            return
        self._sync_spinner_frame += 1
        self._update_status_bar()

    # ── navigation ───────────────────────────────────────────────────

    def _skip_headers(self, direction: int) -> None:
        table = self.query_one(DataTable)
        row = table.cursor_row
        if 0 <= row < len(self._task_rows) and self._task_rows[row] is None:
            # Use DataTable's base method to avoid re-entering our override
            if direction > 0:
                DataTable.action_cursor_down(table)
            else:
                DataTable.action_cursor_up(table)

    # ── input toggle ────────────────────────────────────────────────

    def action_create_task(self) -> None:
        """Show the create-input and focus it."""
        inp = self.query_one("#create-input", Input)
        inp.add_class("visible")
        inp.value = ""
        inp.focus()
        self.notify("Type a task title, Enter to save, Escape to cancel", timeout=3)

    def action_cancel_input(self) -> None:
        """Hide the create-input and return focus to the table."""
        inp = self.query_one("#create-input", Input)
        inp.remove_class("visible")
        inp.clear()
        self.query_one(DataTable).focus()

    # ── row selection ────────────────────────────────────────────────

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        row_idx = event.cursor_row
        if row_idx < 0 or row_idx >= len(self._task_rows):
            return
        task = self._task_rows[row_idx]
        if task is None:
            return
        # Capture task reference now — cursor may shift before callback fires
        self._modal_task = task
        self.push_screen(ActionModal(task), callback=self._handle_action)

    def _handle_action(self, action: str | None) -> None:
        if action is None:
            return
        task = self._modal_task
        if task is None:
            return

        task_id = task["id"]
        if action == "open_browser":
            url = task.get("gh_url")
            if url:
                webbrowser.open(url)
        elif action == "mark_done":
            update_task(self._db_path, task_id, status="done")
            self.refresh_table()
        elif action == "mark_reviewed":
            update_task(self._db_path, task_id, status="reviewed")
            self.refresh_table()
        elif action == "remove":
            remove_task(self._db_path, task_id)
            self.refresh_table()

    # ── task creation via input ──────────────────────────────────────

    def on_input_submitted(self, event: Input.Submitted) -> None:
        title = event.value.strip()
        if not title:
            self.action_cancel_input()
            return
        add_task(self._db_path, title=title, source="manual", status="active")
        event.input.clear()
        event.input.remove_class("visible")
        self.query_one(DataTable).focus()
        self.refresh_table()

    # ── sync ─────────────────────────────────────────────────────────

    def _start_sync(self) -> None:
        """Kick off a sync in a background worker.

        Detects system sleep by comparing wall-clock drift against
        monotonic-clock drift.  On macOS, ``time.monotonic()`` does not
        advance during system sleep while ``time.time()`` does.  If
        wall-clock time jumped significantly more than monotonic time,
        the machine was asleep — enter suspended state and start
        retry-with-backoff instead of syncing immediately.
        """
        now_mono = time.monotonic()
        now_wall = time.time()
        mono_elapsed = now_mono - self._last_sync_mono
        wall_elapsed = now_wall - self._last_sync_wall
        interval = self._config.sync_interval if self._config else 60

        # Drift = how much more the wall clock advanced than monotonic.
        # On macOS sleep this equals the sleep duration.
        drift = wall_elapsed - mono_elapsed

        if drift > interval and self._last_sync is not None:
            log.info(
                "Sleep detected (%.0fs wall drift) — starting sync retry",
                drift,
            )
            self._last_sync_mono = now_mono
            self._last_sync_wall = now_wall
            self._suspended = True
            self._wake_retry_count = 0
            self._update_status_bar()
            self._retry_sync_after_wake()
            return

        self._last_sync_mono = now_mono
        self._last_sync_wall = now_wall

        if self._sync_in_progress:
            return
        self._sync_in_progress = True
        self._sync_spinner_frame = 0
        self._update_status_bar()
        self.run_worker(self._do_sync(), exclusive=True, group="sync")

    def _retry_sync_after_wake(self) -> None:
        """Attempt a sync as part of the wake retry sequence (Task 2)."""
        self._sync_in_progress = True
        self._sync_spinner_frame = 0
        self._update_status_bar()
        self.run_worker(self._do_sync(), exclusive=True, group="sync")

    async def _do_sync(self) -> tuple[int, bool, str | None]:
        """Run sync in a worker thread — does not touch UI."""
        return await run_sync(self._db_path, self._config)

    def on_worker_state_changed(self, event: Worker.StateChanged) -> None:
        """Handle sync worker completion."""
        if event.worker.group != "sync":
            return
        self._sync_in_progress = False
        if event.state != WorkerState.SUCCESS:
            if event.state == WorkerState.ERROR:
                log.exception("Sync failed: %s", event.worker.error)
                self._sync_error = self._format_sync_error(event.worker.error)
                self._update_status_bar()
            return
        changes, attention, error = event.worker.result
        self._last_sync = datetime.now(timezone.utc)
        self._sync_error = error
        self.refresh_table()
        self._update_status_bar()
        if attention and not self._app_focused:
            self.bell()

    def _format_sync_error(self, error: BaseException | None) -> str:
        if error is None:
            return "unknown sync error"
        message = str(error).strip()
        if not message:
            return error.__class__.__name__
        return message

    def action_force_sync(self) -> None:
        self._start_sync()

    # ── focus tracking ───────────────────────────────────────────────

    def _enable_focus_reporting(self) -> None:
        try:
            sys.stdout.write("\x1b[?1004h")
            sys.stdout.flush()
            atexit.register(self._disable_focus_reporting)
        except Exception:
            pass

    def on_app_focus(self) -> None:
        self._app_focused = True
        if self._config:
            self.set_timer(self._config.seen_delay, self._mark_seen)

    def on_app_blur(self) -> None:
        self._app_focused = False

    def _disable_focus_reporting(self) -> None:
        try:
            sys.stdout.write("\x1b[?1004l")
            sys.stdout.flush()
        except Exception:
            pass

    def on_unmount(self) -> None:
        self._disable_focus_reporting()

    def _mark_seen(self) -> None:
        if self._app_focused:
            mark_all_seen(self._db_path)
            self.refresh_table()

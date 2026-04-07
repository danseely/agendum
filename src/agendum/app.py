"""Agendum — main Textual application."""

from __future__ import annotations

import atexit
import logging
import sys
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
        background: #2a2a3a;
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
        self._app_focused = True
        self._modal_task: dict | None = None

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
                if len(title) > title_w:
                    title = title[: title_w - 1] + "…"
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
                table.add_row(dot, status_text, title, author, repo, link)
                self._task_rows.append(task)

        # Update status bar
        total = len(tasks)
        sync_info = ""
        if self._last_sync:
            age = (datetime.now(timezone.utc) - self._last_sync).total_seconds()
            if age < 60:
                sync_info = f" | synced {int(age)}s ago"
            else:
                sync_info = f" | synced {int(age // 60)}m ago"
        bar = self.query_one("#status-bar", Static)
        bar.update(f" agendum — {total} tasks{sync_info}")

    def _update_status_bar(self) -> None:
        """Update the status bar with current sync age."""
        tasks = get_active_tasks(self.db_path)
        unseen = sum(1 for t in tasks if not t["seen"])
        sync_ago = ""
        if self._last_sync:
            delta = int((datetime.now(timezone.utc) - self._last_sync).total_seconds())
            if delta < 60:
                sync_ago = f"synced {delta}s ago"
            else:
                sync_ago = f"synced {delta // 60}m ago"
        else:
            sync_ago = "not yet synced"
        total = len(tasks)
        unseen_str = f" — {unseen} new" if unseen else ""
        self.query_one("#status-bar", Static).update(
            f"agendum — {sync_ago} — {total} tasks{unseen_str}"
        )

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
        """Kick off a sync in a background worker."""
        self.query_one("#status-bar", Static).update("agendum — syncing…")
        self.run_worker(self._do_sync(), exclusive=True, group="sync")

    async def _do_sync(self) -> tuple[int, bool]:
        """Run sync in a worker thread — does not touch UI."""
        return await run_sync(self._db_path, self._config)

    def on_worker_state_changed(self, event: Worker.StateChanged) -> None:
        """Handle sync worker completion."""
        if event.worker.group != "sync":
            return
        if event.state != WorkerState.SUCCESS:
            if event.state == WorkerState.ERROR:
                log.exception("Sync failed: %s", event.worker.error)
            return
        changes, attention = event.worker.result
        self._last_sync = datetime.now(timezone.utc)
        self.refresh_table()
        self._update_status_bar()
        if attention and not self._app_focused:
            self.bell()

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

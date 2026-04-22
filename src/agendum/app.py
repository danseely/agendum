"""Agendum — main Textual application."""

from __future__ import annotations

import atexit
import logging
import sys
import time
import webbrowser
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

log = logging.getLogger(__name__)

from rich.text import Text
from textual import events
from textual.app import App, ComposeResult
from textual.app import ScreenStackError
from textual.binding import Binding
from textual.timer import Timer
from textual.widgets import DataTable, Footer, Input, Static
from textual.widgets._data_table import ColumnKey
from textual.worker import Worker, WorkerState

from agendum.config import (
    AgendumConfig,
    RuntimePaths,
    default_runtime_paths,
    ensure_workspace_config,
    workspace_runtime_paths,
    runtime_base_dir,
    runtime_paths,
)
from agendum.db import (
    add_task,
    get_active_tasks,
    init_db,
    mark_all_seen,
    remove_task,
    update_task,
)
from agendum.gh import auth_login, recover_gh_auth, set_gh_config_dir
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
        Binding("n", "switch_namespace", "Namespace", show=True),
        Binding("escape", "cancel_input", "Cancel", show=False),
    ]

    def __init__(
        self,
        *,
        runtime: RuntimePaths | None = None,
        workspace_base_dir: Path | None = None,
        db_path: Path | None = None,
        config: AgendumConfig | None = None,
    ) -> None:
        super().__init__()
        self._runtime = runtime or (
            runtime_paths(db_path.parent) if db_path is not None else default_runtime_paths()
        )
        if db_path is not None:
            self._runtime = RuntimePaths(
                workspace_root=self._runtime.workspace_root,
                config_path=self._runtime.config_path,
                db_path=db_path,
                gh_config_dir=self._runtime.gh_config_dir,
            )
        self._workspace_base_dir = workspace_base_dir or runtime_base_dir(self._runtime)
        self._db_path = self._runtime.db_path
        set_gh_config_dir(self._runtime.gh_config_dir)
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
        self._title_width_chars: int = 10
        self._input_mode: Literal["create", "namespace"] | None = None
        self._sync_timer: Timer | None = None
        self._spinner_timer: Timer | None = None
        self._status_timer: Timer | None = None
        self._seen_timer: Timer | None = None
        self._sync_context_id = 0

    @property
    def db_path(self) -> Path:
        return self._db_path

    @property
    def runtime(self) -> RuntimePaths:
        return self._runtime

    @property
    def current_namespace(self) -> str | None:
        if self._runtime.workspace_root == self._workspace_base_dir:
            return None
        if self._runtime.workspace_root.parent.name == "workspaces":
            return self._runtime.workspace_root.name
        if self._config and self._config.orgs:
            return self._config.orgs[0]
        return None

    # ── compose ──────────────────────────────────────────────────────

    def compose(self) -> ComposeResult:
        yield Static("agendum", id="status-bar")
        yield AgendumTable(cursor_type="row")
        yield Input(placeholder="type to create a new task…", id="create-input")
        yield Footer()

    # ── lifecycle ────────────────────────────────────────────────────

    # Fixed column widths: dot, status, link
    _COL_DOT = 2
    _COL_STATUS = 18
    _COL_LINK = 12
    _COL_FIXED = _COL_DOT + _COL_STATUS + _COL_LINK
    _COL_COUNT = 6
    _MIN_TITLE = 24
    _MIN_AUTHOR = 12
    _MIN_REPO = 12
    _MAX_TITLE = 60
    _MAX_AUTHOR = 24
    _MAX_REPO = 26
    _WIDTH_KEYS = ("title", "author", "repo")
    _WIDTH_WEIGHTS = {"title": 4, "author": 3, "repo": 3}

    def _weighted_widths(self, budget: int) -> dict[str, int]:
        """Split a width budget across title/author/repo with a 4:3:3 bias."""
        widths = {key: 0 for key in self._WIDTH_KEYS}
        if budget <= 0:
            return widths

        total_weight = sum(self._WIDTH_WEIGHTS.values())
        cycles, remainder = divmod(budget, total_weight)
        for key in self._WIDTH_KEYS:
            widths[key] = cycles * self._WIDTH_WEIGHTS[key]

        if remainder:
            shares = {
                key: remainder * self._WIDTH_WEIGHTS[key] / total_weight
                for key in self._WIDTH_KEYS
            }
            increments = {key: int(shares[key]) for key in self._WIDTH_KEYS}
            leftover = remainder - sum(increments.values())
            order = sorted(
                self._WIDTH_KEYS,
                key=lambda key: (
                    -(shares[key] - increments[key]),
                    -self._WIDTH_WEIGHTS[key],
                    self._WIDTH_KEYS.index(key),
                ),
            )
            for key in order:
                if leftover == 0:
                    break
                increments[key] += 1
                leftover -= 1
            for key in self._WIDTH_KEYS:
                widths[key] += increments[key]

        return widths

    def _column_widths(
        self,
        _tasks: list[dict],
        available_width: int,
        cell_padding: int = 1,
    ) -> tuple[int, int, int]:
        """Compute balanced widths for title/author/repo columns."""
        rendered_fixed = self._COL_FIXED + (2 * cell_padding * self._COL_COUNT)
        content_budget = max(available_width - rendered_fixed, 0)

        widths = {
            "title": self._MIN_TITLE,
            "author": self._MIN_AUTHOR,
            "repo": self._MIN_REPO,
        }
        min_total = sum(widths.values())
        if content_budget < min_total:
            widths = self._weighted_widths(content_budget)
        else:
            extra = content_budget - min_total
            for key, amount in self._weighted_widths(extra).items():
                widths[key] += amount

        self._title_width_chars = widths["title"]
        return widths["title"], widths["author"], widths["repo"]

    def _title_width(self) -> int:
        """Return the current title width based on the available viewport."""
        available_width = self.size.width
        cell_padding = 1
        if self.is_mounted:
            try:
                table = self.query_one(DataTable)
            except ScreenStackError:
                table = None
            if table is not None and table.size.width:
                available_width = table.size.width
                cell_padding = table.cell_padding
        self._title_width_chars = self._column_widths([], available_width, cell_padding)[0]
        return self._title_width_chars

    async def on_mount(self) -> None:
        if self._config is None:
            self._config = ensure_workspace_config(self._runtime)

        init_db(self._db_path)

        table = self.query_one(DataTable)
        title_w, author_w, repo_w = self._column_widths([], self.size.width, table.cell_padding)
        table.add_column("", width=self._COL_DOT, key="dot")
        table.add_column("status", width=self._COL_STATUS, key="status")
        table.add_column("title", width=title_w, key="title")
        table.add_column("author", width=author_w, key="author")
        table.add_column("repo", width=repo_w, key="repo")
        table.add_column("link", width=self._COL_LINK, key="link")
        table.focus()

        self.refresh_table()
        self._enable_focus_reporting()
        # Run initial sync immediately, then on interval
        self._start_sync()
        self._sync_timer = self.set_interval(self._config.sync_interval, self._start_sync)
        self._spinner_timer = self.set_interval(0.25, self._tick_initial_sync_spinner)
        self._status_timer = self.set_interval(10, self._update_status_bar)

    def on_resize(self, event: events.Resize) -> None:
        """Recompute title column width when terminal is resized."""
        table = self.query_one(DataTable)
        if not table.columns:
            return
        tasks = get_active_tasks(self._db_path)
        title_w, author_w, repo_w = self._column_widths(tasks, event.size.width, table.cell_padding)
        for key, width in (
            (ColumnKey("title"), title_w),
            (ColumnKey("author"), author_w),
            (ColumnKey("repo"), repo_w),
        ):
            if key in table.columns:
                table.columns[key].width = width
        self.refresh_table()

    # ── table rendering ──────────────────────────────────────────────

    def refresh_table(self) -> None:
        table = self.query_one(DataTable)
        saved_row = table.cursor_row
        table.clear()
        self._task_rows.clear()

        tasks = get_active_tasks(self._db_path)
        available_width = table.size.width or self.size.width
        title_w, author_w, repo_w = self._column_widths(tasks, available_width, table.cell_padding)
        for key, width in (
            (ColumnKey("title"), title_w),
            (ColumnKey("author"), author_w),
            (ColumnKey("repo"), repo_w),
        ):
            if key in table.columns:
                table.columns[key].width = width
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

            for task in section_tasks:
                seen = task.get("seen", 1)
                dot = Text("●", style="#f87171") if not seen else Text(" ")
                status_text = styled_status(task.get("status", ""))
                title = task.get("title", "")
                title_text = Text(title, no_wrap=False, end="")
                author = task.get("gh_author_name") or task.get("gh_author") or ""
                author = self._truncate_to_width(author, author_w)
                repo = task.get("project") or task.get("gh_repo") or ""
                repo = self._truncate_to_width(repo, repo_w)
                link = format_link(
                    task.get("source", ""),
                    task.get("gh_number"),
                    task.get("gh_url"),
                )
                table.add_row(
                    dot,
                    status_text,
                    title_text,
                    author,
                    repo,
                    link,
                    height=None,
                )
                self._task_rows.append(task)

        if saved_row > 0 and table.row_count > 0:
            table.move_cursor(row=min(saved_row, table.row_count - 1))

        self._update_status_bar()

    @staticmethod
    def _truncate_to_width(text: str, width: int) -> str:
        """Truncate text to fit a cell width, preserving an ellipsis when possible."""
        if width <= 0:
            return ""
        if len(text) <= width:
            return text
        if width == 1:
            return "…"
        return text[: width - 1] + "…"

    def _update_status_bar(self) -> None:
        """Update the status bar with current sync state."""
        tasks = get_active_tasks(self.db_path)
        unseen = sum(1 for t in tasks if not t["seen"])
        total = len(tasks)
        unseen_str = f" — {unseen} new" if unseen else ""
        sync_status = self._format_sync_status()
        namespace = self.current_namespace
        namespace_str = f" [{namespace}]" if namespace else ""
        self.query_one("#status-bar", Static).update(
            f"agendum{namespace_str} — {sync_status} — {total} tasks{unseen_str}"
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
        """Show the command input for task creation."""
        self._show_input(
            mode="create",
            placeholder="type to create a new task…",
            value="",
        )
        self.notify("Type a task title, Enter to save, Escape to cancel", timeout=3)

    def action_switch_namespace(self) -> None:
        """Show the command input for namespace switching."""
        self._show_input(
            mode="namespace",
            placeholder="GitHub namespace (blank for base workspace)…",
            value=self.current_namespace or "",
        )
        self.notify(
            "Enter a GitHub namespace, or submit blank for the base workspace",
            timeout=3,
        )

    def action_cancel_input(self) -> None:
        """Hide the create-input and return focus to the table."""
        self._input_mode = None
        inp = self.query_one("#create-input", Input)
        inp.remove_class("visible")
        inp.clear()
        inp.placeholder = "type to create a new task…"
        self.query_one(DataTable).focus()

    def _show_input(
        self,
        *,
        mode: Literal["create", "namespace"],
        placeholder: str,
        value: str,
    ) -> None:
        self._input_mode = mode
        inp = self.query_one("#create-input", Input)
        inp.placeholder = placeholder
        inp.add_class("visible")
        inp.value = value
        inp.focus()

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
        elif action == "mark_in_progress":
            update_task(self._db_path, task_id, status="in progress")
            self.refresh_table()
        elif action == "mark_backlog":
            update_task(self._db_path, task_id, status="backlog")
            self.refresh_table()
        elif action == "remove":
            remove_task(self._db_path, task_id)
            self.refresh_table()

    # ── task creation via input ──────────────────────────────────────

    def on_input_submitted(self, event: Input.Submitted) -> None:
        mode = self._input_mode
        value = event.value.strip()
        if mode != "namespace" and not value:
            self.action_cancel_input()
            return
        self.action_cancel_input()
        if mode == "namespace":
            self._switch_namespace(value or None)
            return

        add_task(self._db_path, title=value, source="manual", status="backlog")
        self.refresh_table()

    def _switch_namespace(self, namespace: str | None) -> None:
        try:
            target_runtime = workspace_runtime_paths(namespace, self._workspace_base_dir)
        except ValueError as exc:
            self.notify(
                f"Invalid namespace: {str(exc).rstrip('.')}.",
                severity="error",
                timeout=5,
            )
            return

        if target_runtime == self._runtime:
            return

        target_namespace = namespace or None
        if not recover_gh_auth(
            target_runtime.gh_config_dir,
            source_dir=self._runtime.gh_config_dir,
        ):
            with self.suspend():
                authenticated = auth_login(target_runtime.gh_config_dir)
            if not authenticated:
                self._sync_error = "gh auth login failed"
                self._update_status_bar()
                return

        config = ensure_workspace_config(
            target_runtime,
            namespace=target_namespace,
            seed=self._config,
        )
        self._apply_runtime(target_runtime, config)
        self.notify(f"Switched to {target_namespace or 'base workspace'}", timeout=3)

    def _apply_runtime(self, runtime: RuntimePaths, config: AgendumConfig) -> None:
        self._runtime = runtime
        self._db_path = runtime.db_path
        set_gh_config_dir(runtime.gh_config_dir)
        self._config = config
        init_db(self._db_path)
        self._sync_context_id += 1
        self._cancel_seen_timer()
        self._last_sync = None
        self._sync_error = None
        self._sync_in_progress = False
        self._sync_spinner_frame = 0
        self._last_sync_mono = time.monotonic()
        self._last_sync_wall = time.time()
        self._suspended = False
        self._wake_retry_count = 0
        if self._sync_timer is not None:
            self._sync_timer.stop()
            self._sync_timer = self.set_interval(self._config.sync_interval, self._start_sync)
        if self._app_focused:
            self._schedule_mark_seen()
        self.refresh_table()
        self._update_status_bar()
        self._start_sync()

    # ── sync ─────────────────────────────────────────────────────────

    def _sync_group(self) -> str:
        return f"sync:{self._sync_context_id}"

    def _start_sync(self) -> None:
        """Kick off a sync in a background worker.

        Detects system sleep by comparing wall-clock drift against
        monotonic-clock drift.  On macOS, ``time.monotonic()`` does not
        advance during system sleep while ``time.time()`` does.  If
        wall-clock time jumped significantly more than monotonic time,
        the machine was asleep — enter suspended state and start
        retry-with-backoff instead of syncing immediately.
        """
        if self._suspended:
            return  # wake-retry sequence owns sync scheduling

        now_mono = time.monotonic()
        now_wall = time.time()
        mono_elapsed = now_mono - self._last_sync_mono
        wall_elapsed = now_wall - self._last_sync_wall
        interval = self._config.sync_interval if self._config else 120

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
        self.run_worker(
            self._do_sync(self._sync_context_id, self._db_path, self._config),
            exclusive=True,
            group=self._sync_group(),
        )

    def _retry_sync_after_wake(self) -> None:
        """Attempt a sync as part of the wake retry sequence."""
        if not self._suspended or self._sync_in_progress:
            return
        self._sync_in_progress = True
        self._sync_spinner_frame = 0
        self._update_status_bar()
        self.run_worker(
            self._do_sync(self._sync_context_id, self._db_path, self._config),
            exclusive=True,
            group=self._sync_group(),
        )

    def _handle_wake_retry_failure(self) -> None:
        """Schedule the next wake-retry attempt with exponential backoff."""
        self._wake_retry_count += 1
        if self._wake_retry_count > 10:
            log.warning("Wake retries exhausted — resuming normal sync")
            self._suspended = False
            self._wake_retry_count = 0
            self._update_status_bar()
            return
        delay = min(2 * (2 ** (self._wake_retry_count - 1)), 30)
        log.info(
            "Wake retry %d failed — retrying in %ds",
            self._wake_retry_count, delay,
        )
        self._update_status_bar()
        self.set_timer(delay, self._retry_sync_after_wake)

    def _handle_wake_retry_success(self) -> None:
        """Clear suspended state after a successful wake-retry sync."""
        log.info("Wake retry succeeded — resuming normal sync")
        self._suspended = False
        self._wake_retry_count = 0

    async def _do_sync(
        self,
        sync_context_id: int,
        db_path: Path,
        config: AgendumConfig,
    ) -> tuple[int, int, bool, str | None]:
        """Run sync in a worker thread — does not touch UI."""
        changes, attention, error = await run_sync(db_path, config)
        return sync_context_id, changes, attention, error

    def on_worker_state_changed(self, event: Worker.StateChanged) -> None:
        """Handle sync worker completion."""
        if event.worker.group != self._sync_group():
            return
        self._sync_in_progress = False
        if event.state != WorkerState.SUCCESS:
            if event.state == WorkerState.ERROR:
                log.exception("Sync failed: %s", event.worker.error)
                self._sync_error = self._format_sync_error(event.worker.error)
                if self._suspended:
                    self._handle_wake_retry_failure()
                else:
                    self._update_status_bar()
            return
        _, changes, attention, error = event.worker.result
        self._last_sync = datetime.now(timezone.utc)
        self._sync_error = error
        if self._suspended:
            self._handle_wake_retry_success()
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
        if self._suspended:
            self._suspended = False
            self._wake_retry_count = 0
            log.info("Force sync — clearing suspended state")
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
        self._schedule_mark_seen()

    def on_app_blur(self) -> None:
        self._app_focused = False
        self._cancel_seen_timer()

    def _schedule_mark_seen(self) -> None:
        self._cancel_seen_timer()
        if self._config is None:
            return

        sync_context_id = self._sync_context_id
        db_path = self._db_path

        def mark_seen_for_context() -> None:
            if sync_context_id == self._sync_context_id:
                self._seen_timer = None
            self._mark_seen(db_path, sync_context_id)

        self._seen_timer = self.set_timer(self._config.seen_delay, mark_seen_for_context)

    def _cancel_seen_timer(self) -> None:
        if self._seen_timer is None:
            return
        self._seen_timer.stop()
        self._seen_timer = None

    def _disable_focus_reporting(self) -> None:
        try:
            sys.stdout.write("\x1b[?1004l")
            sys.stdout.flush()
        except Exception:
            pass

    def on_unmount(self) -> None:
        self._cancel_seen_timer()
        self._disable_focus_reporting()

    def _mark_seen(self, db_path: Path, sync_context_id: int) -> None:
        if self._app_focused and sync_context_id == self._sync_context_id:
            mark_all_seen(db_path)
            self.refresh_table()

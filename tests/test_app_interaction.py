"""Tests for AgendumApp user interaction — navigation, actions, input, sync."""

from contextlib import nullcontext
from pathlib import Path
from unittest.mock import patch

import pytest
from textual.widgets import DataTable, Input

from agendum.app import AgendumApp
from agendum.config import AgendumConfig, load_config, namespace_runtime_paths, runtime_paths
from agendum.db import add_task, get_active_tasks, init_db, update_task


def _app(tmp_db: Path) -> AgendumApp:
    """Create an app with sync disabled."""
    return AgendumApp(db_path=tmp_db, config=AgendumConfig(orgs=[], sync_interval=9999))


def _seed_tasks(tmp_db: Path) -> None:
    """Add one task per section so the table has headers + data rows."""
    add_task(tmp_db, title="My PR", source="pr_authored", status="awaiting review",
             project="repo", gh_number=1, gh_url="https://github.com/org/repo/pull/1")
    add_task(tmp_db, title="Review PR", source="pr_review", status="review requested",
             project="tool", gh_number=2, gh_url="https://github.com/org/tool/pull/2",
             gh_author="author", gh_author_name="Author")
    add_task(tmp_db, title="An issue", source="issue", status="open",
             project="repo", gh_number=3, gh_url="https://github.com/org/repo/issues/3")
    add_task(tmp_db, title="Manual task", source="manual", status="active")


# ── navigation ───────────────────────────────────────────────────────────


async def test_j_moves_cursor_down(tmp_db: Path) -> None:
    init_db(tmp_db)
    _seed_tasks(tmp_db)
    app = _app(tmp_db)
    async with app.run_test() as pilot:
        table = app.query_one(DataTable)
        start = table.cursor_row
        await pilot.press("j")
        await pilot.pause()
        assert table.cursor_row > start


async def test_k_moves_cursor_up(tmp_db: Path) -> None:
    init_db(tmp_db)
    _seed_tasks(tmp_db)
    app = _app(tmp_db)
    async with app.run_test() as pilot:
        table = app.query_one(DataTable)
        # Move down first so we have room to go up
        await pilot.press("j")
        await pilot.press("j")
        await pilot.pause()
        pos = table.cursor_row
        await pilot.press("k")
        await pilot.pause()
        assert table.cursor_row < pos


async def test_header_rows_are_skipped(tmp_db: Path) -> None:
    init_db(tmp_db)
    _seed_tasks(tmp_db)
    app = _app(tmp_db)
    async with app.run_test() as pilot:
        table = app.query_one(DataTable)
        # Navigate through all rows, cursor should never land on a header
        for _ in range(table.row_count + 2):
            row = table.cursor_row
            if 0 <= row < len(app._task_rows):
                assert app._task_rows[row] is not None or row == 0
            await pilot.press("j")
            await pilot.pause()


# ── task creation and cancellation ───────────────────────────────────────


async def test_cancel_input_hides_widget(tmp_db: Path) -> None:
    init_db(tmp_db)
    app = _app(tmp_db)
    async with app.run_test() as pilot:
        await pilot.press("c")
        await pilot.pause()
        inp = app.query_one("#create-input")
        assert inp.has_class("visible")

        await pilot.press("escape")
        await pilot.pause()
        assert not inp.has_class("visible")


async def test_empty_input_cancelled(tmp_db: Path) -> None:
    init_db(tmp_db)
    app = _app(tmp_db)
    async with app.run_test() as pilot:
        inp = app.query_one("#create-input")
        inp.focus()
        await pilot.pause()
        inp.value = ""
        await pilot.press("enter")
        await pilot.pause()
        assert get_active_tasks(tmp_db) == []


# ── action handling ──────────────────────────────────────────────────────


async def test_mark_done_updates_status(tmp_db: Path) -> None:
    init_db(tmp_db)
    task_id = add_task(tmp_db, title="Finish me", source="manual", status="active")
    app = _app(tmp_db)
    # Directly invoke the handler to test the logic without modal interaction
    app._modal_task = {"id": task_id, "source": "manual", "gh_url": None}
    app._db_path = tmp_db
    async with app.run_test() as pilot:
        app._handle_action("mark_done")
        await pilot.pause()
        tasks = get_active_tasks(tmp_db)
        assert len(tasks) == 0  # terminal status, excluded from active


async def test_mark_reviewed_updates_status(tmp_db: Path) -> None:
    init_db(tmp_db)
    task_id = add_task(tmp_db, title="Review me", source="pr_review", status="review requested",
                       gh_url="https://github.com/org/repo/pull/1")
    app = _app(tmp_db)
    app._modal_task = {"id": task_id, "source": "pr_review", "gh_url": "https://github.com/org/repo/pull/1"}
    async with app.run_test() as pilot:
        app._handle_action("mark_reviewed")
        await pilot.pause()
        from agendum.db import find_task_by_gh_url
        task = find_task_by_gh_url(tmp_db, "https://github.com/org/repo/pull/1")
        assert task["status"] == "reviewed"


async def test_remove_deletes_task(tmp_db: Path) -> None:
    init_db(tmp_db)
    task_id = add_task(tmp_db, title="Remove me", source="manual", status="active")
    app = _app(tmp_db)
    app._modal_task = {"id": task_id, "source": "manual", "gh_url": None}
    async with app.run_test() as pilot:
        app._handle_action("remove")
        await pilot.pause()
        assert get_active_tasks(tmp_db) == []


async def test_open_browser_action(tmp_db: Path) -> None:
    init_db(tmp_db)
    task_id = add_task(tmp_db, title="Open me", source="pr_authored", status="open",
                       gh_url="https://github.com/org/repo/pull/1")
    app = _app(tmp_db)
    app._modal_task = {"id": task_id, "source": "pr_authored", "gh_url": "https://github.com/org/repo/pull/1"}
    async with app.run_test() as pilot:
        with patch("agendum.app.webbrowser.open") as mock_open:
            app._handle_action("open_browser")
            await pilot.pause()
            mock_open.assert_called_once_with("https://github.com/org/repo/pull/1")


async def test_handle_action_none_is_noop(tmp_db: Path) -> None:
    init_db(tmp_db)
    app = _app(tmp_db)
    async with app.run_test() as pilot:
        app._handle_action(None)  # should not raise


# ── sync status ──────────────────────────────────────────────────────────


def test_sync_status_initial_pending(tmp_db: Path) -> None:
    app = _app(tmp_db)
    app._sync_in_progress = False
    app._last_sync = None
    app._sync_error = None
    assert app._format_sync_status() == "initial sync pending"


def test_format_sync_error_with_message(tmp_db: Path) -> None:
    app = _app(tmp_db)
    assert app._format_sync_error(RuntimeError("network timeout")) == "network timeout"


def test_format_sync_error_empty_message(tmp_db: Path) -> None:
    app = _app(tmp_db)
    assert app._format_sync_error(RuntimeError("")) == "RuntimeError"


def test_format_sync_error_none(tmp_db: Path) -> None:
    app = _app(tmp_db)
    assert app._format_sync_error(None) == "unknown sync error"


async def test_switch_namespace_reauths_into_isolated_workspace(
    tmp_path: Path,
    monkeypatch,
) -> None:
    base_root = tmp_path / ".agendum"
    current_runtime = runtime_paths(base_root)
    init_db(current_runtime.db_path)

    auth_calls: list[Path] = []
    monkeypatch.setattr("agendum.app.auth_login", lambda gh_dir: auth_calls.append(gh_dir) or True)
    monkeypatch.setattr(AgendumApp, "suspend", lambda self: nullcontext())

    app = AgendumApp(
        runtime=current_runtime,
        workspace_base_dir=base_root,
        config=AgendumConfig(orgs=["current"], sync_interval=9999, seen_delay=3),
    )
    app._start_sync = lambda: None  # type: ignore[assignment]

    async with app.run_test() as pilot:
        await pilot.press("n")
        await pilot.pause()

        inp = app.query_one("#create-input", Input)
        assert inp.has_class("visible")
        inp.value = "example-org"

        await pilot.press("enter")
        await pilot.pause()

        target_runtime = namespace_runtime_paths("example-org", base_root)
        assert auth_calls == [target_runtime.gh_config_dir]
        assert app.runtime == target_runtime
        assert app.db_path == target_runtime.db_path
        assert load_config(target_runtime.config_path).orgs == ["example-org"]


async def test_switch_namespace_keeps_current_workspace_when_auth_fails(
    tmp_path: Path,
    monkeypatch,
) -> None:
    base_root = tmp_path / ".agendum"
    current_runtime = runtime_paths(base_root)
    init_db(current_runtime.db_path)

    monkeypatch.setattr("agendum.app.auth_login", lambda _gh_dir: False)
    monkeypatch.setattr(AgendumApp, "suspend", lambda self: nullcontext())

    app = AgendumApp(
        runtime=current_runtime,
        workspace_base_dir=base_root,
        config=AgendumConfig(orgs=["current"], sync_interval=9999, seen_delay=3),
    )
    app._start_sync = lambda: None  # type: ignore[assignment]

    async with app.run_test() as pilot:
        await pilot.press("n")
        await pilot.pause()
        inp = app.query_one("#create-input", Input)
        inp.value = "example-org"

        await pilot.press("enter")
        await pilot.pause()

        assert app.runtime == current_runtime
        assert not namespace_runtime_paths("example-org", base_root).config_path.exists()
        assert app._sync_error == "gh auth login failed"


async def test_switch_namespace_rejects_invalid_namespace_without_changing_workspace(
    tmp_path: Path,
    monkeypatch,
) -> None:
    base_root = tmp_path / ".agendum"
    current_runtime = runtime_paths(base_root)
    init_db(current_runtime.db_path)

    auth_calls: list[Path] = []
    monkeypatch.setattr("agendum.app.auth_login", lambda gh_dir: auth_calls.append(gh_dir) or True)
    monkeypatch.setattr(AgendumApp, "suspend", lambda self: nullcontext())

    app = AgendumApp(
        runtime=current_runtime,
        workspace_base_dir=base_root,
        config=AgendumConfig(orgs=["current"], sync_interval=9999, seen_delay=3),
    )
    app._start_sync = lambda: None  # type: ignore[assignment]

    async with app.run_test(notifications=True) as pilot:
        await pilot.press("n")
        await pilot.pause()

        inp = app.query_one("#create-input", Input)
        inp.value = "!!!"

        await pilot.press("enter")
        await pilot.pause()

        notifications = list(app._notifications)
        assert auth_calls == []
        assert app.runtime == current_runtime
        assert app.db_path == current_runtime.db_path
        assert not (base_root / "workspaces").exists()
        assert notifications[-1].severity == "error"
        assert notifications[-1].message == "Invalid namespace: enter at least one letter or number."

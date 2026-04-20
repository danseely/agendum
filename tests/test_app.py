import time
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from textual.worker import Worker, WorkerState
from textual.widgets import DataTable

from agendum.app import AgendumApp
from agendum.config import AgendumConfig, namespace_runtime_paths, runtime_paths


def _capture_worker_call(calls: list):
    def fake_run_worker(coro, *args, **kwargs):
        coro.close()
        calls.append((args, kwargs))

    return fake_run_worker


@pytest.mark.asyncio
async def test_app_starts_and_quits(tmp_db) -> None:
    from agendum.db import init_db
    init_db(tmp_db)
    config = AgendumConfig(orgs=[], sync_interval=9999)
    app = AgendumApp(db_path=tmp_db, config=config)
    async with app.run_test() as pilot:
        assert app.is_running
        await pilot.press("q")


def test_sync_status_shows_initial_spinner(tmp_db) -> None:
    app = AgendumApp(db_path=tmp_db, config=AgendumConfig(orgs=[], sync_interval=9999))
    app._sync_in_progress = True

    assert app._format_sync_status() == "initial sync starting |"


def test_sync_status_shows_green_dot_after_success(tmp_db) -> None:
    app = AgendumApp(db_path=tmp_db, config=AgendumConfig(orgs=[], sync_interval=9999))
    app._last_sync = datetime.now(timezone.utc)

    assert app._format_sync_status() == "🟢 sync status"


def test_sync_status_shows_yellow_dot_with_error(tmp_db) -> None:
    app = AgendumApp(db_path=tmp_db, config=AgendumConfig(orgs=[], sync_interval=9999))
    app._last_sync = datetime.now(timezone.utc)
    app._sync_error = "gh credentials expired"

    assert app._format_sync_status() == "🟡 sync status (gh credentials expired)"


def test_title_width_budget_is_balanced(tmp_db) -> None:
    app = AgendumApp(db_path=tmp_db, config=AgendumConfig(orgs=[], sync_interval=9999))
    title_w, author_w, repo_w = app._column_widths([], 120)

    assert title_w + author_w + repo_w == 120 - app._COL_FIXED - (2 * app._COL_COUNT)
    assert title_w > author_w >= 20
    assert title_w > repo_w >= 20
    assert title_w <= 44


def test_on_resize_recomputes_title_width(tmp_db) -> None:
    app = AgendumApp(db_path=tmp_db, config=AgendumConfig(orgs=[], sync_interval=9999))
    table = MagicMock()
    table.cell_padding = 1
    table.columns = {
        "title": SimpleNamespace(width=0),
        "author": SimpleNamespace(width=0),
        "repo": SimpleNamespace(width=0),
    }
    from agendum.db import add_task, init_db
    init_db(tmp_db)
    add_task(
        tmp_db,
        title="Short title",
        source="pr_review",
        status="review requested",
        gh_author_name="Alexandria Stone",
        project="repo-name",
    )
    app.query_one = MagicMock(return_value=table)  # type: ignore[method-assign]
    app.refresh_table = MagicMock()  # type: ignore[method-assign]
    app._db_path = tmp_db

    app.on_resize(SimpleNamespace(size=SimpleNamespace(width=120)))

    assert (
        table.columns["title"].width
        + table.columns["author"].width
        + table.columns["repo"].width
    ) == 120 - app._COL_FIXED - (2 * app._COL_COUNT)
    assert table.columns["author"].width >= 20
    assert table.columns["repo"].width >= 20
    assert table.columns["title"].width <= 44
    assert table.columns["title"].width > table.columns["author"].width
    app.refresh_table.assert_called_once()


@pytest.mark.asyncio
async def test_table_width_stays_within_viewport(tmp_db) -> None:
    from agendum.db import add_task, init_db

    init_db(tmp_db)
    add_task(
        tmp_db,
        title="Fix the DataTable width-budget regression",
        source="manual",
        status="backlog",
        gh_author_name="Alexandria Stone",
        project="agendum",
    )

    app = AgendumApp(db_path=tmp_db, config=AgendumConfig(orgs=[], sync_interval=9999))
    app._start_sync = lambda: None  # type: ignore[assignment]

    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        table = app.query_one(DataTable)

        assert table.virtual_size.width <= table.size.width
        await pilot.press("q")


def test_stale_seen_delay_callback_is_ignored_after_workspace_switch(tmp_path) -> None:
    from agendum.db import add_task, get_active_tasks, init_db, update_task

    original_db = tmp_path / "original.db"
    switched_db = tmp_path / "switched.db"
    init_db(original_db)
    init_db(switched_db)

    original_task = add_task(original_db, title="Original", source="manual", status="backlog")
    switched_task = add_task(switched_db, title="Switched", source="manual", status="backlog")
    update_task(original_db, original_task, seen=0)
    update_task(switched_db, switched_task, seen=0)

    app = AgendumApp(
        db_path=original_db,
        config=AgendumConfig(orgs=[], sync_interval=9999, seen_delay=3),
    )
    app._app_focused = True

    timer_callbacks: list[object] = []
    app.set_timer = lambda delay, cb: timer_callbacks.append(cb) or SimpleNamespace(stop=lambda: None)  # type: ignore[assignment]

    app.on_app_focus()
    app._sync_context_id += 1
    app._db_path = switched_db

    timer_callbacks[0]()

    assert get_active_tasks(original_db)[0]["seen"] == 0
    assert get_active_tasks(switched_db)[0]["seen"] == 0


def test_apply_runtime_rearms_seen_delay_when_app_is_focused(tmp_path) -> None:
    base_root = tmp_path / ".agendum"
    current_runtime = runtime_paths(base_root)
    target_runtime = namespace_runtime_paths("example-org", base_root)

    from agendum.db import init_db

    init_db(current_runtime.db_path)
    init_db(target_runtime.db_path)

    app = AgendumApp(
        runtime=current_runtime,
        workspace_base_dir=base_root,
        config=AgendumConfig(orgs=["base"], sync_interval=9999, seen_delay=3),
    )
    app._app_focused = True

    stopped: list[bool] = []
    schedule_calls: list[bool] = []
    app._seen_timer = SimpleNamespace(stop=lambda: stopped.append(True))
    app._schedule_mark_seen = lambda: schedule_calls.append(True)  # type: ignore[assignment]
    app.refresh_table = lambda: None  # type: ignore[assignment]
    app._update_status_bar = lambda: None  # type: ignore[assignment]
    app._start_sync = lambda: None  # type: ignore[assignment]

    app._apply_runtime(
        target_runtime,
        AgendumConfig(orgs=["example-org"], sync_interval=9999, seen_delay=3),
    )

    assert stopped == [True]
    assert schedule_calls == [True]


# ── sleep/wake detection ─────────────────────────────────────────


def test_sync_status_shows_suspended_on_sleep(tmp_db) -> None:
    app = AgendumApp(db_path=tmp_db, config=AgendumConfig(orgs=[], sync_interval=9999))
    app._suspended = True

    assert app._format_sync_status() == "💤 sync suspended (waking up…)"


def test_sleep_detected_via_wall_vs_monotonic_drift(tmp_db) -> None:
    """Simulate macOS sleep: wall clock jumps forward but monotonic does not.

    We fake this by setting _last_sync_wall far in the past while keeping
    _last_sync_mono close to now, producing a large drift.
    """
    app = AgendumApp(db_path=tmp_db, config=AgendumConfig(orgs=[], sync_interval=60))
    app._last_sync = datetime.now(timezone.utc)  # pretend we've synced before

    now_mono = time.monotonic()
    now_wall = time.time()

    # Simulate: monotonic barely advanced (60s, one interval) but wall
    # clock jumped 5 minutes — the system was asleep for ~240s
    app._last_sync_mono = now_mono - 60
    app._last_sync_wall = now_wall - 300

    worker_calls: list = []
    app.run_worker = _capture_worker_call(worker_calls)  # type: ignore[assignment]
    app._update_status_bar = lambda: None  # type: ignore[assignment]

    app._start_sync()

    assert app._suspended is True
    assert len(worker_calls) == 1  # retry fires a sync immediately


def test_no_sleep_detected_on_normal_interval(tmp_db) -> None:
    """Normal tick: wall and monotonic advance roughly equally."""
    app = AgendumApp(db_path=tmp_db, config=AgendumConfig(orgs=[], sync_interval=60))
    app._last_sync = datetime.now(timezone.utc)

    now_mono = time.monotonic()
    now_wall = time.time()

    # Both clocks advanced ~60s — no drift
    app._last_sync_mono = now_mono - 60
    app._last_sync_wall = now_wall - 60

    worker_calls: list = []
    app.run_worker = _capture_worker_call(worker_calls)  # type: ignore[assignment]
    app._update_status_bar = lambda: None  # type: ignore[assignment]

    app._start_sync()

    assert app._suspended is False
    assert len(worker_calls) == 1


def test_first_sync_not_detected_as_sleep(tmp_db) -> None:
    """The very first sync (last_sync is None) should never be treated
    as a wake-from-sleep, even if the drift is large."""
    app = AgendumApp(db_path=tmp_db, config=AgendumConfig(orgs=[], sync_interval=60))
    app._last_sync = None  # no prior sync

    now_mono = time.monotonic()
    now_wall = time.time()

    # Huge drift but no prior sync
    app._last_sync_mono = now_mono - 10
    app._last_sync_wall = now_wall - 600

    worker_calls: list = []
    app.run_worker = _capture_worker_call(worker_calls)  # type: ignore[assignment]
    app._update_status_bar = lambda: None  # type: ignore[assignment]

    app._start_sync()

    assert app._suspended is False
    assert len(worker_calls) == 1


# ── retry-with-backoff after wake ────────────────────────────────


def test_wake_retry_attempts_sync_immediately(tmp_db) -> None:
    """After sleep detection, _retry_sync_after_wake should launch a
    sync worker immediately (retry 0 = no delay)."""
    app = AgendumApp(db_path=tmp_db, config=AgendumConfig(orgs=[], sync_interval=60))
    app._suspended = True
    app._wake_retry_count = 0

    worker_calls: list = []
    app.run_worker = _capture_worker_call(worker_calls)  # type: ignore[assignment]
    app._update_status_bar = lambda: None  # type: ignore[assignment]

    app._retry_sync_after_wake()

    assert app._sync_in_progress is True
    assert len(worker_calls) == 1


def test_wake_retry_backs_off_on_failure(tmp_db) -> None:
    """When a wake-retry sync fails, schedule the next attempt with
    exponential backoff."""
    app = AgendumApp(db_path=tmp_db, config=AgendumConfig(orgs=[], sync_interval=60))
    app._suspended = True
    app._wake_retry_count = 0

    timer_calls: list[tuple] = []
    app.set_timer = lambda delay, cb: timer_calls.append((delay, cb))  # type: ignore[assignment]
    app._update_status_bar = lambda: None  # type: ignore[assignment]

    # Simulate sync failure during wake retry
    app._handle_wake_retry_failure()

    assert app._wake_retry_count == 1
    assert app._suspended is True
    assert len(timer_calls) == 1
    # First backoff: 2 * 2^0 = 2s
    assert timer_calls[0][0] == 2


def test_wake_retry_backoff_caps_at_30s(tmp_db) -> None:
    """Backoff delay should cap at 30 seconds."""
    app = AgendumApp(db_path=tmp_db, config=AgendumConfig(orgs=[], sync_interval=60))
    app._suspended = True
    app._wake_retry_count = 4  # 2 * 2^4 = 32, should be capped to 30

    timer_calls: list[tuple] = []
    app.set_timer = lambda delay, cb: timer_calls.append((delay, cb))  # type: ignore[assignment]
    app._update_status_bar = lambda: None  # type: ignore[assignment]

    app._handle_wake_retry_failure()

    assert timer_calls[0][0] == 30


def test_wake_retry_success_clears_suspended(tmp_db) -> None:
    """When a wake-retry sync succeeds, clear suspended state."""
    app = AgendumApp(db_path=tmp_db, config=AgendumConfig(orgs=[], sync_interval=60))
    app._suspended = True
    app._wake_retry_count = 3

    app._handle_wake_retry_success()

    assert app._suspended is False
    assert app._wake_retry_count == 0


def test_wake_retry_backoff_sequence(tmp_db) -> None:
    """Verify the full backoff sequence: 2, 4, 8, 16, 30, 30."""
    app = AgendumApp(db_path=tmp_db, config=AgendumConfig(orgs=[], sync_interval=60))
    app._suspended = True
    app._wake_retry_count = 0

    timer_calls: list[tuple] = []
    app.set_timer = lambda delay, cb: timer_calls.append((delay, cb))  # type: ignore[assignment]
    app._update_status_bar = lambda: None  # type: ignore[assignment]

    expected_delays = [2, 4, 8, 16, 30, 30]
    for expected in expected_delays:
        app._handle_wake_retry_failure()
        assert timer_calls[-1][0] == expected


def test_start_sync_skipped_while_suspended(tmp_db) -> None:
    app = AgendumApp(db_path=tmp_db, config=AgendumConfig(orgs=[], sync_interval=60))
    app._suspended = True
    app._last_sync = datetime.now(timezone.utc)

    worker_calls: list = []
    app.run_worker = _capture_worker_call(worker_calls)  # type: ignore[assignment]
    app._update_status_bar = lambda: None  # type: ignore[assignment]

    app._start_sync()

    assert len(worker_calls) == 0


def test_retry_sync_skipped_while_sync_in_progress(tmp_db) -> None:
    app = AgendumApp(db_path=tmp_db, config=AgendumConfig(orgs=[], sync_interval=60))
    app._suspended = True
    app._sync_in_progress = True

    worker_calls: list = []
    app.run_worker = _capture_worker_call(worker_calls)  # type: ignore[assignment]
    app._update_status_bar = lambda: None  # type: ignore[assignment]

    app._retry_sync_after_wake()

    assert len(worker_calls) == 0
    assert app._sync_in_progress is True  # unchanged


def test_force_sync_clears_suspended_state(tmp_db) -> None:
    app = AgendumApp(db_path=tmp_db, config=AgendumConfig(orgs=[], sync_interval=60))
    app._suspended = True
    app._wake_retry_count = 5
    app._last_sync = datetime.now(timezone.utc)
    app._last_sync_mono = time.monotonic()
    app._last_sync_wall = time.time()

    worker_calls: list = []
    app.run_worker = _capture_worker_call(worker_calls)  # type: ignore[assignment]
    app._update_status_bar = lambda: None  # type: ignore[assignment]

    app.action_force_sync()

    assert app._suspended is False
    assert app._wake_retry_count == 0
    assert len(worker_calls) == 1


def test_stale_retry_timer_is_noop_after_force_sync(tmp_db) -> None:
    app = AgendumApp(db_path=tmp_db, config=AgendumConfig(orgs=[], sync_interval=60))
    app._suspended = False  # force sync already cleared this
    app._wake_retry_count = 0

    worker_calls: list = []
    app.run_worker = _capture_worker_call(worker_calls)  # type: ignore[assignment]
    app._update_status_bar = lambda: None  # type: ignore[assignment]

    # Simulate an orphaned backoff timer firing after suspended was cleared
    app._retry_sync_after_wake()

    assert len(worker_calls) == 0


def test_wake_retry_gives_up_after_max_retries(tmp_db) -> None:
    """After exceeding max retries, fall back to normal sync."""
    app = AgendumApp(db_path=tmp_db, config=AgendumConfig(orgs=[], sync_interval=60))
    app._suspended = True
    app._wake_retry_count = 10  # at the limit

    timer_calls: list[tuple] = []
    app.set_timer = lambda delay, cb: timer_calls.append((delay, cb))  # type: ignore[assignment]
    app._update_status_bar = lambda: None  # type: ignore[assignment]

    status_bar_calls: list = []
    app._update_status_bar = lambda: status_bar_calls.append(1)  # type: ignore[assignment]

    app._handle_wake_retry_failure()

    # Should give up, not schedule another retry
    assert app._suspended is False
    assert app._wake_retry_count == 0
    assert len(timer_calls) == 0
    assert len(status_bar_calls) == 1  # status bar refreshed on exhaustion


# ── on_worker_state_changed integration ──────────────────────────


def _make_worker_event(state: WorkerState, group: str = "sync:0", error: BaseException | None = None, result=None):
    worker = MagicMock(spec=Worker)
    worker.group = group
    worker.error = error
    worker.result = result
    event = MagicMock(spec=Worker.StateChanged)
    event.worker = worker
    event.state = state
    return event


def test_worker_error_while_suspended_triggers_retry(tmp_db) -> None:
    app = AgendumApp(db_path=tmp_db, config=AgendumConfig(orgs=[], sync_interval=60))
    app._suspended = True
    app._wake_retry_count = 2
    app._sync_in_progress = True

    timer_calls: list[tuple] = []
    app.set_timer = lambda delay, cb: timer_calls.append((delay, cb))  # type: ignore[assignment]
    app._update_status_bar = lambda: None  # type: ignore[assignment]

    event = _make_worker_event(WorkerState.ERROR, error=RuntimeError("network"))
    app.on_worker_state_changed(event)

    assert app._sync_in_progress is False
    assert app._wake_retry_count == 3
    assert len(timer_calls) == 1  # backoff timer scheduled


def test_worker_success_while_suspended_clears_suspended(tmp_db) -> None:
    app = AgendumApp(db_path=tmp_db, config=AgendumConfig(orgs=[], sync_interval=60))
    app._suspended = True
    app._wake_retry_count = 2
    app._sync_in_progress = True
    app._app_focused = True

    app.refresh_table = lambda: None  # type: ignore[assignment]
    app._update_status_bar = lambda: None  # type: ignore[assignment]

    event = _make_worker_event(WorkerState.SUCCESS, result=(0, 0, False, None))
    app.on_worker_state_changed(event)

    assert app._sync_in_progress is False
    assert app._suspended is False
    assert app._wake_retry_count == 0
    assert app._last_sync is not None


def test_worker_error_while_not_suspended_does_not_retry(tmp_db) -> None:
    app = AgendumApp(db_path=tmp_db, config=AgendumConfig(orgs=[], sync_interval=60))
    app._suspended = False
    app._sync_in_progress = True

    timer_calls: list[tuple] = []
    app.set_timer = lambda delay, cb: timer_calls.append((delay, cb))  # type: ignore[assignment]
    app._update_status_bar = lambda: None  # type: ignore[assignment]

    event = _make_worker_event(WorkerState.ERROR, error=RuntimeError("network"))
    app.on_worker_state_changed(event)

    assert app._sync_in_progress is False
    assert len(timer_calls) == 0  # no retry scheduled


def test_start_sync_skipped_while_sync_in_progress(tmp_db) -> None:
    app = AgendumApp(db_path=tmp_db, config=AgendumConfig(orgs=[], sync_interval=60))
    app._last_sync = datetime.now(timezone.utc)
    app._sync_in_progress = True
    app._last_sync_mono = time.monotonic()
    app._last_sync_wall = time.time()

    worker_calls: list = []
    app.run_worker = _capture_worker_call(worker_calls)  # type: ignore[assignment]
    app._update_status_bar = lambda: None  # type: ignore[assignment]

    app._start_sync()

    assert len(worker_calls) == 0
    assert app._sync_in_progress is True

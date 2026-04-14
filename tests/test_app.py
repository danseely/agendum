import time
from datetime import datetime, timezone

import pytest
from agendum.app import AgendumApp
from agendum.config import AgendumConfig


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
    app.run_worker = lambda *a, **kw: worker_calls.append(1)  # type: ignore[assignment]
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
    app.run_worker = lambda *a, **kw: worker_calls.append(1)  # type: ignore[assignment]
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
    app.run_worker = lambda *a, **kw: worker_calls.append(1)  # type: ignore[assignment]
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
    app.run_worker = lambda *a, **kw: worker_calls.append(1)  # type: ignore[assignment]
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
    app.run_worker = lambda *a, **kw: worker_calls.append(1)  # type: ignore[assignment]
    app._update_status_bar = lambda: None  # type: ignore[assignment]

    app._start_sync()

    assert len(worker_calls) == 0


def test_retry_sync_skipped_while_sync_in_progress(tmp_db) -> None:
    app = AgendumApp(db_path=tmp_db, config=AgendumConfig(orgs=[], sync_interval=60))
    app._suspended = True
    app._sync_in_progress = True

    worker_calls: list = []
    app.run_worker = lambda *a, **kw: worker_calls.append(1)  # type: ignore[assignment]
    app._update_status_bar = lambda: None  # type: ignore[assignment]

    app._retry_sync_after_wake()

    assert len(worker_calls) == 0
    assert app._sync_in_progress is True  # unchanged


def test_wake_retry_gives_up_after_max_retries(tmp_db) -> None:
    """After exceeding max retries, fall back to normal sync."""
    app = AgendumApp(db_path=tmp_db, config=AgendumConfig(orgs=[], sync_interval=60))
    app._suspended = True
    app._wake_retry_count = 10  # at the limit

    timer_calls: list[tuple] = []
    app.set_timer = lambda delay, cb: timer_calls.append((delay, cb))  # type: ignore[assignment]
    app._update_status_bar = lambda: None  # type: ignore[assignment]

    app._handle_wake_retry_failure()

    # Should give up, not schedule another retry
    assert app._suspended is False
    assert app._wake_retry_count == 0
    assert len(timer_calls) == 0

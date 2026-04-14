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

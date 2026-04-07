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

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

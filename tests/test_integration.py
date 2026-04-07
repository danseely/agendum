import pytest
from agendum.app import AgendumApp
from agendum.config import AgendumConfig
from agendum.db import add_task, init_db
from pathlib import Path


@pytest.mark.asyncio
async def test_table_displays_grouped_tasks(tmp_db: Path) -> None:
    init_db(tmp_db)

    add_task(tmp_db, title="fix: concurrent mode bugs", source="pr_authored", status="awaiting review",
             project="react", gh_number=412, gh_url="https://github.com/facebook/react/pull/412")
    add_task(tmp_db, title="feat: new hook API", source="pr_review", status="review requested",
             project="jest", gh_number=89, gh_url="https://github.com/facebook/jest/pull/89",
             gh_author="sjohnson", gh_author_name="Sarah")
    add_task(tmp_db, title="add missing type exports", source="issue", status="open",
             project="react", gh_number=98, gh_url="https://github.com/facebook/react/issues/98")
    add_task(tmp_db, title="update docs", source="manual", status="active")

    config = AgendumConfig(orgs=[], sync_interval=9999)
    app = AgendumApp(db_path=tmp_db, config=config)

    async with app.run_test() as pilot:
        assert app.is_running
        # 4 tasks + section headers = more than 4 rows
        assert len(app._task_rows) > 4
        await pilot.press("q")


@pytest.mark.asyncio
async def test_quick_create_adds_manual_task(tmp_db: Path) -> None:
    init_db(tmp_db)
    config = AgendumConfig(orgs=[], sync_interval=9999)
    app = AgendumApp(db_path=tmp_db, config=config)

    async with app.run_test() as pilot:
        input_widget = app.query_one("#create-input")
        input_widget.focus()
        await pilot.pause()

        input_widget.value = "my new task"
        await pilot.press("enter")
        await pilot.pause()

        from agendum.db import get_active_tasks
        tasks = get_active_tasks(tmp_db)
        assert len(tasks) == 1
        assert tasks[0]["title"] == "my new task"
        assert tasks[0]["source"] == "manual"

        await pilot.press("q")

from pathlib import Path

import pytest

from agendum.db import add_task, init_db, update_task
from agendum.task_api import create_manual_task, get_task, list_tasks, search_tasks


def test_list_tasks_filters_and_seen_flag(tmp_db: Path) -> None:
    init_db(tmp_db)
    alpha_id = add_task(
        tmp_db,
        title="Alpha PR",
        source="pr_authored",
        status="awaiting review",
        project="alpha",
        gh_repo="org/alpha",
        gh_url="https://github.com/org/alpha/pull/1",
    )
    beta_id = add_task(
        tmp_db,
        title="Beta issue",
        source="issue",
        status="open",
        project="beta",
        gh_repo="org/beta",
        gh_url="https://github.com/org/beta/issues/2",
    )
    gamma_id = add_task(
        tmp_db,
        title="Gamma review",
        source="pr_review",
        status="review requested",
        project="gamma",
        gh_repo="org/gamma",
        gh_url="https://github.com/org/gamma/pull/3",
    )
    update_task(tmp_db, beta_id, seen=0)
    update_task(tmp_db, gamma_id, seen=0)
    update_task(tmp_db, alpha_id, seen=1)

    authored = list_tasks(tmp_db, source="pr_authored")
    assert len(authored) == 1
    assert authored[0]["title"] == "Alpha PR"

    unseen = list_tasks(tmp_db, include_seen=False)
    assert {task["title"] for task in unseen} == {"Beta issue", "Gamma review"}

    project = list_tasks(tmp_db, project="gamma")
    assert len(project) == 1
    assert project[0]["title"] == "Gamma review"


@pytest.mark.parametrize(
    ("query", "title"),
    [
        ("heartbeat", "Heartbeat Title Match"),
        ("platform", "Project Match"),
        ("telemetry-api-aa", "Repo Match"),
        ("morgan", "Author Match"),
        ("org/link/pull/34", "URL Match"),
        ("urgent", "Tags Match"),
        ("alex api", "Multi Token Match"),
    ],
)
def test_search_tasks_matches_across_fields(tmp_db: Path, query: str, title: str) -> None:
    init_db(tmp_db)
    add_task(
        tmp_db,
        title="Heartbeat Title Match",
        source="manual",
        status="active",
    )
    add_task(
        tmp_db,
        title="Project Match",
        source="pr_authored",
        status="awaiting review",
        project="platform",
        gh_repo="org/platform",
        gh_url="https://github.com/org/platform/pull/1",
    )
    add_task(
        tmp_db,
        title="Repo Match",
        source="pr_authored",
        status="awaiting review",
        project="repo",
        gh_repo="adadaptedinc/telemetry-api-aa",
        gh_url="https://github.com/adadaptedinc/telemetry-api-aa/pull/34",
    )
    add_task(
        tmp_db,
        title="Author Match",
        source="pr_review",
        status="review requested",
        project="review",
        gh_repo="org/review",
        gh_url="https://github.com/org/review/pull/2",
        gh_author="morgan-stone",
        gh_author_name="Morgan Stone",
    )
    add_task(
        tmp_db,
        title="URL Match",
        source="issue",
        status="open",
        project="link",
        gh_repo="org/link",
        gh_url="https://github.com/org/link/pull/34",
    )
    add_task(
        tmp_db,
        title="Tags Match",
        source="manual",
        status="active",
        tags='["urgent", "review"]',
    )
    add_task(
        tmp_db,
        title="Multi Token Match",
        source="pr_review",
        status="review requested",
        project="api",
        gh_repo="org/api",
        gh_url="https://github.com/org/api/pull/99",
        gh_author="alex-radaev",
        gh_author_name="Alex Radaev",
        tags='["api"]',
    )

    results = search_tasks(tmp_db, query=query)
    assert [task["title"] for task in results] == [title]


def test_get_task_returns_present_and_missing(tmp_db: Path) -> None:
    init_db(tmp_db)
    task_id = add_task(
        tmp_db,
        title="Lookup me",
        source="manual",
        status="active",
        tags='["one"]',
    )

    task = get_task(tmp_db, task_id)
    assert task is not None
    assert task["id"] == task_id
    assert task["tags"] == ["one"]
    assert get_task(tmp_db, 9999) is None


def test_create_manual_task_sets_defaults_and_tags(tmp_db: Path) -> None:
    init_db(tmp_db)

    task = create_manual_task(tmp_db, title="  Write docs  ", project="docs", tags=["alpha", "beta"])

    assert task["title"] == "Write docs"
    assert task["source"] == "manual"
    assert task["status"] == "active"
    assert task["project"] == "docs"
    assert task["tags"] == ["alpha", "beta"]


@pytest.mark.parametrize(
    ("callable_name", "kwargs"),
    [
        ("create", {"title": "   "}),
        ("list", {"limit": 0}),
        ("search", {"query": "   ", "limit": 201}),
    ],
)
def test_validation_errors(tmp_db: Path, callable_name: str, kwargs: dict) -> None:
    init_db(tmp_db)

    if callable_name == "create":
        with pytest.raises(ValueError):
            create_manual_task(tmp_db, **kwargs)
    elif callable_name == "list":
        with pytest.raises(ValueError):
            list_tasks(tmp_db, **kwargs)
    else:
        with pytest.raises(ValueError):
            search_tasks(tmp_db, **kwargs)

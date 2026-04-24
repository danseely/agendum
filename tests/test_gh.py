import inspect
import json
from pathlib import Path
import re
from types import SimpleNamespace
import pytest

from agendum.gh import (
    auth_status,
    auth_login,
    recover_gh_auth,
    default_gh_config_dir,
    derive_authored_pr_status,
    derive_review_pr_status,
    derive_issue_status,
    fetch_review_detail,
    hydrate_pull_requests,
    get_gh_config_dir,
    _run_gh,
    parse_author_first_name,
    extract_repo_short_name,
    refresh_gh_config_dir,
    seed_gh_config_dir,
    set_gh_config_dir,
    use_gh_config_dir,
)


async def call_gh_primitive(name: str, *args, **kwargs):
    from agendum import gh

    result = getattr(gh, name)(*args, **kwargs)
    if inspect.isawaitable(result):
        return await result
    return result


def query_arg(args: tuple[str, ...]) -> str:
    return next(arg.split("=", 1)[1] for arg in args if arg.startswith("query="))


def authored_pr_status_with_review_feedback(**overrides: object) -> str:
    kwargs = {
        "is_draft": False,
        "review_decision": None,
        "state": "OPEN",
        "has_review_requests": False,
        "latest_commit_time": None,
        "latest_comment_review_id": None,
        "latest_comment_review_time": None,
        "author_login": "author",
        "review_threads": [],
    }
    kwargs.update(overrides)
    return derive_authored_pr_status(**kwargs)


def test_authored_pr_draft() -> None:
    assert derive_authored_pr_status(is_draft=True, review_decision=None, state="OPEN") == "draft"


def test_authored_pr_open_no_reviewers() -> None:
    assert derive_authored_pr_status(is_draft=False, review_decision=None, state="OPEN", has_review_requests=False) == "open"


def test_authored_pr_awaiting_review() -> None:
    assert derive_authored_pr_status(is_draft=False, review_decision=None, state="OPEN", has_review_requests=True) == "awaiting review"


def test_authored_pr_changes_requested_overrides_review_received() -> None:
    assert authored_pr_status_with_review_feedback(
        review_decision="CHANGES_REQUESTED",
        latest_comment_review_id="review-1",
        latest_comment_review_time="2026-04-10T12:00:00Z",
        review_threads=[
            {
                "isResolved": False,
                "comments": {
                    "nodes": [
                        {
                            "createdAt": "2026-04-10T12:01:00Z",
                            "author": {"login": "reviewer"},
                            "pullRequestReview": {"id": "review-1"},
                        },
                    ],
                },
            },
        ],
    ) == "changes requested"


def test_authored_pr_approved() -> None:
    assert derive_authored_pr_status(is_draft=False, review_decision="APPROVED", state="OPEN") == "approved"


def test_authored_pr_merged() -> None:
    assert derive_authored_pr_status(is_draft=False, review_decision=None, state="MERGED") == "merged"


def test_authored_pr_closed() -> None:
    assert derive_authored_pr_status(is_draft=False, review_decision=None, state="CLOSED") == "closed"


def test_authored_pr_review_received_for_non_blocking_feedback() -> None:
    assert authored_pr_status_with_review_feedback(
        latest_comment_review_id="review-1",
        latest_comment_review_time="2026-04-10T12:00:00Z",
        review_threads=[
            {
                "isResolved": False,
                "comments": {
                    "nodes": [
                        {
                            "createdAt": "2026-04-10T12:01:00Z",
                            "author": {"login": "reviewer"},
                            "pullRequestReview": {"id": "review-1"},
                        },
                    ],
                },
            },
        ],
    ) == "review received"


def test_authored_pr_review_received_stays_active_with_sibling_thread_reply() -> None:
    assert authored_pr_status_with_review_feedback(
        qualifying_reviews=[
            {
                "id": "review-1",
                "submittedAt": "2026-04-10T12:00:00Z",
            },
        ],
        review_threads=[
            {
                "isResolved": False,
                "comments": {
                    "nodes": [
                        {
                            "createdAt": "2026-04-10T12:01:00Z",
                            "author": {"login": "reviewer"},
                            "pullRequestReview": {"id": "review-1"},
                        },
                        {
                            "createdAt": "2026-04-10T12:02:00Z",
                            "author": {"login": "author"},
                            "pullRequestReview": {"id": None},
                        },
                    ],
                },
            },
            {
                "isResolved": False,
                "comments": {
                    "nodes": [
                        {
                            "createdAt": "2026-04-10T12:03:00Z",
                            "author": {"login": "reviewer"},
                            "pullRequestReview": {"id": "review-1"},
                        },
                    ],
                },
            },
        ],
    ) == "review received"


def test_authored_pr_review_received_stays_active_for_older_unresolved_review() -> None:
    assert authored_pr_status_with_review_feedback(
        qualifying_reviews=[
            {
                "id": "review-old",
                "submittedAt": "2026-04-10T11:00:00Z",
            },
            {
                "id": "review-new",
                "submittedAt": "2026-04-10T12:00:00Z",
            },
        ],
        review_threads=[
            {
                "isResolved": False,
                "comments": {
                    "nodes": [
                        {
                            "createdAt": "2026-04-10T11:01:00Z",
                            "author": {"login": "reviewer"},
                            "pullRequestReview": {"id": "review-old"},
                        },
                    ],
                },
            },
            {
                "isResolved": True,
                "comments": {
                    "nodes": [
                        {
                            "createdAt": "2026-04-10T12:01:00Z",
                            "author": {"login": "reviewer"},
                            "pullRequestReview": {"id": "review-new"},
                        },
                    ],
                },
            },
        ],
    ) == "review received"


def test_authored_pr_review_received_clears_after_author_reply() -> None:
    assert authored_pr_status_with_review_feedback(
        latest_comment_review_id="review-1",
        latest_comment_review_time="2026-04-10T12:00:00Z",
        review_threads=[
            {
                "isResolved": False,
                "comments": {
                    "nodes": [
                        {
                            "createdAt": "2026-04-10T12:01:00Z",
                            "author": {"login": "reviewer"},
                            "pullRequestReview": {"id": "review-1"},
                        },
                        {
                            "createdAt": "2026-04-10T12:05:00Z",
                            "author": {"login": "author"},
                            "pullRequestReview": {"id": None},
                        },
                    ],
                },
            },
        ],
    ) == "open"


def test_authored_pr_review_received_clears_after_threads_resolved() -> None:
    assert authored_pr_status_with_review_feedback(
        latest_comment_review_id="review-1",
        latest_comment_review_time="2026-04-10T12:00:00Z",
        review_threads=[
            {
                "isResolved": True,
                "comments": {
                    "nodes": [
                        {
                            "createdAt": "2026-04-10T12:01:00Z",
                            "author": {"login": "reviewer"},
                            "pullRequestReview": {"id": "review-1"},
                        },
                    ],
                },
            },
        ],
    ) == "open"


def test_authored_pr_review_received_persists_after_push_when_feedback_threads_exist() -> None:
    assert authored_pr_status_with_review_feedback(
        latest_comment_review_id="review-1",
        latest_comment_review_time="2026-04-10T12:00:00Z",
        latest_commit_time="2026-04-10T12:05:00Z",
        review_threads=[
            {
                "isResolved": False,
                "comments": {
                    "nodes": [
                        {
                            "createdAt": "2026-04-10T12:01:00Z",
                            "author": {"login": "reviewer"},
                            "pullRequestReview": {"id": "review-1"},
                        },
                    ],
                },
            },
        ],
    ) == "review received"


def test_authored_pr_review_received_clears_after_push_without_feedback_threads() -> None:
    assert authored_pr_status_with_review_feedback(
        latest_comment_review_id="review-1",
        latest_comment_review_time="2026-04-10T12:00:00Z",
        latest_commit_time="2026-04-10T12:05:00Z",
    ) == "open"


def test_review_pr_requested() -> None:
    assert derive_review_pr_status(user_has_reviewed=False, new_commits_since_review=False) == "review requested"


def test_auth_login_uses_isolated_gh_config_dir(tmp_path, monkeypatch) -> None:
    calls = {}

    def fake_run(args, *, env, check):
        calls["args"] = args
        calls["env"] = env
        calls["check"] = check
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr("agendum.gh.subprocess.run", fake_run)

    gh_dir = tmp_path / "workspace" / "gh"
    assert auth_login(gh_dir) is True
    assert gh_dir.is_dir()
    assert calls["args"] == ["gh", "auth", "login"]
    assert calls["env"]["GH_CONFIG_DIR"] == str(gh_dir)
    assert calls["check"] is False


def test_auth_status_uses_isolated_gh_config_dir(tmp_path, monkeypatch) -> None:
    calls = {}

    def fake_run(args, *, capture_output, text, env, check):
        calls["args"] = args
        calls["capture_output"] = capture_output
        calls["text"] = text
        calls["env"] = env
        calls["check"] = check
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr("agendum.gh.subprocess.run", fake_run)

    gh_dir = tmp_path / "workspace" / "gh"
    assert auth_status(gh_dir) is True
    assert calls["args"] == ["gh", "auth", "status"]
    assert calls["capture_output"] is True
    assert calls["text"] is True
    assert calls["env"]["GH_CONFIG_DIR"] == str(gh_dir)
    assert calls["check"] is False


def test_default_gh_config_dir_prefers_xdg_env(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("GH_CONFIG_DIR", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))

    assert default_gh_config_dir() == tmp_path / "xdg" / "gh"


def test_seed_gh_config_dir_copies_default_auth_files(tmp_path) -> None:
    source_dir = tmp_path / "source-gh"
    source_dir.mkdir()
    (source_dir / "hosts.yml").write_text("github.com:\n  oauth_token: token\n")
    (source_dir / "config.yml").write_text("git_protocol: ssh\n")

    target_dir = tmp_path / "workspace-gh"
    seed_gh_config_dir(target_dir, source_dir=source_dir)

    assert (target_dir / "hosts.yml").read_text() == (source_dir / "hosts.yml").read_text()
    assert (target_dir / "config.yml").read_text() == (source_dir / "config.yml").read_text()
    assert (target_dir / "hosts.yml").stat().st_mode & 0o777 == 0o600
    assert (target_dir / "config.yml").stat().st_mode & 0o777 == 0o600


def test_seed_gh_config_dir_preserves_existing_workspace_auth(tmp_path) -> None:
    source_dir = tmp_path / "source-gh"
    source_dir.mkdir()
    (source_dir / "hosts.yml").write_text("github.com:\n  oauth_token: old\n")

    target_dir = tmp_path / "workspace-gh"
    target_dir.mkdir()
    (target_dir / "hosts.yml").write_text("github.com:\n  oauth_token: current\n")

    seed_gh_config_dir(target_dir, source_dir=source_dir)

    assert (target_dir / "hosts.yml").read_text() == "github.com:\n  oauth_token: current\n"


def test_refresh_gh_config_dir_overwrites_existing_workspace_auth(tmp_path) -> None:
    source_dir = tmp_path / "source-gh"
    source_dir.mkdir()
    (source_dir / "hosts.yml").write_text("github.com:\n  oauth_token: fresh\n")
    (source_dir / "config.yml").write_text("git_protocol: ssh\n")

    target_dir = tmp_path / "workspace-gh"
    target_dir.mkdir()
    (target_dir / "hosts.yml").write_text("github.com:\n  oauth_token: stale\n")
    (target_dir / "config.yml").write_text("git_protocol: https\n")

    refresh_gh_config_dir(target_dir, source_dir=source_dir)

    assert (target_dir / "hosts.yml").read_text() == "github.com:\n  oauth_token: fresh\n"
    assert (target_dir / "config.yml").read_text() == "git_protocol: ssh\n"


def test_recover_gh_auth_prefers_valid_workspace_auth(tmp_path, monkeypatch) -> None:
    gh_dir = tmp_path / "workspace" / "gh"
    source_dir = tmp_path / "default" / "gh"

    refresh_calls: list[tuple[Path, Path | None]] = []
    login_calls: list[Path] = []

    monkeypatch.setattr("agendum.gh.auth_status", lambda path=None: path == gh_dir)
    monkeypatch.setattr(
        "agendum.gh.refresh_gh_config_dir",
        lambda target, source_dir=None: refresh_calls.append((target, source_dir)),
    )
    monkeypatch.setattr("agendum.gh.auth_login", lambda target: login_calls.append(target) or True)

    assert recover_gh_auth(gh_dir, source_dir=source_dir, interactive=True) is True
    assert refresh_calls == []
    assert login_calls == []


def test_recover_gh_auth_refreshes_workspace_from_default_auth(tmp_path, monkeypatch) -> None:
    gh_dir = tmp_path / "workspace" / "gh"
    source_dir = tmp_path / "default" / "gh"
    status_by_dir = {
        gh_dir: False,
        source_dir: True,
    }
    refresh_calls: list[tuple[Path, Path | None]] = []

    def fake_auth_status(path=None):
        return status_by_dir.get(path, False)

    def fake_refresh(target, source_dir=None):
        refresh_calls.append((target, source_dir))
        status_by_dir[target] = True

    monkeypatch.setattr("agendum.gh.auth_status", fake_auth_status)
    monkeypatch.setattr("agendum.gh.refresh_gh_config_dir", fake_refresh)
    monkeypatch.setattr("agendum.gh.auth_login", lambda _target: pytest.fail("unexpected interactive login"))

    assert recover_gh_auth(gh_dir, source_dir=source_dir) is True
    assert refresh_calls == [(gh_dir, source_dir)]


def test_recover_gh_auth_prefers_current_workspace_auth_before_default(tmp_path, monkeypatch) -> None:
    gh_dir = tmp_path / "target" / "gh"
    source_dir = tmp_path / "current" / "gh"
    default_dir = tmp_path / "default" / "gh"
    status_by_dir = {
        gh_dir: False,
        source_dir: True,
        default_dir: False,
    }
    refresh_calls: list[tuple[Path, Path | None]] = []

    def fake_auth_status(path=None):
        return status_by_dir.get(path, False)

    def fake_refresh(target, source_dir=None):
        refresh_calls.append((target, source_dir))
        status_by_dir[target] = True

    monkeypatch.setattr("agendum.gh.auth_status", fake_auth_status)
    monkeypatch.setattr("agendum.gh.refresh_gh_config_dir", fake_refresh)
    monkeypatch.setattr("agendum.gh.default_gh_config_dir", lambda: default_dir)
    monkeypatch.setattr("agendum.gh.auth_login", lambda _target: pytest.fail("unexpected interactive login"))

    assert recover_gh_auth(gh_dir, source_dir=source_dir) is True
    assert refresh_calls == [(gh_dir, source_dir)]


def test_recover_gh_auth_falls_back_to_interactive_login(tmp_path, monkeypatch) -> None:
    gh_dir = tmp_path / "workspace" / "gh"
    source_dir = tmp_path / "default" / "gh"
    login_calls: list[Path] = []

    monkeypatch.setattr("agendum.gh.auth_status", lambda path=None: False)
    monkeypatch.setattr(
        "agendum.gh.refresh_gh_config_dir",
        lambda _target, source_dir=None: pytest.fail(f"unexpected refresh from {source_dir}"),
    )
    monkeypatch.setattr("agendum.gh.auth_login", lambda target: login_calls.append(target) or True)

    assert recover_gh_auth(gh_dir, source_dir=source_dir, interactive=True) is True
    assert login_calls == [gh_dir]


def test_recover_gh_auth_force_refreshes_even_when_workspace_auth_exists(
    tmp_path,
    monkeypatch,
) -> None:
    gh_dir = tmp_path / "workspace" / "gh"
    source_dir = tmp_path / "current" / "gh"
    status_by_dir = {
        gh_dir: True,
        source_dir: True,
    }
    refresh_calls: list[tuple[Path, Path | None]] = []

    def fake_auth_status(path=None):
        return status_by_dir.get(path, False)

    def fake_refresh(target, source_dir=None):
        refresh_calls.append((target, source_dir))

    monkeypatch.setattr("agendum.gh.auth_status", fake_auth_status)
    monkeypatch.setattr("agendum.gh.refresh_gh_config_dir", fake_refresh)
    monkeypatch.setattr("agendum.gh.auth_login", lambda _target: pytest.fail("unexpected interactive login"))

    assert recover_gh_auth(
        gh_dir,
        source_dir=source_dir,
        interactive=True,
        force_refresh=True,
    ) is True
    assert refresh_calls == [(gh_dir, source_dir)]


@pytest.mark.asyncio
async def test_run_gh_uses_active_workspace_config_dir(tmp_path, monkeypatch) -> None:
    calls = {}

    class FakeProcess:
        returncode = 0

        async def communicate(self):
            return b"ok\n", b""

    async def fake_create_subprocess_exec(*args, **kwargs):
        calls["args"] = args
        calls["kwargs"] = kwargs
        return FakeProcess()

    monkeypatch.setattr("agendum.gh.asyncio.create_subprocess_exec", fake_create_subprocess_exec)

    gh_dir = tmp_path / "workspace" / "gh"
    set_gh_config_dir(gh_dir)
    try:
        assert await _run_gh("api", "user", "--jq", ".login") == "ok\n"
    finally:
        set_gh_config_dir(None)

    assert calls["args"] == ("gh", "api", "user", "--jq", ".login")
    assert calls["kwargs"]["env"]["GH_CONFIG_DIR"] == str(gh_dir)


@pytest.mark.asyncio
async def test_run_gh_prefers_task_local_workspace_config_dir(tmp_path, monkeypatch) -> None:
    calls = {}

    class FakeProcess:
        returncode = 0

        async def communicate(self):
            return b"ok\n", b""

    async def fake_create_subprocess_exec(*args, **kwargs):
        calls["args"] = args
        calls["kwargs"] = kwargs
        return FakeProcess()

    monkeypatch.setattr("agendum.gh.asyncio.create_subprocess_exec", fake_create_subprocess_exec)

    global_dir = tmp_path / "global" / "gh"
    task_dir = tmp_path / "task" / "gh"
    set_gh_config_dir(global_dir)
    try:
        with use_gh_config_dir(task_dir):
            assert get_gh_config_dir() == task_dir
            assert await _run_gh("api", "user", "--jq", ".login") == "ok\n"
        assert get_gh_config_dir() == global_dir
    finally:
        set_gh_config_dir(None)

    assert calls["args"] == ("gh", "api", "user", "--jq", ".login")
    assert calls["kwargs"]["env"]["GH_CONFIG_DIR"] == str(task_dir)


def test_review_pr_reviewed() -> None:
    assert derive_review_pr_status(user_has_reviewed=True, new_commits_since_review=False) == "reviewed"


def test_review_pr_re_review() -> None:
    assert derive_review_pr_status(user_has_reviewed=True, new_commits_since_review=True) == "re-review requested"


def test_review_pr_re_review_via_explicit_rerequest() -> None:
    assert derive_review_pr_status(
        user_has_reviewed=True,
        new_commits_since_review=False,
        re_requested_after_review=True,
    ) == "re-review requested"


def test_issue_open() -> None:
    assert derive_issue_status(state="OPEN", has_linked_pr=False) == "open"


def test_issue_in_progress() -> None:
    assert derive_issue_status(state="OPEN", has_linked_pr=True) == "in progress"


def test_issue_closed() -> None:
    assert derive_issue_status(state="CLOSED", has_linked_pr=False) == "closed"


def test_parse_author_first_name() -> None:
    assert parse_author_first_name("Example Reviewer") == "Example"
    assert parse_author_first_name("Reviewer") == "Reviewer"
    assert parse_author_first_name(None) is None
    assert parse_author_first_name("") is None


def test_extract_repo_short_name() -> None:
    assert extract_repo_short_name("example-org/example-repo") == "example-repo"
    assert extract_repo_short_name("org/repo") == "repo"


@pytest.mark.asyncio
async def test_fetch_review_detail_uses_valid_author_name_query(monkeypatch) -> None:
    calls: list[tuple[str, ...]] = []

    async def fake_run_gh(*args: str) -> str:
        calls.append(args)
        return json.dumps(
            {
                "data": {
                    "repository": {
                        "pullRequest": {
                            "author": {
                                "login": "reviewer",
                                "name": "Review Person",
                            },
                        },
                    },
                },
            }
        )

    from agendum import gh

    monkeypatch.setattr(gh, "_run_gh", fake_run_gh)

    result = await fetch_review_detail("example-org", "example-repo", 34, "current-user")

    assert result["data"]["repository"]["pullRequest"]["author"]["name"] == "Review Person"
    call = calls[0]
    query_arg = next(arg for arg in call if arg.startswith("query="))
    assert "$user" not in query_arg
    assert "-F" in call
    assert "user=current-user" not in call
    assert "... on User" in query_arg


@pytest.mark.asyncio
async def test_hydrate_pull_requests_uses_node_alias_query(monkeypatch) -> None:
    calls: list[tuple[str, ...]] = []

    async def fake_run_gh(*args: str) -> str:
        calls.append(args)
        return json.dumps(
            {
                "data": {
                    "n0": {"id": "PR_node_1", "title": "One"},
                    "n1": {"id": "PR_node_2", "title": "Two"},
                },
            }
        )

    from agendum import gh

    monkeypatch.setattr(gh, "_run_gh", fake_run_gh)

    result, ok = await hydrate_pull_requests(["PR_node_1", "PR_node_2"])

    assert ok is True
    assert [item["id"] for item in result] == ["PR_node_1", "PR_node_2"]
    query_arg = next(arg for arg in calls[0] if arg.startswith("query="))
    assert "n0: node(id: \"PR_node_1\")" in query_arg
    assert "n1: node(id: \"PR_node_2\")" in query_arg
    assert "... on PullRequest" in query_arg


@pytest.mark.asyncio
async def test_search_authored_prs_paginates_and_dedupes_by_node_id(monkeypatch) -> None:
    calls: list[tuple[str, ...]] = []
    responses = [
        {
            "data": {
                "search": {
                    "pageInfo": {
                        "hasNextPage": True,
                        "endCursor": "CURSOR_A",
                    },
                    "nodes": [
                        {"id": "PR_1", "number": 1, "title": "One"},
                        {"id": "PR_2", "number": 2, "title": "Two"},
                    ],
                },
            },
        },
        {
            "data": {
                "search": {
                    "pageInfo": {
                        "hasNextPage": False,
                        "endCursor": None,
                    },
                    "nodes": [
                        {"id": "PR_2", "number": 2, "title": "Two"},
                        {"id": "PR_3", "number": 3, "title": "Three"},
                    ],
                },
            },
        },
        {
            "data": {
                "search": {
                    "pageInfo": {
                        "hasNextPage": False,
                        "endCursor": None,
                    },
                    "nodes": [
                        {"id": "PR_3", "number": 3, "title": "Three"},
                    ],
                },
            },
        },
    ]

    async def fake_run_gh(*args: str) -> str:
        calls.append(args)
        return json.dumps(responses.pop(0))

    from agendum import gh

    monkeypatch.setattr(gh, "_run_gh", fake_run_gh)

    prs, ok = await call_gh_primitive("search_authored_prs", ["org-a", "org-b"], "dan", page_size=2)

    assert ok is True
    assert [pr["id"] for pr in prs] == ["PR_1", "PR_2", "PR_3"]
    assert len(calls) == 3
    assert "pageInfo" in query_arg(calls[0])
    assert "CURSOR_A" in " ".join(calls[1])


@pytest.mark.asyncio
async def test_search_assigned_issues_uses_issue_search_query(monkeypatch) -> None:
    calls: list[tuple[str, ...]] = []

    async def fake_run_gh(*args: str) -> str:
        calls.append(args)
        return json.dumps(
            {
                "data": {
                    "search": {
                        "pageInfo": {
                            "hasNextPage": False,
                            "endCursor": None,
                        },
                        "nodes": [
                            {"id": "ISSUE_1", "number": 11, "title": "Assigned"},
                        ],
                    },
                },
            }
        )

    from agendum import gh

    monkeypatch.setattr(gh, "_run_gh", fake_run_gh)

    issues, ok = await call_gh_primitive("search_assigned_issues", ["org-a"], "dan", page_size=7)

    assert ok is True
    assert [issue["id"] for issue in issues] == ["ISSUE_1"]
    assert "search(type: ISSUE" in query_arg(calls[0])


@pytest.mark.asyncio
async def test_hydrate_pull_requests_batches_and_skips_nulls(monkeypatch) -> None:
    calls: list[tuple[str, ...]] = []
    response_nodes = [
        [
            {"id": "PR_1", "number": 1, "title": "One"},
            None,
        ],
        [
            {"id": "PR_3", "number": 3, "title": "Three"},
        ],
    ]

    async def fake_run_gh(*args: str) -> str:
        calls.append(args)
        aliases = re.findall(r"(\w+)\s*:\s*node\(", query_arg(args))
        nodes = response_nodes[len(calls) - 1]
        return json.dumps(
            {
                "data": {
                    alias: node
                    for alias, node in zip(aliases, nodes)
                },
            }
        )

    from agendum import gh

    monkeypatch.setattr(gh, "_run_gh", fake_run_gh)

    prs, ok = await call_gh_primitive("hydrate_pull_requests", ["PR_1", "PR_2", "PR_3"], batch_size=2)

    assert ok is True
    assert [pr["id"] for pr in prs] == ["PR_1", "PR_3"]
    assert len(calls) == 2
    assert len(re.findall(r"(\w+)\s*:\s*node\(", query_arg(calls[0]))) == 2

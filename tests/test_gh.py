import json
from pathlib import Path
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
    fetch_repo_archive_states_with_completeness,
    fetch_review_detail,
    get_gh_config_dir,
    hydrate_open_authored_prs,
    hydrate_open_issues,
    hydrate_open_review_prs,
    _run_gh,
    parse_author_first_name,
    extract_repo_short_name,
    refresh_gh_config_dir,
    seed_gh_config_dir,
    set_gh_config_dir,
    use_gh_config_dir,
    verify_missing_authored_prs,
    verify_missing_issues,
    verify_missing_review_prs,
)


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
async def test_fetch_repo_archive_states_with_completeness_batches(monkeypatch) -> None:
    calls: list[tuple[str, ...]] = []

    async def fake_run_gh(*args: str) -> str:
        calls.append(args)
        query_arg = next(arg for arg in args if arg.startswith("query="))
        if "org-a" in query_arg and "repo-1" in query_arg:
            return json.dumps(
                {
                    "data": {
                        "repo_0": {
                            "nameWithOwner": "org-a/repo-1",
                            "isArchived": False,
                        },
                        "repo_1": {
                            "nameWithOwner": "org-a/repo-2",
                            "isArchived": True,
                        },
                    }
                }
            )
        return json.dumps(
            {
                "data": {
                    "repo_0": {
                        "nameWithOwner": "org-b/repo-3",
                        "isArchived": False,
                    }
                }
            }
        )

    from agendum import gh

    monkeypatch.setattr(gh, "_run_gh", fake_run_gh)

    states, complete = await fetch_repo_archive_states_with_completeness(
        ["org-a/repo-1", "org-a/repo-2", "org-b/repo-3"],
        batch_size=2,
    )

    assert complete is True
    assert states == {
        "org-a/repo-1": False,
        "org-a/repo-2": True,
        "org-b/repo-3": False,
    }
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_hydrate_open_authored_prs_uses_lane_specific_minimal_query(monkeypatch) -> None:
    calls: list[tuple[str, ...]] = []

    async def fake_run_gh(*args: str) -> str:
        calls.append(args)
        return json.dumps(
            {
                "data": {
                    "nodes": [
                        {
                            "__typename": "PullRequest",
                            "id": "PR_node_1",
                            "number": 12,
                            "title": "Improve sync",
                            "url": "https://github.com/example-org/example-repo/pull/12",
                            "state": "OPEN",
                            "isDraft": False,
                            "reviewDecision": "APPROVED",
                            "repository": {
                                "nameWithOwner": "example-org/example-repo",
                                "isArchived": False,
                            },
                            "author": {"login": "author"},
                            "labels": {"nodes": [{"name": "bug"}]},
                            "reviewRequests": {"totalCount": 1},
                            "commits": {"nodes": []},
                            "reviews": {"nodes": []},
                            "reviewThreads": {"nodes": []},
                        }
                    ]
                }
            }
        )

    from agendum import gh

    monkeypatch.setattr(gh, "_run_gh", fake_run_gh)

    hydrated = await hydrate_open_authored_prs([{"gh_node_id": "PR_node_1"}])

    assert hydrated == [
        {
            "gh_node_id": "PR_node_1",
            "number": 12,
            "title": "Improve sync",
            "url": "https://github.com/example-org/example-repo/pull/12",
            "repository": {
                "nameWithOwner": "example-org/example-repo",
                "isArchived": False,
            },
            "state": "OPEN",
            "isDraft": False,
            "reviewDecision": "APPROVED",
            "author": {"login": "author"},
            "labels": {"nodes": [{"name": "bug"}]},
            "reviewRequests": {"totalCount": 1},
            "commits": {"nodes": []},
            "reviews": {"nodes": []},
            "reviewThreads": {"nodes": []},
        }
    ]
    query_arg = next(arg for arg in calls[0] if arg.startswith("query="))
    assert "HydrateOpenAuthoredPRs" in query_arg
    assert "reviewDecision" in query_arg
    assert "reviewThreads" in query_arg
    assert "timelineItems" not in query_arg
    assert "labels" in query_arg
    assert "isArchived" in query_arg
    assert "mergedPRs" not in query_arg


@pytest.mark.asyncio
async def test_hydrate_open_review_prs_uses_lane_specific_minimal_query(monkeypatch) -> None:
    calls: list[tuple[str, ...]] = []

    async def fake_run_gh(*args: str) -> str:
        calls.append(args)
        return json.dumps(
            {
                "data": {
                    "nodes": [
                        {
                            "__typename": "PullRequest",
                            "id": "PR_review_1",
                            "number": 24,
                            "title": "Review this",
                            "url": "https://github.com/example-org/example-repo/pull/24",
                            "repository": {
                                "nameWithOwner": "example-org/example-repo",
                                "isArchived": False,
                            },
                            "author": {"login": "author", "name": "Author Name"},
                            "commits": {"nodes": []},
                            "reviews": {"nodes": []},
                            "timelineItems": {"nodes": []},
                        }
                    ]
                }
            }
        )

    from agendum import gh

    monkeypatch.setattr(gh, "_run_gh", fake_run_gh)

    hydrated = await hydrate_open_review_prs([{"gh_node_id": "PR_review_1"}])

    assert hydrated == [
        {
            "gh_node_id": "PR_review_1",
            "number": 24,
            "title": "Review this",
            "url": "https://github.com/example-org/example-repo/pull/24",
            "repository": {
                "nameWithOwner": "example-org/example-repo",
                "isArchived": False,
            },
            "author": {"login": "author", "name": "Author Name"},
            "commits": {"nodes": []},
            "reviews": {"nodes": []},
            "timelineItems": {"nodes": []},
        }
    ]
    query_arg = next(arg for arg in calls[0] if arg.startswith("query="))
    assert "HydrateOpenReviewPRs" in query_arg
    assert "timelineItems" in query_arg
    assert "reviewDecision" not in query_arg
    assert "reviewThreads" not in query_arg
    assert "labels" not in query_arg
    assert "isArchived" in query_arg


@pytest.mark.asyncio
async def test_hydrate_open_issues_uses_lane_specific_minimal_query(monkeypatch) -> None:
    calls: list[tuple[str, ...]] = []

    async def fake_run_gh(*args: str) -> str:
        calls.append(args)
        return json.dumps(
            {
                "data": {
                    "nodes": [
                        {
                            "__typename": "Issue",
                            "id": "I_node_1",
                            "number": 7,
                            "title": "Assigned issue",
                            "url": "https://github.com/example-org/example-repo/issues/7",
                            "state": "OPEN",
                            "repository": {
                                "nameWithOwner": "example-org/example-repo",
                                "isArchived": False,
                            },
                            "labels": {"nodes": [{"name": "ops"}]},
                            "timelineItems": {"nodes": []},
                        }
                    ]
                }
            }
        )

    from agendum import gh

    monkeypatch.setattr(gh, "_run_gh", fake_run_gh)

    hydrated = await hydrate_open_issues([{"gh_node_id": "I_node_1"}])

    assert hydrated == [
        {
            "gh_node_id": "I_node_1",
            "number": 7,
            "title": "Assigned issue",
            "url": "https://github.com/example-org/example-repo/issues/7",
            "repository": {
                "nameWithOwner": "example-org/example-repo",
                "isArchived": False,
            },
            "state": "OPEN",
            "labels": {"nodes": [{"name": "ops"}]},
            "timelineItems": {"nodes": []},
        }
    ]
    query_arg = next(arg for arg in calls[0] if arg.startswith("query="))
    assert "HydrateOpenIssues" in query_arg
    assert "timelineItems" in query_arg
    assert "labels" in query_arg
    assert "isArchived" in query_arg
    assert "reviewDecision" not in query_arg
    assert "reviews" not in query_arg
    assert "commits" not in query_arg


@pytest.mark.asyncio
async def test_verify_missing_authored_prs_uses_lane_specific_minimal_query(monkeypatch) -> None:
    calls: list[tuple[str, ...]] = []

    async def fake_run_gh(*args: str) -> str:
        calls.append(args)
        return json.dumps(
            {
                "data": {
                    "nodes": [
                        {
                            "__typename": "PullRequest",
                            "id": "PR_node_1",
                            "url": "https://github.com/example-org/example-repo/pull/12",
                            "state": "MERGED",
                        }
                    ]
                }
            }
        )

    from agendum import gh

    monkeypatch.setattr(gh, "_run_gh", fake_run_gh)

    verified = await verify_missing_authored_prs([{"gh_node_id": "PR_node_1"}])

    assert verified == [
        {
            "gh_node_id": "PR_node_1",
            "gh_url": "https://github.com/example-org/example-repo/pull/12",
            "state": "MERGED",
        }
    ]
    query_arg = next(arg for arg in calls[0] if arg.startswith("query="))
    assert "VerifyMissingAuthoredPRs" in query_arg
    assert "state" in query_arg
    assert "reviewDecision" not in query_arg
    assert "reviewRequests" not in query_arg
    assert "timelineItems" not in query_arg


@pytest.mark.asyncio
async def test_verify_missing_issues_uses_lane_specific_minimal_query(monkeypatch) -> None:
    calls: list[tuple[str, ...]] = []

    async def fake_run_gh(*args: str) -> str:
        calls.append(args)
        return json.dumps(
            {
                "data": {
                    "nodes": [
                        {
                            "__typename": "Issue",
                            "id": "I_node_1",
                            "url": "https://github.com/example-org/example-repo/issues/7",
                            "state": "OPEN",
                            "assignees": {"nodes": [{"login": "current-user"}]},
                        }
                    ]
                }
            }
        )

    from agendum import gh

    monkeypatch.setattr(gh, "_run_gh", fake_run_gh)

    verified = await verify_missing_issues(
        [{"gh_node_id": "I_node_1"}],
        gh_user="current-user",
    )

    assert verified == [
        {
            "gh_node_id": "I_node_1",
            "gh_url": "https://github.com/example-org/example-repo/issues/7",
            "state": "OPEN",
            "is_assigned_to_user": True,
        }
    ]
    query_arg = next(arg for arg in calls[0] if arg.startswith("query="))
    assert "VerifyMissingIssues" in query_arg
    assert "assignees" in query_arg
    assert "reviewRequests" not in query_arg
    assert "timelineItems" not in query_arg


@pytest.mark.asyncio
async def test_verify_missing_review_prs_uses_lane_specific_minimal_query(monkeypatch) -> None:
    calls: list[tuple[str, ...]] = []

    async def fake_run_gh(*args: str) -> str:
        calls.append(args)
        return json.dumps(
            {
                "data": {
                    "nodes": [
                        {
                            "__typename": "PullRequest",
                            "id": "PR_review_1",
                            "url": "https://github.com/example-org/example-repo/pull/24",
                            "state": "OPEN",
                            "reviewRequests": {
                                "nodes": [
                                    {"requestedReviewer": {"login": "current-user"}},
                                ]
                            },
                        }
                    ]
                }
            }
        )

    from agendum import gh

    monkeypatch.setattr(gh, "_run_gh", fake_run_gh)

    verified = await verify_missing_review_prs(
        [{"gh_node_id": "PR_review_1"}],
        gh_user="current-user",
    )

    assert verified == [
        {
            "gh_node_id": "PR_review_1",
            "gh_url": "https://github.com/example-org/example-repo/pull/24",
            "state": "OPEN",
            "is_review_requested": True,
        }
    ]
    query_arg = next(arg for arg in calls[0] if arg.startswith("query="))
    assert "VerifyMissingReviewPRs" in query_arg
    assert "reviewRequests" in query_arg
    assert "reviewDecision" not in query_arg
    assert "timelineItems" not in query_arg

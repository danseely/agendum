"""GitHub data fetching and status derivation via the gh CLI."""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
from contextvars import ContextVar
import json
import logging
import os
import re
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator, cast

log = logging.getLogger(__name__)
_GH_CONFIG_DIR: Path | None = None
_GH_CONFIG_DIR_UNSET = object()
_GH_CONFIG_FILES = ("hosts.yml", "config.yml")
_TASK_GH_CONFIG_DIR: ContextVar[Path | None | object] = ContextVar(
    "agendum_task_gh_config_dir",
    default=_GH_CONFIG_DIR_UNSET,
)
_SEARCH_PAGE_SIZE = 100
_SEARCH_REPO_CHUNK_SIZE = 10
_REPO_ARCHIVE_BATCH_SIZE = 20
_HYDRATE_BATCH_SIZE = 50
_VERIFY_BATCH_SIZE = 50
_GITHUB_TASK_URL_RE = re.compile(
    r"^https://github\.com/([^/]+)/([^/]+)/(pull|issues)/(\d+)/?$"
)


# ---------------------------------------------------------------------------
# Status derivation (pure functions, no I/O)
# ---------------------------------------------------------------------------

def derive_authored_pr_status(
    *,
    is_draft: bool,
    review_decision: str | None,
    state: str,
    has_review_requests: bool = False,
    latest_commit_time: str | None = None,
    latest_comment_review_id: str | None = None,
    latest_comment_review_time: str | None = None,
    qualifying_reviews: list[dict[str, Any]] | None = None,
    author_login: str | None = None,
    review_threads: list[dict[str, Any]] | None = None,
) -> str:
    if state == "MERGED":
        return "merged"
    if state == "CLOSED":
        return "closed"
    if is_draft:
        return "draft"
    if review_decision == "APPROVED":
        return "approved"
    if review_decision == "CHANGES_REQUESTED":
        return "changes requested"
    if has_unacknowledged_review_feedback(
        latest_comment_review_id=latest_comment_review_id,
        latest_comment_review_time=latest_comment_review_time,
        latest_commit_time=latest_commit_time,
        author_login=author_login,
        qualifying_reviews=qualifying_reviews or [],
        review_threads=review_threads or [],
    ):
        return "review received"
    if has_review_requests:
        return "awaiting review"
    return "open"


def derive_review_pr_status(
    *,
    user_has_reviewed: bool,
    new_commits_since_review: bool,
    re_requested_after_review: bool = False,
) -> str:
    if not user_has_reviewed:
        return "review requested"
    if re_requested_after_review or new_commits_since_review:
        return "re-review requested"
    return "reviewed"


def derive_issue_status(*, state: str, has_linked_pr: bool) -> str:
    if state == "CLOSED":
        return "closed"
    if has_linked_pr:
        return "in progress"
    return "open"


def parse_author_first_name(display_name: str | None) -> str | None:
    if not display_name:
        return None
    return display_name.strip().split()[0]


def extract_repo_short_name(full_repo: str) -> str:
    return full_repo.split("/", 1)[-1]


def _parse_github_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    normalized = value.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(normalized)
    except ValueError:
        return None


def _thread_has_author_reply_after(
    thread: dict[str, Any],
    *,
    author_login: str,
    review_time: str,
) -> bool:
    comments = (thread.get("comments") or {}).get("nodes", [])
    for comment in comments:
        comment_author = (comment.get("author") or {}).get("login", "")
        created_at = comment.get("createdAt")
        if (
            comment_author.lower() == author_login.lower()
            and created_at
            and created_at > review_time
        ):
            return True
    return False


def _relevant_review_threads(
    review_threads: list[dict[str, Any]],
    *,
    review_id: str,
) -> list[dict[str, Any]]:
    relevant: list[dict[str, Any]] = []
    for thread in review_threads:
        comments = (thread.get("comments") or {}).get("nodes", [])
        if any(
            ((comment.get("pullRequestReview") or {}).get("id") == review_id)
            for comment in comments
        ):
            relevant.append(thread)
    return relevant


def has_unacknowledged_review_feedback(
    *,
    latest_comment_review_id: str | None,
    latest_comment_review_time: str | None,
    latest_commit_time: str | None,
    author_login: str | None,
    qualifying_reviews: list[dict[str, Any]],
    review_threads: list[dict[str, Any]],
) -> bool:
    reviews = qualifying_reviews
    if not reviews and latest_comment_review_id and latest_comment_review_time:
        reviews = [
            {
                "id": latest_comment_review_id,
                "submittedAt": latest_comment_review_time,
            },
        ]
    if not reviews:
        return False

    commit_dt = _parse_github_datetime(latest_commit_time)

    for review in reviews:
        review_id = review.get("id")
        review_time = review.get("submittedAt")
        if not review_id or not review_time:
            continue

        relevant_threads = _relevant_review_threads(
            review_threads,
            review_id=review_id,
        )
        if relevant_threads:
            for thread in relevant_threads:
                if thread.get("isResolved", False):
                    continue
                if author_login and _thread_has_author_reply_after(
                    thread,
                    author_login=author_login,
                    review_time=review_time,
                ):
                    continue
                return True
            continue

        review_dt = _parse_github_datetime(review_time)
        if not review_dt or not commit_dt or commit_dt <= review_dt:
            return True

    return False


# ---------------------------------------------------------------------------
# gh CLI subprocess helpers
# ---------------------------------------------------------------------------

async def _run_gh(*args: str) -> str:
    """Run a gh CLI command and return stdout."""
    env = os.environ.copy()
    gh_config_dir = get_gh_config_dir()
    if gh_config_dir is not None:
        env["GH_CONFIG_DIR"] = str(gh_config_dir)
    proc = await asyncio.create_subprocess_exec(
        "gh", *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        log.warning("gh %s failed: %s", " ".join(args), stderr.decode().strip())
        return ""
    return stdout.decode()


async def get_gh_username() -> str:
    """Get the authenticated GitHub username."""
    result = await _run_gh("api", "user", "--jq", ".login")
    return result.strip()


def auth_status(gh_config_dir: Path | None = None) -> bool:
    """Return whether gh has a valid authenticated session for a config dir."""
    env = os.environ.copy()
    if gh_config_dir is not None:
        env["GH_CONFIG_DIR"] = str(gh_config_dir)
    try:
        result = subprocess.run(
            ["gh", "auth", "status"],
            capture_output=True,
            text=True,
            env=env,
            check=False,
        )
    except FileNotFoundError:
        return False
    return result.returncode == 0


def auth_login(gh_config_dir: Path) -> bool:
    """Run an interactive gh auth login with an isolated config directory."""
    gh_config_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    env = os.environ.copy()
    env["GH_CONFIG_DIR"] = str(gh_config_dir)
    try:
        result = subprocess.run(["gh", "auth", "login"], env=env, check=False)
    except FileNotFoundError:
        return False
    return result.returncode == 0


def set_gh_config_dir(gh_config_dir: Path | None) -> None:
    """Configure the gh subprocess environment for the active workspace."""
    global _GH_CONFIG_DIR
    _GH_CONFIG_DIR = gh_config_dir


def default_gh_config_dir() -> Path:
    """Return gh's default config directory for this environment."""
    if gh_config_dir := os.environ.get("GH_CONFIG_DIR"):
        return Path(gh_config_dir)
    if xdg_config_home := os.environ.get("XDG_CONFIG_HOME"):
        return Path(xdg_config_home) / "gh"
    return Path.home() / ".config" / "gh"


def seed_gh_config_dir(gh_config_dir: Path, source_dir: Path | None = None) -> None:
    """Copy the user's existing gh auth/config into a workspace-local gh dir."""
    gh_config_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    source_dir = source_dir or default_gh_config_dir()
    if source_dir == gh_config_dir:
        return

    for filename in _GH_CONFIG_FILES:
        source_path = source_dir / filename
        target_path = gh_config_dir / filename
        if target_path.exists() or not source_path.exists():
            continue
        shutil.copy2(source_path, target_path)
        os.chmod(target_path, 0o600)


def refresh_gh_config_dir(gh_config_dir: Path, source_dir: Path | None = None) -> None:
    """Refresh workspace-local gh auth/config from another gh config directory."""
    gh_config_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    source_dir = source_dir or default_gh_config_dir()
    if source_dir == gh_config_dir:
        return

    for filename in _GH_CONFIG_FILES:
        source_path = source_dir / filename
        if not source_path.exists():
            continue
        target_path = gh_config_dir / filename
        shutil.copy2(source_path, target_path)
        os.chmod(target_path, 0o600)


def _recovery_source_dirs(
    gh_config_dir: Path,
    *,
    source_dir: Path | None,
) -> list[Path]:
    """List distinct upstream gh config dirs in recovery preference order."""
    candidates: list[Path] = []
    seen: set[Path] = set()
    for candidate in (source_dir, default_gh_config_dir()):
        if candidate is None or candidate == gh_config_dir or candidate in seen:
            continue
        candidates.append(candidate)
        seen.add(candidate)
    return candidates


def recover_gh_auth(
    gh_config_dir: Path,
    *,
    source_dir: Path | None = None,
    interactive: bool = False,
    force_refresh: bool = False,
) -> bool:
    """Recover or refresh workspace-local gh auth from upstream state or login."""
    if not force_refresh and auth_status(gh_config_dir):
        return True

    for candidate in _recovery_source_dirs(gh_config_dir, source_dir=source_dir):
        if not auth_status(candidate):
            continue
        refresh_gh_config_dir(gh_config_dir, candidate)
        if auth_status(gh_config_dir):
            return True

    if not interactive:
        return False
    return auth_login(gh_config_dir)


def get_gh_config_dir() -> Path | None:
    """Return the effective gh config dir for the current task."""
    gh_config_dir = _TASK_GH_CONFIG_DIR.get()
    if gh_config_dir is _GH_CONFIG_DIR_UNSET:
        return _GH_CONFIG_DIR
    return cast(Path | None, gh_config_dir)


@contextmanager
def use_gh_config_dir(gh_config_dir: Path | None) -> Iterator[None]:
    """Temporarily bind a gh config dir to the current async task tree."""
    token = _TASK_GH_CONFIG_DIR.set(gh_config_dir)
    try:
        yield
    finally:
        _TASK_GH_CONFIG_DIR.reset(token)


def _chunked(items: list[str], size: int) -> Iterator[list[str]]:
    for index in range(0, len(items), size):
        yield items[index:index + size]


def _graphql_ids_literal(node_ids: list[str]) -> str:
    return json.dumps(node_ids)


async def _run_nodes_query(query: str) -> list[dict[str, Any]]:
    nodes, _ = await _run_nodes_query_with_success(query)
    return nodes


async def _run_nodes_query_with_success(query: str) -> tuple[list[dict[str, Any]], bool]:
    result = await _run_gh(
        "api",
        "graphql",
        "-f",
        f"query={query}",
    )
    if not result:
        return [], False
    try:
        payload = json.loads(result)
    except json.JSONDecodeError:
        log.warning("Failed to parse GraphQL nodes response")
        return [], False
    nodes = (payload.get("data") or {}).get("nodes")
    if not isinstance(nodes, list):
        return [], False
    return [node for node in nodes if isinstance(node, dict)], True


async def _run_graphql_query(query: str) -> dict[str, Any]:
    data, _ = await _run_graphql_query_with_success(query)
    return data


async def _run_graphql_query_with_success(query: str) -> tuple[dict[str, Any], bool]:
    result = await _run_gh(
        "api",
        "graphql",
        "-f",
        f"query={query}",
    )
    if not result:
        return {}, False
    try:
        payload = json.loads(result)
    except json.JSONDecodeError:
        log.warning("Failed to parse GraphQL response")
        return {}, False
    data = payload.get("data")
    return (data if isinstance(data, dict) else {}), isinstance(data, dict)


# ---------------------------------------------------------------------------
# Open-only discovery helpers
# ---------------------------------------------------------------------------


def _repository_name_from_api_url(repository_url: str | None) -> str:
    if not repository_url or "/repos/" not in repository_url:
        return ""
    return repository_url.split("/repos/", 1)[1]


def _normalize_open_search_item(item: dict[str, Any]) -> dict | None:
    repo_name = _repository_name_from_api_url(item.get("repository_url"))
    url = item.get("html_url") or ""
    number = item.get("number")
    if not repo_name or not url or number is None:
        return None
    return {
        "gh_node_id": item.get("node_id"),
        "number": number,
        "title": item.get("title", ""),
        "url": url,
        "repository": {
            "nameWithOwner": repo_name,
        },
    }


async def _search_open_items(query: str) -> list[dict]:
    items, _ = await _search_open_items_with_completeness(query)
    return items


async def _search_open_items_with_completeness(query: str) -> tuple[list[dict], bool]:
    items: list[dict] = []
    seen: set[str] = set()
    page = 1

    while True:
        out = await _run_gh(
            "api",
            "search/issues",
            "--method",
            "GET",
            "-f",
            f"q={query}",
            "-F",
            f"per_page={_SEARCH_PAGE_SIZE}",
            "-F",
            f"page={page}",
        )
        if not out:
            return items, False
        try:
            payload = json.loads(out)
        except json.JSONDecodeError:
            log.warning("Failed to parse search/issues response for query %s", query)
            return items, False

        page_items = payload.get("items", [])
        if not isinstance(page_items, list):
            return items, False

        for item in page_items:
            if not isinstance(item, dict):
                continue
            normalized = _normalize_open_search_item(item)
            if normalized is None:
                continue
            identity = normalized.get("gh_node_id") or normalized["url"]
            if identity in seen:
                continue
            seen.add(identity)
            items.append(normalized)

        if len(page_items) < _SEARCH_PAGE_SIZE:
            return items, True
        page += 1

    return items, True


def _build_repo_scoped_queries(
    base_terms: str,
    repos: list[str],
    *,
    chunk_size: int | None = None,
) -> list[str]:
    chunk_size = chunk_size or _SEARCH_REPO_CHUNK_SIZE
    queries: list[str] = []
    for chunk in _chunked(repos, chunk_size):
        qualifiers = " ".join(f"repo:{repo}" for repo in chunk)
        queries.append(f"{base_terms} {qualifiers}")
    return queries


async def search_open_authored_prs(orgs: list[str], gh_user: str) -> list[dict]:
    results, _ = await search_open_authored_prs_with_completeness(orgs, gh_user)
    return results


async def search_open_authored_prs_with_completeness(
    orgs: list[str],
    gh_user: str,
) -> tuple[list[dict], bool]:
    """Return lightweight skeletons for open authored PR discovery."""
    results: list[dict] = []
    seen: set[str] = set()
    complete = True
    for org in orgs:
        query = f"is:open is:pr author:{gh_user} org:{org}"
        items, query_complete = await _search_open_items_with_completeness(query)
        complete = complete and query_complete
        for item in items:
            identity = item.get("gh_node_id") or item["url"]
            if identity in seen:
                continue
            seen.add(identity)
            results.append(item)
    return results, complete


async def search_open_assigned_issues(orgs: list[str], gh_user: str) -> list[dict]:
    results, _ = await search_open_assigned_issues_with_completeness(orgs, gh_user)
    return results


async def search_open_assigned_issues_with_completeness(
    orgs: list[str],
    gh_user: str,
) -> tuple[list[dict], bool]:
    """Return lightweight skeletons for open assigned issue discovery."""
    results: list[dict] = []
    seen: set[str] = set()
    complete = True
    for org in orgs:
        query = f"is:open is:issue assignee:{gh_user} org:{org}"
        items, query_complete = await _search_open_items_with_completeness(query)
        complete = complete and query_complete
        for item in items:
            identity = item.get("gh_node_id") or item["url"]
            if identity in seen:
                continue
            seen.add(identity)
            results.append(item)
    return results, complete


async def search_open_review_requested_prs(orgs: list[str], gh_user: str) -> list[dict]:
    results, _ = await search_open_review_requested_prs_with_completeness(orgs, gh_user)
    return results


async def search_open_review_requested_prs_with_completeness(
    orgs: list[str],
    gh_user: str,
) -> tuple[list[dict], bool]:
    """Return lightweight skeletons for open review-requested PR discovery."""
    results: list[dict] = []
    seen: set[str] = set()
    complete = True
    for org in orgs:
        query = f"is:open is:pr review-requested:{gh_user} org:{org}"
        items, query_complete = await _search_open_items_with_completeness(query)
        complete = complete and query_complete
        for item in items:
            identity = item.get("gh_node_id") or item["url"]
            if identity in seen:
                continue
            seen.add(identity)
            results.append(item)
    return results, complete


async def search_open_authored_prs_for_repos(
    repos: list[str],
    gh_user: str,
) -> list[dict]:
    results, _ = await search_open_authored_prs_for_repos_with_completeness(repos, gh_user)
    return results


async def search_open_authored_prs_for_repos_with_completeness(
    repos: list[str],
    gh_user: str,
) -> tuple[list[dict], bool]:
    """Return lightweight skeletons for open authored PR discovery across repos."""
    results: list[dict] = []
    seen: set[str] = set()
    complete = True
    for query in _build_repo_scoped_queries(f"is:open is:pr author:{gh_user}", repos):
        items, query_complete = await _search_open_items_with_completeness(query)
        complete = complete and query_complete
        for item in items:
            identity = item.get("gh_node_id") or item["url"]
            if identity in seen:
                continue
            seen.add(identity)
            results.append(item)
    return results, complete


async def search_open_assigned_issues_for_repos(
    repos: list[str],
    gh_user: str,
) -> list[dict]:
    results, _ = await search_open_assigned_issues_for_repos_with_completeness(repos, gh_user)
    return results


async def search_open_assigned_issues_for_repos_with_completeness(
    repos: list[str],
    gh_user: str,
) -> tuple[list[dict], bool]:
    """Return lightweight skeletons for open assigned issue discovery across repos."""
    results: list[dict] = []
    seen: set[str] = set()
    complete = True
    for query in _build_repo_scoped_queries(f"is:open is:issue assignee:{gh_user}", repos):
        items, query_complete = await _search_open_items_with_completeness(query)
        complete = complete and query_complete
        for item in items:
            identity = item.get("gh_node_id") or item["url"]
            if identity in seen:
                continue
            seen.add(identity)
            results.append(item)
    return results, complete


async def search_open_review_requested_prs_for_repos(
    repos: list[str],
    gh_user: str,
) -> list[dict]:
    results, _ = await search_open_review_requested_prs_for_repos_with_completeness(
        repos,
        gh_user,
    )
    return results


async def search_open_review_requested_prs_for_repos_with_completeness(
    repos: list[str],
    gh_user: str,
) -> tuple[list[dict], bool]:
    """Return lightweight skeletons for open review-requested PR discovery across repos."""
    results: list[dict] = []
    seen: set[str] = set()
    complete = True
    for query in _build_repo_scoped_queries(f"is:open is:pr review-requested:{gh_user}", repos):
        items, query_complete = await _search_open_items_with_completeness(query)
        complete = complete and query_complete
        for item in items:
            identity = item.get("gh_node_id") or item["url"]
            if identity in seen:
                continue
            seen.add(identity)
            results.append(item)
    return results, complete


def _graphql_repo_string_literal(value: str) -> str:
    return json.dumps(value)


def _build_repo_archive_states_query(repos: list[str]) -> str:
    lines = ["query FetchRepoArchiveStates {"]
    for index, repo_full in enumerate(repos):
        owner, name = repo_full.split("/", 1)
        lines.append(
            f"  repo_{index}: repository(owner: {_graphql_repo_string_literal(owner)}, "
            f"name: {_graphql_repo_string_literal(name)}) {{"
        )
        lines.append("    nameWithOwner")
        lines.append("    isArchived")
        lines.append("  }")
    lines.append("}")
    return "\n".join(lines)


async def fetch_repo_archive_states_with_completeness(
    repos: list[str],
    *,
    batch_size: int = _REPO_ARCHIVE_BATCH_SIZE,
) -> tuple[dict[str, bool], bool]:
    states: dict[str, bool] = {}
    complete = True
    for batch in _chunked(repos, batch_size):
        data, batch_complete = await _run_graphql_query_with_success(
            _build_repo_archive_states_query(batch)
        )
        complete = complete and batch_complete
        for value in data.values():
            if not isinstance(value, dict):
                continue
            repo_full = value.get("nameWithOwner")
            is_archived = value.get("isArchived")
            if isinstance(repo_full, str) and isinstance(is_archived, bool):
                states[repo_full] = is_archived
    complete = complete and len(states) == len(set(repos))
    return states, complete


def _normalize_hydrated_authored_pr(node: dict[str, Any]) -> dict[str, Any] | None:
    if node.get("__typename") != "PullRequest":
        return None
    repo_name = ((node.get("repository") or {}).get("nameWithOwner") or "")
    url = node.get("url") or ""
    number = node.get("number")
    gh_node_id = node.get("id")
    if not repo_name or not url or number is None or not gh_node_id:
        return None
    return {
        "gh_node_id": gh_node_id,
        "number": number,
        "title": node.get("title", ""),
        "url": url,
        "repository": {
            "nameWithOwner": repo_name,
            "isArchived": bool((node.get("repository") or {}).get("isArchived", False)),
        },
        "state": node.get("state"),
        "isDraft": node.get("isDraft", False),
        "reviewDecision": node.get("reviewDecision"),
        "author": node.get("author") or {},
        "labels": node.get("labels") or {"nodes": []},
        "reviewRequests": node.get("reviewRequests") or {"totalCount": 0},
        "commits": node.get("commits") or {"nodes": []},
        "reviews": node.get("reviews") or {"nodes": []},
        "reviewThreads": node.get("reviewThreads") or {"nodes": []},
    }


def _normalize_hydrated_review_pr(node: dict[str, Any]) -> dict[str, Any] | None:
    if node.get("__typename") != "PullRequest":
        return None
    repo_name = ((node.get("repository") or {}).get("nameWithOwner") or "")
    url = node.get("url") or ""
    number = node.get("number")
    gh_node_id = node.get("id")
    if not repo_name or not url or number is None or not gh_node_id:
        return None
    return {
        "gh_node_id": gh_node_id,
        "number": number,
        "title": node.get("title", ""),
        "url": url,
        "repository": {
            "nameWithOwner": repo_name,
            "isArchived": bool((node.get("repository") or {}).get("isArchived", False)),
        },
        "author": node.get("author") or {},
        "commits": node.get("commits") or {"nodes": []},
        "reviews": node.get("reviews") or {"nodes": []},
        "timelineItems": node.get("timelineItems") or {"nodes": []},
    }


def _normalize_hydrated_issue(node: dict[str, Any]) -> dict[str, Any] | None:
    if node.get("__typename") != "Issue":
        return None
    repo_name = ((node.get("repository") or {}).get("nameWithOwner") or "")
    url = node.get("url") or ""
    number = node.get("number")
    gh_node_id = node.get("id")
    if not repo_name or not url or number is None or not gh_node_id:
        return None
    return {
        "gh_node_id": gh_node_id,
        "number": number,
        "title": node.get("title", ""),
        "url": url,
        "repository": {
            "nameWithOwner": repo_name,
            "isArchived": bool((node.get("repository") or {}).get("isArchived", False)),
        },
        "state": node.get("state"),
        "labels": node.get("labels") or {"nodes": []},
        "timelineItems": node.get("timelineItems") or {"nodes": []},
    }


async def _hydrate_nodes(
    items: list[dict[str, Any]],
    *,
    query_builder: Callable[[list[str]], str],
    normalizer: Callable[[dict[str, Any]], dict[str, Any] | None],
    batch_size: int = _HYDRATE_BATCH_SIZE,
) -> list[dict[str, Any]]:
    hydrated, _ = await _hydrate_nodes_with_completeness(
        items,
        query_builder=query_builder,
        normalizer=normalizer,
        batch_size=batch_size,
    )
    return hydrated


async def _hydrate_nodes_with_completeness(
    items: list[dict[str, Any]],
    *,
    query_builder: Callable[[list[str]], str],
    normalizer: Callable[[dict[str, Any]], dict[str, Any] | None],
    batch_size: int = _HYDRATE_BATCH_SIZE,
) -> tuple[list[dict[str, Any]], bool]:
    ordered_node_ids: list[str] = []
    seen: set[str] = set()
    for item in items:
        gh_node_id = item.get("gh_node_id")
        if not gh_node_id or gh_node_id in seen:
            continue
        seen.add(gh_node_id)
        ordered_node_ids.append(gh_node_id)

    hydrated_by_node_id: dict[str, dict[str, Any]] = {}
    complete = True
    for node_ids in _chunked(ordered_node_ids, batch_size):
        nodes, batch_complete = await _run_nodes_query_with_success(query_builder(node_ids))
        complete = complete and batch_complete
        for node in nodes:
            normalized = normalizer(node)
            if normalized is None:
                continue
            hydrated_by_node_id[normalized["gh_node_id"]] = normalized

    hydrated = [
        hydrated_by_node_id[gh_node_id]
        for gh_node_id in ordered_node_ids
        if gh_node_id in hydrated_by_node_id
    ]
    complete = complete and len(hydrated) == len(ordered_node_ids)
    return hydrated, complete


def _build_authored_pr_hydration_query(node_ids: list[str]) -> str:
    return f"""
query HydrateOpenAuthoredPRs {{
  nodes(ids: {_graphql_ids_literal(node_ids)}) {{
    __typename
    ... on PullRequest {{
      id
      number
      title
      url
      state
      isDraft
      reviewDecision
      repository {{
        nameWithOwner
        isArchived
      }}
      author {{
        login
      }}
      labels(first: 10) {{
        nodes {{
          name
        }}
      }}
      reviewRequests(first: 10) {{
        totalCount
      }}
      commits(last: 1) {{
        nodes {{
          commit {{
            committedDate
          }}
        }}
      }}
      reviews(last: 20) {{
        nodes {{
          id
          state
          submittedAt
          author {{
            login
          }}
        }}
      }}
      reviewThreads(last: 50) {{
        nodes {{
          isResolved
          comments(last: 20) {{
            nodes {{
              createdAt
              pullRequestReview {{
                id
              }}
              author {{
                login
              }}
            }}
          }}
        }}
      }}
    }}
  }}
}}
""".strip()


def _build_review_pr_hydration_query(node_ids: list[str]) -> str:
    return f"""
query HydrateOpenReviewPRs {{
  nodes(ids: {_graphql_ids_literal(node_ids)}) {{
    __typename
    ... on PullRequest {{
      id
      number
      title
      url
      repository {{
        nameWithOwner
        isArchived
      }}
      author {{
        login
        ... on User {{
          name
        }}
      }}
      commits(last: 1) {{
        nodes {{
          commit {{
            committedDate
          }}
        }}
      }}
      reviews(first: 50) {{
        nodes {{
          state
          submittedAt
          author {{
            login
          }}
        }}
      }}
      timelineItems(last: 50, itemTypes: [REVIEW_REQUESTED_EVENT]) {{
        nodes {{
          ... on ReviewRequestedEvent {{
            createdAt
            requestedReviewer {{
              ... on User {{
                login
              }}
            }}
          }}
        }}
      }}
    }}
  }}
}}
""".strip()


def _build_issue_hydration_query(node_ids: list[str]) -> str:
    return f"""
query HydrateOpenIssues {{
  nodes(ids: {_graphql_ids_literal(node_ids)}) {{
    __typename
    ... on Issue {{
      id
      number
      title
      url
      state
      repository {{
        nameWithOwner
        isArchived
      }}
      labels(first: 10) {{
        nodes {{
          name
        }}
      }}
      timelineItems(last: 20, itemTypes: [CONNECTED_EVENT, CROSS_REFERENCED_EVENT]) {{
        nodes {{
          ... on ConnectedEvent {{
            subject {{
              ... on PullRequest {{
                url
              }}
            }}
          }}
          ... on CrossReferencedEvent {{
            source {{
              ... on PullRequest {{
                url
              }}
            }}
          }}
        }}
      }}
    }}
  }}
}}
""".strip()


async def hydrate_open_authored_prs(
    prs: list[dict[str, Any]],
    *,
    batch_size: int = _HYDRATE_BATCH_SIZE,
) -> list[dict[str, Any]]:
    hydrated, _ = await hydrate_open_authored_prs_with_completeness(
        prs,
        batch_size=batch_size,
    )
    return hydrated


async def hydrate_open_authored_prs_with_completeness(
    prs: list[dict[str, Any]],
    *,
    batch_size: int = _HYDRATE_BATCH_SIZE,
) -> tuple[list[dict[str, Any]], bool]:
    """Hydrate open authored PR skeletons with only authored-state fields."""
    return await _hydrate_nodes_with_completeness(
        prs,
        query_builder=_build_authored_pr_hydration_query,
        normalizer=_normalize_hydrated_authored_pr,
        batch_size=batch_size,
    )


async def hydrate_open_review_prs(
    prs: list[dict[str, Any]],
    *,
    batch_size: int = _HYDRATE_BATCH_SIZE,
) -> list[dict[str, Any]]:
    hydrated, _ = await hydrate_open_review_prs_with_completeness(
        prs,
        batch_size=batch_size,
    )
    return hydrated


async def hydrate_open_review_prs_with_completeness(
    prs: list[dict[str, Any]],
    *,
    batch_size: int = _HYDRATE_BATCH_SIZE,
) -> tuple[list[dict[str, Any]], bool]:
    """Hydrate open review-requested PR skeletons with only review-state fields."""
    return await _hydrate_nodes_with_completeness(
        prs,
        query_builder=_build_review_pr_hydration_query,
        normalizer=_normalize_hydrated_review_pr,
        batch_size=batch_size,
    )


async def hydrate_open_issues(
    issues: list[dict[str, Any]],
    *,
    batch_size: int = _HYDRATE_BATCH_SIZE,
) -> list[dict[str, Any]]:
    hydrated, _ = await hydrate_open_issues_with_completeness(
        issues,
        batch_size=batch_size,
    )
    return hydrated


async def hydrate_open_issues_with_completeness(
    issues: list[dict[str, Any]],
    *,
    batch_size: int = _HYDRATE_BATCH_SIZE,
) -> tuple[list[dict[str, Any]], bool]:
    """Hydrate open issue skeletons with only issue-state fields."""
    return await _hydrate_nodes_with_completeness(
        issues,
        query_builder=_build_issue_hydration_query,
        normalizer=_normalize_hydrated_issue,
        batch_size=batch_size,
    )


def _parse_github_task_url(url: str | None) -> tuple[str, str, str, int] | None:
    if not url:
        return None
    match = _GITHUB_TASK_URL_RE.match(url)
    if not match:
        return None
    owner, name, kind, number = match.groups()
    return owner, name, kind, int(number)


def _normalize_verified_authored_pr(node: dict[str, Any]) -> dict[str, Any] | None:
    if node.get("__typename") != "PullRequest":
        return None
    gh_node_id = node.get("id")
    gh_url = node.get("url")
    state = node.get("state")
    if not gh_node_id or not gh_url or not state:
        return None
    return {
        "gh_node_id": gh_node_id,
        "gh_url": gh_url,
        "state": state,
    }


def _normalize_verified_issue(
    node: dict[str, Any],
    *,
    gh_user: str,
) -> dict[str, Any] | None:
    if node.get("__typename") != "Issue":
        return None
    gh_node_id = node.get("id")
    gh_url = node.get("url")
    state = node.get("state")
    assignees = ((node.get("assignees") or {}).get("nodes") or [])
    if not gh_node_id or not gh_url or not state:
        return None
    return {
        "gh_node_id": gh_node_id,
        "gh_url": gh_url,
        "state": state,
        "is_assigned_to_user": any(
            (assignee.get("login") or "").lower() == gh_user.lower()
            for assignee in assignees
            if isinstance(assignee, dict)
        ),
    }


def _normalize_verified_review_pr(
    node: dict[str, Any],
    *,
    gh_user: str,
) -> dict[str, Any] | None:
    if node.get("__typename") != "PullRequest":
        return None
    gh_node_id = node.get("id")
    gh_url = node.get("url")
    state = node.get("state")
    review_requests = ((node.get("reviewRequests") or {}).get("nodes") or [])
    if not gh_node_id or not gh_url or not state:
        return None
    return {
        "gh_node_id": gh_node_id,
        "gh_url": gh_url,
        "state": state,
        "is_review_requested": any(
            (
                ((review_request.get("requestedReviewer") or {}).get("login") or "").lower()
                == gh_user.lower()
            )
            for review_request in review_requests
            if isinstance(review_request, dict)
        ),
    }


async def _verify_missing_items(
    items: list[dict[str, Any]],
    *,
    node_query_builder: Callable[[list[str]], str],
    node_normalizer: Callable[[dict[str, Any]], dict[str, Any] | None],
    legacy_verifier: Callable[[dict[str, Any]], Any],
    batch_size: int,
) -> list[dict[str, Any]]:
    verified, _ = await _verify_missing_items_with_completeness(
        items,
        node_query_builder=node_query_builder,
        node_normalizer=node_normalizer,
        legacy_verifier=legacy_verifier,
        batch_size=batch_size,
    )
    return verified


async def _verify_missing_items_with_completeness(
    items: list[dict[str, Any]],
    *,
    node_query_builder: Callable[[list[str]], str],
    node_normalizer: Callable[[dict[str, Any]], dict[str, Any] | None],
    legacy_verifier: Callable[[dict[str, Any]], Any],
    batch_size: int,
) -> tuple[list[dict[str, Any]], bool]:
    ordered_items: list[dict[str, Any]] = []
    ordered_keys: list[str] = []
    seen: set[str] = set()
    for item in items:
        key = item.get("gh_node_id") or item.get("gh_url")
        if not key or key in seen:
            continue
        seen.add(key)
        ordered_keys.append(key)
        ordered_items.append(item)

    verified_by_key: dict[str, dict[str, Any]] = {}
    complete = True
    for batch in _chunked(ordered_items, batch_size):
        node_ids = [
            item["gh_node_id"]
            for item in batch
            if item.get("gh_node_id")
        ]
        legacy_items = [
            item
            for item in batch
            if not item.get("gh_node_id") and item.get("gh_url")
        ]

        if node_ids:
            nodes, batch_complete = await _run_nodes_query_with_success(
                node_query_builder(node_ids)
            )
            complete = complete and batch_complete
            for node in nodes:
                normalized = node_normalizer(node)
                if normalized is None:
                    continue
                verified_by_key[normalized["gh_node_id"]] = normalized

        if legacy_items:
            results = await asyncio.gather(
                *(legacy_verifier(item) for item in legacy_items)
            )
            for item, result in zip(legacy_items, results, strict=False):
                if result is not None:
                    verified_by_key[item["gh_url"]] = result

    verified = [verified_by_key[key] for key in ordered_keys if key in verified_by_key]
    complete = complete and len(verified) == len(ordered_keys)
    return verified, complete


def _build_authored_pr_verification_query(node_ids: list[str]) -> str:
    return f"""
query VerifyMissingAuthoredPRs {{
  nodes(ids: {_graphql_ids_literal(node_ids)}) {{
    __typename
    ... on PullRequest {{
      id
      url
      state
    }}
  }}
}}
""".strip()


def _build_issue_verification_query(node_ids: list[str]) -> str:
    return f"""
query VerifyMissingIssues {{
  nodes(ids: {_graphql_ids_literal(node_ids)}) {{
    __typename
    ... on Issue {{
      id
      url
      state
      assignees(first: 50) {{
        nodes {{
          login
        }}
      }}
    }}
  }}
}}
""".strip()


def _build_review_pr_verification_query(node_ids: list[str]) -> str:
    return f"""
query VerifyMissingReviewPRs {{
  nodes(ids: {_graphql_ids_literal(node_ids)}) {{
    __typename
    ... on PullRequest {{
      id
      url
      state
      reviewRequests(first: 50) {{
        nodes {{
          requestedReviewer {{
            ... on User {{
              login
            }}
          }}
        }}
      }}
    }}
  }}
}}
""".strip()


async def _verify_missing_authored_pr_by_url(item: dict[str, Any]) -> dict[str, Any] | None:
    parsed = _parse_github_task_url(item.get("gh_url"))
    if parsed is None:
        return None
    owner, name, kind, number = parsed
    if kind != "pull":
        return None
    data, complete = await _run_graphql_query_with_success(
        f"""
query VerifyMissingAuthoredPRByUrl {{
  repository(owner: {json.dumps(owner)}, name: {json.dumps(name)}) {{
    pullRequest(number: {number}) {{
      id
      url
      state
    }}
  }}
}}
""".strip()
    )
    if not complete:
        return None
    pull_request = ((data.get("repository") or {}).get("pullRequest") or {})
    if not isinstance(pull_request, dict) or not pull_request:
        return None
    return {
        "gh_node_id": pull_request.get("id"),
        "gh_url": pull_request.get("url"),
        "state": pull_request.get("state"),
    }


async def _verify_missing_issue_by_url(
    item: dict[str, Any],
    *,
    gh_user: str,
) -> dict[str, Any] | None:
    parsed = _parse_github_task_url(item.get("gh_url"))
    if parsed is None:
        return None
    owner, name, kind, number = parsed
    if kind != "issues":
        return None
    data, complete = await _run_graphql_query_with_success(
        f"""
query VerifyMissingIssueByUrl {{
  repository(owner: {json.dumps(owner)}, name: {json.dumps(name)}) {{
    issue(number: {number}) {{
      id
      url
      state
      assignees(first: 50) {{
        nodes {{
          login
        }}
      }}
    }}
  }}
}}
""".strip()
    )
    if not complete:
        return None
    issue = ((data.get("repository") or {}).get("issue") or {})
    if not isinstance(issue, dict) or not issue:
        return None
    assignees = ((issue.get("assignees") or {}).get("nodes") or [])
    return {
        "gh_node_id": issue.get("id"),
        "gh_url": issue.get("url"),
        "state": issue.get("state"),
        "is_assigned_to_user": any(
            (assignee.get("login") or "").lower() == gh_user.lower()
            for assignee in assignees
            if isinstance(assignee, dict)
        ),
    }


async def _verify_missing_review_pr_by_url(
    item: dict[str, Any],
    *,
    gh_user: str,
) -> dict[str, Any] | None:
    parsed = _parse_github_task_url(item.get("gh_url"))
    if parsed is None:
        return None
    owner, name, kind, number = parsed
    if kind != "pull":
        return None
    data, complete = await _run_graphql_query_with_success(
        f"""
query VerifyMissingReviewPRByUrl {{
  repository(owner: {json.dumps(owner)}, name: {json.dumps(name)}) {{
    pullRequest(number: {number}) {{
      id
      url
      state
      reviewRequests(first: 50) {{
        nodes {{
          requestedReviewer {{
            ... on User {{
              login
            }}
          }}
        }}
      }}
    }}
  }}
}}
""".strip()
    )
    if not complete:
        return None
    pull_request = ((data.get("repository") or {}).get("pullRequest") or {})
    if not isinstance(pull_request, dict) or not pull_request:
        return None
    review_requests = ((pull_request.get("reviewRequests") or {}).get("nodes") or [])
    return {
        "gh_node_id": pull_request.get("id"),
        "gh_url": pull_request.get("url"),
        "state": pull_request.get("state"),
        "is_review_requested": any(
            (
                ((review_request.get("requestedReviewer") or {}).get("login") or "").lower()
                == gh_user.lower()
            )
            for review_request in review_requests
            if isinstance(review_request, dict)
        ),
    }


async def verify_missing_authored_prs(
    prs: list[dict[str, Any]],
    *,
    batch_size: int = _VERIFY_BATCH_SIZE,
) -> list[dict[str, Any]]:
    """Verify missing authored PR rows without broad terminal-history search."""
    verified, _ = await verify_missing_authored_prs_with_completeness(
        prs,
        batch_size=batch_size,
    )
    return verified


async def verify_missing_authored_prs_with_completeness(
    prs: list[dict[str, Any]],
    *,
    batch_size: int = _VERIFY_BATCH_SIZE,
) -> tuple[list[dict[str, Any]], bool]:
    return await _verify_missing_items_with_completeness(
        prs,
        node_query_builder=_build_authored_pr_verification_query,
        node_normalizer=_normalize_verified_authored_pr,
        legacy_verifier=_verify_missing_authored_pr_by_url,
        batch_size=batch_size,
    )


async def verify_missing_issues(
    issues: list[dict[str, Any]],
    *,
    gh_user: str,
    batch_size: int = _VERIFY_BATCH_SIZE,
) -> list[dict[str, Any]]:
    """Verify missing issue rows without broad terminal-history search."""
    verified, _ = await verify_missing_issues_with_completeness(
        issues,
        gh_user=gh_user,
        batch_size=batch_size,
    )
    return verified


async def verify_missing_issues_with_completeness(
    issues: list[dict[str, Any]],
    *,
    gh_user: str,
    batch_size: int = _VERIFY_BATCH_SIZE,
) -> tuple[list[dict[str, Any]], bool]:
    return await _verify_missing_items_with_completeness(
        issues,
        node_query_builder=_build_issue_verification_query,
        node_normalizer=lambda node: _normalize_verified_issue(node, gh_user=gh_user),
        legacy_verifier=lambda item: _verify_missing_issue_by_url(item, gh_user=gh_user),
        batch_size=batch_size,
    )


async def verify_missing_review_prs(
    prs: list[dict[str, Any]],
    *,
    gh_user: str,
    batch_size: int = _VERIFY_BATCH_SIZE,
) -> list[dict[str, Any]]:
    """Verify missing review PR rows without broad terminal-history search."""
    verified, _ = await verify_missing_review_prs_with_completeness(
        prs,
        gh_user=gh_user,
        batch_size=batch_size,
    )
    return verified


async def verify_missing_review_prs_with_completeness(
    prs: list[dict[str, Any]],
    *,
    gh_user: str,
    batch_size: int = _VERIFY_BATCH_SIZE,
) -> tuple[list[dict[str, Any]], bool]:
    return await _verify_missing_items_with_completeness(
        prs,
        node_query_builder=_build_review_pr_verification_query,
        node_normalizer=lambda node: _normalize_verified_review_pr(node, gh_user=gh_user),
        legacy_verifier=lambda item: _verify_missing_review_pr_by_url(item, gh_user=gh_user),
        batch_size=batch_size,
    )


async def fetch_notifications(gh_user: str) -> list[dict]:
    """Fetch unread GitHub notifications."""
    out = await _run_gh(
        "api", "notifications",
        "--method", "GET",
        "-f", "all=false",
    )
    if not out:
        return []
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        return []

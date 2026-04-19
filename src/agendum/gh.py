"""GitHub data fetching and status derivation via the gh CLI."""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
from contextvars import ContextVar
import json
import logging
import os
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, cast

log = logging.getLogger(__name__)
_GH_CONFIG_DIR: Path | None = None
_GH_CONFIG_DIR_UNSET = object()
_GH_CONFIG_FILES = ("hosts.yml", "config.yml")
_TASK_GH_CONFIG_DIR: ContextVar[Path | None | object] = ContextVar(
    "agendum_task_gh_config_dir",
    default=_GH_CONFIG_DIR_UNSET,
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


def recover_gh_auth(
    gh_config_dir: Path,
    *,
    source_dir: Path | None = None,
    interactive: bool = False,
) -> bool:
    """Recover workspace-local gh auth from local state, default auth, or login."""
    if auth_status(gh_config_dir):
        return True

    source_dir = source_dir or default_gh_config_dir()
    if source_dir != gh_config_dir and auth_status(source_dir):
        refresh_gh_config_dir(gh_config_dir, source_dir)
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


# ---------------------------------------------------------------------------
# GraphQL query for a single repo
# ---------------------------------------------------------------------------

REPO_QUERY = """
query($owner: String!, $name: String!, $user: String!) {
  repository(owner: $owner, name: $name) {
    isArchived
    openIssues: issues(
      first: 50, states: OPEN,
      filterBy: {assignee: $user}
    ) {
      nodes {
        number title url state createdAt
        labels(first: 10) { nodes { name } }
        timelineItems(last: 20, itemTypes: [CONNECTED_EVENT, CROSS_REFERENCED_EVENT]) {
          nodes {
            ... on ConnectedEvent { subject { ... on PullRequest { url } } }
            ... on CrossReferencedEvent { source { ... on PullRequest { url } } }
          }
        }
      }
    }
    closedIssues: issues(
      first: 20, states: CLOSED,
      filterBy: {assignee: $user}
      orderBy: {field: UPDATED_AT, direction: DESC}
    ) {
      nodes { number url state }
    }
    authoredPRs: pullRequests(
      first: 50, states: OPEN,
      orderBy: {field: UPDATED_AT, direction: DESC}
    ) {
      nodes {
        number title url state isDraft createdAt
        headRefName
        author { login }
        reviewDecision
        reviewRequests(first: 10) { totalCount }
        commits(last: 1) {
          nodes {
            commit {
              committedDate
            }
          }
        }
        reviews(last: 20) {
          nodes {
            id
            state
            submittedAt
            author { login }
          }
        }
        reviewThreads(last: 50) {
          nodes {
            isResolved
            isOutdated
            comments(last: 20) {
              nodes {
                createdAt
                pullRequestReview { id }
                author { login }
              }
            }
          }
        }
        labels(first: 10) { nodes { name } }
      }
    }
    mergedPRs: pullRequests(
      first: 20, states: MERGED,
      orderBy: {field: UPDATED_AT, direction: DESC}
    ) {
      nodes { number url state author { login } }
    }
    closedPRs: pullRequests(
      first: 20, states: CLOSED,
      orderBy: {field: UPDATED_AT, direction: DESC}
    ) {
      nodes { number url state author { login } }
    }
  }
}
"""

REVIEW_QUERY = """
query($owner: String!, $name: String!, $number: Int!) {
  repository(owner: $owner, name: $name) {
    pullRequest(number: $number) {
      number title url state createdAt isDraft
      headRefName
      author {
        login
        ... on User {
          name
        }
      }
      commits(last: 1) { nodes { commit { committedDate } } }
      reviews(first: 50) {
        nodes { author { login } submittedAt state }
      }
      timelineItems(last: 50, itemTypes: [REVIEW_REQUESTED_EVENT]) {
        nodes {
          ... on ReviewRequestedEvent {
            createdAt
            requestedReviewer {
              ... on User { login }
            }
          }
        }
      }
    }
  }
}
"""


async def fetch_repo_data(owner: str, name: str, gh_user: str) -> dict:
    """Fetch all relevant data for a single repo via GraphQL."""
    result = await _run_gh(
        "api", "graphql",
        "-f", f"query={REPO_QUERY}",
        "-F", f"owner={owner}",
        "-F", f"name={name}",
        "-F", f"user={gh_user}",
    )
    if not result:
        return {}
    try:
        return json.loads(result)
    except json.JSONDecodeError:
        log.warning("Failed to parse GraphQL response for %s/%s", owner, name)
        return {}


async def fetch_review_detail(owner: str, name: str, number: int, gh_user: str) -> dict:
    """Fetch review detail for a single PR to determine review status."""
    result = await _run_gh(
        "api", "graphql",
        "-f", f"query={REVIEW_QUERY}",
        "-F", f"owner={owner}",
        "-F", f"name={name}",
        "-F", f"number={number}",
    )
    if not result:
        return {}
    try:
        return json.loads(result)
    except json.JSONDecodeError:
        return {}


# ---------------------------------------------------------------------------
# Discover repos and review requests
# ---------------------------------------------------------------------------

async def discover_repos(orgs: list[str], gh_user: str) -> set[str]:
    """Find all repos where the user has activity, across all orgs."""
    repos: set[str] = set()
    for org in orgs:
        out = await _run_gh(
            "search", "prs",
            "--author", gh_user,
            "--owner", org,
            "--state", "open",
            "--json", "repository",
            "--limit", "200",
        )
        if out:
            for item in json.loads(out):
                repo = item.get("repository", {})
                name = repo.get("nameWithOwner") or repo.get("name", "")
                if name:
                    repos.add(name)

        out = await _run_gh(
            "search", "issues",
            "--assignee", gh_user,
            "--owner", org,
            "--state", "open",
            "--json", "repository",
            "--limit", "200",
        )
        if out:
            for item in json.loads(out):
                repo = item.get("repository", {})
                name = repo.get("nameWithOwner") or repo.get("name", "")
                if name:
                    repos.add(name)

        out = await _run_gh(
            "search", "prs",
            "--review-requested", gh_user,
            "--owner", org,
            "--state", "open",
            "--json", "repository",
            "--limit", "200",
        )
        if out:
            for item in json.loads(out):
                repo = item.get("repository", {})
                name = repo.get("nameWithOwner") or repo.get("name", "")
                if name:
                    repos.add(name)

    return repos


async def discover_review_prs(orgs: list[str], gh_user: str) -> tuple[list[dict], bool]:
    """Find all PRs where user's review is requested, across all orgs.

    Returns (prs, ok) where *ok* is False if any org query failed,
    indicating the result set may be incomplete.
    """
    prs: list[dict] = []
    ok = True
    for org in orgs:
        out = await _run_gh(
            "search", "prs",
            "--review-requested", gh_user,
            "--owner", org,
            "--state", "open",
            "--json", "number,title,url,repository,author",
            "--limit", "200",
        )
        if out:
            prs.extend(json.loads(out))
        else:
            ok = False
    return prs, ok


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

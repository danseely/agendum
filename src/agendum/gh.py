"""GitHub data fetching and status derivation via the gh CLI."""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Status derivation (pure functions, no I/O)
# ---------------------------------------------------------------------------

def derive_authored_pr_status(
    *,
    is_draft: bool,
    review_decision: str | None,
    state: str,
    has_review_requests: bool = False,
) -> str:
    if state in ("MERGED", "CLOSED"):
        return "merged"
    if is_draft:
        return "draft"
    if review_decision == "APPROVED":
        return "approved"
    if review_decision == "CHANGES_REQUESTED":
        return "changes requested"
    if has_review_requests:
        return "awaiting review"
    return "open"


def derive_review_pr_status(
    *,
    user_has_reviewed: bool,
    new_commits_since_review: bool,
) -> str:
    if not user_has_reviewed:
        return "review requested"
    if new_commits_since_review:
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


# ---------------------------------------------------------------------------
# gh CLI subprocess helpers
# ---------------------------------------------------------------------------

async def _run_gh(*args: str) -> str:
    """Run a gh CLI command and return stdout."""
    proc = await asyncio.create_subprocess_exec(
        "gh", *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
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
query($owner: String!, $name: String!, $number: Int!, $user: String!) {
  repository(owner: $owner, name: $name) {
    pullRequest(number: $number) {
      number title url state createdAt isDraft
      headRefName
      author { login name }
      commits(last: 1) { nodes { commit { committedDate } } }
      reviews(first: 50) {
        nodes { author { login } submittedAt state }
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
        "-F", f"user={gh_user}",
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


async def discover_review_prs(orgs: list[str], gh_user: str) -> list[dict]:
    """Find all PRs where user's review is requested, across all orgs."""
    prs: list[dict] = []
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
    return prs


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

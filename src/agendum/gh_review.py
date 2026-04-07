"""Live GitHub pull request review lookups."""

from __future__ import annotations

import json
import re
from typing import Any

from agendum import gh

_PR_URL_RE = re.compile(r"^https://github\.com/([^/]+)/([^/]+)/pull/(\d+)/?$")

_REVIEW_QUERY = """
query($owner: String!, $name: String!, $number: Int!) {
  repository(owner: $owner, name: $name) {
    pullRequest(number: $number) {
      number
      url
      reviews(last: 100) {
        nodes {
          state
          submittedAt
          url
          author {
            login
            ... on User {
              name
            }
          }
        }
      }
    }
  }
}
"""


def parse_github_pr_url(url: str) -> tuple[str, str, int] | None:
    """Parse a canonical GitHub PR URL."""
    match = _PR_URL_RE.match(url.strip())
    if not match:
        return None
    owner, repo, number = match.groups()
    return owner, repo, int(number)


def _normalize_match_text(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


def reviewer_matches(reviewer_query: str, *, login: str | None, name: str | None) -> bool:
    """Match reviewer text against login or display name."""
    query = _normalize_match_text(reviewer_query)
    if not query:
        return False

    candidates = [login or "", name or ""]
    for candidate in candidates:
        normalized = _normalize_match_text(candidate)
        if normalized and query in normalized:
            return True
    return False


def _review_author(node: dict[str, Any]) -> dict[str, str | None]:
    author = node.get("author") or {}
    return {
        "login": author.get("login"),
        "name": author.get("name"),
    }


def _parse_reviews(payload: dict[str, Any]) -> list[dict[str, Any]]:
    reviews = (
        payload.get("data", {})
        .get("repository", {})
        .get("pullRequest", {})
        .get("reviews", {})
        .get("nodes", [])
    )
    result: list[dict[str, Any]] = []
    for review in reviews:
        author = _review_author(review)
        result.append(
            {
                "login": author["login"],
                "name": author["name"],
                "state": review.get("state"),
                "submitted_at": review.get("submittedAt"),
                "url": review.get("url"),
            }
        )
    result.sort(key=lambda item: item.get("submitted_at") or "", reverse=True)
    return result


async def fetch_pr_reviews(owner: str, repo: str, number: int) -> list[dict[str, Any]]:
    """Fetch pull request reviews from GitHub."""
    result = await gh._run_gh(
        "api", "graphql",
        "-f", f"query={_REVIEW_QUERY}",
        "-F", f"owner={owner}",
        "-F", f"name={repo}",
        "-F", f"number={number}",
    )
    if not result:
        return []
    try:
        payload = json.loads(result)
    except json.JSONDecodeError:
        return []
    return _parse_reviews(payload)


def _latest_state(reviews: list[dict[str, Any]]) -> str | None:
    if not reviews:
        return None
    latest = max(reviews, key=lambda item: item.get("submitted_at") or "")
    return latest.get("state")


async def get_pr_review_status(*, url: str, reviewer: str | None = None) -> dict[str, Any]:
    """Return structured review status for a PR URL."""
    parsed = parse_github_pr_url(url)
    if parsed is None:
        raise ValueError(f"Invalid GitHub PR URL: {url}")

    owner, repo, number = parsed
    reviews = await fetch_pr_reviews(owner, repo, number)

    matches = reviews
    if reviewer:
        matches = [
            review
            for review in reviews
            if reviewer_matches(reviewer, login=review.get("login"), name=review.get("name"))
        ]

    return {
        "url": url,
        "owner": owner,
        "repo": repo,
        "number": number,
        "reviewer": reviewer,
        "matches": matches,
        "latest_state": _latest_state(matches),
    }

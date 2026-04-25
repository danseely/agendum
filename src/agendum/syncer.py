"""Sync engine — orchestrates GitHub fetching, diffing, and DB updates."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from agendum import gh
from agendum.config import AgendumConfig
from agendum.db import (
    add_task,
    find_tasks_by_gh_urls,
    get_active_tasks,
    get_sync_state,
    set_sync_state,
    update_task,
    TERMINAL_STATUSES,
)

log = logging.getLogger(__name__)
_NOTIFICATIONS_SINCE_KEY = "github_notifications_since"


@dataclass
class SyncResult:
    to_create: list[dict] = field(default_factory=list)
    to_update: list[dict] = field(default_factory=list)
    to_close: list[dict] = field(default_factory=list)


@dataclass
class CollectedSyncInputs:
    incoming_tasks: list[dict]
    fetched_repos: set[str]
    review_fetch_ok: bool


def diff_tasks(
    existing: list[dict],
    incoming: list[dict],
    *,
    fetched_repos: set[str] | None = None,
    review_fetch_ok: bool = True,
) -> SyncResult:
    """Compare existing DB tasks against incoming GitHub state.

    If *fetched_repos* is provided, only close tasks whose repo was
    actually fetched.  This prevents a partial API failure from
    wiping out items belonging to repos that simply weren't reached.
    ``pr_review`` tasks are exempt from this guard — their completeness
    is governed by *review_fetch_ok*, and their repo may no longer appear
    in *fetched_repos* once GitHub drops the user from ``--review-requested``.

    If *review_fetch_ok* is False, review tasks are never closed
    (the review discovery may have returned incomplete results).
    """
    result = SyncResult()

    existing_by_url: dict[str, dict] = {}
    for task in existing:
        url = task.get("gh_url")
        if url:
            existing_by_url[url] = task

    incoming_urls: set[str] = set()
    for item in incoming:
        url = item["gh_url"]
        incoming_urls.add(url)

        if url in existing_by_url:
            old = existing_by_url[url]
            changes: dict = {"id": old["id"]}
            changed = False
            if old.get("status") != item.get("status"):
                changes["status"] = item["status"]
                changed = True
            if old.get("title") != item.get("title"):
                changes["title"] = item["title"]
                changed = True
            for key in ("gh_author", "gh_author_name", "tags", "project"):
                if key in item and old.get(key) != item.get(key):
                    changes[key] = item[key]
                    changed = True
            if changed:
                result.to_update.append(changes)
        else:
            result.to_create.append(item)

    for task in existing:
        url = task.get("gh_url")
        if url and url not in incoming_urls and task.get("source") != "manual":
            # Don't close review tasks when the review fetch was incomplete.
            if not review_fetch_ok and task.get("source") == "pr_review":
                continue
            # Only close items from repos we actually fetched data for.
            # pr_review tasks are gated by review_fetch_ok instead — their
            # repo may drop out of fetched_repos once GitHub removes the
            # user from --review-requested.
            if fetched_repos is not None and task.get("source") != "pr_review":
                task_repo = task.get("gh_repo", "")
                if task_repo not in fetched_repos:
                    continue
            result.to_close.append(task)

    return result


def _repo_owner(repo_full: str) -> str:
    return repo_full.split("/", 1)[0] if "/" in repo_full else ""


def _repo_in_scope(repo_full: str, config: AgendumConfig) -> bool:
    if not repo_full or repo_full in config.exclude_repos:
        return False
    if config.repos and repo_full not in config.repos:
        return False
    return True


def _search_scope_owners(config: AgendumConfig) -> list[str]:
    owners = {org for org in config.orgs if org}
    owners.update(
        owner
        for repo in config.repos
        if repo not in config.exclude_repos
        if (owner := _repo_owner(repo))
    )
    return sorted(owners)


def _search_item_repo(item: dict) -> str:
    repo = item.get("repository", {})
    return repo.get("nameWithOwner", "")


def _covered_repos_for_search_sync(
    existing: list[dict],
    incoming_tasks: list[dict],
    config: AgendumConfig,
) -> set[str]:
    if config.repos:
        return {repo for repo in config.repos if repo not in config.exclude_repos}

    covered_repos = {
        task_repo
        for task in existing
        if (task_repo := task.get("gh_repo"))
        and _repo_owner(task_repo) in set(config.orgs)
        and task_repo not in config.exclude_repos
    }
    covered_repos.update(
        task_repo
        for item in incoming_tasks
        if (task_repo := item.get("gh_repo"))
    )
    return covered_repos


def _normalize_authored_pr_task(pr: dict, gh_user: str) -> dict | None:
    repo_full = _search_item_repo(pr)
    author_login = (pr.get("author") or {}).get("login", "")
    if author_login.lower() != gh_user.lower():
        return None

    reviews = pr.get("reviews", {}).get("nodes", [])
    qualifying_reviews = [
        review
        for review in reviews
        if (review.get("author") or {}).get("login", "").lower() != gh_user.lower()
        and review.get("submittedAt")
        and review.get("id")
        and review.get("state") not in ("APPROVED", "CHANGES_REQUESTED", "PENDING")
    ]
    latest_comment_review = None
    if qualifying_reviews:
        latest_comment_review = max(
            qualifying_reviews,
            key=lambda review: review.get("submittedAt", ""),
        )
    last_commit_nodes = pr.get("commits", {}).get("nodes", [])
    latest_commit_time = None
    if last_commit_nodes:
        latest_commit_time = (
            last_commit_nodes[0].get("commit", {}).get("committedDate")
        )
    status = gh.derive_authored_pr_status(
        is_draft=pr.get("isDraft", False),
        review_decision=pr.get("reviewDecision"),
        state=pr.get("state", "OPEN"),
        has_review_requests=(pr.get("reviewRequests", {}).get("totalCount", 0) > 0),
        latest_commit_time=latest_commit_time,
        latest_comment_review_id=(latest_comment_review or {}).get("id"),
        latest_comment_review_time=(latest_comment_review or {}).get("submittedAt"),
        qualifying_reviews=qualifying_reviews,
        author_login=author_login,
        review_threads=pr.get("reviewThreads", {}).get("nodes", []),
    )
    labels = [l["name"] for l in (pr.get("labels", {}).get("nodes", []))]
    return {
        "title": pr["title"],
        "source": "pr_authored",
        "status": status,
        "project": gh.extract_repo_short_name(repo_full),
        "gh_repo": repo_full,
        "gh_url": pr["url"],
        "gh_number": pr["number"],
        "tags": json.dumps(labels) if labels else None,
    }


def _normalize_review_pr_task(pr: dict, gh_user: str) -> dict:
    reviews = pr.get("reviews", {}).get("nodes", [])
    user_reviews = [
        review
        for review in reviews
        if (review.get("author") or {}).get("login", "").lower() == gh_user.lower()
    ]
    user_has_reviewed = len(user_reviews) > 0

    new_commits_since = False
    re_requested_after_review = False
    if user_has_reviewed:
        last_review_time = max((review.get("submittedAt") or "" for review in user_reviews), default="")
        last_commit_nodes = pr.get("commits", {}).get("nodes", [])
        if last_commit_nodes:
            last_commit_time = (last_commit_nodes[0].get("commit", {}).get("committedDate") or "")
            new_commits_since = last_commit_time > last_review_time

        request_events = pr.get("timelineItems", {}).get("nodes", [])
        for event in request_events:
            reviewer_login = ((event.get("requestedReviewer") or {}).get("login") or "")
            if reviewer_login.lower() != gh_user.lower():
                continue
            created_at = event.get("createdAt") or ""
            if created_at and created_at > last_review_time:
                re_requested_after_review = True
                break

    status = gh.derive_review_pr_status(
        user_has_reviewed=user_has_reviewed,
        new_commits_since_review=new_commits_since,
        re_requested_after_review=re_requested_after_review,
    )

    repo_full = _search_item_repo(pr)
    author_info = pr.get("author") or {}
    author_login = author_info.get("login", "")
    author_name = gh.parse_author_first_name(author_info.get("name"))
    return {
        "title": pr.get("title", ""),
        "source": "pr_review",
        "status": status,
        "project": gh.extract_repo_short_name(repo_full),
        "gh_repo": repo_full,
        "gh_url": pr.get("url", ""),
        "gh_number": pr.get("number"),
        "gh_author": author_login,
        "gh_author_name": author_name or author_login,
        "tags": json.dumps(["review"]),
    }


def _normalize_issue_task(issue: dict) -> dict:
    repo_full = _search_item_repo(issue)
    timeline = issue.get("timelineItems", {}).get("nodes", [])
    has_linked_pr = any(
        (node.get("subject") or node.get("source") or {}).get("url")
        for node in timeline
    )
    status = gh.derive_issue_status(state=issue["state"], has_linked_pr=has_linked_pr)
    labels = [l["name"] for l in (issue.get("labels", {}).get("nodes", []))]
    return {
        "title": issue["title"],
        "source": "issue",
        "status": status,
        "project": gh.extract_repo_short_name(repo_full),
        "gh_repo": repo_full,
        "gh_url": issue["url"],
        "gh_number": issue["number"],
        "tags": json.dumps(labels) if labels else None,
    }


def _normalize_terminal_search_task(item: dict, *, source: str, status: str) -> dict:
    repo_full = _search_item_repo(item)
    labels = [l["name"] for l in (item.get("labels", {}).get("nodes", []))]
    return {
        "title": item.get("title", ""),
        "source": source,
        "status": status,
        "project": gh.extract_repo_short_name(repo_full),
        "gh_repo": repo_full,
        "gh_url": item.get("url", ""),
        "gh_number": item.get("number"),
        "tags": json.dumps(labels) if labels else None,
    }


async def _collect_search_first_sync_inputs(
    *,
    config: AgendumConfig,
    gh_user: str,
    existing: list[dict],
) -> CollectedSyncInputs:
    incoming_tasks: list[dict] = []
    search_owners = _search_scope_owners(config)

    authored_open, authored_open_ok = await gh.search_authored_prs(search_owners, gh_user)
    authored_merged, authored_merged_ok = await gh.search_merged_authored_prs(search_owners, gh_user)
    authored_closed, authored_closed_ok = await gh.search_closed_authored_prs(search_owners, gh_user)
    issue_open, issue_open_ok = await gh.search_assigned_issues(search_owners, gh_user)
    issue_closed, issue_closed_ok = await gh.search_closed_issues(search_owners, gh_user)
    review_open, review_search_ok = await gh.search_review_requested_prs(search_owners, gh_user)

    authored_open = [item for item in authored_open if _repo_in_scope(_search_item_repo(item), config)]
    authored_merged = [item for item in authored_merged if _repo_in_scope(_search_item_repo(item), config)]
    authored_closed = [item for item in authored_closed if _repo_in_scope(_search_item_repo(item), config)]
    issue_open = [item for item in issue_open if _repo_in_scope(_search_item_repo(item), config)]
    issue_closed = [item for item in issue_closed if _repo_in_scope(_search_item_repo(item), config)]
    review_open = [item for item in review_open if _repo_in_scope(_search_item_repo(item), config)]

    authored_ids = [item["id"] for item in authored_open if item.get("id")]
    issue_ids = [item["id"] for item in issue_open if item.get("id")]
    review_ids = [item["id"] for item in review_open if item.get("id")]

    hydrated_authored_prs: list[dict] = []
    authored_hydrate_ok = True
    if authored_ids:
        hydrated_authored_prs, authored_hydrate_ok = await gh.hydrate_pull_requests(authored_ids)

    hydrated_issues: list[dict] = []
    issue_hydrate_ok = True
    if issue_ids:
        hydrated_issues, issue_hydrate_ok = await gh.hydrate_issues(issue_ids)

    hydrated_review_prs: list[dict] = []
    review_hydrate_ok = True
    if review_ids:
        hydrated_review_prs, review_hydrate_ok = await gh.hydrate_pull_requests(review_ids)

    authored_by_id = {item["id"]: item for item in hydrated_authored_prs if item.get("id")}
    issues_by_id = {item["id"]: item for item in hydrated_issues if item.get("id")}
    review_by_id = {item["id"]: item for item in hydrated_review_prs if item.get("id")}

    authored_ok = (
        authored_open_ok
        and authored_merged_ok
        and authored_closed_ok
        and authored_hydrate_ok
        and len(authored_by_id) == len(authored_ids)
    )
    issues_ok = (
        issue_open_ok
        and issue_closed_ok
        and issue_hydrate_ok
        and len(issues_by_id) == len(issue_ids)
    )
    review_fetch_ok = (
        review_search_ok
        and review_hydrate_ok
        and len(review_by_id) == len(review_ids)
    )

    for item in authored_open:
        hydrated = authored_by_id.get(item.get("id"))
        if not hydrated:
            continue
        task = _normalize_authored_pr_task(hydrated, gh_user)
        if task:
            incoming_tasks.append(task)

    incoming_tasks.extend(
        _normalize_terminal_search_task(item, source="pr_authored", status="merged")
        for item in authored_merged
    )
    incoming_tasks.extend(
        _normalize_terminal_search_task(item, source="pr_authored", status="closed")
        for item in authored_closed
    )

    for item in issue_open:
        hydrated = issues_by_id.get(item.get("id"))
        if not hydrated:
            continue
        incoming_tasks.append(_normalize_issue_task(hydrated))

    incoming_tasks.extend(
        _normalize_terminal_search_task(item, source="issue", status="closed")
        for item in issue_closed
    )

    for item in review_open:
        hydrated = review_by_id.get(item.get("id"))
        if not hydrated:
            continue
        incoming_tasks.append(_normalize_review_pr_task(hydrated, gh_user))

    fetched_repos = set()
    if authored_ok and issues_ok:
        fetched_repos = _covered_repos_for_search_sync(existing, incoming_tasks, config)
    else:
        log.warning("Search-first authored/issue discovery was incomplete — skipping non-review cleanup")

    if not review_fetch_ok:
        log.warning("Review PR discovery had failures — skipping review task cleanup")

    return CollectedSyncInputs(
        incoming_tasks=incoming_tasks,
        fetched_repos=fetched_repos,
        review_fetch_ok=review_fetch_ok,
    )


async def run_sync(db_path: Path, config: AgendumConfig) -> tuple[int, bool, str | None]:
    """
    Execute a full sync cycle.
    Returns (changes_count, has_attention_items, error_message).
    """
    if not config.orgs and not config.repos:
        log.warning("No orgs or repos configured — skipping sync")
        return 0, False, None

    with gh.use_gh_config_dir(_workspace_gh_config_dir(db_path)):
        with gh.capture_gh_calls() as gh_calls:
            result = await _run_sync_once(db_path, config)

    log.info(
        "GitHub API usage: total=%d graphql=%d search=%d hydrate=%d notifications=%d rest=%d bytes=%d",
        gh_calls.total_calls,
        gh_calls.graphql_calls,
        gh_calls.graphql_search_calls,
        gh_calls.graphql_hydrate_calls,
        gh_calls.notification_calls,
        gh_calls.rest_calls,
        gh_calls.response_bytes,
    )
    return result


def _workspace_gh_config_dir(db_path: Path) -> Path:
    """Map a workspace DB path to its colocated gh auth/config directory."""
    return db_path.parent / "gh"


async def _run_sync_once(
    db_path: Path,
    config: AgendumConfig,
) -> tuple[int, bool, str | None]:
    """Execute a full sync cycle with the gh workspace already bound."""

    gh_user = await gh.get_gh_username()
    if not gh_user:
        log.error("Could not determine GitHub username")
        return 0, False, "gh credentials expired"

    existing = get_active_tasks(db_path)
    collected = await _collect_search_first_sync_inputs(
        config=config,
        gh_user=gh_user,
        existing=existing,
    )

    diff = diff_tasks(
        existing,
        collected.incoming_tasks,
        fetched_repos=collected.fetched_repos,
        review_fetch_ok=collected.review_fetch_ok,
    )

    changes = 0
    attention = False
    now = datetime.now(timezone.utc).isoformat()
    existing_by_create_url = find_tasks_by_gh_urls(
        db_path,
        [item["gh_url"] for item in diff.to_create if item.get("gh_url")],
    )

    for item in diff.to_create:
        existing_task = None
        if item.get("gh_url"):
            existing_task = existing_by_create_url.get(item["gh_url"])
        if item.get("status") in TERMINAL_STATUSES:
            if existing_task:
                update_task(db_path, existing_task["id"], status=item["status"])
                changes += 1
            continue
        if existing_task:
            update_fields = {
                k: item[k] for k in ("title", "source", "status", "project",
                                      "gh_repo", "gh_number", "gh_author",
                                      "gh_author_name", "tags")
                if item.get(k) is not None
            }
            update_fields["seen"] = 0
            update_fields["last_changed_at"] = now
            update_task(db_path, existing_task["id"], **update_fields)
        else:
            add_task(
                db_path,
                title=item["title"],
                source=item["source"],
                status=item["status"],
                project=item.get("project"),
                gh_repo=item.get("gh_repo"),
                gh_url=item.get("gh_url"),
                gh_number=item.get("gh_number"),
                gh_author=item.get("gh_author"),
                gh_author_name=item.get("gh_author_name"),
                tags=item.get("tags"),
            )
        changes += 1
        if item.get("source") == "pr_review" and item.get("status") in (
            "review requested", "re-review requested",
        ):
            attention = True

    for item in diff.to_update:
        task_id = item.pop("id")
        item["seen"] = 0
        item["last_changed_at"] = now
        update_task(db_path, task_id, **item)
        changes += 1
        if "status" in item and item["status"] in (
            "changes requested", "approved", "review received", "re-review requested",
        ):
            attention = True

    for item in diff.to_close:
        terminal = "merged" if item.get("source") == "pr_authored" else "closed"
        if item.get("source") == "pr_review":
            terminal = "done"
        update_task(db_path, item["id"], status=terminal)
        changes += 1

    notifications_since = get_sync_state(db_path, _NOTIFICATIONS_SINCE_KEY)
    notification_fetch_started_at = datetime.now(timezone.utc).isoformat()
    notifications, notifications_ok = await gh.fetch_notifications(
        gh_user,
        since=notifications_since,
    )
    if not notifications_ok:
        log.warning("Notification fetch failed — keeping previous notification cursor")
    notification_urls: list[str] = []
    for notif in notifications:
        reason = notif.get("reason", "")
        if reason not in ("mention", "comment", "review_requested"):
            continue
        subject = notif.get("subject", {})
        subject_url = subject.get("url", "")
        if subject_url and "/pulls/" in subject_url:
            notification_urls.append(
                subject_url.replace("api.github.com/repos", "github.com").replace("/pulls/", "/pull/")
            )
        elif subject_url and "/issues/" in subject_url:
            notification_urls.append(
                subject_url.replace("api.github.com/repos", "github.com")
            )

    notification_tasks_by_url = find_tasks_by_gh_urls(db_path, notification_urls)
    for web_url, task in notification_tasks_by_url.items():
        if task.get("seen") != 1:
            continue
        update_task(db_path, task["id"], seen=0, last_changed_at=now)
        changes += 1
        attention = True
    if notifications_ok:
        set_sync_state(db_path, _NOTIFICATIONS_SINCE_KEY, notification_fetch_started_at)

    log.info("Sync complete: %d changes, attention=%s", changes, attention)
    return changes, attention, None

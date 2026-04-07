"""Sync engine — orchestrates GitHub fetching, diffing, and DB updates."""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from agendum import gh
from agendum.config import AgendumConfig
from agendum.db import (
    add_task,
    find_task_by_gh_url,
    get_active_tasks,
    update_task,
    TERMINAL_STATUSES,
)

log = logging.getLogger(__name__)


@dataclass
class SyncResult:
    to_create: list[dict] = field(default_factory=list)
    to_update: list[dict] = field(default_factory=list)
    to_close: list[dict] = field(default_factory=list)


def diff_tasks(existing: list[dict], incoming: list[dict]) -> SyncResult:
    """Compare existing DB tasks against incoming GitHub state."""
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
            result.to_close.append(task)

    return result


async def run_sync(db_path: Path, config: AgendumConfig) -> tuple[int, bool, str | None]:
    """
    Execute a full sync cycle.
    Returns (changes_count, has_attention_items, error_message).
    """
    if not config.orgs and not config.repos:
        log.warning("No orgs or repos configured — skipping sync")
        return 0, False, None

    gh_user = await gh.get_gh_username()
    if not gh_user:
        log.error("Could not determine GitHub username")
        return 0, False, "gh credentials expired"

    if config.repos:
        repos = set(config.repos)
    else:
        repos = await gh.discover_repos(config.orgs, gh_user)
    repos -= {r for r in repos if r in config.exclude_repos}

    sem = asyncio.Semaphore(8)
    incoming_tasks: list[dict] = []

    async def fetch_one_repo(repo_full: str) -> None:
        async with sem:
            owner, name = repo_full.split("/", 1)
            data = await gh.fetch_repo_data(owner, name, gh_user)
            if not data:
                return
            repo_data = data.get("data", {}).get("repository", {})
            if not repo_data or repo_data.get("isArchived"):
                return
            short_name = gh.extract_repo_short_name(repo_full)

            for pr in repo_data.get("authoredPRs", {}).get("nodes", []):
                author_login = (pr.get("author") or {}).get("login", "")
                if author_login.lower() != gh_user.lower():
                    continue
                status = gh.derive_authored_pr_status(
                    is_draft=pr.get("isDraft", False),
                    review_decision=pr.get("reviewDecision"),
                    state=pr.get("state", "OPEN"),
                    has_review_requests=(pr.get("reviewRequests", {}).get("totalCount", 0) > 0),
                )
                labels = [l["name"] for l in (pr.get("labels", {}).get("nodes", []))]
                incoming_tasks.append({
                    "title": pr["title"],
                    "source": "pr_authored",
                    "status": status,
                    "project": short_name,
                    "gh_repo": repo_full,
                    "gh_url": pr["url"],
                    "gh_number": pr["number"],
                    "tags": json.dumps(labels) if labels else None,
                })

            for pr in repo_data.get("mergedPRs", {}).get("nodes", []):
                author_login = (pr.get("author") or {}).get("login", "")
                if author_login.lower() != gh_user.lower():
                    continue
                incoming_tasks.append({
                    "title": "",
                    "source": "pr_authored",
                    "status": "merged",
                    "gh_url": pr["url"],
                    "gh_number": pr["number"],
                    "project": short_name,
                    "gh_repo": repo_full,
                })
            for pr in repo_data.get("closedPRs", {}).get("nodes", []):
                author_login = (pr.get("author") or {}).get("login", "")
                if author_login.lower() != gh_user.lower():
                    continue
                incoming_tasks.append({
                    "title": "",
                    "source": "pr_authored",
                    "status": "closed",
                    "gh_url": pr["url"],
                    "gh_number": pr["number"],
                    "project": short_name,
                    "gh_repo": repo_full,
                })

            for issue in repo_data.get("openIssues", {}).get("nodes", []):
                timeline = issue.get("timelineItems", {}).get("nodes", [])
                has_linked_pr = any(
                    (n.get("subject") or n.get("source") or {}).get("url")
                    for n in timeline
                )
                status = gh.derive_issue_status(state=issue["state"], has_linked_pr=has_linked_pr)
                labels = [l["name"] for l in (issue.get("labels", {}).get("nodes", []))]
                incoming_tasks.append({
                    "title": issue["title"],
                    "source": "issue",
                    "status": status,
                    "project": short_name,
                    "gh_repo": repo_full,
                    "gh_url": issue["url"],
                    "gh_number": issue["number"],
                    "tags": json.dumps(labels) if labels else None,
                })

            for issue in repo_data.get("closedIssues", {}).get("nodes", []):
                incoming_tasks.append({
                    "title": "",
                    "source": "issue",
                    "status": "closed",
                    "gh_url": issue["url"],
                    "gh_number": issue["number"],
                    "project": short_name,
                    "gh_repo": repo_full,
                })

    await asyncio.gather(*(fetch_one_repo(r) for r in repos))

    review_prs = await gh.discover_review_prs(config.orgs, gh_user)
    for pr_info in review_prs:
        repo_info = pr_info.get("repository", {})
        repo_full = repo_info.get("nameWithOwner", "")
        if not repo_full or repo_full in config.exclude_repos:
            continue
        if config.repos and repo_full not in config.repos:
            continue
        owner, name = repo_full.split("/", 1)
        detail_data = await gh.fetch_review_detail(owner, name, pr_info["number"], gh_user)
        pr_detail = (detail_data.get("data", {}).get("repository", {}).get("pullRequest") or {})
        if not pr_detail:
            continue

        reviews = pr_detail.get("reviews", {}).get("nodes", [])
        user_reviews = [r for r in reviews if (r.get("author") or {}).get("login", "").lower() == gh_user.lower()]
        user_has_reviewed = len(user_reviews) > 0

        new_commits_since = False
        if user_has_reviewed:
            last_review_time = max(r.get("submittedAt", "") for r in user_reviews)
            last_commit_nodes = pr_detail.get("commits", {}).get("nodes", [])
            if last_commit_nodes:
                last_commit_time = last_commit_nodes[0].get("commit", {}).get("committedDate", "")
                new_commits_since = last_commit_time > last_review_time

        status = gh.derive_review_pr_status(
            user_has_reviewed=user_has_reviewed,
            new_commits_since_review=new_commits_since,
        )

        author_info = pr_detail.get("author") or {}
        author_login = author_info.get("login", "")
        author_name = gh.parse_author_first_name(author_info.get("name"))

        incoming_tasks.append({
            "title": pr_detail.get("title", pr_info.get("title", "")),
            "source": "pr_review",
            "status": status,
            "project": gh.extract_repo_short_name(repo_full),
            "gh_repo": repo_full,
            "gh_url": pr_detail.get("url", pr_info.get("url", "")),
            "gh_number": pr_detail.get("number", pr_info.get("number")),
            "gh_author": author_login,
            "gh_author_name": author_name or author_login,
            "tags": json.dumps(["review"]),
        })

    existing = get_active_tasks(db_path)
    diff = diff_tasks(existing, incoming_tasks)

    changes = 0
    attention = False
    now = datetime.now(timezone.utc).isoformat()

    for item in diff.to_create:
        if item.get("status") in TERMINAL_STATUSES:
            existing_task = find_task_by_gh_url(db_path, item["gh_url"])
            if existing_task:
                update_task(db_path, existing_task["id"], status=item["status"])
                changes += 1
            continue
        existing_task = find_task_by_gh_url(db_path, item["gh_url"]) if item.get("gh_url") else None
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
        if item.get("source") == "pr_review" and item.get("status") == "review requested":
            attention = True

    for item in diff.to_update:
        task_id = item.pop("id")
        item["seen"] = 0
        item["last_changed_at"] = now
        update_task(db_path, task_id, **item)
        changes += 1
        if "status" in item and item["status"] in ("changes requested", "approved"):
            attention = True

    for item in diff.to_close:
        terminal = "merged" if item.get("source") == "pr_authored" else "closed"
        if item.get("source") == "pr_review":
            terminal = "done"
        update_task(db_path, item["id"], status=terminal)
        changes += 1

    notifications = await gh.fetch_notifications(gh_user)
    for notif in notifications:
        reason = notif.get("reason", "")
        if reason in ("mention", "comment", "review_requested"):
            subject = notif.get("subject", {})
            subject_url = subject.get("url", "")
            if subject_url and "/pulls/" in subject_url:
                web_url = subject_url.replace("api.github.com/repos", "github.com").replace("/pulls/", "/pull/")
                task = find_task_by_gh_url(db_path, web_url)
                if task and task.get("seen") == 1:
                    update_task(db_path, task["id"], seen=0, last_changed_at=now)
                    changes += 1
                    attention = True
            elif subject_url and "/issues/" in subject_url:
                web_url = subject_url.replace("api.github.com/repos", "github.com")
                task = find_task_by_gh_url(db_path, web_url)
                if task and task.get("seen") == 1:
                    update_task(db_path, task["id"], seen=0, last_changed_at=now)
                    changes += 1
                    attention = True

    log.info("Sync complete: %d changes, attention=%s", changes, attention)
    return changes, attention, None

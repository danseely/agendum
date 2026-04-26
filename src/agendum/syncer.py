"""Sync engine — orchestrates GitHub fetching, diffing, and DB updates."""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

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


@dataclass(frozen=True)
class OpenDiscoveryCoverage:
    authored_complete: bool = True
    issues_complete: bool = True
    review_complete: bool = True


@dataclass(frozen=True)
class OpenHydrationBundle:
    authored_prs: list[dict[str, Any]] = field(default_factory=list)
    issues: list[dict[str, Any]] = field(default_factory=list)
    review_prs: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True)
class TrackedTaskRef:
    task_id: int | None
    source: str
    gh_repo: str | None
    gh_url: str | None
    gh_node_id: str | None
    gh_number: int | None
    title: str | None


@dataclass(frozen=True)
class MissingVerificationRequest:
    authored_prs: list[TrackedTaskRef] = field(default_factory=list)
    issues: list[TrackedTaskRef] = field(default_factory=list)
    review_prs: list[TrackedTaskRef] = field(default_factory=list)


@dataclass(frozen=True)
class VerifiedMissingItem:
    gh_node_id: str | None
    gh_url: str | None
    state: str
    is_assigned_to_user: bool | None = None
    is_review_requested: bool | None = None


@dataclass(frozen=True)
class MissingVerificationBundle:
    authored_prs: list[VerifiedMissingItem] = field(default_factory=list)
    issues: list[VerifiedMissingItem] = field(default_factory=list)
    review_prs: list[VerifiedMissingItem] = field(default_factory=list)
    authored_complete: bool = True
    issues_complete: bool = True
    review_complete: bool = True


@dataclass(frozen=True)
class CloseSuppression:
    authored: bool = False
    issues: bool = False
    review: bool = False
    authored_urls: frozenset[str] = frozenset()
    issue_urls: frozenset[str] = frozenset()
    review_urls: frozenset[str] = frozenset()


@dataclass(frozen=True)
class NormalizedIncomingTask:
    title: str
    source: str
    status: str
    project: str | None = None
    gh_repo: str | None = None
    gh_url: str | None = None
    gh_node_id: str | None = None
    gh_number: int | None = None
    gh_author: str | None = None
    gh_author_name: str | None = None
    tags: str | None = None

    def as_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "title": self.title,
            "source": self.source,
            "status": self.status,
        }
        for key in (
            "project",
            "gh_repo",
            "gh_url",
            "gh_node_id",
            "gh_number",
            "gh_author",
            "gh_author_name",
            "tags",
        ):
            value = getattr(self, key)
            if value is not None:
                data[key] = value
        return data


@dataclass(frozen=True)
class SyncPlan:
    coverage: OpenDiscoveryCoverage
    open_hydration: OpenHydrationBundle
    missing_verification_request: MissingVerificationRequest
    missing_verification: MissingVerificationBundle
    close_suppression: CloseSuppression
    normalized_incoming_tasks: list[NormalizedIncomingTask]


def _lane_identity(item: dict[str, Any]) -> str | None:
    return item.get("gh_node_id") or item.get("gh_url")


def _tracked_task_ref(task: dict[str, Any]) -> TrackedTaskRef | None:
    if task.get("source") not in {"pr_authored", "issue", "pr_review"}:
        return None
    if not (task.get("gh_node_id") or task.get("gh_url")):
        return None
    return TrackedTaskRef(
        task_id=task.get("id"),
        source=task["source"],
        gh_repo=task.get("gh_repo"),
        gh_url=task.get("gh_url"),
        gh_node_id=task.get("gh_node_id"),
        gh_number=task.get("gh_number"),
        title=task.get("title"),
    )


def _open_identities(items: list[dict[str, Any]]) -> set[str]:
    return {
        identity
        for identity in (_lane_identity(item) for item in items)
        if identity
    }


def plan_missing_verification_requests(
    existing: list[dict[str, Any]],
    open_hydration: OpenHydrationBundle,
    coverage: OpenDiscoveryCoverage,
) -> MissingVerificationRequest:
    authored_open = _open_identities(open_hydration.authored_prs)
    issue_open = _open_identities(open_hydration.issues)
    review_open = _open_identities(open_hydration.review_prs)

    authored_missing: list[TrackedTaskRef] = []
    issues_missing: list[TrackedTaskRef] = []
    review_missing: list[TrackedTaskRef] = []
    seen: set[tuple[str, str]] = set()

    for task in existing:
        tracked = _tracked_task_ref(task)
        if tracked is None:
            continue
        identity = tracked.gh_node_id or tracked.gh_url
        assert identity is not None
        seen_key = (tracked.source, identity)
        if seen_key in seen:
            continue
        seen.add(seen_key)

        if tracked.source == "pr_authored":
            if not coverage.authored_complete or identity in authored_open:
                continue
            authored_missing.append(tracked)
        elif tracked.source == "issue":
            if not coverage.issues_complete or identity in issue_open:
                continue
            issues_missing.append(tracked)
        elif tracked.source == "pr_review":
            if not coverage.review_complete or identity in review_open:
                continue
            review_missing.append(tracked)

    return MissingVerificationRequest(
        authored_prs=authored_missing,
        issues=issues_missing,
        review_prs=review_missing,
    )


def _normalize_open_authored_task(item: dict[str, Any], *, gh_user: str) -> NormalizedIncomingTask:
    reviews = item.get("reviews", {}).get("nodes", [])
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
    last_commit_nodes = item.get("commits", {}).get("nodes", [])
    latest_commit_time = None
    if last_commit_nodes:
        latest_commit_time = (
            last_commit_nodes[0].get("commit", {}).get("committedDate")
        )
    author_login = (item.get("author") or {}).get("login", "")
    status = gh.derive_authored_pr_status(
        is_draft=item.get("isDraft", False),
        review_decision=item.get("reviewDecision"),
        state=item.get("state", "OPEN"),
        has_review_requests=(item.get("reviewRequests", {}).get("totalCount", 0) > 0),
        latest_commit_time=latest_commit_time,
        latest_comment_review_id=(latest_comment_review or {}).get("id"),
        latest_comment_review_time=(latest_comment_review or {}).get("submittedAt"),
        qualifying_reviews=qualifying_reviews,
        author_login=author_login,
        review_threads=item.get("reviewThreads", {}).get("nodes", []),
    )
    repo_full = item["repository"]["nameWithOwner"]
    labels = [label["name"] for label in (item.get("labels", {}).get("nodes", []))]
    return NormalizedIncomingTask(
        title=item.get("title", ""),
        source="pr_authored",
        status=status,
        project=gh.extract_repo_short_name(repo_full),
        gh_repo=repo_full,
        gh_url=item.get("url"),
        gh_node_id=item.get("gh_node_id"),
        gh_number=item.get("number"),
        tags=json.dumps(labels) if labels else None,
    )


def _normalize_open_issue_task(item: dict[str, Any]) -> NormalizedIncomingTask:
    timeline = item.get("timelineItems", {}).get("nodes", [])
    has_linked_pr = any(
        (node.get("subject") or node.get("source") or {}).get("url")
        for node in timeline
    )
    repo_full = item["repository"]["nameWithOwner"]
    labels = [label["name"] for label in (item.get("labels", {}).get("nodes", []))]
    return NormalizedIncomingTask(
        title=item.get("title", ""),
        source="issue",
        status=gh.derive_issue_status(
            state=item.get("state", "OPEN"),
            has_linked_pr=has_linked_pr,
        ),
        project=gh.extract_repo_short_name(repo_full),
        gh_repo=repo_full,
        gh_url=item.get("url"),
        gh_node_id=item.get("gh_node_id"),
        gh_number=item.get("number"),
        tags=json.dumps(labels) if labels else None,
    )


def _normalize_open_review_task(item: dict[str, Any], *, gh_user: str) -> NormalizedIncomingTask:
    reviews = item.get("reviews", {}).get("nodes", [])
    user_reviews = [
        review
        for review in reviews
        if (review.get("author") or {}).get("login", "").lower() == gh_user.lower()
    ]
    user_has_reviewed = len(user_reviews) > 0

    new_commits_since = False
    re_requested_after_review = False
    if user_has_reviewed:
        last_review_time = max(
            (review.get("submittedAt") or "" for review in user_reviews),
            default="",
        )
        last_commit_nodes = item.get("commits", {}).get("nodes", [])
        if last_commit_nodes:
            last_commit_time = (
                last_commit_nodes[0].get("commit", {}).get("committedDate") or ""
            )
            new_commits_since = last_commit_time > last_review_time

        request_events = item.get("timelineItems", {}).get("nodes", [])
        for event in request_events:
            reviewer_login = ((event.get("requestedReviewer") or {}).get("login") or "")
            if reviewer_login.lower() != gh_user.lower():
                continue
            created_at = event.get("createdAt") or ""
            if created_at and created_at > last_review_time:
                re_requested_after_review = True
                break

    author_info = item.get("author") or {}
    author_login = author_info.get("login", "")
    author_name = gh.parse_author_first_name(author_info.get("name"))
    repo_full = item["repository"]["nameWithOwner"]
    return NormalizedIncomingTask(
        title=item.get("title", ""),
        source="pr_review",
        status=gh.derive_review_pr_status(
            user_has_reviewed=user_has_reviewed,
            new_commits_since_review=new_commits_since,
            re_requested_after_review=re_requested_after_review,
        ),
        project=gh.extract_repo_short_name(repo_full),
        gh_repo=repo_full,
        gh_url=item.get("url"),
        gh_node_id=item.get("gh_node_id"),
        gh_number=item.get("number"),
        gh_author=author_login,
        gh_author_name=author_name or author_login,
        tags=json.dumps(["review"]),
    )


def normalize_open_hydration_bundle(
    open_hydration: OpenHydrationBundle,
    *,
    gh_user: str,
) -> list[NormalizedIncomingTask]:
    tasks: list[NormalizedIncomingTask] = []
    tasks.extend(
        _normalize_open_authored_task(item, gh_user=gh_user)
        for item in open_hydration.authored_prs
    )
    tasks.extend(_normalize_open_issue_task(item) for item in open_hydration.issues)
    tasks.extend(
        _normalize_open_review_task(item, gh_user=gh_user)
        for item in open_hydration.review_prs
    )
    return tasks


def _verified_by_identity(items: list[VerifiedMissingItem]) -> dict[str, VerifiedMissingItem]:
    verified: dict[str, VerifiedMissingItem] = {}
    for item in items:
        if item.gh_node_id:
            verified[item.gh_node_id] = item
        if item.gh_url:
            verified[item.gh_url] = item
    return verified


def _normalize_verified_authored_task(
    tracked: TrackedTaskRef,
    verified: VerifiedMissingItem,
) -> NormalizedIncomingTask | None:
    if verified.state not in {"MERGED", "CLOSED"}:
        return None
    repo_full = tracked.gh_repo
    return NormalizedIncomingTask(
        title=tracked.title or "",
        source="pr_authored",
        status="merged" if verified.state == "MERGED" else "closed",
        project=gh.extract_repo_short_name(repo_full) if repo_full else None,
        gh_repo=repo_full,
        gh_url=verified.gh_url or tracked.gh_url,
        gh_node_id=verified.gh_node_id or tracked.gh_node_id,
        gh_number=tracked.gh_number,
    )


def _normalize_verified_issue_task(
    tracked: TrackedTaskRef,
    verified: VerifiedMissingItem,
) -> NormalizedIncomingTask | None:
    if verified.state == "OPEN" and verified.is_assigned_to_user:
        return None
    repo_full = tracked.gh_repo
    return NormalizedIncomingTask(
        title=tracked.title or "",
        source="issue",
        status="closed",
        project=gh.extract_repo_short_name(repo_full) if repo_full else None,
        gh_repo=repo_full,
        gh_url=verified.gh_url or tracked.gh_url,
        gh_node_id=verified.gh_node_id or tracked.gh_node_id,
        gh_number=tracked.gh_number,
    )


def _normalize_verified_review_task(
    tracked: TrackedTaskRef,
    verified: VerifiedMissingItem,
) -> NormalizedIncomingTask | None:
    if verified.state == "OPEN" and verified.is_review_requested:
        return None
    repo_full = tracked.gh_repo
    return NormalizedIncomingTask(
        title=tracked.title or "",
        source="pr_review",
        status="done",
        project=gh.extract_repo_short_name(repo_full) if repo_full else None,
        gh_repo=repo_full,
        gh_url=verified.gh_url or tracked.gh_url,
        gh_node_id=verified.gh_node_id or tracked.gh_node_id,
        gh_number=tracked.gh_number,
    )


def normalize_missing_verification_bundle(
    request: MissingVerificationRequest,
    verification: MissingVerificationBundle,
) -> list[NormalizedIncomingTask]:
    tasks: list[NormalizedIncomingTask] = []

    authored_verified = _verified_by_identity(verification.authored_prs)
    for tracked in request.authored_prs:
        identity = tracked.gh_node_id or tracked.gh_url
        if not identity or identity not in authored_verified:
            continue
        normalized = _normalize_verified_authored_task(tracked, authored_verified[identity])
        if normalized is not None:
            tasks.append(normalized)

    issues_verified = _verified_by_identity(verification.issues)
    for tracked in request.issues:
        identity = tracked.gh_node_id or tracked.gh_url
        if not identity or identity not in issues_verified:
            continue
        normalized = _normalize_verified_issue_task(tracked, issues_verified[identity])
        if normalized is not None:
            tasks.append(normalized)

    review_verified = _verified_by_identity(verification.review_prs)
    for tracked in request.review_prs:
        identity = tracked.gh_node_id or tracked.gh_url
        if not identity or identity not in review_verified:
            continue
        normalized = _normalize_verified_review_task(tracked, review_verified[identity])
        if normalized is not None:
            tasks.append(normalized)

    return tasks


def compute_close_suppression(
    coverage: OpenDiscoveryCoverage,
    request: MissingVerificationRequest,
    verification: MissingVerificationBundle,
) -> CloseSuppression:
    authored_urls = set()
    issue_urls = set()
    review_urls = set()

    if not verification.authored_complete:
        authored_verified = {
            item.gh_node_id or item.gh_url
            for item in verification.authored_prs
            if (item.gh_node_id or item.gh_url)
        }
        authored_urls = {
            tracked.gh_url
            for tracked in request.authored_prs
            if tracked.gh_url and (tracked.gh_node_id or tracked.gh_url) not in authored_verified
        }

    if not verification.issues_complete:
        issue_verified = {
            item.gh_node_id or item.gh_url
            for item in verification.issues
            if (item.gh_node_id or item.gh_url)
        }
        issue_urls = {
            tracked.gh_url
            for tracked in request.issues
            if tracked.gh_url and (tracked.gh_node_id or tracked.gh_url) not in issue_verified
        }

    if not verification.review_complete:
        review_verified = {
            item.gh_node_id or item.gh_url
            for item in verification.review_prs
            if (item.gh_node_id or item.gh_url)
        }
        review_urls = {
            tracked.gh_url
            for tracked in request.review_prs
            if tracked.gh_url and (tracked.gh_node_id or tracked.gh_url) not in review_verified
        }

    for tracked in request.authored_prs:
        identity = tracked.gh_node_id or tracked.gh_url
        verified = _verified_by_identity(verification.authored_prs).get(identity or "")
        if verified is not None and verified.state not in {"MERGED", "CLOSED"} and tracked.gh_url:
            authored_urls.add(tracked.gh_url)

    for tracked in request.issues:
        identity = tracked.gh_node_id or tracked.gh_url
        verified = _verified_by_identity(verification.issues).get(identity or "")
        if verified is not None and verified.state == "OPEN" and verified.is_assigned_to_user and tracked.gh_url:
            issue_urls.add(tracked.gh_url)

    for tracked in request.review_prs:
        identity = tracked.gh_node_id or tracked.gh_url
        verified = _verified_by_identity(verification.review_prs).get(identity or "")
        if verified is not None and verified.state == "OPEN" and verified.is_review_requested and tracked.gh_url:
            review_urls.add(tracked.gh_url)

    return CloseSuppression(
        authored=not coverage.authored_complete,
        issues=not coverage.issues_complete,
        review=not coverage.review_complete,
        authored_urls=frozenset(authored_urls),
        issue_urls=frozenset(issue_urls),
        review_urls=frozenset(review_urls),
    )


def build_sync_plan(
    existing: list[dict[str, Any]],
    open_hydration: OpenHydrationBundle,
    *,
    gh_user: str,
    coverage: OpenDiscoveryCoverage | None = None,
    verification: MissingVerificationBundle | None = None,
) -> SyncPlan:
    coverage = coverage or OpenDiscoveryCoverage()
    request = plan_missing_verification_requests(existing, open_hydration, coverage)
    verification = verification or MissingVerificationBundle()
    normalized_incoming_tasks = normalize_open_hydration_bundle(
        open_hydration,
        gh_user=gh_user,
    ) + normalize_missing_verification_bundle(request, verification)
    close_suppression = compute_close_suppression(coverage, request, verification)
    return SyncPlan(
        coverage=coverage,
        open_hydration=open_hydration,
        missing_verification_request=request,
        missing_verification=verification,
        close_suppression=close_suppression,
        normalized_incoming_tasks=normalized_incoming_tasks,
    )


def diff_tasks(
    existing: list[dict],
    incoming: list[dict],
    *,
    fetched_repos: set[str] | None = None,
    review_fetch_ok: bool = True,
    close_suppression: CloseSuppression | None = None,
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

    If *close_suppression* is provided, lane-wide or per-row close
    suppression is applied on top of the legacy guards.
    """
    result = SyncResult()
    close_suppression = close_suppression or CloseSuppression()

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
            for key in (
                "gh_repo",
                "gh_node_id",
                "gh_number",
                "gh_author",
                "gh_author_name",
                "tags",
                "project",
            ):
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
            source = task.get("source")
            if source == "pr_authored":
                if close_suppression.authored or url in close_suppression.authored_urls:
                    continue
            elif source == "issue":
                if close_suppression.issues or url in close_suppression.issue_urls:
                    continue
            elif source == "pr_review":
                if close_suppression.review or url in close_suppression.review_urls:
                    continue
            # Don't close review tasks when the review fetch was incomplete.
            if not review_fetch_ok and source == "pr_review":
                continue
            # Only close items from repos we actually fetched data for.
            # pr_review tasks are gated by review_fetch_ok instead — their
            # repo may drop out of fetched_repos once GitHub removes the
            # user from --review-requested.
            if fetched_repos is not None and source != "pr_review":
                task_repo = task.get("gh_repo", "")
                if task_repo not in fetched_repos:
                    continue
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

    with gh.use_gh_config_dir(_workspace_gh_config_dir(db_path)):
        return await _run_sync_once(db_path, config)


def _workspace_gh_config_dir(db_path: Path) -> Path:
    """Map a workspace DB path to its colocated gh auth/config directory."""
    return db_path.parent / "gh"


async def _run_sync_once(
    db_path: Path,
    config: AgendumConfig,
) -> tuple[int, bool, str | None]:
    gh_user = await gh.get_gh_username()
    if not gh_user:
        log.error("Could not determine GitHub username")
        return 0, False, "gh credentials expired"

    return await _run_sync_once_planner(db_path, config, gh_user=gh_user)


def _task_is_in_scope(task: dict[str, Any], *, excluded_repos: set[str]) -> bool:
    repo_full = task.get("gh_repo")
    if repo_full and repo_full in excluded_repos:
        return False
    return True


def _task_repo_for_scope(task: dict[str, Any]) -> str | None:
    repo_full = task.get("gh_repo")
    if repo_full:
        return repo_full
    parsed = gh._parse_github_task_url(task.get("gh_url"))
    if parsed is None:
        return None
    owner, name, _, _ = parsed
    return f"{owner}/{name}"


def _planner_active_repos(
    *,
    scoped_repos: list[str],
    scoped_orgs: list[str],
    existing_tasks: list[dict[str, Any]],
    authored_hydrated: list[dict[str, Any]],
    issues_hydrated: list[dict[str, Any]],
    review_hydrated: list[dict[str, Any]],
) -> set[str]:
    if scoped_repos:
        return set(scoped_repos)

    active_repos: set[str] = set()
    for items in (authored_hydrated, issues_hydrated, review_hydrated):
        for item in items:
            repo_full = ((item.get("repository") or {}).get("nameWithOwner") or "")
            if repo_full:
                active_repos.add(repo_full)

    # Tracked rows in repos within the configured orgs are still in scope
    # even when the repo currently has zero open discovered items. Without
    # this, terminal verification skips dormant in-scope repos and tracked
    # authored/issue rows stay open forever.
    scoped_org_lower = {org.lower() for org in scoped_orgs}
    for task in existing_tasks:
        repo_full = _task_repo_for_scope(task)
        if not repo_full:
            continue
        owner = repo_full.split("/", 1)[0]
        if owner.lower() in scoped_org_lower:
            active_repos.add(repo_full)
    return active_repos


def _task_is_verifiable_in_planner_scope(
    task: dict[str, Any],
    *,
    active_repos: set[str],
) -> bool:
    source = task.get("source")
    if source == "pr_review":
        return True
    if source in {"pr_authored", "issue"}:
        task_repo = _task_repo_for_scope(task)
        return bool(task_repo) and task_repo in active_repos
    return False


def _repo_is_archived(item: dict[str, Any]) -> bool:
    return bool((item.get("repository") or {}).get("isArchived", False))


async def _run_sync_once_planner(
    db_path: Path,
    config: AgendumConfig,
    *,
    gh_user: str,
) -> tuple[int, bool, str | None]:
    excluded_repos = set(config.exclude_repos)
    scoped_repos = [repo for repo in config.repos if repo not in excluded_repos]
    scoped_orgs = [] if scoped_repos else config.orgs
    if not scoped_repos and not scoped_orgs:
        log.warning("No orgs or repos configured after exclusions — skipping sync")
        return 0, False, None

    existing = [
        task
        for task in get_active_tasks(db_path)
        if _task_is_in_scope(task, excluded_repos=excluded_repos)
    ]

    if scoped_repos:
        discovered = await asyncio.gather(
            gh.search_open_authored_prs_for_repos_with_completeness(scoped_repos, gh_user),
            gh.search_open_assigned_issues_for_repos_with_completeness(scoped_repos, gh_user),
            gh.search_open_review_requested_prs_for_repos_with_completeness(
                scoped_repos,
                gh_user,
            ),
        )
    else:
        discovered = await asyncio.gather(
            gh.search_open_authored_prs_with_completeness(scoped_orgs, gh_user),
            gh.search_open_assigned_issues_with_completeness(scoped_orgs, gh_user),
            gh.search_open_review_requested_prs_with_completeness(scoped_orgs, gh_user),
        )
    (authored_discovered, authored_search_complete), (
        issues_discovered,
        issues_search_complete,
    ), (
        review_discovered,
        review_search_complete,
    ) = discovered

    def _keep(item: dict[str, Any]) -> bool:
        repo_full = ((item.get("repository") or {}).get("nameWithOwner") or "")
        return bool(repo_full) and repo_full not in excluded_repos

    authored_discovered = [item for item in authored_discovered if _keep(item)]
    issues_discovered = [item for item in issues_discovered if _keep(item)]
    review_discovered = [item for item in review_discovered if _keep(item)]
    if scoped_repos:
        repo_archive_states, _ = await gh.fetch_repo_archive_states_with_completeness(
            scoped_repos
        )
        # Drop only repos confirmed archived. Repos with no entry came back
        # from a partial lookup; treat them as in-scope so a flaky archive
        # query does not silently remove healthy repos from planner scope.
        scoped_repos = [
            repo
            for repo in scoped_repos
            if repo_archive_states.get(repo) is not True
        ]
        authored_discovered = [
            item
            for item in authored_discovered
            if ((item.get("repository") or {}).get("nameWithOwner") or "") in scoped_repos
        ]
        issues_discovered = [
            item
            for item in issues_discovered
            if ((item.get("repository") or {}).get("nameWithOwner") or "") in scoped_repos
        ]
        review_discovered = [
            item
            for item in review_discovered
            if ((item.get("repository") or {}).get("nameWithOwner") or "") in scoped_repos
        ]

    hydrated = await asyncio.gather(
        gh.hydrate_open_authored_prs_with_completeness(authored_discovered),
        gh.hydrate_open_issues_with_completeness(issues_discovered),
        gh.hydrate_open_review_prs_with_completeness(review_discovered),
    )
    (authored_hydrated, authored_hydrate_complete), (
        issues_hydrated,
        issues_hydrate_complete,
    ), (
        review_hydrated,
        review_hydrate_complete,
    ) = hydrated
    authored_hydrated = [item for item in authored_hydrated if not _repo_is_archived(item)]
    issues_hydrated = [item for item in issues_hydrated if not _repo_is_archived(item)]
    review_hydrated = [item for item in review_hydrated if not _repo_is_archived(item)]
    active_repos = _planner_active_repos(
        scoped_repos=scoped_repos,
        scoped_orgs=scoped_orgs,
        existing_tasks=existing,
        authored_hydrated=authored_hydrated,
        issues_hydrated=issues_hydrated,
        review_hydrated=review_hydrated,
    )

    coverage = OpenDiscoveryCoverage(
        authored_complete=authored_search_complete and authored_hydrate_complete,
        issues_complete=issues_search_complete and issues_hydrate_complete,
        review_complete=review_search_complete and review_hydrate_complete,
    )
    open_hydration = OpenHydrationBundle(
        authored_prs=authored_hydrated,
        issues=issues_hydrated,
        review_prs=review_hydrated,
    )

    existing_for_plan = [
        task
        for task in existing
        if _task_is_verifiable_in_planner_scope(task, active_repos=active_repos)
    ]

    initial_plan = build_sync_plan(
        existing_for_plan,
        open_hydration,
        gh_user=gh_user,
        coverage=coverage,
    )

    verified = await asyncio.gather(
        gh.verify_missing_authored_prs_with_completeness(
            [item.__dict__ for item in initial_plan.missing_verification_request.authored_prs]
        ),
        gh.verify_missing_issues_with_completeness(
            [item.__dict__ for item in initial_plan.missing_verification_request.issues],
            gh_user=gh_user,
        ),
        gh.verify_missing_review_prs_with_completeness(
            [item.__dict__ for item in initial_plan.missing_verification_request.review_prs],
            gh_user=gh_user,
        ),
    )
    (authored_verified, authored_verify_complete), (
        issues_verified,
        issues_verify_complete,
    ), (
        review_verified,
        review_verify_complete,
    ) = verified

    final_plan = build_sync_plan(
        existing_for_plan,
        open_hydration,
        gh_user=gh_user,
        coverage=coverage,
        verification=MissingVerificationBundle(
            authored_prs=[VerifiedMissingItem(**item) for item in authored_verified],
            issues=[VerifiedMissingItem(**item) for item in issues_verified],
            review_prs=[VerifiedMissingItem(**item) for item in review_verified],
            authored_complete=authored_verify_complete,
            issues_complete=issues_verify_complete,
            review_complete=review_verify_complete,
        ),
    )

    diff = diff_tasks(
        existing,
        [item.as_dict() for item in final_plan.normalized_incoming_tasks],
        fetched_repos=active_repos,
        close_suppression=final_plan.close_suppression,
    )
    changes, attention = _apply_sync_diff(db_path, diff)
    notification_changes, notification_attention = await _apply_notifications(
        db_path,
        gh_user=gh_user,
    )
    changes += notification_changes
    attention = attention or notification_attention
    log.info("Sync complete: %d changes, attention=%s", changes, attention)
    return changes, attention, None


def _apply_sync_diff(db_path: Path, diff: SyncResult) -> tuple[int, bool]:
    changes = 0
    attention = False
    now = datetime.now(timezone.utc).isoformat()
    material_update_keys = {
        "title",
        "source",
        "status",
        "project",
        "gh_author",
        "gh_author_name",
        "tags",
    }

    for item in diff.to_create:
        if item.get("status") in TERMINAL_STATUSES:
            existing_task = find_task_by_gh_url(db_path, item["gh_url"])
            if existing_task:
                update_fields = {"status": item["status"]}
                if item.get("gh_node_id") is not None:
                    update_fields["gh_node_id"] = item["gh_node_id"]
                update_task(db_path, existing_task["id"], **update_fields)
                changes += 1
            continue
        existing_task = find_task_by_gh_url(db_path, item["gh_url"]) if item.get("gh_url") else None
        if existing_task:
            update_fields = {
                k: item[k]
                for k in (
                    "title",
                    "source",
                    "status",
                    "project",
                    "gh_repo",
                    "gh_node_id",
                    "gh_number",
                    "gh_author",
                    "gh_author_name",
                    "tags",
                )
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
                gh_node_id=item.get("gh_node_id"),
                gh_number=item.get("gh_number"),
                gh_author=item.get("gh_author"),
                gh_author_name=item.get("gh_author_name"),
                tags=item.get("tags"),
            )
        changes += 1
        if item.get("source") == "pr_review" and item.get("status") in (
            "review requested",
            "re-review requested",
        ):
            attention = True

    for item in diff.to_update:
        task_id = item.pop("id")
        is_material_update = any(key in material_update_keys for key in item)
        if is_material_update:
            item["seen"] = 0
            item["last_changed_at"] = now
        update_task(db_path, task_id, **item)
        changes += 1
        if "status" in item and item["status"] in (
            "changes requested",
            "approved",
            "review received",
            "re-review requested",
        ):
            attention = True

    for item in diff.to_close:
        terminal = "merged" if item.get("source") == "pr_authored" else "closed"
        if item.get("source") == "pr_review":
            terminal = "done"
        update_task(db_path, item["id"], status=terminal)
        changes += 1

    return changes, attention


async def _apply_notifications(
    db_path: Path,
    *,
    gh_user: str,
) -> tuple[int, bool]:
    changes = 0
    attention = False
    now = datetime.now(timezone.utc).isoformat()
    notifications = await gh.fetch_notifications(gh_user)
    for notif in notifications:
        reason = notif.get("reason", "")
        if reason not in ("mention", "comment", "review_requested"):
            continue
        subject = notif.get("subject", {})
        subject_url = subject.get("url", "")
        if subject_url and "/pulls/" in subject_url:
            web_url = subject_url.replace("api.github.com/repos", "github.com").replace(
                "/pulls/",
                "/pull/",
            )
        elif subject_url and "/issues/" in subject_url:
            web_url = subject_url.replace("api.github.com/repos", "github.com")
        else:
            continue
        task = find_task_by_gh_url(db_path, web_url)
        if task and task.get("seen") == 1:
            update_task(db_path, task["id"], seen=0, last_changed_at=now)
            changes += 1
            attention = True
    return changes, attention

#!/usr/bin/env python3
"""Run isolated live sync benchmarks against GitHub-backed workspaces."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import tempfile
import time
from collections import Counter, defaultdict
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterator

from agendum.config import AgendumConfig, runtime_paths, write_config
from agendum.db import get_active_tasks, init_db
from agendum import gh
from agendum.syncer import run_sync

METRIC_LOG_MARKERS = ("sync metric", "sync metrics")
PAGINATION_LANES = {
    "search_open_authored_prs",
    "search_open_assigned_issues",
    "search_open_review_requested_prs",
}


def _normalize_multi(values: list[list[str]] | None) -> list[str]:
    normalized: list[str] = []
    for group in values or []:
        normalized.extend(item for item in group if item)
    return normalized


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--org",
        dest="orgs",
        action="append",
        nargs="+",
        help="GitHub org(s) to scan; repeat the flag or pass multiple values",
    )
    parser.add_argument(
        "--repos",
        action="append",
        nargs="+",
        help='Explicit repo whitelist in "owner/repo" format',
    )
    parser.add_argument(
        "--exclude-repos",
        dest="exclude_repos",
        action="append",
        nargs="+",
        help='Repo exclusions in "owner/repo" format',
    )
    parser.add_argument(
        "--runs",
        type=int,
        default=1,
        help="Number of cold+warm benchmark runs to execute",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Optional JSON output path; stdout is used when omitted",
    )
    args = parser.parse_args()
    args.orgs = _normalize_multi(args.orgs)
    args.repos = _normalize_multi(args.repos)
    args.exclude_repos = _normalize_multi(args.exclude_repos)
    if args.runs < 1:
        parser.error("--runs must be at least 1")
    if not args.orgs and not args.repos:
        parser.error("pass at least one --org or one --repos entry")
    return args


def classify_gh_call(args: tuple[str, ...]) -> str:
    """Classify a gh CLI call into a stable benchmark category."""
    if len(args) >= 2 and args[0] == "api" and args[1] == "user":
        return "user_lookup"
    if len(args) >= 2 and args[0] == "api" and args[1] == "notifications":
        return "notifications"
    if len(args) >= 2 and args[0] == "search" and args[1] == "prs":
        if "--author" in args and "--state" in args:
            return "search_open_authored_prs"
        if "--review-requested" in args and "--state" in args:
            return "search_open_review_requested_prs"
    if len(args) >= 2 and args[0] == "search" and args[1] == "issues":
        if "--assignee" in args and "--state" in args:
            return "search_open_assigned_issues"
    if len(args) >= 2 and args[0] == "api" and args[1] == "graphql":
        query = next((arg[6:] for arg in args if arg.startswith("query=")), "")
        if "pullRequest(number:" in query:
            return "review_detail_graphql"
        if "authoredPRs:" in query or "openIssues:" in query:
            return "repo_graphql"
        return "graphql"
    return "other"


def extract_batch_sizes(category: str, payload: str) -> list[int]:
    """Extract future-facing hydration batch sizes when present."""
    if not payload or not category.startswith(("hydrate_", "verify_missing_")):
        return []
    try:
        decoded = json.loads(payload)
    except json.JSONDecodeError:
        return []
    if isinstance(decoded, list):
        return [len(decoded)]
    if isinstance(decoded, dict):
        nodes = decoded.get("nodes")
        if isinstance(nodes, list):
            return [len(nodes)]
    return []


class MetricsLogHandler(logging.Handler):
    """Collect sync metric log lines if the syncer exposes them."""

    def __init__(self) -> None:
        super().__init__(level=logging.INFO)
        self.messages: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        message = record.getMessage()
        lowered = message.lower()
        if any(marker in lowered for marker in METRIC_LOG_MARKERS):
            self.messages.append(message)


@contextmanager
def instrument_gh_calls() -> Iterator[dict[str, Any]]:
    """Wrap agendum.gh._run_gh so benchmark runs can record CLI usage."""
    original_run_gh = gh._run_gh
    metrics: dict[str, Any] = {
        "call_records": [],
        "call_classification": Counter(),
        "payload_bytes_by_classification": Counter(),
        "lane_pagination_counts": Counter(),
        "hydration_batch_sizes": defaultdict(list),
    }

    async def wrapped_run_gh(*args: str) -> str:
        started = time.perf_counter()
        output = await original_run_gh(*args)
        duration_s = time.perf_counter() - started
        category = classify_gh_call(args)
        payload_bytes = len(output.encode())
        metrics["call_records"].append({
            "args": list(args),
            "category": category,
            "duration_s": round(duration_s, 6),
            "payload_bytes": payload_bytes,
        })
        metrics["call_classification"][category] += 1
        metrics["payload_bytes_by_classification"][category] += payload_bytes
        if category in PAGINATION_LANES:
            metrics["lane_pagination_counts"][category] += 1
        batch_sizes = extract_batch_sizes(category, output)
        if batch_sizes:
            metrics["hydration_batch_sizes"][category].extend(batch_sizes)
        return output

    gh._run_gh = wrapped_run_gh
    try:
        yield metrics
    finally:
        gh._run_gh = original_run_gh


def make_config(args: argparse.Namespace) -> AgendumConfig:
    return AgendumConfig(
        orgs=args.orgs,
        repos=args.repos,
        exclude_repos=args.exclude_repos,
    )


def _json_ready_counter(counter: Counter[str]) -> dict[str, int]:
    return dict(sorted(counter.items()))


def _json_ready_list_map(values: defaultdict[str, list[int]]) -> dict[str, list[int]]:
    return {key: value for key, value in sorted(values.items())}


async def run_phase(
    *,
    db_path: Path,
    config: AgendumConfig,
    metrics_handler: MetricsLogHandler,
) -> dict[str, Any]:
    with instrument_gh_calls() as call_metrics:
        started = time.perf_counter()
        changes, attention, error = await run_sync(db_path, config)
        wall_time_s = time.perf_counter() - started

    return {
        "wall_time_s": round(wall_time_s, 6),
        "changes": changes,
        "attention": attention,
        "error": error,
        "active_task_count": len(get_active_tasks(db_path)),
        "total_gh_calls": len(call_metrics["call_records"]),
        "payload_bytes": sum(record["payload_bytes"] for record in call_metrics["call_records"]),
        "call_classification": _json_ready_counter(call_metrics["call_classification"]),
        "payload_bytes_by_classification": _json_ready_counter(
            call_metrics["payload_bytes_by_classification"]
        ),
        "lane_pagination_counts": _json_ready_counter(call_metrics["lane_pagination_counts"]),
        "hydration_batch_sizes": _json_ready_list_map(call_metrics["hydration_batch_sizes"]),
        "sync_metrics_log_surface": list(metrics_handler.messages),
        "calls": call_metrics["call_records"],
    }


async def run_benchmark(args: argparse.Namespace) -> dict[str, Any]:
    config = make_config(args)
    report: dict[str, Any] = {
        "generated_at": datetime.now(UTC).isoformat(),
        "config": {
            "orgs": config.orgs,
            "repos": config.repos,
            "exclude_repos": config.exclude_repos,
            "runs": args.runs,
        },
        "runs": [],
    }

    with tempfile.TemporaryDirectory(prefix="agendum-live-sync-bench-") as tmp_dir:
        workspace_root = Path(tmp_dir) / "workspace"
        paths = runtime_paths(workspace_root)
        paths.workspace_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        write_config(paths.config_path, config)
        gh.seed_gh_config_dir(paths.gh_config_dir)
        if not gh.recover_gh_auth(paths.gh_config_dir):
            raise SystemExit(
                "Unable to prepare isolated gh auth. Run `gh auth status` and try again."
            )

        metrics_handler = MetricsLogHandler()
        sync_logger = logging.getLogger("agendum.syncer")
        sync_logger.addHandler(metrics_handler)
        try:
            for run_index in range(1, args.runs + 1):
                init_db(paths.db_path)
                metrics_handler.messages.clear()
                cold = await run_phase(
                    db_path=paths.db_path,
                    config=config,
                    metrics_handler=metrics_handler,
                )
                metrics_handler.messages.clear()
                warm = await run_phase(
                    db_path=paths.db_path,
                    config=config,
                    metrics_handler=metrics_handler,
                )
                report["runs"].append({
                    "run_index": run_index,
                    "cold": cold,
                    "warm": warm,
                })
                paths.db_path.unlink(missing_ok=True)
        finally:
            sync_logger.removeHandler(metrics_handler)

    return report


def write_report(report: dict[str, Any], output_path: Path | None) -> None:
    payload = json.dumps(report, indent=2, sort_keys=True)
    if output_path is None:
        print(payload)
        return
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(payload + "\n")


def main() -> None:
    args = parse_args()
    report = asyncio.run(run_benchmark(args))
    write_report(report, args.output)


if __name__ == "__main__":
    main()

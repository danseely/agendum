#!/usr/bin/env python3
"""Compare two live sync benchmark reports."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

NUMERIC_FIELDS = (
    "wall_time_s",
    "changes",
    "active_task_count",
    "total_gh_calls",
    "payload_bytes",
)
PHASES = ("cold", "warm")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("before", type=Path, help="Baseline benchmark JSON")
    parser.add_argument("after", type=Path, help="Candidate benchmark JSON")
    return parser.parse_args()


def load_report(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text())


def summarize_phase(report: dict[str, Any], phase: str) -> dict[str, float]:
    runs = report.get("runs", [])
    if not runs:
        raise ValueError(f"benchmark report has no runs: {report!r}")

    summary: dict[str, float] = {}
    for field in NUMERIC_FIELDS:
        values = [float(run[phase][field]) for run in runs]
        summary[field] = sum(values) / len(values)
    return summary


def aggregate_counter(report: dict[str, Any], phase: str, field: str) -> Counter[str]:
    counter: Counter[str] = Counter()
    for run in report.get("runs", []):
        counter.update(run[phase].get(field, {}))
    return counter


def format_delta(before: float, after: float, *, suffix: str = "") -> str:
    delta = after - before
    if before == 0:
        percent = "n/a"
    else:
        percent = f"{(delta / before) * 100:+.1f}%"
    sign = "+" if delta >= 0 else ""
    return (
        f"before={before:.2f}{suffix} after={after:.2f}{suffix} "
        f"delta={sign}{delta:.2f}{suffix} ({percent})"
    )


def print_counter_delta(
    *,
    label: str,
    before: Counter[str],
    after: Counter[str],
) -> None:
    keys = sorted(set(before) | set(after))
    if not keys:
        return
    print(label)
    for key in keys:
        before_value = before.get(key, 0)
        after_value = after.get(key, 0)
        delta = after_value - before_value
        sign = "+" if delta >= 0 else ""
        print(f"  {key}: before={before_value} after={after_value} delta={sign}{delta}")


def main() -> None:
    args = parse_args()
    before = load_report(args.before)
    after = load_report(args.after)

    print(
        f"Comparing {args.before} to {args.after} "
        f"across {len(before.get('runs', []))} baseline run(s) "
        f"and {len(after.get('runs', []))} candidate run(s)"
    )

    for phase in PHASES:
        before_summary = summarize_phase(before, phase)
        after_summary = summarize_phase(after, phase)
        print(f"\n{phase.title()}:")
        print(
            f"  wall time: {format_delta(before_summary['wall_time_s'], after_summary['wall_time_s'], suffix='s')}"
        )
        print(
            f"  gh calls: {format_delta(before_summary['total_gh_calls'], after_summary['total_gh_calls'])}"
        )
        print(
            f"  payload bytes: {format_delta(before_summary['payload_bytes'], after_summary['payload_bytes'])}"
        )
        print(
            f"  changes: {format_delta(before_summary['changes'], after_summary['changes'])}"
        )
        print(
            f"  active tasks: {format_delta(before_summary['active_task_count'], after_summary['active_task_count'])}"
        )

        print_counter_delta(
            label="  call classification totals:",
            before=aggregate_counter(before, phase, "call_classification"),
            after=aggregate_counter(after, phase, "call_classification"),
        )
        print_counter_delta(
            label="  lane pagination totals:",
            before=aggregate_counter(before, phase, "lane_pagination_counts"),
            after=aggregate_counter(after, phase, "lane_pagination_counts"),
        )


if __name__ == "__main__":
    main()

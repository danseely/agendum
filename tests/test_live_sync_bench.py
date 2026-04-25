from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_script_module(name: str, relative_path: str):
    script_path = ROOT / relative_path
    spec = importlib.util.spec_from_file_location(name, script_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


live_sync_bench = load_script_module("live_sync_bench_script", "scripts/live_sync_bench.py")
compare_live_sync_bench = load_script_module(
    "compare_live_sync_bench_script",
    "scripts/compare_live_sync_bench.py",
)


def test_classify_gh_call_recognizes_expected_hot_path_commands() -> None:
    assert live_sync_bench.classify_gh_call(("api", "user", "--jq", ".login")) == "user_lookup"
    assert live_sync_bench.classify_gh_call(
        (
            "api",
            "search/issues",
            "--method",
            "GET",
            "-f",
            "q=is:open is:pr author:dan org:example-org",
            "-F",
            "per_page=100",
            "-F",
            "page=1",
        )
    ) == "search_open_authored_prs"
    assert live_sync_bench.classify_gh_call(
        (
            "api",
            "search/issues",
            "--method",
            "GET",
            "-f",
            "q=is:open is:issue assignee:dan repo:example-org/example-repo",
            "-F",
            "per_page=100",
            "-F",
            "page=1",
        )
    ) == "search_open_assigned_issues"
    assert live_sync_bench.classify_gh_call(
        (
            "api",
            "search/issues",
            "--method",
            "GET",
            "-f",
            "q=is:open is:pr review-requested:dan org:example-org",
            "-F",
            "per_page=100",
            "-F",
            "page=1",
        )
    ) == "search_open_review_requested_prs"
    assert live_sync_bench.classify_gh_call(
        (
            "search",
            "prs",
            "--author",
            "dan",
            "--owner",
            "example-org",
            "--state",
            "open",
        )
    ) == "search_open_authored_prs"
    assert live_sync_bench.classify_gh_call(
        (
            "search",
            "issues",
            "--assignee",
            "dan",
            "--owner",
            "example-org",
            "--state",
            "open",
        )
    ) == "search_open_assigned_issues"
    assert live_sync_bench.classify_gh_call(
        (
            "search",
            "prs",
            "--review-requested",
            "dan",
            "--owner",
            "example-org",
            "--state",
            "open",
        )
    ) == "search_open_review_requested_prs"


def test_classify_gh_call_distinguishes_repo_and_review_graphql_queries() -> None:
    assert live_sync_bench.classify_gh_call(
        ("api", "graphql", "-f", "query=query { repository(owner: $owner, name: $name) { authoredPRs: pullRequests(first: 50) { nodes { number } } } }")
    ) == "repo_graphql"
    assert live_sync_bench.classify_gh_call(
        ("api", "graphql", "-f", "query=query { repository(owner: $owner, name: $name) { pullRequest(number: $number) { number } } }")
    ) == "review_detail_graphql"


def test_classify_gh_call_recognizes_hydration_queries() -> None:
    assert live_sync_bench.classify_gh_call(
        ("api", "graphql", "-f", "query=query HydrateOpenAuthoredPRs { nodes(ids: [\"PR_1\"]) { __typename } }")
    ) == "hydrate_open_authored_prs"
    assert live_sync_bench.classify_gh_call(
        ("api", "graphql", "-f", "query=query HydrateOpenReviewPRs { nodes(ids: [\"PR_1\"]) { __typename } }")
    ) == "hydrate_open_review_prs"
    assert live_sync_bench.classify_gh_call(
        ("api", "graphql", "-f", "query=query HydrateOpenIssues { nodes(ids: [\"I_1\"]) { __typename } }")
    ) == "hydrate_open_issues"


def test_classify_gh_call_recognizes_verification_queries() -> None:
    assert live_sync_bench.classify_gh_call(
        ("api", "graphql", "-f", "query=query VerifyMissingAuthoredPRs { nodes(ids: [\"PR_1\"]) { __typename } }")
    ) == "verify_missing_authored_prs"
    assert live_sync_bench.classify_gh_call(
        ("api", "graphql", "-f", "query=query VerifyMissingReviewPRByUrl { repository(owner: \"org\", name: \"repo\") { pullRequest(number: 1) { id } } }")
    ) == "verify_missing_review_prs"
    assert live_sync_bench.classify_gh_call(
        ("api", "graphql", "-f", "query=query VerifyMissingIssueByUrl { repository(owner: \"org\", name: \"repo\") { issue(number: 1) { id } } }")
    ) == "verify_missing_issues"


def test_extract_batch_sizes_reads_graphql_nodes_payload() -> None:
    payload = '{"data":{"nodes":[{"id":"PR_1"},{"id":"PR_2"},{"id":"PR_3"}]}}'

    assert live_sync_bench.extract_batch_sizes("hydrate_open_authored_prs", payload) == [3]


def test_summarize_phase_averages_numeric_fields() -> None:
    report = {
        "runs": [
            {
                "cold": {
                    "wall_time_s": 10.0,
                    "changes": 2,
                    "active_task_count": 7,
                    "total_gh_calls": 11,
                    "payload_bytes": 1000,
                },
                "warm": {
                    "wall_time_s": 5.0,
                    "changes": 0,
                    "active_task_count": 7,
                    "total_gh_calls": 8,
                    "payload_bytes": 700,
                },
            },
            {
                "cold": {
                    "wall_time_s": 14.0,
                    "changes": 4,
                    "active_task_count": 9,
                    "total_gh_calls": 13,
                    "payload_bytes": 1500,
                },
                "warm": {
                    "wall_time_s": 7.0,
                    "changes": 1,
                    "active_task_count": 9,
                    "total_gh_calls": 9,
                    "payload_bytes": 900,
                },
            },
        ]
    }

    cold = compare_live_sync_bench.summarize_phase(report, "cold")
    warm = compare_live_sync_bench.summarize_phase(report, "warm")

    assert cold == {
        "wall_time_s": 12.0,
        "changes": 3.0,
        "active_task_count": 8.0,
        "total_gh_calls": 12.0,
        "payload_bytes": 1250.0,
    }
    assert warm == {
        "wall_time_s": 6.0,
        "changes": 0.5,
        "active_task_count": 8.0,
        "total_gh_calls": 8.5,
        "payload_bytes": 800.0,
    }

"""Disposable demo workspace helpers for README screenshots."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory

from agendum.app import AgendumApp
from agendum.config import AgendumConfig, RuntimePaths, load_config, runtime_paths
from agendum.db import add_task, init_db, update_task


DEMO_CONFIG_TOML = """\
[github]
orgs = []
repos = []
exclude_repos = []

[sync]
interval = 9999

[display]
seen_delay = 3
"""


@dataclass(frozen=True)
class DemoWorkspace:
    paths: RuntimePaths
    config: AgendumConfig


def _write_demo_config(config_path: Path) -> AgendumConfig:
    config_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    config_path.write_text(DEMO_CONFIG_TOML)
    config_path.chmod(0o600)
    return load_config(config_path)


def prepare_demo_workspace(workspace_root: Path) -> DemoWorkspace:
    paths = runtime_paths(workspace_root)
    config = _write_demo_config(paths.config_path)

    init_db(paths.db_path)
    seed_demo_data(paths.db_path)
    return DemoWorkspace(paths=paths, config=config)


def seed_demo_data(db_path: Path) -> None:
    demo_tasks = [
        {
            "title": "hero shot",
            "source": "pr_authored",
            "status": "draft",
            "project": "agendum",
            "gh_repo": "example-org/agendum",
            "gh_url": "https://github.com/example-org/agendum/pull/128",
            "gh_number": 128,
            "tags": json.dumps(["docs", "demo", "readme"]),
            "seen": False,
        },
        {
            "title": "spacing",
            "source": "pr_authored",
            "status": "awaiting review",
            "project": "ui",
            "gh_repo": "example-org/ui",
            "gh_url": "https://github.com/example-org/agendum-ui/pull/87",
            "gh_number": 87,
            "tags": json.dumps(["ui", "layout"]),
            "seen": True,
        },
        {
            "title": "docs: add a screenshot workflow note to the README and explain when to re-capture the board after major changes to the release flow or table layout",
            "source": "pr_authored",
            "status": "review received",
            "project": "agendum",
            "gh_repo": "example-org/agendum",
            "gh_url": "https://github.com/example-org/agendum/pull/121",
            "gh_number": 121,
            "tags": json.dumps(["docs"]),
            "seen": False,
        },
        {
            "title": "flicker",
            "source": "pr_authored",
            "status": "approved",
            "project": "perf",
            "gh_repo": "example-org/perf",
            "gh_url": "https://github.com/example-org/agendum/pull/119",
            "gh_number": 119,
            "tags": json.dumps(["perf", "ui"]),
            "seen": True,
        },
        {
            "title": "refactor: make modal actions easier to scan in narrow windows while preserving enough contrast and spacing for dense screenshot crops on smaller laptop terminals",
            "source": "pr_authored",
            "status": "changes requested",
            "project": "agendum-ui-shell",
            "gh_repo": "example-org/agendum-ui-shell",
            "gh_url": "https://github.com/example-org/agendum-ui/pull/93",
            "gh_number": 93,
            "tags": json.dumps(["ui", "follow-up"]),
            "seen": False,
        },
        {
            "title": "queued notifications",
            "source": "pr_authored",
            "status": "open",
            "project": "notify",
            "gh_repo": "example-org/notify",
            "gh_url": "https://github.com/example-org/notify-center/pull/56",
            "gh_number": 56,
            "tags": json.dumps(["notifications"]),
            "seen": True,
        },
        {
            "title": "review the crop",
            "source": "pr_review",
            "status": "review requested",
            "project": "demo-app",
            "gh_repo": "example-org/demo-app",
            "gh_url": "https://github.com/example-org/demo-app/pull/44",
            "gh_number": 44,
            "gh_author": "ps",
            "gh_author_name": "Priya",
            "tags": json.dumps(["demo", "review"]),
            "seen": False,
        },
        {
            "title": "sync copy",
            "source": "pr_review",
            "status": "re-review requested",
            "project": "sync-lab",
            "gh_repo": "example-org/sync-lab",
            "gh_url": "https://github.com/example-org/demo-app/pull/31",
            "gh_number": 31,
            "gh_author": "alex",
            "gh_author_name": "Alex Nguyen",
            "tags": json.dumps(["layout"]),
            "seen": True,
        },
        {
            "title": "review: sanity-check the release notes copy before the next cut and call out any sections that will read awkwardly in the generated GitHub release summary",
            "source": "pr_review",
            "status": "reviewed",
            "project": "release-bot-enterprise",
            "gh_repo": "example-org/release-bot-enterprise",
            "gh_url": "https://github.com/example-org/release-bot/pull/12",
            "gh_number": 12,
            "gh_author": "morgan",
            "gh_author_name": "Morgan Stone",
            "tags": json.dumps(["release"]),
            "seen": True,
        },
        {
            "title": "homebrew notes",
            "source": "pr_review",
            "status": "review requested",
            "project": "tap",
            "gh_repo": "example-org/tap",
            "gh_url": "https://github.com/example-org/tap-tools/pull/18",
            "gh_number": 18,
            "gh_author": "casey",
            "gh_author_name": "Casey Brooks-Winters",
            "tags": json.dumps(["homebrew", "docs"]),
            "seen": False,
        },
        {
            "title": "review: confirm screenshot crop dimensions for the hero panel and check whether the rightmost columns still feel balanced after the latest width-policy adjustments",
            "source": "pr_review",
            "status": "re-review requested",
            "project": "design-sync-lab",
            "gh_repo": "example-org/design-sync-lab",
            "gh_url": "https://github.com/example-org/design-sync/pull/72",
            "gh_number": 72,
            "gh_author": "riley",
            "gh_author_name": "Riley Chen-Santiago",
            "tags": json.dumps(["design"]),
            "seen": False,
        },
        {
            "title": "docs command",
            "source": "issue",
            "status": "open",
            "project": "agendum",
            "gh_repo": "example-org/agendum",
            "gh_url": "https://github.com/example-org/agendum/issues/58",
            "gh_number": 58,
            "gh_author": "sam",
            "gh_author_name": "Sam Lee",
            "tags": json.dumps(["docs", "good first issue"]),
            "seen": False,
        },
        {
            "title": "status bar jump",
            "source": "issue",
            "status": "in progress",
            "project": "core",
            "gh_repo": "example-org/core",
            "gh_url": "https://github.com/example-org/agendum/issues/73",
            "gh_number": 73,
            "gh_author": "taylor",
            "gh_author_name": "Taylor",
            "tags": json.dumps(["bug"]),
            "seen": True,
        },
        {
            "title": "feature: surface release health in the bottom status bar with enough detail to spot stale rolling release PRs without needing to open GitHub in a browser",
            "source": "issue",
            "status": "open",
            "project": "status-hub-prod",
            "gh_repo": "example-org/status-hub-prod",
            "gh_url": "https://github.com/example-org/status-hub/issues/212",
            "gh_number": 212,
            "gh_author": "jamie",
            "gh_author_name": "Jamie Rivera",
            "tags": json.dumps(["feature", "status"]),
            "seen": False,
        },
        {
            "title": "retain selection",
            "source": "issue",
            "status": "open",
            "project": "ui",
            "gh_repo": "example-org/ui",
            "gh_url": "https://github.com/example-org/agendum/issues/91",
            "gh_number": 91,
            "gh_author": "noah",
            "gh_author_name": "Noah",
            "tags": json.dumps(["bug", "ui"]),
            "seen": False,
        },
        {
            "title": "docs: note the screenshot seed command in contributor setup and explain which fake rows are intentionally unrealistic so future screenshots don’t accidentally imply unsupported states",
            "source": "issue",
            "status": "in progress",
            "project": "docs-site-next",
            "gh_repo": "example-org/docs-site-next",
            "gh_url": "https://github.com/example-org/docs-site/issues/34",
            "gh_number": 34,
            "gh_author": "micah",
            "gh_author_name": "Micah Ross-Carver",
            "tags": json.dumps(["docs"]),
            "seen": True,
        },
        {
            "title": "hero shot",
            "source": "manual",
            "status": "in progress",
            "project": "screenshot-run",
            "tags": json.dumps(["manual", "screenshots"]),
            "seen": False,
        },
        {
            "title": "trim rows",
            "source": "manual",
            "status": "backlog",
            "project": "shot",
            "tags": json.dumps(["manual", "tuning"]),
            "seen": True,
        },
        {
            "title": "capture a second crop with the review section centered and enough extra rows visible to show how titles wrap once the table gets visually busy",
            "source": "manual",
            "status": "in progress",
            "project": "screenshot-run-wide",
            "tags": json.dumps(["manual", "framing"]),
            "seen": False,
        },
        {
            "title": "cleanup",
            "source": "manual",
            "status": "backlog",
            "project": "cleanup",
            "tags": json.dumps(["manual", "cleanup"]),
            "seen": True,
        },
    ]

    for task in demo_tasks:
        seen = task.pop("seen")
        task_id = add_task(db_path, **task)
        if not seen:
            update_task(db_path, task_id, seen=0)


def _launch_demo(workspace_root: Path) -> None:
    workspace = prepare_demo_workspace(workspace_root)
    print(f"Demo workspace: {workspace.paths.config_dir}")
    print("Seeded demo data in a disposable temp database.")
    print("Quit the app with `q` to clean up the workspace.")
    app = AgendumApp(runtime=workspace.paths, config=workspace.config)
    app.run()


def run_demo_screenshots(workspace_root: Path | None = None) -> None:
    if workspace_root is not None:
        workspace_root.mkdir(parents=True, exist_ok=True)
        _launch_demo(workspace_root)
        return

    with TemporaryDirectory(prefix="agendum-demo-") as temp_dir:
        _launch_demo(Path(temp_dir))

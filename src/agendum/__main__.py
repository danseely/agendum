"""Agendum entry point."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from agendum import __version__
from agendum.config import CONFIG_DIR, CONFIG_PATH, DB_PATH, DEFAULT_CONFIG, ensure_config, runtime_paths
from agendum.demo import run_demo_screenshots
from agendum.gh import auth_status, recover_gh_auth


def check_gh_cli(gh_config_dir: Path | None = None) -> bool:
    """Verify gh CLI is installed and authenticated for a config dir."""
    return auth_status(gh_config_dir)


def first_run_setup(
    config_path: Path | None = None,
    gh_config_dir: Path | None = None,
) -> None:
    """Interactive first-run setup."""
    config_path = config_path or CONFIG_PATH
    gh_config_dir = gh_config_dir or runtime_paths(config_path.parent).gh_config_dir
    print("Welcome to agendum!")
    print()

    if not recover_gh_auth(gh_config_dir, interactive=True):
        print("Error: gh CLI is not installed or not authenticated.")
        print("Install: https://cli.github.com/")
        print(f"Then run: GH_CONFIG_DIR={gh_config_dir} gh auth login")
        sys.exit(1)

    if not config_path.exists():
        print(f"Creating config at {config_path}")
        config_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)

        org = input("GitHub org to scan (e.g. 'example-org'): ").strip()
        if org:
            config_text = DEFAULT_CONFIG.replace("orgs = []", f"orgs = [{json.dumps(org)}]")
        else:
            config_text = DEFAULT_CONFIG
        config_path.write_text(config_text)
        config_path.chmod(0o600)
        print(f"Config written to {config_path}")
        print()

    print("Starting agendum...")
    print()


def self_check(db_path: Path | None = None) -> None:
    """Run a non-interactive local validation for packaging and diagnostics."""
    from agendum.db import init_db
    from agendum.task_api import create_manual_task, list_tasks

    db_path = db_path or DB_PATH
    db_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    init_db(db_path)

    task = create_manual_task(
        db_path,
        title="agendum self-check",
        project="packaging",
        tags=["self-check"],
    )
    tasks = list_tasks(db_path, limit=5)

    if task["title"] != "agendum self-check":
        raise RuntimeError("self-check failed to create the expected task")
    if not tasks or tasks[0]["title"] != "agendum self-check":
        raise RuntimeError("self-check failed to read back the expected task")

    print("agendum self-check ok")


def main() -> None:
    parser = argparse.ArgumentParser(prog="agendum")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    subparsers = parser.add_subparsers(dest="command")
    subparsers.add_parser("self-check", help="run a local non-interactive installation check")
    subparsers.add_parser(
        "demo-screenshots",
        help="launch a disposable seeded workspace for README screenshots",
    )
    subparsers.add_parser(
        "reauth",
        help="refresh the base workspace gh auth from local/default state or interactive login",
    )
    args = parser.parse_args()

    if args.command == "self-check":
        self_check()
        return

    if args.command == "demo-screenshots":
        run_demo_screenshots()
        return

    paths = runtime_paths(CONFIG_DIR)

    if args.command == "reauth":
        if not recover_gh_auth(paths.gh_config_dir, interactive=True, force_refresh=True):
            print("Error: gh CLI is not installed or not authenticated.")
            print("Install: https://cli.github.com/")
            print(f"Then run: GH_CONFIG_DIR={paths.gh_config_dir} gh auth login")
            sys.exit(1)
        print(f"Workspace gh auth ready at {paths.gh_config_dir}")
        return

    if not paths.config_path.exists() or not paths.db_path.exists():
        first_run_setup(paths.config_path, paths.gh_config_dir)

    recover_gh_auth(paths.gh_config_dir)
    config = ensure_config(paths.config_path)

    from agendum.app import AgendumApp
    from agendum.db import init_db

    init_db(paths.db_path)
    app = AgendumApp(runtime=paths, config=config)
    app.run()


if __name__ == "__main__":
    main()

"""Agendum entry point."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys

from agendum import __version__
from agendum.config import CONFIG_DIR, CONFIG_PATH, DB_PATH, DEFAULT_CONFIG, ensure_config


def check_gh_cli() -> bool:
    """Verify gh CLI is installed and authenticated."""
    try:
        result = subprocess.run(
            ["gh", "auth", "status"],
            capture_output=True,
            text=True,
        )
        return result.returncode == 0
    except FileNotFoundError:
        return False


def first_run_setup() -> None:
    """Interactive first-run setup."""
    print("Welcome to agendum!")
    print()

    if not check_gh_cli():
        print("Error: gh CLI is not installed or not authenticated.")
        print("Install: https://cli.github.com/")
        print("Then run: gh auth login")
        sys.exit(1)

    if not CONFIG_PATH.exists():
        print(f"Creating config at {CONFIG_PATH}")
        CONFIG_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)

        org = input("GitHub org to scan (e.g. 'example-org'): ").strip()
        if org:
            config_text = DEFAULT_CONFIG.replace("orgs = []", f"orgs = [{json.dumps(org)}]")
        else:
            config_text = DEFAULT_CONFIG
        CONFIG_PATH.write_text(config_text)
        CONFIG_PATH.chmod(0o600)
        print(f"Config written to {CONFIG_PATH}")
        print()

    print("Starting agendum...")
    print()


def self_check() -> None:
    """Run a non-interactive local validation for packaging and diagnostics."""
    from agendum.db import init_db
    from agendum.task_api import create_manual_task, list_tasks

    CONFIG_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    init_db(DB_PATH)

    task = create_manual_task(
        DB_PATH,
        title="agendum self-check",
        project="packaging",
        tags=["self-check"],
    )
    tasks = list_tasks(DB_PATH, limit=5)

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
    args = parser.parse_args()

    if args.command == "self-check":
        self_check()
        return

    if not CONFIG_PATH.exists() or not DB_PATH.exists():
        first_run_setup()

    config = ensure_config()

    from agendum.app import AgendumApp
    from agendum.db import init_db

    init_db(DB_PATH)
    app = AgendumApp(db_path=DB_PATH, config=config)
    app.run()


if __name__ == "__main__":
    main()

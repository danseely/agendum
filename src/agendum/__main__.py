"""Agendum entry point."""

from __future__ import annotations

import json
import subprocess
import sys

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


def main() -> None:
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

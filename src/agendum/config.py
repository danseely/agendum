import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

CONFIG_DIR = Path.home() / ".agendum"
CONFIG_PATH = CONFIG_DIR / "config.toml"
DB_PATH = CONFIG_DIR / "agendum.db"

DEFAULT_CONFIG = """\
[github]
# GitHub org(s) to scan
orgs = []

# Explicit repo whitelist ("owner/repo" format).
# If set, only these repos are synced — org-wide discovery is skipped.
repos = []

# Repos to exclude (optional, "owner/repo" format)
exclude_repos = []

[sync]
# Poll interval in seconds
interval = 60

[display]
# Seconds after focus before marking items seen
seen_delay = 3
"""


@dataclass
class AgendumConfig:
    orgs: list[str] = field(default_factory=list)
    repos: list[str] = field(default_factory=list)
    exclude_repos: list[str] = field(default_factory=list)
    sync_interval: int = 60
    seen_delay: int = 3


@dataclass(frozen=True)
class RuntimePaths:
    config_path: Path
    db_path: Path

    @property
    def config_dir(self) -> Path:
        return self.config_path.parent


def default_runtime_paths() -> RuntimePaths:
    return RuntimePaths(config_path=CONFIG_PATH, db_path=DB_PATH)


def runtime_paths(config_dir: Path) -> RuntimePaths:
    return RuntimePaths(
        config_path=config_dir / "config.toml",
        db_path=config_dir / "agendum.db",
    )


def load_config(path: Path | None = None) -> AgendumConfig:
    path = path or CONFIG_PATH
    if not path.exists():
        return AgendumConfig()

    with open(path, "rb") as f:
        raw = tomllib.load(f)

    gh = raw.get("github", {})
    sync = raw.get("sync", {})
    display = raw.get("display", {})

    return AgendumConfig(
        orgs=gh.get("orgs", []),
        repos=gh.get("repos", []),
        exclude_repos=gh.get("exclude_repos", []),
        sync_interval=sync.get("interval", 60),
        seen_delay=display.get("seen_delay", 3),
    )


def ensure_config(path: Path | None = None) -> AgendumConfig:
    """Create config dir/file if missing, then load."""
    path = path or CONFIG_PATH
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if not path.exists():
        path.write_text(DEFAULT_CONFIG)
        os.chmod(path, 0o600)
    return load_config(path)

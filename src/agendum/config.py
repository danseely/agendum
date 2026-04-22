import json
import os
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

CONFIG_DIR = Path.home() / ".agendum"
CONFIG_PATH = CONFIG_DIR / "config.toml"
DB_PATH = CONFIG_DIR / "agendum.db"
WORKSPACES_DIRNAME = "workspaces"
_GITHUB_OWNER_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?$")

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
interval = 120

[display]
# Seconds after focus before marking items seen
seen_delay = 3
"""


@dataclass
class AgendumConfig:
    orgs: list[str] = field(default_factory=list)
    repos: list[str] = field(default_factory=list)
    exclude_repos: list[str] = field(default_factory=list)
    sync_interval: int = 120
    seen_delay: int = 3


@dataclass(frozen=True)
class RuntimePaths:
    workspace_root: Path
    config_path: Path
    db_path: Path
    gh_config_dir: Path

    @property
    def config_dir(self) -> Path:
        return self.workspace_root


def default_runtime_paths() -> RuntimePaths:
    return runtime_paths(CONFIG_DIR)


def runtime_paths(workspace_root: Path) -> RuntimePaths:
    return RuntimePaths(
        workspace_root=workspace_root,
        config_path=workspace_root / "config.toml",
        db_path=workspace_root / "agendum.db",
        gh_config_dir=workspace_root / "gh",
    )


def runtime_base_dir(paths: RuntimePaths) -> Path:
    workspace_root = paths.workspace_root
    if workspace_root.parent.name == WORKSPACES_DIRNAME:
        return workspace_root.parent.parent
    return workspace_root


def workspace_runtime_paths(
    namespace: str | None,
    base_root: Path | None = None,
) -> RuntimePaths:
    base_root = base_root or CONFIG_DIR
    normalized = normalize_namespace(namespace)
    if normalized is None:
        return runtime_paths(base_root)
    return runtime_paths(base_root / WORKSPACES_DIRNAME / _namespace_directory_name(normalized))


def namespace_runtime_paths(namespace: str, base_root: Path | None = None) -> RuntimePaths:
    normalized = normalize_namespace(namespace)
    if normalized is None:
        raise ValueError("enter at least one letter or number")
    return workspace_runtime_paths(normalized, base_root)


def normalize_namespace(namespace: str | None) -> str | None:
    if namespace is None:
        return None

    normalized = namespace.strip()
    if not normalized:
        return None
    if "/" in normalized:
        raise ValueError("enter a GitHub owner name, not owner/repo")
    if not re.search(r"[A-Za-z0-9]", normalized):
        raise ValueError("enter at least one letter or number")
    if "--" in normalized or not _GITHUB_OWNER_RE.fullmatch(normalized):
        raise ValueError("enter a valid GitHub owner name")
    return normalized


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
        sync_interval=sync.get("interval", 120),
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


def ensure_workspace_config(
    paths: RuntimePaths,
    *,
    namespace: str | None = None,
    seed: AgendumConfig | None = None,
) -> AgendumConfig:
    paths.workspace_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    if paths.config_path.exists():
        config = load_config(paths.config_path)
        if config.orgs or config.repos or namespace is None:
            return config
        seed = config

    config = _default_workspace_config(namespace=namespace, seed=seed)
    write_config(paths.config_path, config)
    return config


def write_config(path: Path, config: AgendumConfig) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.write_text(render_config(config))
    os.chmod(path, 0o600)


def render_config(config: AgendumConfig) -> str:
    return "\n".join(
        [
            "[github]",
            "# GitHub org(s) to scan",
            f"orgs = {json.dumps(config.orgs)}",
            "",
            '# Explicit repo whitelist ("owner/repo" format).',
            "# If set, only these repos are synced — org-wide discovery is skipped.",
            f"repos = {json.dumps(config.repos)}",
            "",
            '# Repos to exclude (optional, "owner/repo" format)',
            f"exclude_repos = {json.dumps(config.exclude_repos)}",
            "",
            "[sync]",
            "# Poll interval in seconds",
            f"interval = {config.sync_interval}",
            "",
            "[display]",
            "# Seconds after focus before marking items seen",
            f"seen_delay = {config.seen_delay}",
            "",
        ]
    )


def _default_workspace_config(
    *,
    namespace: str | None,
    seed: AgendumConfig | None,
) -> AgendumConfig:
    seed = seed or AgendumConfig()
    if namespace is None:
        return seed
    return AgendumConfig(
        orgs=[namespace],
        repos=[],
        exclude_repos=[],
        sync_interval=seed.sync_interval,
        seen_delay=seed.seen_delay,
    )


def _namespace_directory_name(namespace: str) -> str:
    normalized = normalize_namespace(namespace)
    if normalized is None:
        raise ValueError("enter at least one letter or number")
    return normalized.lower()

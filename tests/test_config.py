from pathlib import Path

import pytest

from agendum.config import (
    DEFAULT_CONFIG,
    AgendumConfig,
    ensure_workspace_config,
    load_config,
    namespace_runtime_paths,
    runtime_base_dir,
    runtime_paths,
    workspace_runtime_paths,
)


def test_default_config_when_no_file(tmp_path: Path) -> None:
    config = load_config(tmp_path / "nonexistent.toml")
    assert config.sync_interval == 60
    assert config.seen_delay == 3
    assert config.orgs == []
    assert config.repos == []
    assert config.exclude_repos == []


def test_load_config_from_file(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text("""
[github]
orgs = ["example-org", "another-org"]
repos = ["example-org/pinned-repo"]
exclude_repos = ["example-org/deprecated-repo"]

[sync]
interval = 30

[display]
seen_delay = 5
""")
    config = load_config(config_path)
    assert config.orgs == ["example-org", "another-org"]
    assert config.repos == ["example-org/pinned-repo"]
    assert config.exclude_repos == ["example-org/deprecated-repo"]
    assert config.sync_interval == 30
    assert config.seen_delay == 5


def test_partial_config_uses_defaults(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text("""
[github]
orgs = ["example-org"]
""")
    config = load_config(config_path)
    assert config.orgs == ["example-org"]
    assert config.sync_interval == 60
    assert config.seen_delay == 3


def test_default_config_string() -> None:
    assert "[github]" in DEFAULT_CONFIG
    assert "orgs" in DEFAULT_CONFIG


def test_runtime_paths_include_workspace_and_gh_dir(tmp_path: Path) -> None:
    paths = runtime_paths(tmp_path / ".agendum")

    assert paths.workspace_root == tmp_path / ".agendum"
    assert paths.config_path == tmp_path / ".agendum" / "config.toml"
    assert paths.db_path == tmp_path / ".agendum" / "agendum.db"
    assert paths.gh_config_dir == tmp_path / ".agendum" / "gh"


def test_namespace_runtime_paths_are_nested_under_workspaces(tmp_path: Path) -> None:
    paths = namespace_runtime_paths("Example-Org", tmp_path / ".agendum")

    assert paths.workspace_root == tmp_path / ".agendum" / "workspaces" / "example-org"
    assert runtime_base_dir(paths) == tmp_path / ".agendum"


def test_workspace_runtime_paths_with_blank_namespace_returns_base_workspace(tmp_path: Path) -> None:
    paths = workspace_runtime_paths("", tmp_path / ".agendum")

    assert paths == runtime_paths(tmp_path / ".agendum")


def test_namespace_runtime_paths_rejects_normalized_empty_namespace(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="at least one letter or number"):
        namespace_runtime_paths("!!!", tmp_path / ".agendum")


def test_namespace_runtime_paths_reject_owner_repo_input(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="owner name, not owner/repo"):
        namespace_runtime_paths("owner/repo", tmp_path / ".agendum")


def test_ensure_workspace_config_seeds_namespace_and_timing(tmp_path: Path) -> None:
    paths = namespace_runtime_paths("example-org", tmp_path / ".agendum")
    config = ensure_workspace_config(
        paths,
        namespace="example-org",
        seed=AgendumConfig(sync_interval=120, seen_delay=9),
    )

    assert config.orgs == ["example-org"]
    assert config.repos == []
    assert config.exclude_repos == []
    assert config.sync_interval == 120
    assert config.seen_delay == 9
    assert load_config(paths.config_path).orgs == ["example-org"]

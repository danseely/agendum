from pathlib import Path
from agendum.config import load_config, AgendumConfig, DEFAULT_CONFIG


def test_default_config_when_no_file(tmp_path: Path) -> None:
    config = load_config(tmp_path / "nonexistent.toml")
    assert config.sync_interval == 60
    assert config.seen_delay == 3
    assert config.orgs == []
    assert config.exclude_repos == []


def test_load_config_from_file(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text("""
[github]
orgs = ["facebook", "google"]
exclude_repos = ["facebook/deprecated-repo"]

[sync]
interval = 30

[display]
seen_delay = 5
""")
    config = load_config(config_path)
    assert config.orgs == ["facebook", "google"]
    assert config.exclude_repos == ["facebook/deprecated-repo"]
    assert config.sync_interval == 30
    assert config.seen_delay == 5


def test_partial_config_uses_defaults(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text("""
[github]
orgs = ["facebook"]
""")
    config = load_config(config_path)
    assert config.orgs == ["facebook"]
    assert config.sync_interval == 60
    assert config.seen_delay == 3


def test_default_config_string() -> None:
    assert "[github]" in DEFAULT_CONFIG
    assert "orgs" in DEFAULT_CONFIG

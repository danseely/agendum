from pathlib import Path

from agendum import config as config_module
from agendum import demo
from agendum.db import get_active_tasks


def test_prepare_demo_workspace_uses_isolated_paths(tmp_path: Path, monkeypatch) -> None:
    sentinel_config = tmp_path / "real" / "config.toml"
    sentinel_db = tmp_path / "real" / "agendum.db"
    monkeypatch.setattr(config_module, "CONFIG_PATH", sentinel_config)
    monkeypatch.setattr(config_module, "DB_PATH", sentinel_db)

    workspace_root = tmp_path / "demo"
    workspace = demo.prepare_demo_workspace(workspace_root)

    assert workspace.paths.config_path == workspace_root / "config.toml"
    assert workspace.paths.db_path == workspace_root / "agendum.db"
    assert workspace.paths.config_path.exists()
    assert workspace.paths.db_path.exists()
    assert workspace.config.orgs == []
    assert workspace.config.repos == []
    assert workspace.config.exclude_repos == []
    assert workspace.config.sync_interval == 9999
    assert not sentinel_config.exists()
    assert not sentinel_db.exists()


def test_seed_demo_data_creates_rich_dataset_shape(tmp_path: Path) -> None:
    workspace = demo.prepare_demo_workspace(tmp_path / "demo")
    tasks = get_active_tasks(workspace.paths.db_path)

    assert len(tasks) >= 20
    assert {task["source"] for task in tasks} == {
        "pr_authored",
        "pr_review",
        "issue",
        "manual",
    }
    assert any(not task.get("seen", 1) for task in tasks)
    assert any(task.get("gh_url") for task in tasks)
    assert any(not task.get("gh_url") for task in tasks)
    authored = [task for task in tasks if task["source"] == "pr_authored"]
    assert authored
    assert all(not task.get("gh_author") for task in authored)
    assert all(not task.get("gh_author_name") for task in authored)
    title_lengths = [len(task["title"]) for task in tasks]
    assert min(title_lengths) <= 8
    assert max(title_lengths) >= 120
    named_tasks = [task for task in tasks if task.get("gh_author_name")]
    author_lengths = [len(task["gh_author_name"]) for task in named_tasks]
    repo_lengths = [len(task.get("project") or task.get("gh_repo") or "") for task in tasks]
    assert min(author_lengths) <= 6
    assert max(author_lengths) >= 18
    assert min(repo_lengths) <= 4
    assert max(repo_lengths) >= 18


def test_run_demo_screenshots_uses_prepared_workspace(tmp_path: Path, monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_run(self) -> None:
        captured["db_path"] = self.db_path
        captured["config"] = self._config

    monkeypatch.setattr(demo.AgendumApp, "run", fake_run)

    workspace_root = tmp_path / "demo"
    demo.run_demo_screenshots(workspace_root)

    assert captured["db_path"] == workspace_root / "agendum.db"
    config = captured["config"]
    assert config is not None
    assert config.orgs == []
    assert config.repos == []

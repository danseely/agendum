from pathlib import Path

import pytest

from agendum import __version__
from agendum import __main__ as main
from agendum.config import AgendumConfig


def test_first_run_setup_writes_private_escaped_config(
    tmp_path: Path,
    monkeypatch,
) -> None:
    config_dir = tmp_path / ".agendum"
    config_path = config_dir / "config.toml"
    gh_dir = config_dir / "gh"

    monkeypatch.setattr(main, "CONFIG_DIR", config_dir)
    monkeypatch.setattr(main, "CONFIG_PATH", config_path)
    monkeypatch.setattr(main, "recover_gh_auth", lambda gh_path, interactive=False: gh_path == gh_dir and interactive)
    monkeypatch.setattr("builtins.input", lambda _: 'example-"org')

    main.first_run_setup()

    assert config_dir.stat().st_mode & 0o777 == 0o700
    assert config_path.stat().st_mode & 0o777 == 0o600
    assert 'orgs = ["example-\\"org"]' in config_path.read_text()


def test_main_version_flag_prints_version(capsys: pytest.CaptureFixture[str], monkeypatch) -> None:
    monkeypatch.setattr(main.sys, "argv", ["agendum", "--version"])

    with pytest.raises(SystemExit) as exc_info:
        main.main()

    assert exc_info.value.code == 0
    assert capsys.readouterr().out.strip() == f"agendum {__version__}"


def test_self_check_initializes_storage_and_prints_ok(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch,
) -> None:
    config_dir = tmp_path / ".agendum"
    config_path = config_dir / "config.toml"
    db_path = config_dir / "agendum.db"

    monkeypatch.setattr(main, "CONFIG_DIR", config_dir)
    monkeypatch.setattr(main, "CONFIG_PATH", config_path)
    monkeypatch.setattr(main, "DB_PATH", db_path)
    monkeypatch.setattr(main.sys, "argv", ["agendum", "self-check"])

    main.main()

    assert db_path.exists()
    assert capsys.readouterr().out.strip() == "agendum self-check ok"


def test_demo_screenshots_command_dispatches(monkeypatch) -> None:
    called = []

    monkeypatch.setattr(main, "run_demo_screenshots", lambda: called.append(True))
    monkeypatch.setattr(main.sys, "argv", ["agendum", "demo-screenshots"])

    main.main()

    assert called == [True]


def test_main_recovers_default_workspace_gh_auth_before_launch(
    tmp_path: Path,
    monkeypatch,
) -> None:
    config_dir = tmp_path / ".agendum"
    config_path = config_dir / "config.toml"
    db_path = config_dir / "agendum.db"
    gh_dir = config_dir / "gh"
    config_dir.mkdir(parents=True)
    config_path.write_text("")
    db_path.touch()

    recover_calls: list[tuple[Path, bool]] = []
    init_calls: list[Path] = []
    app_calls: dict[str, object] = {}

    monkeypatch.setattr(main, "CONFIG_DIR", config_dir)
    monkeypatch.setattr(main, "CONFIG_PATH", config_path)
    monkeypatch.setattr(main, "DB_PATH", db_path)
    monkeypatch.setattr(main, "ensure_config", lambda path: AgendumConfig(orgs=["example-org"]))
    monkeypatch.setattr(
        main,
        "recover_gh_auth",
        lambda gh_path, interactive=False: recover_calls.append((gh_path, interactive)) or True,
    )
    monkeypatch.setattr(main.sys, "argv", ["agendum"])

    from agendum import app as app_module
    from agendum import db as db_module

    class FakeApp:
        def __init__(self, *, runtime, config):
            app_calls["runtime"] = runtime
            app_calls["config"] = config

        def run(self) -> None:
            app_calls["ran"] = True

    monkeypatch.setattr(app_module, "AgendumApp", FakeApp)
    monkeypatch.setattr(db_module, "init_db", lambda path: init_calls.append(path))

    main.main()

    assert recover_calls == [(gh_dir, False)]
    assert init_calls == [db_path]
    assert app_calls["ran"] is True
    assert app_calls["runtime"].gh_config_dir == gh_dir


def test_main_reauth_refreshes_base_workspace_auth(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch,
) -> None:
    config_dir = tmp_path / ".agendum"
    gh_dir = config_dir / "gh"

    calls: list[tuple[Path, bool]] = []

    monkeypatch.setattr(main, "CONFIG_DIR", config_dir)
    monkeypatch.setattr(
        main,
        "recover_gh_auth",
        lambda gh_path, interactive=False: calls.append((gh_path, interactive)) or True,
    )
    monkeypatch.setattr(main.sys, "argv", ["agendum", "reauth"])

    main.main()

    assert calls == [(gh_dir, True)]
    assert capsys.readouterr().out.strip() == f"Workspace gh auth ready at {gh_dir}"

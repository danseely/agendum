from pathlib import Path

import pytest

from agendum import __main__ as main


def test_first_run_setup_writes_private_escaped_config(
    tmp_path: Path,
    monkeypatch,
) -> None:
    config_dir = tmp_path / ".agendum"
    config_path = config_dir / "config.toml"

    monkeypatch.setattr(main, "CONFIG_DIR", config_dir)
    monkeypatch.setattr(main, "CONFIG_PATH", config_path)
    monkeypatch.setattr(main, "check_gh_cli", lambda: True)
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
    assert capsys.readouterr().out.strip() == "agendum 0.1.0"


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

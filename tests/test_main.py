from pathlib import Path

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

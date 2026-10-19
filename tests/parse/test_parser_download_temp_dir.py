from pathlib import Path

from openviking_cli.utils.config.open_viking_config import OpenVikingConfig


def test_parser_download_temp_dir_is_configurable() -> None:
    config = OpenVikingConfig.from_dict(
        {"parsers": {"download_temp_dir": "/var/tmp/openviking-downloads"}}
    )

    assert config.parser.download_temp_dir == "/var/tmp/openviking-downloads"


def test_parser_download_temp_dir_helper_creates_configured_directory(tmp_path, monkeypatch) -> None:
    from openviking.parse import temp

    configured = tmp_path / "downloads"
    config = OpenVikingConfig.from_dict(
        {"parsers": {"download_temp_dir": str(configured)}}
    )
    monkeypatch.setattr(
        "openviking_cli.utils.config.get_openviking_config",
        lambda: config,
    )

    assert temp.get_parser_download_temp_dir() == str(configured)
    assert Path(configured).is_dir()

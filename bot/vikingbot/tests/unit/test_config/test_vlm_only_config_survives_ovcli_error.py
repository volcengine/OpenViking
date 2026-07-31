"""Bot configuration must remain independent from ovcli.conf.

VLM settings and the static OpenViking upstream are loaded from ov.conf. An
invalid ovcli.conf must not discard VLM settings, and a valid ovcli user key
must not be copied into the Bot process configuration.
"""

import json

from vikingbot.config import loader


def _write_conf(tmp_path, monkeypatch):
    conf = tmp_path / "ov.conf"
    conf.write_text(
        json.dumps(
            {
                "vlm": {
                    "provider": "deepseek",
                    "api_base": "https://api.deepseek.com",
                    "api_key": "sk-deepseek-test-key",
                    "model": "deepseek-chat",
                },
                # api_key auth mode is what drives the ovcli user-key lookup.
                "server": {"auth_mode": "api_key"},
            }
        )
    )
    monkeypatch.setattr(loader, "CONFIG_PATH", conf)
    return conf


def test_vlm_config_preserved_when_ovcli_config_is_invalid(tmp_path, monkeypatch):
    _write_conf(tmp_path, monkeypatch)
    ovcli_conf = tmp_path / "ovcli.conf"
    ovcli_conf.write_text("{invalid json")
    monkeypatch.setenv("OPENVIKING_CLI_CONFIG_FILE", str(ovcli_conf))

    config = loader.load_config()

    # The vlm-derived agent config must survive the ovcli failure.
    assert config.agents.model == "deepseek-chat"
    assert config.agents.provider == "deepseek"
    assert config.agents.api_key == "sk-deepseek-test-key"
    assert config.agents.api_base == "https://api.deepseek.com"
    assert not config.ov_server.api_key


def test_ovcli_user_key_is_not_copied_into_bot_config(tmp_path, monkeypatch):
    _write_conf(tmp_path, monkeypatch)
    ovcli_conf = tmp_path / "ovcli.conf"
    ovcli_conf.write_text(json.dumps({"url": "http://ov.local", "api_key": "user-api-key"}))
    monkeypatch.setenv("OPENVIKING_CLI_CONFIG_FILE", str(ovcli_conf))

    config = loader.load_config()

    assert config.agents.provider == "deepseek"
    assert config.agents.model == "deepseek-chat"
    assert not config.ov_server.api_key

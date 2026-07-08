"""Regression test for vlm-only ov.conf losing its provider on ovcli errors.

Repro: with only a `vlm` section configured (no `bot.agents`) and a `server`
section whose effective auth mode is `api_key`, load_config() used to call
_fill_user_api_key_from_ovcli() -> load_ovcli_config(), which raises ValueError
when no valid ovcli identity exists. load_config()'s broad
`except (json.JSONDecodeError, ValueError)` then swallowed it and fell back to a
default Config(), discarding the vlm-derived model/provider/credentials. The bot
then ran on the default `openai/*` model with no key and failed with
"Missing credentials ... set OPENAI_API_KEY".

An ovcli/user-identity problem must only degrade OpenViking memory/file tools,
never discard the unrelated LLM/vlm provider config.
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


def test_vlm_config_preserved_when_ovcli_lookup_fails(tmp_path, monkeypatch):
    _write_conf(tmp_path, monkeypatch)

    def _raise(*_args, **_kwargs):
        raise ValueError("Invalid CLI config: no ovcli identity configured")

    monkeypatch.setattr(loader, "load_ovcli_config", _raise)

    config = loader.load_config()

    # The vlm-derived agent config must survive the ovcli failure.
    assert config.agents.model == "deepseek-chat"
    assert config.agents.provider == "deepseek"
    assert config.agents.api_key == "sk-deepseek-test-key"
    assert config.agents.api_base == "https://api.deepseek.com"


def test_vlm_config_used_when_ovcli_supplies_user_key(tmp_path, monkeypatch):
    _write_conf(tmp_path, monkeypatch)

    class _Cli:
        api_key = "user-api-key"

    monkeypatch.setattr(loader, "load_ovcli_config", lambda *a, **k: _Cli())

    config = loader.load_config()

    # LLM provider still comes from vlm; ovcli only fills the OpenViking user key.
    assert config.agents.provider == "deepseek"
    assert config.agents.model == "deepseek-chat"
    assert config.ov_server.api_key == "user-api-key"

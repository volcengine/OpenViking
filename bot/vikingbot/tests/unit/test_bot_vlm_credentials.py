import json

from openviking.models.vlm import MultiCredentialVLM
from vikingbot.config import loader
from vikingbot.providers.vlm_adapter import VLMProviderAdapter


def _write_config(tmp_path, monkeypatch, data):
    config_path = tmp_path / "ov.conf"
    config_path.write_text(json.dumps(data))
    monkeypatch.setattr(loader, "CONFIG_PATH", config_path)
    return loader.load_config()


def test_bot_inherits_root_vlm_credentials_when_agents_model_is_omitted(tmp_path, monkeypatch):
    config = _write_config(
        tmp_path,
        monkeypatch,
        {
            "vlm": {
                "model": "root-primary",
                "credentials": [
                    {
                        "id": "root-primary",
                        "provider": "openai",
                        "model": "root-primary",
                        "api_key": "root-primary-key",
                    },
                    {
                        "id": "root-backup",
                        "provider": "openai",
                        "model": "root-backup",
                        "api_key": "root-backup-key",
                    },
                ],
            }
        },
    )

    from vikingbot.cli.commands import _make_provider

    provider = _make_provider(config)

    assert config.agents.inherits_root_vlm() is True
    assert isinstance(provider, VLMProviderAdapter)
    assert isinstance(provider._vlm, MultiCredentialVLM)
    assert config.get_provider_name() == "openai"
    assert provider._vlm._credential_ids == ["root-primary", "root-backup"]
    assert [vlm.model for vlm in provider._vlm._vlm_instances] == [
        "root-primary",
        "root-backup",
    ]


def test_explicit_bot_model_uses_bot_credentials_instead_of_root(tmp_path, monkeypatch):
    config = _write_config(
        tmp_path,
        monkeypatch,
        {
            "vlm": {
                "model": "root-primary",
                "credentials": [
                    {
                        "id": "root-primary",
                        "provider": "openai",
                        "api_key": "root-key",
                    }
                ],
            },
            "bot": {
                "agents": {
                    "model": "bot-primary",
                    "credentials": [
                        {
                            "id": "bot-primary",
                            "provider": "openai",
                            "model": "bot-primary",
                            "api_key": "bot-primary-key",
                        },
                        {
                            "id": "bot-backup",
                            "provider": "openai",
                            "model": "bot-backup",
                            "api_key": "bot-backup-key",
                        },
                    ],
                    "failback_timeout_seconds": 30,
                    "failback_request_count": 5,
                }
            },
        },
    )

    from vikingbot.cli.commands import _make_provider

    provider = _make_provider(config)

    assert config.agents.inherits_root_vlm() is False
    assert isinstance(provider._vlm, MultiCredentialVLM)
    assert config.get_provider_name() == "openai"
    assert provider._vlm._credential_ids == ["bot-primary", "bot-backup"]
    assert [vlm.model for vlm in provider._vlm._vlm_instances] == [
        "bot-primary",
        "bot-backup",
    ]
    assert provider._vlm._switcher._failback_timeout == 30
    assert provider._vlm._switcher._failback_request_count == 5


def test_bot_credentials_without_outer_model_use_bot_chain(tmp_path, monkeypatch):
    config = _write_config(
        tmp_path,
        monkeypatch,
        {
            "vlm": {
                "model": "root-primary",
                "credentials": [
                    {
                        "id": "root-primary",
                        "provider": "openai",
                        "model": "root-primary",
                        "api_key": "root-key",
                    }
                ],
            },
            "bot": {
                "agents": {
                    "credentials": [
                        {
                            "id": "bot-primary",
                            "provider": "openai",
                            "model": "bot-primary",
                            "api_key": "bot-primary-key",
                        },
                        {
                            "id": "bot-backup",
                            "provider": "openai",
                            "model": "bot-backup",
                            "api_key": "bot-backup-key",
                        },
                    ]
                }
            },
        },
    )

    from vikingbot.cli.commands import _make_provider

    provider = _make_provider(config)

    assert config.agents.inherits_root_vlm() is False
    assert config.agents.model == ""
    assert isinstance(provider._vlm, MultiCredentialVLM)
    assert provider._vlm._credential_ids == ["bot-primary", "bot-backup"]
    assert [vlm.model for vlm in provider._vlm._vlm_instances] == [
        "bot-primary",
        "bot-backup",
    ]


def test_explicit_bot_model_without_credentials_keeps_single_model_behavior(tmp_path, monkeypatch):
    config = _write_config(
        tmp_path,
        monkeypatch,
        {
            "vlm": {
                "provider": "openai",
                "model": "root-model",
                "api_key": "root-key",
            },
            "bot": {
                "agents": {
                    "provider": "openai",
                    "model": "bot-model",
                    "api_key": "bot-key",
                }
            },
        },
    )

    from vikingbot.cli.commands import _make_provider

    provider = _make_provider(config)

    assert config.agents.inherits_root_vlm() is False
    assert isinstance(provider, VLMProviderAdapter)
    assert not isinstance(provider._vlm, MultiCredentialVLM)
    assert provider._vlm.model == "bot-model"


def test_saving_inherited_config_does_not_turn_root_model_into_bot_override(tmp_path, monkeypatch):
    config = _write_config(
        tmp_path,
        monkeypatch,
        {
            "vlm": {
                "provider": "openai",
                "model": "root-model",
                "api_key": "root-key",
            }
        },
    )

    loader.save_config(config, tmp_path / "ov.conf")

    saved = json.loads((tmp_path / "ov.conf").read_text())
    assert saved["vlm"]["model"] == "root-model"
    assert "model" not in saved.get("bot", {}).get("agents", {})

    reloaded = loader.load_config()
    assert reloaded.agents.inherits_root_vlm() is True


def test_saving_credentials_only_config_keeps_model_omitted(tmp_path, monkeypatch):
    config = _write_config(
        tmp_path,
        monkeypatch,
        {
            "vlm": {
                "provider": "openai",
                "model": "root-model",
                "api_key": "root-key",
            },
            "bot": {
                "agents": {
                    "credentials": [
                        {
                            "id": "bot-primary",
                            "provider": "openai",
                            "model": "bot-primary",
                            "api_key": "bot-key",
                        }
                    ]
                }
            },
        },
    )

    loader.save_config(config, tmp_path / "ov.conf")

    saved = json.loads((tmp_path / "ov.conf").read_text())
    assert "model" not in saved["bot"]["agents"]

    reloaded = loader.load_config()
    assert reloaded.agents.inherits_root_vlm() is False
    assert reloaded.agents.model == ""

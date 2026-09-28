import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from typer.testing import CliRunner
from vikingbot.cli.commands import _make_provider
from vikingbot.config import loader
from vikingbot.providers.vlm_adapter import VLMProviderAdapter

from openviking.models.vlm import MultiCredentialVLM
from openviking.models.vlm.backends.litellm_vlm import LiteLLMVLMProvider


def _write_config(tmp_path, monkeypatch, data):
    config_path = tmp_path / "ov.conf"
    config_path.write_text(json.dumps(data))
    monkeypatch.setattr(loader, "CONFIG_PATH", config_path)
    monkeypatch.setenv("OPENVIKING_CONFIG_FILE", str(config_path))
    return loader.load_config()


def _credential_chain(prefix):
    return [
        {
            "id": f"{prefix}-{name}",
            "provider": "openai",
            "model": f"{prefix}-{name}",
            "api_key": f"{prefix}-{name}-key",
        }
        for name in ("primary", "backup")
    ]


@pytest.mark.parametrize("explicit_key", [None, "channel-secret", ""])
def test_legacy_groq_key_migrates_to_telegram_without_overriding_channel_config(
    tmp_path, monkeypatch, explicit_key
):
    pytest.importorskip("telegram")
    from vikingbot.bus.queue import MessageBus
    from vikingbot.channels.manager import ChannelManager

    channel = {"type": "telegram", "enabled": True, "token": "123:test-token"}
    if explicit_key is not None:
        channel["groqApiKey"] = explicit_key
    data = {
        "bot": {
            "providers": {"groq": {"apiKey": "legacy-groq-secret"}},
            "channels": [channel],
        }
    }
    config = _write_config(tmp_path, monkeypatch, data)
    manager = ChannelManager(MessageBus())

    manager.load_channels_from_config(config)

    expected_key = "legacy-groq-secret" if explicit_key is None else explicit_key
    assert manager.channels["telegram__123"].groq_api_key == expected_key
    assert json.loads((tmp_path / "ov.conf").read_text()) == data
    loader.save_config(config, tmp_path / "ov.conf")
    saved = json.loads((tmp_path / "ov.conf").read_text())
    assert "providers" not in saved["bot"]
    assert saved["bot"]["channels"][0]["groqApiKey"] == expected_key
    reloaded = loader.load_config()
    manager = ChannelManager(MessageBus())
    manager.load_channels_from_config(reloaded)
    assert manager.channels["telegram__123"].groq_api_key == expected_key


@pytest.mark.parametrize("bot_override", [False, True])
@pytest.mark.parametrize("credential_chain", [False, True])
def test_status_shows_selected_model_config_without_exposing_secrets(
    tmp_path, monkeypatch, bot_override, credential_chain
):
    from vikingbot.cli import commands

    selected = {
        "provider": "openai",
        "model": "selected-model",
        "api_key": "selected-secret",
        "extra_headers": {"Authorization": "header-secret"},
    }
    if credential_chain:
        selected["credentials"] = [
            {"id": "primary", "model": "first-model", "api_key": "first-secret"},
            {"id": "backup"},
        ]
    data = {"vlm": selected}
    if bot_override:
        data = {
            "vlm": {"provider": "openai", "model": "unused-root", "api_key": "root-secret"},
            "bot": {"agents": selected},
        }
    _write_config(tmp_path, monkeypatch, data)

    def fail_create(*args, **kwargs):
        raise AssertionError("status must not initialize model backends")

    monkeypatch.setattr("openviking.models.vlm.base.VLMFactory.create", fail_create)
    result = CliRunner().invoke(commands.app, ["status"])

    assert result.exit_code == 0, result.output
    source = "bot.agents" if bot_override else "vlm (inherited)"
    assert f"Model config: {source}" in result.output
    assert "Model: selected-model" in result.output
    assert "Provider: openai" in result.output
    assert "API key: configured" in result.output
    assert "Extra headers: configured" in result.output
    assert "not set in config" not in result.output
    assert "not a live health check" in result.output
    if credential_chain:
        assert result.output.index("Model: first-model") < result.output.index(
            "Model: selected-model"
        )
        assert result.output.count("API key: configured") == 2
    for hidden in (
        "selected-secret",
        "first-secret",
        "header-secret",
        "root-secret",
        "unused-root",
    ):
        assert hidden not in result.output


def test_bot_inherits_root_vlm_credentials_when_agents_model_is_omitted(tmp_path, monkeypatch):
    config = _write_config(
        tmp_path,
        monkeypatch,
        {
            "vlm": {
                "model": "root-primary",
                "credentials": _credential_chain("root"),
            }
        },
    )

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


@pytest.mark.parametrize("bot_override", [False, True], ids=["inherited-model", "bot-model"])
def test_agent_max_tokens_reaches_provider(tmp_path, monkeypatch, bot_override):
    agents = {"max_tokens": 8192}
    if bot_override:
        agents.update(provider="openai", model="bot-model", api_key="bot-key")
    config = _write_config(
        tmp_path,
        monkeypatch,
        {
            "vlm": {
                "provider": "openai",
                "model": "root-model",
                "api_key": "root-key",
                "max_tokens": 4096,
            },
            "bot": {"agents": agents},
        },
    )
    provider = _make_provider(config)
    assert config.agents.inherits_root_vlm() is (not bot_override)
    assert provider._vlm.max_tokens == 8192


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
                    "credentials": _credential_chain("bot"),
                    "failback_timeout_seconds": 30,
                    "failback_request_count": 5,
                }
            },
        },
    )

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


@pytest.mark.asyncio
async def test_bot_multi_credentials_preserve_thinking_for_chat(tmp_path, monkeypatch):
    config = _write_config(
        tmp_path,
        monkeypatch,
        {
            "bot": {
                "agents": {
                    "model": "bot-primary",
                    "thinking": True,
                    "credentials": _credential_chain("bot"),
                }
            }
        },
    )

    provider = _make_provider(
        config,
        langfuse_client=SimpleNamespace(enabled=False, _client=None),
    )
    primary = provider._vlm._vlm_instances[0]
    primary.get_completion_async = AsyncMock(return_value="ok")

    response = await provider.chat(messages=[{"role": "user", "content": "hello"}])

    assert provider._vlm.thinking is True
    assert response.content == "ok"
    assert primary.get_completion_async.await_args.kwargs["thinking"] is True


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
            "bot": {"agents": {"credentials": _credential_chain("bot")}},
        },
    )

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

    provider = _make_provider(config)

    assert config.agents.inherits_root_vlm() is False
    assert isinstance(provider, VLMProviderAdapter)
    assert not isinstance(provider._vlm, MultiCredentialVLM)
    assert provider._vlm.model == "bot-model"


def test_bot_credentials_override_or_inherit_agent_max_tokens(tmp_path, monkeypatch):
    config = _write_config(
        tmp_path,
        monkeypatch,
        {
            "bot": {
                "agents": {
                    "max_tokens": 8192,
                    "credentials": [
                        {
                            "id": "bot-primary",
                            "provider": "openai",
                            "model": "bot-primary",
                            "api_key": "bot-primary-key",
                            "max_tokens": 2048,
                        },
                        {
                            "id": "bot-backup",
                            "provider": "openai",
                            "model": "bot-backup",
                            "api_key": "bot-backup-key",
                        },
                    ],
                }
            }
        },
    )

    provider = _make_provider(config)

    assert isinstance(provider._vlm, MultiCredentialVLM)
    assert [vlm.max_tokens for vlm in provider._vlm._vlm_instances] == [2048, 8192]


def test_explicit_litellm_provider_uses_vlm_adapter(tmp_path, monkeypatch):
    config = _write_config(
        tmp_path,
        monkeypatch,
        {
            "bot": {
                "agents": {
                    "provider": "litellm",
                    "model": "openrouter/openai/gpt-4o-mini",
                    "api_key": "bot-key",
                }
            }
        },
    )

    provider = _make_provider(config)

    assert isinstance(provider, VLMProviderAdapter)
    assert isinstance(provider._vlm, LiteLLMVLMProvider)
    assert provider._vlm.model == "openrouter/openai/gpt-4o-mini"


def test_bot_model_without_provider_rejects_legacy_fallback(tmp_path, monkeypatch):
    config = _write_config(
        tmp_path,
        monkeypatch,
        {
            "bot": {
                "agents": {
                    "model": "openrouter/openai/gpt-4o-mini",
                    "api_key": "bot-key",
                }
            }
        },
    )

    with pytest.raises(RuntimeError, match="Set provider to 'litellm'"):
        _make_provider(config)


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

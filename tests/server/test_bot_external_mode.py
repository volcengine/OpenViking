# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""External VikingBot gateway wiring: no managed child, same proxy surface.

An operator who deploys and supervises the gateway themselves configures
``server.bot_api_url`` (plus ``server.bot_gateway_token``) instead of
``server.with_bot``. The Bot proxy, Studio channel management and the local
Compile backend must behave the same in both modes.
"""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

import openviking.server.app as app_module
import openviking.server.routers.bot as bot_module
from openviking.server.config import BOT_STUDIO_TOKEN_ENV, ServerConfig


@pytest.fixture
def bot_proxy_state(monkeypatch):
    """Snapshot and restore the process-wide Bot proxy module state."""
    monkeypatch.setattr(bot_module, "BOT_API_URL", None)
    monkeypatch.setattr(bot_module, "BOT_API_KEY", "")
    monkeypatch.setattr(bot_module, "BOT_MODE", "disabled")
    monkeypatch.delenv(BOT_STUDIO_TOKEN_ENV, raising=False)
    return bot_module


def _config(**overrides) -> ServerConfig:
    return ServerConfig(auth_mode="dev", host="127.0.0.1", port=1933, **overrides)


def test_external_gateway_configures_the_proxy_without_with_bot(bot_proxy_state):
    config = _config(
        bot_api_url="http://127.0.0.1:18790",
        bot_gateway_token="gateway-token",
    )

    app_module.create_app(config)

    assert bot_proxy_state.BOT_API_URL == "http://127.0.0.1:18790"
    assert bot_proxy_state.BOT_API_KEY == "gateway-token"
    assert bot_proxy_state.BOT_MODE == "external"


def test_managed_gateway_still_reports_managed_mode(bot_proxy_state):
    config = _config(with_bot=True, bot_api_url="http://127.0.0.1:18790")

    app_module.create_app(config)

    assert bot_proxy_state.BOT_MODE == "managed"
    assert bot_proxy_state.BOT_API_URL == "http://127.0.0.1:18790"


def test_internal_studio_token_overrides_configured_gateway_token(bot_proxy_state, monkeypatch):
    monkeypatch.setenv(BOT_STUDIO_TOKEN_ENV, "internal-token")
    config = _config(
        bot_api_url="http://127.0.0.1:18790",
        bot_gateway_token="gateway-token",
    )

    app_module.create_app(config)

    assert bot_proxy_state.BOT_API_KEY == "internal-token"


def test_disabled_proxy_is_not_configured(bot_proxy_state):
    app_module.create_app(_config())

    assert bot_proxy_state.BOT_API_URL is None
    assert bot_proxy_state.BOT_API_KEY == ""
    assert bot_proxy_state.BOT_MODE == "disabled"


@pytest.mark.parametrize("mode", ["managed", "external"])
def test_bot_gateway_backs_compile_in_both_modes(mode):
    service = SimpleNamespace(compile=SimpleNamespace(configure_local_backend=Mock()))
    config = _config(bot_api_url="http://127.0.0.1:18790")

    app_module._configure_bot_compile_backend(service, config, mode, "gateway-token")

    service.compile.configure_local_backend.assert_called_once_with(
        "http://127.0.0.1:18790", "gateway-token"
    )


def test_compile_backend_is_untouched_when_the_proxy_is_disabled():
    service = SimpleNamespace(compile=SimpleNamespace(configure_local_backend=Mock()))

    app_module._configure_bot_compile_backend(service, _config(), "disabled", "")

    service.compile.configure_local_backend.assert_not_called()

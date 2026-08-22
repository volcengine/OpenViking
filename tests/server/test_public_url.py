# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Security contracts for public server URL configuration."""

from types import SimpleNamespace

import pytest

from openviking.server.config import ServerConfig
from openviking.server.public_url import (
    resolve_configured_public_base_url,
    validate_public_deployment_config,
)


def test_loopback_defaults_to_no_cors_allowlist():
    config = ServerConfig()

    validate_public_deployment_config(config)

    assert config.cors_origins == []


def test_loopback_keeps_existing_development_wildcard_compatibility():
    validate_public_deployment_config(ServerConfig(cors_origins=["*"]))


@pytest.mark.parametrize(
    "config",
    [
        ServerConfig(host="0.0.0.0"),
        ServerConfig(host="0.0.0.0", cors_origins=["*"]),
        ServerConfig(
            host="0.0.0.0",
            cors_origins=["https://studio.example"],
            public_base_url="http://api.example",
        ),
    ],
)
def test_public_bind_requires_explicit_https_base_and_nonwildcard_origins(config):
    with pytest.raises(ValueError):
        validate_public_deployment_config(config)


def test_public_bind_uses_explicit_configured_origin_not_request_headers():
    config = ServerConfig(
        host="0.0.0.0",
        cors_origins=["https://studio.example"],
        public_base_url="https://api.example/",
    )

    validate_public_deployment_config(config)

    assert resolve_configured_public_base_url(config) == ("https://api.example", "config")


def test_oauth_issuer_must_match_effective_public_origin():
    config = ServerConfig(
        host="0.0.0.0",
        cors_origins=["https://studio.example"],
        public_base_url="https://api.example",
        auth_mode="api_key",
        root_api_key="test-root-key",
    )
    oauth = SimpleNamespace(enabled=True, issuer="https://other.example")

    with pytest.raises(ValueError, match="must match"):
        validate_public_deployment_config(config, oauth)


def test_create_app_does_not_hide_oauth_origin_mismatch(monkeypatch):
    import openviking.server.app as app_module

    config = ServerConfig(
        host="0.0.0.0",
        cors_origins=["https://studio.example"],
        public_base_url="https://api.example",
        auth_mode="api_key",
        root_api_key="test-root-key",
    )
    monkeypatch.setattr(
        app_module,
        "get_openviking_config",
        lambda: SimpleNamespace(oauth=SimpleNamespace(enabled=True, issuer="https://other.example")),
    )

    with pytest.raises(ValueError, match="must match"):
        app_module.create_app(config=config)

# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Focused tests for Connector configuration."""

import pytest

from openviking_cli.utils.config.open_viking_config import ConnectorConfig, OpenVikingConfig


def test_connector_config_is_opt_in_and_tos_only_by_default():
    config = ConnectorConfig()

    assert config.enable is False
    assert config.allowed_add_types == ["tos"]


def test_openviking_config_parses_connector_section():
    config = OpenVikingConfig.from_dict(
        {
            "connector": {
                "enable": True,
                "connector": "https://connector.example/doc/add",
                "tracker": "https://connector.example/task/info",
                "timeout_seconds": 120,
                "poll_interval_ms": 250,
                "allowed_add_types": ["tos"],
            }
        }
    )

    assert config.connector.model_dump() == {
        "enable": True,
        "connector": "https://connector.example/doc/add",
        "tracker": "https://connector.example/task/info",
        "timeout_seconds": 120,
        "poll_interval_ms": 250,
        "allowed_add_types": ["tos"],
    }


@pytest.mark.parametrize(
    ("kwargs", "match"),
    [
        ({"enable": True}, "connector.connector is required"),
        (
            {
                "enable": True,
                "connector": "connector.example/doc/add",
                "tracker": "https://connector.example/task/info",
            },
            "must be a full endpoint URL",
        ),
        ({"timeout_seconds": 0}, "timeout_seconds must be > 0"),
        ({"poll_interval_ms": 0}, "poll_interval_ms must be > 0"),
    ],
)
def test_connector_config_rejects_invalid_runtime_settings(kwargs, match):
    with pytest.raises(ValueError, match=match):
        ConnectorConfig(**kwargs)

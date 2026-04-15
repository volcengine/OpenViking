# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import json

import pytest

from openviking.server.config import load_server_config


def test_load_server_config_rejects_unknown_field(tmp_path):
    config_path = tmp_path / "ov.conf"
    config_path.write_text(json.dumps({"server": {"host": "0.0.0.0", "prt": 9999}}))

    with pytest.raises(
        ValueError,
        match=r"server\.prt'.*server\.port",
    ):
        load_server_config(str(config_path))


def test_load_server_config_rejects_unknown_nested_field(tmp_path):
    config_path = tmp_path / "ov.conf"
    config_path.write_text(json.dumps({"server": {"telemetry": {"prometheus": {"enabld": True}}}}))

    with pytest.raises(
        ValueError,
        match=r"server\.telemetry\.prometheus\.enabld'.*server\.telemetry\.prometheus\.enabled",
    ):
        load_server_config(str(config_path))


def test_load_server_config_reports_invalid_value_path(tmp_path):
    config_path = tmp_path / "ov.conf"
    config_path.write_text(json.dumps({"server": {"port": "abc"}}))

    with pytest.raises(ValueError, match=r"Invalid value for 'server\.port'"):
        load_server_config(str(config_path))


def test_load_server_config_preserves_supported_fields(tmp_path):
    config_path = tmp_path / "ov.conf"
    config_path.write_text(
        json.dumps(
            {
                "server": {
                    "host": "0.0.0.0",
                    "port": 1944,
                    "workers": 2,
                    "auth_mode": "trusted",
                    "with_bot": True,
                    "bot_api_url": "http://localhost:19999",
                    "telemetry": {"prometheus": {"enabled": True}},
                },
                "encryption": {"enabled": True},
            }
        )
    )

    config = load_server_config(str(config_path))

    assert config.host == "0.0.0.0"
    assert config.port == 1944
    assert config.workers == 2
    assert config.auth_mode == "trusted"
    assert config.with_bot is True
    assert config.bot_api_url == "http://localhost:19999"
    assert config.telemetry.prometheus.enabled is True
    assert config.encryption_enabled is True


def test_load_server_config_preserves_metrics_account_dimension_fields(tmp_path):
    config_path = tmp_path / "ov.conf"
    config_path.write_text(
        json.dumps(
            {
                "server": {
                    "metrics": {
                        "enabled": True,
                        "account_dimension": {
                            "enabled": True,
                            "max_active_accounts": 5,
                            "metric_allowlist": [
                                "openviking_http_requests_total",
                                "openviking_task_pending",
                            ],
                        },
                    }
                }
            }
        )
    )

    config = load_server_config(str(config_path))

    assert config.metrics.enabled is True
    assert config.metrics.account_dimension.enabled is True
    assert config.metrics.account_dimension.max_active_accounts == 5
    assert config.metrics.account_dimension.metric_allowlist == [
        "openviking_http_requests_total",
        "openviking_task_pending",
    ]

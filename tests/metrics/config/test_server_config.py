# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import json

import pytest

from openviking.server.config import load_server_config


def test_load_server_config_rejects_legacy_server_metrics_field(tmp_path):
    config_path = tmp_path / "ov.conf"
    config_path.write_text(json.dumps({"server": {"metrics": {"enabled": True}}}))

    with pytest.raises(ValueError, match=r"server\.metrics"):
        load_server_config(str(config_path))


def test_load_server_config_rejects_legacy_server_telemetry_field(tmp_path):
    config_path = tmp_path / "ov.conf"
    config_path.write_text(json.dumps({"server": {"telemetry": {"prometheus": {"enabled": True}}}}))

    with pytest.raises(ValueError, match=r"server\.telemetry"):
        load_server_config(str(config_path))


def test_load_server_config_preserves_metrics_fields_under_server_observability(tmp_path):
    config_path = tmp_path / "ov.conf"
    config_path.write_text(
        json.dumps(
            {
                "server": {
                    "observability": {
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
            }
        )
    )

    config = load_server_config(str(config_path))

    assert config.observability.metrics.enabled is True
    assert config.observability.metrics.account_dimension.enabled is True
    assert config.observability.metrics.account_dimension.max_active_accounts == 5
    assert config.observability.metrics.account_dimension.metric_allowlist == [
        "openviking_http_requests_total",
        "openviking_task_pending",
    ]

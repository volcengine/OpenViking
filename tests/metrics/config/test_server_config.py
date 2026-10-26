# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import json

from openviking.server.config import load_server_config


def test_load_server_config_ignores_legacy_observability_fields(tmp_path):
    config_path = tmp_path / "ov.conf"
    config_path.write_text(
        json.dumps(
            {
                "server": {
                    "metrics": {"enabled": True},
                    "telemetry": {"prometheus": {"enabled": True}},
                }
            }
        )
    )

    config = load_server_config(str(config_path))

    assert config.observability.metrics.enabled is False
    assert "metrics" not in config.model_dump()
    assert "telemetry" not in config.model_dump()


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

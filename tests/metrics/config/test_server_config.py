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

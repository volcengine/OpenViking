# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Exercise the observer commands in the shipped CLI against an OpenViking server."""

import pytest
from conftest import ov

pytestmark = pytest.mark.cli_remote


@pytest.mark.parametrize("component", ["queue", "models", "system"])
def test_observer_json_keeps_component_payload(component):
    response = ov(["observer", component, "-o", "json"])
    assert response["exit_code"] == 0, response["stderr"]
    envelope = response["json"]
    assert envelope["ok"] is True
    result = envelope["result"]
    assert isinstance(result["is_healthy"], bool)
    if component == "system":
        assert result["components"]["queue"]["name"] == "queue"
        assert result["components"]["models"]["name"] == "models"
    else:
        assert result["name"] == component
        assert isinstance(result["status"], str) and result["status"].strip()

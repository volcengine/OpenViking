# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Regression tests for bot proxy endpoint auth enforcement."""

import openviking.server.routers.bot as bot_router_module


def test_set_bot_api_key_updates_module_state():
    bot_router_module.set_bot_api_key("gateway-secret")
    assert bot_router_module.BOT_API_KEY == "gateway-secret"

    bot_router_module.set_bot_api_key("")
    assert bot_router_module.BOT_API_KEY == ""

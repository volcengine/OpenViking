# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import inspect

from openviking.async_client import AsyncOpenViking
from openviking.client.local import LocalClient
from openviking.sync_client import SyncOpenViking
from openviking_cli.client.base import BaseClient


def _assert_add_resource_keeps_telemetry_before_tags(func):
    params = list(inspect.signature(func).parameters)
    assert params.index("telemetry") < params.index("tags")
    assert params.index("telemetry") < params.index("tag_mode")


def test_top_level_add_resource_signatures_keep_telemetry_position():
    _assert_add_resource_keeps_telemetry_before_tags(AsyncOpenViking.add_resource)
    _assert_add_resource_keeps_telemetry_before_tags(SyncOpenViking.add_resource)
    _assert_add_resource_keeps_telemetry_before_tags(LocalClient.add_resource)
    _assert_add_resource_keeps_telemetry_before_tags(BaseClient.add_resource)

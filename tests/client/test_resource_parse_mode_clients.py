# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Public Python client coverage for args.parse_mode."""

import inspect

from openviking_cli.client.sync_http import SyncHTTPClient


def test_http_client_does_not_expose_parse_mode_parameter():
    assert "parse_mode" not in inspect.signature(SyncHTTPClient.add_resource).parameters

# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""OpenViking HTTP client compatibility exports."""

from openviking_cli.client.http import AsyncHTTPClient
from openviking_cli.client.sync_http import SyncHTTPClient

__all__ = [
    "AsyncHTTPClient",
    "SyncHTTPClient",
]

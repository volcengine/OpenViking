# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
OpenViking client.
This module provides both synchronous and asynchronous clients.
"""

from openviking_sdk import AsyncHTTPClient, SyncHTTPClient

from openviking.async_client import AsyncOpenViking
from openviking.sync_client import SyncOpenViking

__all__ = ["SyncOpenViking", "AsyncOpenViking", "SyncHTTPClient", "AsyncHTTPClient"]

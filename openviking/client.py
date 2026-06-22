# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
OpenViking client.
This module provides both synchronous and asynchronous clients.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from openviking.async_client import AsyncOpenViking
    from openviking.sync_client import SyncOpenViking
    from openviking_cli.client.http import AsyncHTTPClient
    from openviking_cli.client.sync_http import SyncHTTPClient

__all__ = ["SyncOpenViking", "AsyncOpenViking", "SyncHTTPClient", "AsyncHTTPClient"]


def __getattr__(name: str):
    if name == "AsyncOpenViking":
        from openviking.async_client import AsyncOpenViking

        return AsyncOpenViking
    if name == "SyncOpenViking":
        from openviking.sync_client import SyncOpenViking

        return SyncOpenViking
    if name == "AsyncHTTPClient":
        from openviking_cli.client.http import AsyncHTTPClient

        return AsyncHTTPClient
    if name == "SyncHTTPClient":
        from openviking_cli.client.sync_http import SyncHTTPClient

        return SyncHTTPClient
    raise AttributeError(name)

# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""HTTP client compatibility exports for the main OpenViking package."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from openviking_cli.client.http import AsyncHTTPClient
    from openviking_cli.client.sync_http import SyncHTTPClient

__all__ = [
    "AsyncHTTPClient",
    "SyncHTTPClient",
]


def __getattr__(name: str):
    if name == "AsyncHTTPClient":
        from openviking_cli.client.http import AsyncHTTPClient

        return AsyncHTTPClient
    if name == "SyncHTTPClient":
        from openviking_cli.client.sync_http import SyncHTTPClient

        return SyncHTTPClient
    raise AttributeError(name)

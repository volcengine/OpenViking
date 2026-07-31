# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""OpenViking Client module.

Provides client implementations for embedded (LocalClient) and HTTP (AsyncHTTPClient/SyncHTTPClient) modes.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from openviking.client.local import LocalClient
    from openviking.client.session import Session
    from openviking_cli.client.base import BaseClient
    from openviking_cli.client.http import AsyncHTTPClient
    from openviking_cli.client.sync_http import SyncHTTPClient

__all__ = [
    "BaseClient",
    "AsyncHTTPClient",
    "SyncHTTPClient",
    "LocalClient",
    "Session",
]


def __getattr__(name: str):
    if name == "AsyncHTTPClient":
        from openviking_cli.client.http import AsyncHTTPClient

        return AsyncHTTPClient
    if name == "SyncHTTPClient":
        from openviking_cli.client.sync_http import SyncHTTPClient

        return SyncHTTPClient
    if name == "LocalClient":
        from openviking.client.local import LocalClient

        return LocalClient
    if name == "Session":
        from openviking.client.session import Session

        return Session
    if name == "BaseClient":
        from openviking_cli.client.base import BaseClient

        return BaseClient
    raise AttributeError(name)

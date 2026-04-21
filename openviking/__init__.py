# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
OpenViking - An Agent-native context database

Data in, Context out.
"""

from typing import TYPE_CHECKING

try:
    from ._version import version as __version__
except ImportError:
    try:
        from importlib.metadata import version

        __version__ = version("openviking")
    except ImportError:
        __version__ = "0.0.0+unknown"

try:
    from openviking.pyagfs import AGFSClient
except ImportError as exc:
    raise ImportError(
        "Bundled OpenViking AGFS client is unavailable. "
        "Reinstall openviking or run 'pip install -e .' from the project root."
    ) from exc

if TYPE_CHECKING:
    from openviking.async_client import AsyncOpenViking
    from openviking.session import Session
    from openviking.sync_client import SyncOpenViking
    from openviking_cli.client.http import AsyncHTTPClient
    from openviking_cli.client.sync_http import SyncHTTPClient
    from openviking_cli.session.user_id import UserIdentifier

    OpenViking = SyncOpenViking


def __getattr__(name: str):
    if name == "AsyncOpenViking":
        from openviking.async_client import AsyncOpenViking

        return AsyncOpenViking
    if name == "SyncOpenViking":
        from openviking.sync_client import SyncOpenViking

        return SyncOpenViking
    if name == "OpenViking":
        from openviking.sync_client import SyncOpenViking

        return SyncOpenViking
    if name == "Session":
        from openviking.session import Session

        return Session
    if name == "AsyncHTTPClient":
        from openviking_cli.client.http import AsyncHTTPClient

        return AsyncHTTPClient
    if name == "SyncHTTPClient":
        from openviking_cli.client.sync_http import SyncHTTPClient

        return SyncHTTPClient
    if name == "UserIdentifier":
        from openviking_cli.session.user_id import UserIdentifier

        return UserIdentifier
    raise AttributeError(name)


__all__ = [
    "OpenViking",
    "SyncOpenViking",
    "AsyncOpenViking",
    "SyncHTTPClient",
    "AsyncHTTPClient",
    "Session",
    "UserIdentifier",
]

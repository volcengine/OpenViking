# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""
OpenViking - An Agent-native context database

Data in, Context out.
"""

from openviking.async_client import AsyncOpenViking
from openviking.client.http import AsyncHTTPClient
from openviking.client.sync_http import SyncHTTPClient
from openviking.session import Session
from openviking.session.user_id import UserIdentifier
from openviking.sync_client import SyncOpenViking

OpenViking = SyncOpenViking

try:
    from ._version import version as __version__
except ImportError:
    try:
        from importlib.metadata import version

        __version__ = version("openviking")
    except ImportError:
        __version__ = "0.0.0+unknown"

__all__ = [
    "OpenViking",
    "SyncOpenViking",
    "AsyncOpenViking",
    "SyncHTTPClient",
    "AsyncHTTPClient",
    "Session",
    "UserIdentifier",
]

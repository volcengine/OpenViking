# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""
OpenViking - An Agent-native context database

Data in, Context out.
"""

from openviking.client import AsyncOpenViking, SyncOpenViking
from openviking.session import Session

OpenViking = SyncOpenViking

__version__ = "0.1.0"
__all__ = [
    "OpenViking",
    "SyncOpenViking",
    "AsyncOpenViking",
    "Session",
]

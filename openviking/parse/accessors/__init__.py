# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
Data Accessors for OpenViking.

This module provides the two-layer architecture for resource processing:
- DataAccessor: Fetches data from remote sources or special paths
- DataParser: Parses local files/directories (existing Parser system)
"""

from .base import DataAccessor, LocalResource
from .feishu_accessor import FeishuAccessor
from .git_accessor import GitAccessor
from .http_accessor import HTTPAccessor
from .local_accessor import LocalAccessor
from .registry import (
    AccessorRegistry,
    access,
    get_accessor_registry,
)

__all__ = [
    # Base classes
    "DataAccessor",
    "LocalResource",
    # Registry
    "AccessorRegistry",
    "get_accessor_registry",
    "access",
    # Accessors
    "GitAccessor",
    "HTTPAccessor",
    "FeishuAccessor",
    "LocalAccessor",
]

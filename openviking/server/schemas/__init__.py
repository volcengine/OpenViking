# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Typed response models shared across OpenViking HTTP routers.

Business routers place module-specific response models alongside this
package (e.g. ``schemas/sessions.py``); cross-module primitives live in
``schemas/common``.
"""

from openviking.server.schemas.common import (
    ExcludeNoneRoute,
    PaginatedResult,
    Pagination,
    URIRef,
)

__all__ = [
    "ExcludeNoneRoute",
    "PaginatedResult",
    "Pagination",
    "URIRef",
]

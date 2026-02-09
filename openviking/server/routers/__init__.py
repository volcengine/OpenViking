# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""OpenViking HTTP Server routers."""

from openviking.server.routers.content import router as content_router
from openviking.server.routers.debug import router as debug_router
from openviking.server.routers.filesystem import router as filesystem_router
from openviking.server.routers.observer import router as observer_router
from openviking.server.routers.pack import router as pack_router
from openviking.server.routers.relations import router as relations_router
from openviking.server.routers.resources import router as resources_router
from openviking.server.routers.search import router as search_router
from openviking.server.routers.sessions import router as sessions_router
from openviking.server.routers.system import router as system_router

__all__ = [
    "system_router",
    "resources_router",
    "filesystem_router",
    "content_router",
    "search_router",
    "relations_router",
    "sessions_router",
    "pack_router",
    "debug_router",
    "observer_router",
]

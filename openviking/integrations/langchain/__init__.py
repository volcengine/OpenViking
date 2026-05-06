# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""LangChain and LangGraph integrations for OpenViking.

The objects in this package depend on optional framework packages. Importing
``openviking`` itself does not install or import LangChain/LangGraph.
"""

from __future__ import annotations

from typing import Any

__all__ = [
    "InMemoryOpenVikingClient",
    "OpenVikingContextMiddleware",
    "OpenVikingRetriever",
    "OpenVikingStore",
    "create_openviking_tools",
]


def __getattr__(name: str) -> Any:
    if name == "OpenVikingRetriever":
        from openviking.integrations.langchain.retrievers import OpenVikingRetriever

        return OpenVikingRetriever
    if name == "create_openviking_tools":
        from openviking.integrations.langchain.tools import create_openviking_tools

        return create_openviking_tools
    if name == "OpenVikingStore":
        from openviking.integrations.langchain.store import OpenVikingStore

        return OpenVikingStore
    if name == "OpenVikingContextMiddleware":
        from openviking.integrations.langchain.middleware import OpenVikingContextMiddleware

        return OpenVikingContextMiddleware
    if name == "InMemoryOpenVikingClient":
        from openviking.integrations.langchain.testing import InMemoryOpenVikingClient

        return InMemoryOpenVikingClient
    raise AttributeError(name)


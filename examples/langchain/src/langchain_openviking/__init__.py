# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""LangChain and LangGraph integrations for OpenViking.

The base distribution depends on ``langchain-core``. LangGraph middleware is
loaded only when the ``langgraph`` extra (or compatible dependencies) is
installed.
"""

from __future__ import annotations

from importlib.util import find_spec
from typing import Any

from langchain_openviking.actor_peer import (
    has_request_actor_peer_support,
)

__all__ = [
    "InMemoryOpenVikingClient",
    "OpenVikingChatMessageHistory",
    "OpenVikingCancellationProgress",
    "OpenVikingCommitPolicy",
    "OpenVikingContextRunnable",
    "OpenVikingPartialWriteError",
    "OpenVikingRecordResult",
    "OpenVikingRetriever",
    "OpenVikingSessionContextAssembler",
    "OpenVikingSessionRecorder",
    "OpenVikingStore",
    "create_openviking_tools",
    "get_openviking_cancellation_progress",
    "has_request_actor_peer_support",
    "with_openviking_context",
]

if find_spec("langchain") is not None:
    __all__.append("OpenVikingContextMiddleware")


def __getattr__(name: str) -> Any:
    if name == "OpenVikingRetriever":
        from langchain_openviking.retrievers import OpenVikingRetriever

        return OpenVikingRetriever
    if name == "create_openviking_tools":
        from langchain_openviking.tools import create_openviking_tools

        return create_openviking_tools
    if name == "OpenVikingStore":
        from langchain_openviking.store import OpenVikingStore

        return OpenVikingStore
    if name == "OpenVikingChatMessageHistory":
        from langchain_openviking.history import OpenVikingChatMessageHistory

        return OpenVikingChatMessageHistory
    if name == "OpenVikingSessionContextAssembler":
        from langchain_openviking.context import OpenVikingSessionContextAssembler

        return OpenVikingSessionContextAssembler
    if name == "OpenVikingContextRunnable":
        from langchain_openviking.context import OpenVikingContextRunnable

        return OpenVikingContextRunnable
    if name == "OpenVikingSessionRecorder":
        from langchain_openviking.recording import OpenVikingSessionRecorder

        return OpenVikingSessionRecorder
    if name == "OpenVikingPartialWriteError":
        from langchain_openviking.recording import OpenVikingPartialWriteError

        return OpenVikingPartialWriteError
    if name == "OpenVikingRecordResult":
        from langchain_openviking.recording import OpenVikingRecordResult

        return OpenVikingRecordResult
    if name == "OpenVikingCancellationProgress":
        from langchain_openviking.recording import (
            OpenVikingCancellationProgress,
        )

        return OpenVikingCancellationProgress
    if name == "get_openviking_cancellation_progress":
        from langchain_openviking.recording import (
            get_openviking_cancellation_progress,
        )

        return get_openviking_cancellation_progress
    if name == "OpenVikingCommitPolicy":
        from langchain_openviking.client import OpenVikingCommitPolicy

        return OpenVikingCommitPolicy
    if name == "with_openviking_context":
        from langchain_openviking.context import with_openviking_context

        return with_openviking_context
    if name == "OpenVikingContextMiddleware":
        from langchain_openviking.middleware import OpenVikingContextMiddleware

        return OpenVikingContextMiddleware
    if name == "InMemoryOpenVikingClient":
        from langchain_openviking.testing import InMemoryOpenVikingClient

        return InMemoryOpenVikingClient
    raise AttributeError(name)

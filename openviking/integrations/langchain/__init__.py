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
    "OpenVikingChatMessageHistory",
    "OpenVikingCancellationProgress",
    "OpenVikingCommitPolicy",
    "OpenVikingContextRunnable",
    "OpenVikingContextMiddleware",
    "OpenVikingPartialWriteError",
    "OpenVikingRecordResult",
    "OpenVikingRetriever",
    "OpenVikingSessionContextAssembler",
    "OpenVikingSessionRecorder",
    "OpenVikingStore",
    "create_openviking_tools",
    "get_openviking_cancellation_progress",
    "with_openviking_context",
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
    if name == "OpenVikingChatMessageHistory":
        from openviking.integrations.langchain.history import OpenVikingChatMessageHistory

        return OpenVikingChatMessageHistory
    if name == "OpenVikingSessionContextAssembler":
        from openviking.integrations.langchain.context import OpenVikingSessionContextAssembler

        return OpenVikingSessionContextAssembler
    if name == "OpenVikingContextRunnable":
        from openviking.integrations.langchain.context import OpenVikingContextRunnable

        return OpenVikingContextRunnable
    if name == "OpenVikingSessionRecorder":
        from openviking.integrations.langchain.recording import OpenVikingSessionRecorder

        return OpenVikingSessionRecorder
    if name == "OpenVikingPartialWriteError":
        from openviking.integrations.langchain.recording import OpenVikingPartialWriteError

        return OpenVikingPartialWriteError
    if name == "OpenVikingRecordResult":
        from openviking.integrations.langchain.recording import OpenVikingRecordResult

        return OpenVikingRecordResult
    if name == "OpenVikingCancellationProgress":
        from openviking.integrations.langchain.recording import (
            OpenVikingCancellationProgress,
        )

        return OpenVikingCancellationProgress
    if name == "get_openviking_cancellation_progress":
        from openviking.integrations.langchain.recording import (
            get_openviking_cancellation_progress,
        )

        return get_openviking_cancellation_progress
    if name == "OpenVikingCommitPolicy":
        from openviking.integrations.langchain.client import OpenVikingCommitPolicy

        return OpenVikingCommitPolicy
    if name == "with_openviking_context":
        from openviking.integrations.langchain.context import with_openviking_context

        return with_openviking_context
    if name == "OpenVikingContextMiddleware":
        from openviking.integrations.langchain.middleware import OpenVikingContextMiddleware

        return OpenVikingContextMiddleware
    if name == "InMemoryOpenVikingClient":
        from openviking.integrations.langchain.testing import InMemoryOpenVikingClient

        return InMemoryOpenVikingClient
    raise AttributeError(name)

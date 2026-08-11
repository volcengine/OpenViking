# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Compatibility imports for the standalone ``langchain-openviking`` package."""

from __future__ import annotations

from importlib import import_module
from typing import Any

_LEGACY_EXPORTS = [
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
    "has_request_actor_peer_support",
    "with_openviking_context",
]


def _missing_standalone_error() -> ImportError:
    return ImportError(
        "The legacy OpenViking LangChain integration requires the standalone "
        "langchain-openviking package. Install it with "
        "`pip install langchain-openviking` (or "
        '`pip install "langchain-openviking[langgraph]"` for LangGraph support).'
    )


def _forward_legacy_module(module_name: str, namespace: dict[str, Any]) -> None:
    """Populate a legacy submodule with the canonical package's public names."""

    try:
        canonical = import_module(f"langchain_openviking.{module_name}")
    except ModuleNotFoundError as exc:
        if exc.name != "langchain_openviking":
            raise
        raise _missing_standalone_error() from exc

    public_names = getattr(canonical, "__all__", None)
    if public_names is None:
        public_names = (name for name in vars(canonical) if not name.startswith("_"))
    namespace.update({name: getattr(canonical, name) for name in public_names})


try:
    from langchain_openviking import __all__, __getattr__, has_request_actor_peer_support
except ModuleNotFoundError as exc:
    if exc.name != "langchain_openviking":
        raise

    __all__ = _LEGACY_EXPORTS

    def has_request_actor_peer_support() -> bool:
        """Return whether the installed SDK supports request-scoped actor peers."""

        openviking_sdk = import_module("openviking_sdk")
        return (
            getattr(openviking_sdk, "get_actor_peer_id", None) is not None
            and getattr(openviking_sdk, "use_actor_peer", None) is not None
        )

    def __getattr__(name: str) -> Any:
        if name in __all__:
            raise _missing_standalone_error()
        raise AttributeError(name)

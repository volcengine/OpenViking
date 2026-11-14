# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Extraction tool dispatch must be scoped to the advertised set (#6922).

The memory-tool registry is process-global, and the extraction VLM invents
tool names from in-context vocabulary (the calling agent's own tools, schema
field names). Dispatching any registered name — or erroring on any
unregistered one without scope — burned a ReAct iteration per event and, in
the wrong-signature case, nearly read an arbitrary host path.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from openviking.session.memory.session_extract_context_provider import SessionExtractContextProvider


@pytest.fixture(autouse=True)
def _no_config(monkeypatch: pytest.MonkeyPatch):
    """The provider reads eager_prefetch from the app config in __init__; the
    local test host has no OpenViking config file, so patch the module-level
    name the provider bound at import time and drive the flag directly."""
    monkeypatch.setattr(
        "openviking.session.memory.session_extract_context_provider.get_openviking_config",
        lambda: SimpleNamespace(memory=None),
    )
    monkeypatch.setattr(
        "openviking_cli.utils.config.get_openviking_config",
        lambda: SimpleNamespace(memory=None),
    )
    monkeypatch.setattr(
        "openviking.session.memory.utils.language.get_openviking_config",
        lambda: SimpleNamespace(memory=None),
    )


def _provider(eager_prefetch: bool) -> SessionExtractContextProvider:
    provider = SessionExtractContextProvider(messages=[])
    provider._eager_prefetch = eager_prefetch
    return provider


def _tool_call(name: str) -> SimpleNamespace:
    return SimpleNamespace(name=name, arguments={})


@pytest.mark.asyncio
async def test_prefetch_mode_rejects_invented_tool_without_registry_lookup():
    """Under eager_prefetch nothing is advertised: an invented name is rejected
    before the global registry is consulted."""
    provider = _provider(eager_prefetch=True)

    with patch(
        "openviking.session.memory.session_extract_context_provider.get_tool",
        side_effect=AssertionError("registry must not be consulted for a non-advertised tool"),
    ) as lookup:
        result = await provider.execute_tool(_tool_call("search_memories"))

    lookup.assert_not_called()
    assert "Protocol violation" in result["error"]
    assert "search_memories" in result["error"]
    assert "none" in result["error"]  # the advertised set is empty here


@pytest.mark.asyncio
async def test_registered_but_unadvertised_tool_is_rejected():
    """A real registry tool the phase did not advertise (``read`` in prefetch
    mode) is refused with the valid-names error — this closes the
    wrong-signature host-path case from the report."""
    provider = _provider(eager_prefetch=True)

    result = await provider.execute_tool(_tool_call("read"))

    assert "Protocol violation" in result["error"]
    assert "read" in result["error"] or "none" in result["error"]


@pytest.mark.asyncio
async def test_advertised_tool_still_dispatches():
    """With prefetch disabled the phase advertises ``read``; a call to it must
    reach the registry and execute as before."""
    provider = _provider(eager_prefetch=False)

    tool = SimpleNamespace(execute=AsyncMock(return_value={"ok": True}))
    with patch(
        "openviking.session.memory.session_extract_context_provider.get_tool",
        return_value=tool,
    ) as lookup:
        result = await provider.execute_tool(_tool_call("read"))

    lookup.assert_called_once_with("read")
    tool.execute.assert_awaited_once()
    assert result == {"ok": True}


@pytest.mark.asyncio
async def test_unknown_tool_in_advertised_mode_keeps_legacy_error():
    """A name outside both the advertised set and the registry in a tool-offering
    phase gets the scoped protocol error naming the valid tools."""
    provider = _provider(eager_prefetch=False)

    result = await provider.execute_tool(_tool_call("memory_search"))

    assert "Protocol violation" in result["error"]
    assert "read" in result["error"]  # the advertised set is listed

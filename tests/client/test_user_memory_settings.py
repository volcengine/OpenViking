# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from openviking.async_client import AsyncOpenViking
from openviking.client.local import LocalClient
from openviking.sync_client import SyncOpenViking


@pytest.mark.asyncio
async def test_local_client_exposes_memory_settings():
    client = LocalClient.__new__(LocalClient)
    client._ctx = object()
    client._service = SimpleNamespace(
        sessions=SimpleNamespace(
            get_memory_settings=AsyncMock(
                return_value={
                    "override": {"agent_evolution_enabled": None},
                    "effective": {"agent_evolution_enabled": False},
                }
            ),
            patch_memory_settings=AsyncMock(
                return_value={
                    "override": {"agent_evolution_enabled": True},
                    "effective": {"agent_evolution_enabled": True},
                }
            ),
        )
    )

    settings = await client.get_memory_settings()
    updated = await client.patch_memory_settings(agent_evolution_enabled=True)

    assert settings["effective"]["agent_evolution_enabled"] is False
    assert updated["effective"]["agent_evolution_enabled"] is True
    client._service.sessions.get_memory_settings.assert_awaited_once_with(client._ctx)
    client._service.sessions.patch_memory_settings.assert_awaited_once_with(
        client._ctx,
        agent_evolution_enabled=True,
    )


@pytest.mark.asyncio
async def test_async_embedded_client_forwards_memory_settings():
    client = object.__new__(AsyncOpenViking)
    client._initialized = True
    client._client = SimpleNamespace(
        get_memory_settings=AsyncMock(return_value={"effective": {}}),
        patch_memory_settings=AsyncMock(return_value={"effective": {}}),
    )

    await client.get_memory_settings()
    await client.patch_memory_settings(agent_evolution_enabled=True)

    client._client.get_memory_settings.assert_awaited_once_with()
    client._client.patch_memory_settings.assert_awaited_once_with(agent_evolution_enabled=True)


def test_sync_embedded_client_forwards_memory_settings(monkeypatch):
    client = SyncOpenViking.__new__(SyncOpenViking)
    client._async_client = SimpleNamespace(
        get_memory_settings=Mock(return_value=object()),
        patch_memory_settings=Mock(return_value=object()),
    )
    monkeypatch.setattr("openviking.sync_client.run_async", lambda value: value)

    client.get_memory_settings()
    client.patch_memory_settings(agent_evolution_enabled=True)

    client._async_client.get_memory_settings.assert_called_once_with()
    client._async_client.patch_memory_settings.assert_called_once_with(agent_evolution_enabled=True)

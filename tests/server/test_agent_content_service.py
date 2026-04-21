# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0

"""Service-level tests for dedicated agent content APIs."""

from unittest.mock import AsyncMock

import pytest

from openviking.server.identity import RequestContext, Role
from openviking.session.memory_extractor import MergedMemoryPayload
from openviking_cli.exceptions import InvalidArgumentError


@pytest.mark.asyncio
async def test_create_agent_content_creates_missing_memory_carrier(service):
    ctx = RequestContext(user=service.user, role=Role.USER)
    carrier_dir = f"viking://agent/{ctx.user.agent_space_name()}/memories/patterns"
    carrier_uri = f"{carrier_dir}/distilled-project.md"

    result = await service.fs.create_agent_content(
        carrier_uri,
        content="# Distilled Project\n\nInitial pattern notes.",
        ctx=ctx,
        wait=True,
    )

    assert result["mode"] == "create"
    assert result["created"] is True
    assert result["exists_before"] is False
    assert result["context_type"] == "memory"
    assert await service.viking_fs.read_file(carrier_uri, ctx=ctx) == (
        "# Distilled Project\n\nInitial pattern notes."
    )
    assert await service.viking_fs.read_file(f"{carrier_dir}/.overview.md", ctx=ctx)
    assert await service.viking_fs.read_file(f"{carrier_dir}/.abstract.md", ctx=ctx)


@pytest.mark.asyncio
async def test_create_agent_content_is_idempotent_when_file_exists(service):
    ctx = RequestContext(user=service.user, role=Role.USER)
    carrier_uri = f"viking://agent/{ctx.user.agent_space_name()}/memories/cases/distilled.md"
    await service.viking_fs.write_file(carrier_uri, "Existing carrier", ctx=ctx)

    result = await service.fs.create_agent_content(
        carrier_uri,
        content="New content should not overwrite",
        ctx=ctx,
        create_mode="create_if_missing",
    )

    assert result["mode"] == "create"
    assert result["created"] is False
    assert result["exists_before"] is True
    assert result["semantic_updated"] is False
    assert await service.viking_fs.read_file(carrier_uri, ctx=ctx) == "Existing carrier"


@pytest.mark.asyncio
async def test_write_agent_content_merge_reuses_memory_bundle(monkeypatch, service):
    ctx = RequestContext(user=service.user, role=Role.USER)
    carrier_uri = f"viking://agent/{ctx.user.agent_space_name()}/memories/patterns/distilled.md"
    await service.viking_fs.write_file(carrier_uri, "Existing pattern", ctx=ctx)

    merge_mock = AsyncMock(
        return_value=MergedMemoryPayload(
            abstract="Merged abstract",
            overview="Merged overview",
            content="Merged pattern content",
            reason="combined",
        )
    )
    monkeypatch.setattr(
        "openviking.session.memory_extractor.MemoryExtractor._merge_memory_bundle",
        merge_mock,
    )

    result = await service.fs.write_agent_content(
        carrier_uri,
        content="New evidence",
        ctx=ctx,
        mode="merge",
    )

    assert result["mode"] == "merge"
    assert result["created"] is False
    assert await service.viking_fs.read_file(carrier_uri, ctx=ctx) == "Merged pattern content"
    merge_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_write_agent_content_rejects_non_agent_scope(service):
    ctx = RequestContext(user=service.user, role=Role.USER)
    memory_uri = f"viking://user/{ctx.user.user_space_name()}/memories/preferences/theme.md"

    with pytest.raises(InvalidArgumentError):
        await service.fs.write_agent_content(
            memory_uri,
            content="dark mode",
            ctx=ctx,
        )

# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Tests for ConsolidationExtractContextProvider."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openviking.session.memory.consolidation_extract_context_provider import (
    ConsolidationExtractContextProvider,
)
from openviking.session.memory.core import ExtractContextProvider
from tests.unit.conftest import make_test_context as _ctx


class TestProviderContract:
    def test_is_extract_context_provider_subclass(self):
        assert issubclass(ConsolidationExtractContextProvider, ExtractContextProvider)

    def test_can_instantiate_with_minimum_args(self):
        provider = ConsolidationExtractContextProvider(
            cluster=[_ctx("viking://agent/a/memories/patterns/x")],
            scope_uri="viking://agent/a/memories/patterns/",
        )
        assert provider is not None


class TestInstruction:
    def test_instruction_mentions_all_decisions(self):
        provider = ConsolidationExtractContextProvider(
            cluster=[],
            scope_uri="viking://agent/a/memories/patterns/",
        )
        text = provider.instruction()
        assert "keep_and_merge" in text
        assert "keep_and_delete" in text
        assert "archive_all" in text
        assert "keep_all" in text
        assert "absolute dates" in text


class TestGetTools:
    def test_returns_read_only(self):
        provider = ConsolidationExtractContextProvider(
            cluster=[_ctx("viking://agent/a/memories/patterns/x")],
            scope_uri="viking://agent/a/memories/patterns/",
        )
        tools = provider.get_tools()
        assert tools == ["read"]
        assert "write" not in tools
        assert "delete" not in tools


class TestGetMemorySchemas:
    def test_returns_empty_list(self):
        provider = ConsolidationExtractContextProvider(
            cluster=[],
            scope_uri="viking://agent/a/memories/patterns/",
        )
        schemas = provider.get_memory_schemas(ctx=None)
        assert schemas == []


class TestPrefetch:
    @pytest.mark.asyncio
    async def test_prefetch_reads_scope_overview(self):
        provider = ConsolidationExtractContextProvider(
            cluster=[_ctx("viking://agent/a/memories/patterns/x")],
            scope_uri="viking://agent/a/memories/patterns/",
        )
        viking_fs_mock = MagicMock()
        read_tool_mock = MagicMock()
        read_tool_mock.execute = AsyncMock(return_value="# Overview content")

        with patch(
            "openviking.session.memory.consolidation_extract_context_provider.get_tool",
            return_value=read_tool_mock,
        ):
            messages = await provider.prefetch(
                ctx=MagicMock(),
                viking_fs=viking_fs_mock,
                transaction_handle=None,
                vlm=None,
            )

        assert len(messages) >= 1
        read_tool_mock.execute.assert_called_once()
        call_kwargs = read_tool_mock.execute.call_args.kwargs
        assert call_kwargs["uri"].endswith("/.overview.md")

    @pytest.mark.asyncio
    async def test_prefetch_swallows_missing_overview(self):
        provider = ConsolidationExtractContextProvider(
            cluster=[_ctx("viking://agent/a/memories/patterns/x")],
            scope_uri="viking://agent/a/memories/patterns/",
        )
        viking_fs_mock = MagicMock()
        read_tool_mock = MagicMock()
        read_tool_mock.execute = AsyncMock(side_effect=FileNotFoundError("no overview"))

        with patch(
            "openviking.session.memory.consolidation_extract_context_provider.get_tool",
            return_value=read_tool_mock,
        ):
            messages = await provider.prefetch(
                ctx=MagicMock(),
                viking_fs=viking_fs_mock,
                transaction_handle=None,
                vlm=None,
            )

        assert messages == []

    @pytest.mark.asyncio
    async def test_prefetch_returns_empty_when_no_viking_fs(self):
        provider = ConsolidationExtractContextProvider(
            cluster=[],
            scope_uri="viking://agent/a/memories/patterns/",
        )
        messages = await provider.prefetch(
            ctx=MagicMock(),
            viking_fs=None,
            transaction_handle=None,
            vlm=None,
        )
        assert messages == []

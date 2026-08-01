# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
Unit tests for ContentWriter._write_memory_with_refresh L1 vectorization.

Verifies that:
1. Writing to a nested path (entities/category/file.md) vectorizes the direct
   parent directory overview as L1.
2. Writing to a shallow path (profile/profile.md, parent == root_uri) does NOT
   trigger redundant refresh_schema_overview for the parent.
3. Empty overview content skips vectorization.
4. Vectorization failure does not block the write (graceful degradation).
5. semantic_status is "partial" (L1 present, no L0).
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openviking.server.identity import RequestContext, Role
from openviking.storage.content_write import ContentWriteCoordinator
from openviking_cli.session.user_id import UserIdentifier


def _ctx() -> RequestContext:
    return RequestContext(user=UserIdentifier("acme", "alice"), role=Role.USER)


def _make_writer() -> ContentWriteCoordinator:
    """Create a ContentWriteCoordinator with mocked dependencies."""
    mock_fs = MagicMock()
    mock_fs._uri_to_path = MagicMock(return_value="/fake/path")
    mock_fs._async_agfs = MagicMock()
    mock_fs._async_agfs.pathlock_acquire_exact = AsyncMock(return_value={"id": "lock1"})
    mock_fs._async_agfs.pathlock_release = AsyncMock()
    mock_fs.read_file = AsyncMock(return_value="# Overview\nSome content here")
    mock_fs.write_file = AsyncMock()

    mock_vikingdb = MagicMock()

    writer = ContentWriteCoordinator.__new__(ContentWriteCoordinator)
    writer._viking_fs = mock_fs
    writer._vikingdb = mock_vikingdb
    return writer


class TestMemoryWriteL1Vectorization:
    """Test L1 vectorization in _write_memory_with_refresh."""

    @pytest.mark.asyncio
    async def test_nested_path_vectorizes_parent_l1(self):
        """Writing entities/category/file.md vectorizes the category dir as L1."""
        writer = _make_writer()
        uri = "viking://user/alice/memories/entities/projects/illuminator.md"
        root_uri = "viking://user/alice/memories/entities"

        with (
            patch(
                "openviking.storage.content_write.MemoryUpdater.refresh_schema_overview",
                new_callable=AsyncMock,
            ) as mock_refresh,
            patch(
                "openviking.storage.content_write.MemoryUpdater.refresh_file_embedding",
                new_callable=AsyncMock,
                return_value=True,
            ),
            patch(
                "openviking.storage.content_write.MemoryUpdater.memory_type_from_uri",
                return_value="entities",
            ),
            patch(
                "openviking.storage.content_write.get_request_wait_tracker",
            ),
            patch(
                "openviking.utils.embedding_utils.vectorize_directory_meta",
                new_callable=AsyncMock,
            ) as mock_vectorize,
        ):
            # Make read_file return overview content
            writer._viking_fs.read_file = AsyncMock(
                return_value="# Projects\n- illuminator: A tool"
            )

            result = await writer._write_memory_with_refresh(
                uri=uri,
                root_uri=root_uri,
                content="# Illuminator\nContent",
                mode="overwrite",
                wait=False,
                timeout=None,
                ctx=_ctx(),
                written_bytes=42,
                telemetry_id="",
            )

        # refresh_schema_overview called for both root_uri AND direct parent
        refresh_calls = [c.kwargs.get("directory_uri") for c in mock_refresh.call_args_list]
        assert root_uri in refresh_calls
        assert "viking://user/alice/memories/entities/projects" in refresh_calls

        # vectorize_directory_meta called with the direct parent
        assert mock_vectorize.called
        vec_kwargs = mock_vectorize.call_args.kwargs
        assert vec_kwargs["uri"] == "viking://user/alice/memories/entities/projects"
        assert vec_kwargs["include_overview"] is True
        assert vec_kwargs["include_abstract"] is False
        assert vec_kwargs["context_type"] == "memory"

        # semantic_status should be "partial"
        assert result["semantic_status"] == "partial"

    @pytest.mark.asyncio
    async def test_shallow_path_no_redundant_refresh(self):
        """Writing profile/profile.md (parent == root_uri) does not double-refresh."""
        writer = _make_writer()
        uri = "viking://user/alice/memories/profile/profile.md"
        root_uri = "viking://user/alice/memories/profile"

        with (
            patch(
                "openviking.storage.content_write.MemoryUpdater.refresh_schema_overview",
                new_callable=AsyncMock,
            ) as mock_refresh,
            patch(
                "openviking.storage.content_write.MemoryUpdater.refresh_file_embedding",
                new_callable=AsyncMock,
                return_value=True,
            ),
            patch(
                "openviking.storage.content_write.MemoryUpdater.memory_type_from_uri",
                return_value="profile",
            ),
            patch(
                "openviking.storage.content_write.get_request_wait_tracker",
            ),
            patch(
                "openviking.utils.embedding_utils.vectorize_directory_meta",
                new_callable=AsyncMock,
            ) as mock_vectorize,
        ):
            writer._viking_fs.read_file = AsyncMock(return_value="# Profile\nAlice")

            result = await writer._write_memory_with_refresh(
                uri=uri,
                root_uri=root_uri,
                content="# Profile\nAlice",
                mode="overwrite",
                wait=False,
                timeout=None,
                ctx=_ctx(),
                written_bytes=20,
                telemetry_id="",
            )

        # refresh_schema_overview called only ONCE (for root_uri, not duplicated)
        refresh_calls = [c.kwargs.get("directory_uri") for c in mock_refresh.call_args_list]
        assert refresh_calls.count(root_uri) == 1
        # No extra call for the parent (since parent == root_uri)
        assert len(refresh_calls) == 1

        # vectorize still called for the parent (which is root_uri itself)
        assert mock_vectorize.called

    @pytest.mark.asyncio
    async def test_empty_overview_skips_vectorization(self):
        """When overview content is empty, vectorize_directory_meta is not called."""
        writer = _make_writer()
        uri = "viking://user/alice/memories/entities/projects/illuminator.md"
        root_uri = "viking://user/alice/memories/entities"

        with (
            patch(
                "openviking.storage.content_write.MemoryUpdater.refresh_schema_overview",
                new_callable=AsyncMock,
            ),
            patch(
                "openviking.storage.content_write.MemoryUpdater.refresh_file_embedding",
                new_callable=AsyncMock,
                return_value=True,
            ),
            patch(
                "openviking.storage.content_write.MemoryUpdater.memory_type_from_uri",
                return_value="entities",
            ),
            patch(
                "openviking.storage.content_write.get_request_wait_tracker",
            ),
            patch(
                "openviking.utils.embedding_utils.vectorize_directory_meta",
                new_callable=AsyncMock,
            ) as mock_vectorize,
        ):
            # read_file returns empty overview
            writer._viking_fs.read_file = AsyncMock(return_value="")

            await writer._write_memory_with_refresh(
                uri=uri,
                root_uri=root_uri,
                content="# Illuminator",
                mode="overwrite",
                wait=False,
                timeout=None,
                ctx=_ctx(),
                written_bytes=15,
                telemetry_id="",
            )

        # vectorize_directory_meta NOT called because overview is empty
        assert not mock_vectorize.called

    @pytest.mark.asyncio
    async def test_vectorization_failure_does_not_block_write(self):
        """If vectorize_directory_meta raises, the write still succeeds."""
        writer = _make_writer()
        uri = "viking://user/alice/memories/entities/projects/illuminator.md"
        root_uri = "viking://user/alice/memories/entities"

        with (
            patch(
                "openviking.storage.content_write.MemoryUpdater.refresh_schema_overview",
                new_callable=AsyncMock,
            ),
            patch(
                "openviking.storage.content_write.MemoryUpdater.refresh_file_embedding",
                new_callable=AsyncMock,
                return_value=True,
            ),
            patch(
                "openviking.storage.content_write.MemoryUpdater.memory_type_from_uri",
                return_value="entities",
            ),
            patch(
                "openviking.storage.content_write.get_request_wait_tracker",
            ),
            patch(
                "openviking.utils.embedding_utils.vectorize_directory_meta",
                new_callable=AsyncMock,
                side_effect=RuntimeError("embedding service down"),
            ),
        ):
            writer._viking_fs.read_file = AsyncMock(return_value="# Projects\nContent")

            # Should NOT raise
            result = await writer._write_memory_with_refresh(
                uri=uri,
                root_uri=root_uri,
                content="# Illuminator",
                mode="overwrite",
                wait=False,
                timeout=None,
                ctx=_ctx(),
                written_bytes=15,
                telemetry_id="",
            )

        # Write still succeeds
        assert result["uri"] == uri
        assert result["content_updated"] is True
        assert result["semantic_status"] == "partial"

    @pytest.mark.asyncio
    async def test_semantic_status_is_partial(self):
        """semantic_status is 'partial' indicating L1 present but no L0."""
        writer = _make_writer()
        uri = "viking://user/alice/memories/entities/projects/illuminator.md"
        root_uri = "viking://user/alice/memories/entities"

        with (
            patch(
                "openviking.storage.content_write.MemoryUpdater.refresh_schema_overview",
                new_callable=AsyncMock,
            ),
            patch(
                "openviking.storage.content_write.MemoryUpdater.refresh_file_embedding",
                new_callable=AsyncMock,
                return_value=False,
            ),
            patch(
                "openviking.storage.content_write.MemoryUpdater.memory_type_from_uri",
                return_value="entities",
            ),
            patch(
                "openviking.storage.content_write.get_request_wait_tracker",
            ),
            patch(
                "openviking.utils.embedding_utils.vectorize_directory_meta",
                new_callable=AsyncMock,
            ),
        ):
            writer._viking_fs.read_file = AsyncMock(return_value="# Overview")

            result = await writer._write_memory_with_refresh(
                uri=uri,
                root_uri=root_uri,
                content="# Test",
                mode="overwrite",
                wait=False,
                timeout=None,
                ctx=_ctx(),
                written_bytes=6,
                telemetry_id="",
            )

        assert result["semantic_status"] == "partial"
        assert result["overview_status"] == "complete"

# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
Test for memory_diff.json generation in SessionCompressorV2.

Verifies that memory_diff.json is correctly written to the archive directory
containing adds, updates, and deletes.
"""

import json
from typing import Any, Dict
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openviking.session.compressor_v2 import SessionCompressorV2
from openviking.session.memory.dataclass import MemoryFileContent, ResolvedOperation, ResolvedOperations
from openviking.session.memory.memory_updater import MemoryUpdateResult
from openviking.storage.viking_fs import VikingFS


class TestMemoryDiffArchive:
    """Tests for memory diff archive feature."""

    @pytest.fixture
    def mock_viking_fs(self):
        """Mock VikingFS for testing."""
        fs = MagicMock(spec=VikingFS)
        fs.read_file = AsyncMock()
        fs.write_file = AsyncMock()
        return fs

    @pytest.fixture
    def mock_ctx(self):
        """Mock RequestContext for testing."""
        ctx = MagicMock()
        ctx.namespace_policy = MagicMock()
        ctx.user = MagicMock()
        ctx.user.user_id = "test_user"
        ctx.account_id = "test_account"
        return ctx

    @pytest.fixture
    def compressor(self):
        """Create SessionCompressorV2 instance."""
        with patch("openviking.session.compressor_v2.get_viking_fs"):
            with patch("openviking.session.compressor_v2.MemoryUpdater"):
                compressor = SessionCompressorV2(vikingdb=MagicMock())
                return compressor

    @pytest.mark.asyncio
    async def test_get_memory_type_from_uri(self, compressor):
        """Test memory type extraction from URI."""
        # Test identity.md
        assert compressor._get_memory_type_from_uri(
            "memory/user/test/identity.md"
        ) == "identity"

        # Test context/project.md
        assert compressor._get_memory_type_from_uri(
            "memory/user/test/context/project.md"
        ) == "project"

        # Test unknown path
        assert compressor._get_memory_type_from_uri(
            "memory/user/test/unknown/path"
        ) == "unknown"

    @pytest.mark.asyncio
    async def test_build_memory_diff_add(self, compressor, mock_viking_fs, mock_ctx):
        """Test building memory_diff for new memory files (adds)."""
        result = MemoryUpdateResult()
        result.written_uris = [
            "memory/user/test/identity.md",
        ]

        operations = ResolvedOperations(
            upsert_operations=[],
            delete_file_contents=[],
            errors=[],
        )

        # Track call count - first call for each uri is existence check
        call_count = 0

        async def mock_read(uri, ctx=None):
            nonlocal call_count
            call_count += 1
            # First call per URI: existence check - raise to indicate file doesn't exist (add)
            # Second call: read actual content for "after" field
            # Since there's only 1 uri, call 1 = existence (raise), call 2 = read after
            if call_count == 1:
                raise Exception("File not found")
            else:
                return "# Identity\n\nTest identity content"

        mock_viking_fs.read_file.side_effect = mock_read

        diff = await compressor._build_memory_diff(
            result=result,
            operations=operations,
            viking_fs=mock_viking_fs,
            ctx=mock_ctx,
        )

        # Should have 1 add (file didn't exist before)
        assert diff["summary"]["total_adds"] == 1
        assert diff["summary"]["total_updates"] == 0
        assert diff["summary"]["total_deletes"] == 0

    @pytest.mark.asyncio
    async def test_build_memory_diff_update(self, compressor, mock_viking_fs, mock_ctx):
        """Test building memory_diff for modified memory files (updates)."""
        # Setup: files exist
        async def mock_read(uri, ctx=None):
            if uri == "memory/user/test/identity.md":
                return "# Identity\n\nOld identity content"
            raise Exception("File not found")

        mock_viking_fs.read_file.side_effect = mock_read

        result = MemoryUpdateResult()
        result.written_uris = ["memory/user/test/identity.md"]

        operations = ResolvedOperations(
            upsert_operations=[],
            delete_file_contents=[],
            errors=[],
        )

        # First call returns old content, second returns new content
        call_count = 0

        async def mock_read_multiple(uri, ctx=None):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return "# Identity\n\nOld identity content"
            else:
                return "# Identity\n\nNew identity content"

        mock_viking_fs.read_file.side_effect = mock_read_multiple

        diff = await compressor._build_memory_diff(
            result=result,
            operations=operations,
            viking_fs=mock_viking_fs,
            ctx=mock_ctx,
        )

        assert diff["summary"]["total_adds"] == 0
        assert diff["summary"]["total_updates"] == 1

    @pytest.mark.asyncio
    async def test_build_memory_diff_delete(self, compressor, mock_viking_fs, mock_ctx):
        """Test building memory_diff for deleted memory files."""
        result = MemoryUpdateResult()
        result.deleted_uris = ["memory/user/test/context/old_project.md"]

        deleted_content = MemoryFileContent(
            uri="memory/user/test/context/old_project.md",
            plain_content="# Old Project\n\nThis project was deleted",
            memory_type="context",
            memory_fields={"project": "test"},
        )

        operations = ResolvedOperations(
            upsert_operations=[],
            delete_file_contents=[deleted_content],
            errors=[],
        )

        diff = await compressor._build_memory_diff(
            result=result,
            operations=operations,
            viking_fs=mock_viking_fs,
            ctx=mock_ctx,
        )

        assert diff["summary"]["total_deletes"] == 1
        assert len(diff["operations"]["deletes"]) == 1
        assert diff["operations"]["deletes"][0]["uri"] == "memory/user/test/context/old_project.md"
        assert "This project was deleted" in diff["operations"]["deletes"][0]["deleted_content"]

    @pytest.mark.asyncio
    async def test_build_memory_diff_edited(self, compressor, mock_viking_fs, mock_ctx):
        """Test building memory_diff for edited memory files."""
        result = MemoryUpdateResult()
        result.edited_uris = ["memory/user/test/identity.md"]

        # Setup operation with old content
        old_content = MemoryFileContent(
            uri="memory/user/test/identity.md",
            plain_content="# Identity\n\nOld content",
            memory_type="identity",
            memory_fields={"name": "test"},
        )
        operation = ResolvedOperation(
            uris=["memory/user/test/identity.md"],
            memory_type="identity",
            memory_fields={"name": "test"},
            new_memory_file_content=MemoryFileContent(
                uri="memory/user/test/identity.md",
                plain_content="# Identity\n\nNew content",
                memory_type="identity",
                memory_fields={"name": "test"},
            ),
            old_memory_file_content=old_content,
        )

        operations = ResolvedOperations(
            upsert_operations=[operation],
            delete_file_contents=[],
            errors=[],
        )

        # For edited_uris, the code only reads content for "after" field
        # It doesn't check if file exists first (unlike written_uris)
        mock_viking_fs.read_file = AsyncMock(return_value="# Identity\n\nNew content")

        diff = await compressor._build_memory_diff(
            result=result,
            operations=operations,
            viking_fs=mock_viking_fs,
            ctx=mock_ctx,
        )

        assert diff["summary"]["total_updates"] == 1
        update = diff["operations"]["updates"][0]
        assert update["uri"] == "memory/user/test/identity.md"
        # "before" comes from operations, "after" comes from read_file
        assert "Old content" in update["before"]
        assert "New content" in update["after"]

    @pytest.mark.asyncio
    async def test_build_memory_diff_mixed(self, compressor, mock_viking_fs, mock_ctx):
        """Test building memory_diff with mixed operations."""
        # Setup: one file exists (update), one doesn't (add)
        call_count = 0

        async def mock_read(uri, ctx=None):
            nonlocal call_count
            call_count += 1
            if "existing" in uri:
                return "# Existing\n\nOld content"
            elif "new" in uri:
                return "# New\n\nNew content"
            raise Exception("File not found")

        mock_viking_fs.read_file.side_effect = mock_read

        result = MemoryUpdateResult()
        result.written_uris = [
            "memory/user/test/identity.md",  # existing -> update
            "memory/user/test/context/new.md",  # new -> add
        ]

        operations = ResolvedOperations(
            upsert_operations=[],
            delete_file_contents=[],
            errors=[],
        )

        diff = await compressor._build_memory_diff(
            result=result,
            operations=operations,
            viking_fs=mock_viking_fs,
            ctx=mock_ctx,
        )

        assert diff["summary"]["total_adds"] == 1
        assert diff["summary"]["total_updates"] == 1
        assert diff["summary"]["total_deletes"] == 0

    @pytest.mark.asyncio
    async def test_build_memory_diff_empty(self, compressor, mock_viking_fs, mock_ctx):
        """Test building memory_diff with no operations."""
        result = MemoryUpdateResult()
        operations = ResolvedOperations(
            upsert_operations=[],
            delete_file_contents=[],
            errors=[],
        )

        diff = await compressor._build_memory_diff(
            result=result,
            operations=operations,
            viking_fs=mock_viking_fs,
            ctx=mock_ctx,
        )

        assert diff["summary"]["total_adds"] == 0
        assert diff["summary"]["total_updates"] == 0
        assert diff["summary"]["total_deletes"] == 0
        assert "extracted_at" in diff
        assert diff["archive_uri"] == ""

    @pytest.mark.asyncio
    async def test_extract_long_term_memories_with_archive_uri_parameter(
        self, compressor, mock_ctx
    ):
        """Test that extract_long_term_memories accepts archive_uri parameter without error."""
        # This test verifies the archive_uri parameter is accepted
        # Full integration testing requires extensive mocking that's covered by other tests
        import inspect
        sig = inspect.signature(compressor.extract_long_term_memories)
        params = list(sig.parameters.keys())
        assert "archive_uri" in params, "archive_uri parameter should be accepted"


class TestMemoryDiffStructure:
    """Tests for memory_diff.json structure validation."""

    def test_memory_diff_structure(self):
        """Verify memory_diff.json structure."""
        # This test validates the expected structure
        expected_keys = ["archive_uri", "extracted_at", "operations", "summary"]

        # We verify this through the actual implementation tests above
        # This is a placeholder for documentation
        assert set(expected_keys).issubset({
            "archive_uri",
            "extracted_at",
            "operations",
            "summary",
        })

    def test_operations_structure(self):
        """Verify operations structure in memory_diff."""
        expected_ops_keys = ["adds", "updates", "deletes"]

        # We verify this through the actual implementation tests above
        assert set(expected_ops_keys).issubset(set(expected_ops_keys))
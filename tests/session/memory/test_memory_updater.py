# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
Tests for MemoryUpdater.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from openviking.server.identity import AccountNamespacePolicy, RequestContext, Role
from openviking.session.memory.dataclass import MemoryTypeSchema
from openviking.session.memory.memory_type_registry import MemoryTypeRegistry
from openviking.session.memory.memory_updater import (
    MemoryUpdater,
    MemoryUpdateResult,
)
from openviking.session.memory.merge_op import (
    SearchReplaceBlock,
    StrPatch,
)
from openviking.session.memory.utils import (
    ResolvedOperation,
    ResolvedOperations,
    deserialize_full,
    serialize_with_metadata,
)
from openviking_cli.session.user_id import UserIdentifier


class TestMemoryUpdateResult:
    """Tests for MemoryUpdateResult."""

    def test_create_empty(self):
        """Test creating an empty result."""
        result = MemoryUpdateResult()

        assert len(result.written_uris) == 0
        assert len(result.edited_uris) == 0
        assert len(result.deleted_uris) == 0
        assert len(result.errors) == 0
        assert result.has_changes() is False

    def test_add_written(self):
        """Test adding written URI."""
        result = MemoryUpdateResult()
        result.add_written("viking://user/test/memories/profile.md")

        assert len(result.written_uris) == 1
        assert result.has_changes() is True

    def test_add_edited(self):
        """Test adding edited URI."""
        result = MemoryUpdateResult()
        result.add_edited("viking://user/test/memories/profile.md")

        assert len(result.edited_uris) == 1
        assert result.has_changes() is True

    def test_add_deleted(self):
        """Test adding deleted URI."""
        result = MemoryUpdateResult()
        result.add_deleted("viking://user/test/memories/to_delete.md")

        assert len(result.deleted_uris) == 1
        assert result.has_changes() is True

    def test_summary(self):
        """Test summary generation."""
        result = MemoryUpdateResult()
        result.add_written("uri1")
        result.add_edited("uri2")
        result.add_deleted("uri3")

        summary = result.summary()
        assert "Written: 1" in summary
        assert "Edited: 1" in summary
        assert "Deleted: 1" in summary
        assert "Errors: 0" in summary


class TestMemoryUpdater:
    """Tests for MemoryUpdater."""

    def test_create(self):
        """Test creating a MemoryUpdater."""
        updater = MemoryUpdater()

        assert updater is not None
        assert updater._viking_fs is None
        assert updater._registry is None

    def test_create_with_registry(self):
        """Test creating a MemoryUpdater with registry."""
        registry = MemoryTypeRegistry()
        updater = MemoryUpdater(registry)

        assert updater._registry == registry

    def test_set_registry(self):
        """Test setting registry after creation."""
        updater = MemoryUpdater()
        registry = MemoryTypeRegistry()

        updater.set_registry(registry)

        assert updater._registry == registry

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("policy", "schema_directory", "resolved_uri", "expected_directory", "memory_type"),
        [
            (
                AccountNamespacePolicy(
                    isolate_user_scope_by_agent=True,
                    isolate_agent_scope_by_user=False,
                ),
                "viking://user/{{ user_space }}/memories/preferences",
                "viking://user/alice/agent/bot/memories/preferences/theme.md",
                "viking://user/alice/agent/bot/memories/preferences",
                "preferences",
            ),
            (
                AccountNamespacePolicy(
                    isolate_user_scope_by_agent=False,
                    isolate_agent_scope_by_user=True,
                ),
                "viking://agent/{{ agent_space }}/memories/tools",
                "viking://agent/bot/user/alice/memories/tools/web_search.md",
                "viking://agent/bot/user/alice/memories/tools",
                "tools",
            ),
        ],
    )
    async def test_apply_operations_matches_overview_directories_with_namespace_policy(
        self,
        monkeypatch,
        policy,
        schema_directory,
        resolved_uri,
        expected_directory,
        memory_type,
    ):
        """Overview generation should use policy-expanded user/agent space fragments."""
        schema = MemoryTypeSchema(
            memory_type=memory_type,
            description=f"{memory_type} memory",
            directory=schema_directory,
            filename_template="{{ name }}.md",
            fields=[],
            overview_template="overview",
        )
        registry = MagicMock()
        registry.list_all.return_value = [schema]

        updater = MemoryUpdater(registry=registry)
        updater._get_viking_fs = MagicMock(return_value=MagicMock())
        updater._apply_upsert = AsyncMock(return_value=False)
        updater._vectorize_memories = AsyncMock()
        updater.generate_overview = AsyncMock()

        resolved = ResolvedOperations()
        resolved.operations.append(
            ResolvedOperation(
                model={"name": "demo"},
                uri=resolved_uri,
                memory_type=memory_type,
            )
        )
        monkeypatch.setattr(
            "openviking.session.memory.memory_updater.resolve_all_operations",
            lambda *args, **kwargs: resolved,
        )

        ctx = RequestContext(
            user=UserIdentifier("acme", "alice", "bot"),
            role=Role.USER,
            namespace_policy=policy,
        )

        result = await updater.apply_operations(operations=SimpleNamespace(), ctx=ctx)

        assert result.written_uris == [resolved_uri]
        updater.generate_overview.assert_awaited_once_with(
            memory_type,
            expected_directory,
            ctx,
            None,
        )


# The TestApplyWriteWithContentInFields tests are outdated because WriteOp no longer exists
# The _apply_write method now accepts any flat model (dict or Pydantic model) that
# can be converted with flat_model_to_dict(). Since the main issue we're fixing is
# the StrPatch handling in _apply_edit, we'll keep the focus on that.


class TestApplyEditWithSearchReplacePatch:
    """Tests for _apply_edit with SEARCH/REPLACE patches."""

    @pytest.mark.asyncio
    async def test_apply_edit_with_str_patch_instance(self):
        """Test _apply_edit with StrPatch instance."""
        updater = MemoryUpdater()

        # Original content
        original_content = """Line 1
Line 2
Line 3
Line 4"""
        original_metadata = {"name": "test"}
        original_metadata_with_content = {**original_metadata, "content": original_content}
        original_full_content = serialize_with_metadata(original_metadata_with_content)

        # Mock VikingFS
        mock_viking_fs = MagicMock()
        mock_viking_fs.read_file = AsyncMock(return_value=original_full_content)
        written_content = None

        async def mock_write_file(uri, content, **kwargs):
            nonlocal written_content
            written_content = content

        mock_viking_fs.write_file = mock_write_file
        updater._get_viking_fs = MagicMock(return_value=mock_viking_fs)

        # Create StrPatch
        patch = StrPatch(
            blocks=[
                SearchReplaceBlock(
                    search="Line 2\nLine 3",
                    replace="Line 2 modified\nLine 3 modified",
                )
            ]
        )

        # Mock request context
        mock_ctx = MagicMock()

        # Apply edit
        await updater._apply_upsert({"content": patch}, "viking://test/test.md", mock_ctx)

        # Verify
        assert written_content is not None
        result = deserialize_full(written_content)
        assert "Line 1" in result.plain_content
        assert "Line 2 modified" in result.plain_content
        assert "Line 3 modified" in result.plain_content
        assert "Line 4" in result.plain_content

    @pytest.mark.asyncio
    async def test_apply_edit_with_str_patch_dict(self):
        """Test _apply_edit with StrPatch in dict form (from JSON parsing)."""
        updater = MemoryUpdater()

        # Original content
        original_content = """Hello world
This is a test
Goodbye"""
        original_metadata = {"name": "test"}
        original_metadata_with_content = {**original_metadata, "content": original_content}
        original_full_content = serialize_with_metadata(original_metadata_with_content)

        # Mock VikingFS
        mock_viking_fs = MagicMock()
        mock_viking_fs.read_file = AsyncMock(return_value=original_full_content)
        written_content = None

        async def mock_write_file(uri, content, **kwargs):
            nonlocal written_content
            written_content = content

        mock_viking_fs.write_file = mock_write_file
        updater._get_viking_fs = MagicMock(return_value=mock_viking_fs)

        # StrPatch as dict (this is what JSON parsing gives us)
        patch_dict = {"blocks": [{"search": "This is a test", "replace": "This has been modified"}]}

        # Mock request context
        mock_ctx = MagicMock()

        # Apply edit
        await updater._apply_upsert({"content": patch_dict}, "viking://test/test.md", mock_ctx)

        # Verify
        assert written_content is not None
        result = deserialize_full(written_content)
        assert "Hello world" in result.plain_content
        assert "This has been modified" in result.plain_content
        assert "Goodbye" in result.plain_content

    @pytest.mark.asyncio
    async def test_apply_edit_with_simple_string_replacement(self):
        """Test _apply_edit with simple string full replacement."""
        updater = MemoryUpdater()

        # Original content
        original_content = "Old content"
        original_metadata = {"name": "test"}
        original_metadata_with_content = {**original_metadata, "content": original_content}
        original_full_content = serialize_with_metadata(original_metadata_with_content)

        # Mock VikingFS
        mock_viking_fs = MagicMock()
        mock_viking_fs.read_file = AsyncMock(return_value=original_full_content)
        written_content = None

        async def mock_write_file(uri, content, **kwargs):
            nonlocal written_content
            written_content = content

        mock_viking_fs.write_file = mock_write_file
        updater._get_viking_fs = MagicMock(return_value=mock_viking_fs)

        # Simple string replacement
        new_content = "Completely new content"

        # Mock request context
        mock_ctx = MagicMock()

        # Apply edit
        await updater._apply_upsert({"content": new_content}, "viking://test/test.md", mock_ctx)

        # Verify
        assert written_content is not None
        result = deserialize_full(written_content)
        assert result.plain_content == new_content

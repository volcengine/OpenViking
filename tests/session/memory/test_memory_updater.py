# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
Tests for MemoryUpdater.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from openviking.message import Message
from openviking.message.part import TextPart
from openviking.server.identity import AccountNamespacePolicy, RequestContext, Role
from openviking.session.memory.dataclass import (
    MemoryFile,
    MemoryField,
    MemoryTypeSchema,
    ResolvedOperation,
    ResolvedOperations,
    StoredLink,
)
from openviking.session.memory.memory_type_registry import MemoryTypeRegistry
from openviking.session.memory.memory_updater import (
    ExtractContext,
    MemoryUpdater,
    MemoryUpdateResult,
)
from openviking.session.memory.merge_op import (
    FieldType,
    MergeOp,
    SearchReplaceBlock,
    StrPatch,
)
from openviking.session.memory.utils import (
    MemoryFileUtils,
    parse_memory_file_with_fields,
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

    def test_extract_context_initializes_page_id_map(self):
        extract_context = ExtractContext(
            messages=[Message(id="1", role="user", parts=[TextPart(text="hi")])]
        )

        assert extract_context.page_id_map is not None
        page_id = extract_context.page_id_map.get_page_id("viking://user/a/memories/profile.md")
        assert page_id == 1

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
    async def test_apply_operations_preserves_pre_resolved_multi_uris_for_new_page_ids(self):
        registry = MagicMock()
        registry.get.return_value = MemoryTypeSchema(
            memory_type="entities",
            description="entity memory",
            directory="viking://user/{{ user_space }}/memories/entities",
            filename_template="{{ name }}.md",
            fields=[],
        )

        updater = MemoryUpdater(registry=registry)
        updater._get_viking_fs = MagicMock(return_value=MagicMock())
        updater._apply_upsert = AsyncMock(return_value=None)
        updater._vectorize_memories = AsyncMock()
        updater.generate_overview = AsyncMock()

        alice_uri = "viking://user/alice/memories/entities/SharedFact.md"
        bob_uri = "viking://user/bob/memories/entities/SharedFact.md"
        operation = ResolvedOperation(
            memory_fields={"name": "SharedFact", "content": "shared content"},
            memory_type="entities",
            uris=[alice_uri, bob_uri],
            page_id=100,
        )
        operations = ResolvedOperations(
            upsert_operations=[operation],
            delete_file_contents=[],
            errors=[],
        )

        extract_context = ExtractContext([])
        extract_context.page_id_map.register_new_page_id(alice_uri, 100)
        extract_context.page_id_map.register_new_page_id(bob_uri, 100)
        isolation_handler = MagicMock()

        ctx = RequestContext(user=UserIdentifier("acme", "alice", "bot"), role=Role.USER)

        result = await updater.apply_operations(
            operations=operations,
            ctx=ctx,
            extract_context=extract_context,
            isolation_handler=isolation_handler,
        )

        assert set(operation.uris) == {alice_uri, bob_uri}
        assert set(result.written_uris) == {alice_uri, bob_uri}
        isolation_handler.calculate_memory_uris.assert_not_called()

    @pytest.mark.asyncio
    async def test_apply_operations_requires_pre_resolved_uris(self):
        registry = MagicMock()
        registry.get.return_value = MemoryTypeSchema(
            memory_type="entities",
            description="entity memory",
            directory="viking://user/{{ user_space }}/memories/entities",
            filename_template="{{ name }}.md",
            fields=[],
        )

        updater = MemoryUpdater(registry=registry)
        updater._get_viking_fs = MagicMock(return_value=MagicMock())

        operations = ResolvedOperations(
            upsert_operations=[
                ResolvedOperation(
                    memory_fields={"name": "SharedFact", "content": "shared content"},
                    memory_type="entities",
                    uris=[],
                    page_id=100,
                )
            ],
            delete_file_contents=[],
            errors=[],
        )
        ctx = RequestContext(user=UserIdentifier("acme", "alice", "bot"), role=Role.USER)

        with pytest.raises(ValueError, match="missing resolved URIs"):
            await updater.apply_operations(operations=operations, ctx=ctx)

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

        resolved = ResolvedOperations(
            upsert_operations=[
                ResolvedOperation(
                    memory_fields={"name": "demo"},
                    memory_type=memory_type,
                    uris=[resolved_uri],
                )
            ],
            delete_file_contents=[],
            errors=[],
        )

        ctx = RequestContext(
            user=UserIdentifier("acme", "alice", "bot"),
            role=Role.USER,
            namespace_policy=policy,
        )

        result = await updater.apply_operations(operations=resolved, ctx=ctx)

        assert result.written_uris == [resolved_uri]
        updater.generate_overview.assert_awaited_once_with(
            memory_type,
            expected_directory,
            ctx,
            None,
        )
    @pytest.mark.asyncio
    async def test_apply_operations_skips_link_updates_for_deleted_uris(self, monkeypatch):
        deleted_uri = "viking://agent/agent_sample_3/memories/experiences/old.md"
        written_uri = "viking://agent/agent_sample_3/memories/experiences/new.md"

        schema = MemoryTypeSchema(
            memory_type="experiences",
            description="experience memory",
            directory="viking://agent/{{ agent_space }}/memories/experiences",
            filename_template="{{ experience_name }}.md",
            fields=[],
            overview_template="overview",
        )
        registry = MagicMock()
        registry.get.return_value = schema

        updater = MemoryUpdater(registry=registry)
        updater._vectorize_memories = AsyncMock()
        updater.generate_overview = AsyncMock()

        mock_viking_fs = MagicMock()
        mock_viking_fs.read_file = AsyncMock(side_effect=AssertionError("deleted URI should not be read"))
        mock_viking_fs.write_file = AsyncMock()
        updater._get_viking_fs = MagicMock(return_value=mock_viking_fs)

        resolved = ResolvedOperations(
            upsert_operations=[
                ResolvedOperation(
                    memory_fields={"experience_name": "new"},
                    memory_type="experiences",
                    uris=[written_uri],
                )
            ],
            delete_file_contents=[MemoryFile(uri=deleted_uri, extra_fields={"memory_type": "experiences"})],
            errors=[],
            resolved_links=[
                StoredLink(
                    from_uri=deleted_uri,
                    to_uri=written_uri,
                )
            ],
        )


        async def mock_apply_upsert(resolved_op, ctx, extract_context=None):
            return None

        async def mock_apply_delete(uri, ctx):
            assert uri == deleted_uri

        updater._apply_upsert = AsyncMock(side_effect=mock_apply_upsert)
        updater._apply_delete = AsyncMock(side_effect=mock_apply_delete)

        ctx = RequestContext(user=UserIdentifier("acme", "alice", "bot"), role=Role.USER)

        result = await updater.apply_operations(operations=resolved, ctx=ctx)

        assert result.written_uris == [written_uri]
        assert result.deleted_uris == [deleted_uri]
        mock_viking_fs.read_file.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_apply_operations_routes_backlinks_to_matching_uri_only(self):
        caroline_uri = "viking://user/Caroline/memories/events/2023/05/08/career_education_planning.md"
        melanie_uri = "viking://user/Melanie/memories/events/2023/05/08/career_education_planning.md"
        profile_uri = "viking://user/Caroline/memories/profile.md"

        schema = MemoryTypeSchema(
            memory_type="events",
            description="event memory",
            directory="viking://user/{{ user_space }}/memories/events/{{ year }}/{{ month }}/{{ day }}",
            filename_template="{{ event_name }}.md",
            fields=[],
            overview_template="overview",
        )
        registry = MagicMock()
        registry.get.return_value = schema

        store = {}
        mock_viking_fs = MagicMock()

        async def mock_read_file(uri, **kwargs):
            return store.get(uri)

        async def mock_write_file(uri, content, **kwargs):
            store[uri] = content

        mock_viking_fs.read_file = mock_read_file
        mock_viking_fs.write_file = mock_write_file

        updater = MemoryUpdater(registry=registry)
        updater._get_viking_fs = MagicMock(return_value=mock_viking_fs)
        updater._vectorize_memories = AsyncMock()
        updater.generate_overview = AsyncMock()

        operations = ResolvedOperations(
            upsert_operations=[
                ResolvedOperation(
                    memory_fields={
                        "event_name": "career_education_planning",
                        "content": "Career planning conversation",
                    },
                    memory_type="events",
                    uris=[caroline_uri, melanie_uri],
                    page_id=101,
                )
            ],
            delete_file_contents=[],
            errors=[],
            resolved_links=[
                StoredLink(
                    from_uri=profile_uri,
                    to_uri=caroline_uri,
                    match_text="career",
                    description="Caroline's profile references her career plans",
                )
            ],
        )

        ctx = RequestContext(user=UserIdentifier("acme", "alice", "bot"), role=Role.USER)

        await updater.apply_operations(operations=operations, ctx=ctx)

        caroline = parse_memory_file_with_fields(store[caroline_uri])
        melanie = parse_memory_file_with_fields(store[melanie_uri])

        assert [link["to_uri"] for link in caroline["backlinks"]] == [caroline_uri]
        assert melanie.get("backlinks", []) == []


# The TestApplyWriteWithContentInFields tests are outdated because WriteOp no longer exists
# The _apply_write method now accepts any flat model (dict or Pydantic model) that
# can be converted with flat_model_to_dict(). Since the main issue we're fixing is
# the StrPatch handling in _apply_edit, we'll keep the focus on that.


class TestApplyEditWithSearchReplacePatch:
    """Tests for _apply_edit with SEARCH/REPLACE patches."""

    def _make_updater_with_registry(self):
        content_field = MemoryField(
            name="content",
            field_type=FieldType.STRING,
            merge_op=MergeOp.PATCH,
        )
        schema = MemoryTypeSchema(
            memory_type="test",
            description="test",
            fields=[content_field],
        )
        registry = MemoryTypeRegistry()
        registry.register(schema)
        return MemoryUpdater(registry=registry)

    @pytest.mark.asyncio
    async def test_apply_edit_with_str_patch_instance(self):
        """Test _apply_edit with StrPatch instance."""
        updater = self._make_updater_with_registry()

        # Original content
        original_content = """Line 1
Line 2
Line 3
Line 4"""
        original_mf = MemoryFile(content=original_content, extra_fields={"name": "test"})
        original_full_content = MemoryFileUtils.write(original_mf)

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
        op = ResolvedOperation(
            memory_fields={"content": patch},
            memory_type="test",
            uris=["viking://test/test.md"],
        )
        await updater._apply_upsert(op, mock_ctx)

        # Verify
        assert written_content is not None
        result = MemoryFileUtils.read(written_content)
        assert "Line 1" in result.content
        assert "Line 2 modified" in result.content
        assert "Line 3 modified" in result.content
        assert "Line 4" in result.content

    @pytest.mark.asyncio
    async def test_apply_edit_with_str_patch_dict(self):
        """Test _apply_edit with StrPatch in dict form (from JSON parsing)."""
        updater = self._make_updater_with_registry()

        # Original content
        original_content = """Hello world
This is a test
Goodbye"""
        original_mf = MemoryFile(content=original_content, extra_fields={"name": "test"})
        original_full_content = MemoryFileUtils.write(original_mf)

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
        op = ResolvedOperation(
            memory_fields={"content": patch_dict},
            memory_type="test",
            uris=["viking://test/test.md"],
        )
        await updater._apply_upsert(op, mock_ctx)

        # Verify
        assert written_content is not None
        result = MemoryFileUtils.read(written_content)
        assert "Hello world" in result.content
        assert "This has been modified" in result.content
        assert "Goodbye" in result.content

    @pytest.mark.asyncio
    async def test_apply_edit_with_stripped_search_content_against_linked_storage(self):
        """Patch content should match the stripped read-tool view, not raw markdown links."""
        updater = self._make_updater_with_registry()

        uri = "viking://test/test.md"
        original_full_content = (
            "# [John](entities/fitness/beginner-yoga.md)\n"
            "- [爱好](entities/hobbies/reading.md)：游戏开发、音乐演奏、公益活动\n\n"
            "<!-- MEMORY_FIELDS\n"
            '{"memory_type": "test", "name": "test", "links": ['
            '{"from_uri": "viking://test/test.md", "to_uri": "viking://test/entities/fitness/beginner-yoga.md", "match_text": "John"}, '
            '{"from_uri": "viking://test/test.md", "to_uri": "viking://test/entities/hobbies/reading.md", "match_text": "爱好"}'
            "]}\n"
            "-->"
        )

        mock_viking_fs = MagicMock()
        mock_viking_fs.read_file = AsyncMock(return_value=original_full_content)
        written_content = None

        async def mock_write_file(uri, content, **kwargs):
            nonlocal written_content
            written_content = content

        mock_viking_fs.write_file = mock_write_file
        updater._get_viking_fs = MagicMock(return_value=mock_viking_fs)

        patch = StrPatch(
            blocks=[
                SearchReplaceBlock(
                    search="# John\n- 爱好：游戏开发、音乐演奏、公益活动",
                    replace="# John\n- 爱好：游戏开发、音乐演奏、公益活动\n- 近期动态：加入志愿者队伍",
                )
            ]
        )

        mock_ctx = MagicMock()
        op = ResolvedOperation(
            memory_fields={"content": patch},
            memory_type="test",
            uris=[uri],
        )

        await updater._apply_upsert(op, mock_ctx)

        assert written_content is not None
        result = MemoryFileUtils.read(written_content)
        assert "近期动态：加入志愿者队伍" in result.plain_content()


class TestConsecutivePatchesSameURI:
    """Regression test: consecutive patches to the same URI in one batch
    must see each other's changes (write-before-read)."""

    @pytest.mark.asyncio
    async def test_two_upserts_same_uri_second_sees_first_write(self):
        """Two _apply_upsert calls on the same URI.

        The second upsert must read the content written by the first from disk,
        not the stale old_memory_file_content from before the batch started.
        """
        uri = "viking://user/test/memories/notes.md"
        memory_type = "notes"

        content_field = MemoryField(
            name="content",
            field_type=FieldType.STRING,
            merge_op=MergeOp.PATCH,
        )
        schema = MemoryTypeSchema(
            memory_type=memory_type,
            description="notes",
            fields=[content_field],
        )
        registry = MemoryTypeRegistry()
        registry.register(schema)

        # In-memory VikingFS store
        store: dict[str, str] = {}
        mock_viking_fs = MagicMock()

        async def mock_read_file(uri, **kwargs):
            return store.get(uri)

        async def mock_write_file(uri, content, **kwargs):
            store[uri] = content

        mock_viking_fs.read_file = mock_read_file
        mock_viking_fs.write_file = mock_write_file

        updater = MemoryUpdater(registry=registry)
        updater._get_viking_fs = MagicMock(return_value=mock_viking_fs)

        mock_ctx = MagicMock()

        # First upsert: write "Step A" (no prior content on disk)
        op1 = ResolvedOperation(
            old_memory_file_content=None,
            memory_fields={"content": "Step A"},
            memory_type=memory_type,
            uris=[uri],
        )
        await updater._apply_upsert(op1, mock_ctx)

        # Second upsert: overwrite with "Step B"
        # old_memory_file_content is still None (stale from before the batch),
        # but _apply_upsert reads from disk first, so it sees "Step A"
        op2 = ResolvedOperation(
            old_memory_file_content=None,
            memory_fields={"content": "Step B"},
            memory_type=memory_type,
            uris=[uri],
        )
        await updater._apply_upsert(op2, mock_ctx)

        # Final content on disk should be "Step B" (the second write)
        final_content = store[uri]
        parsed = parse_memory_file_with_fields(final_content)
        assert parsed["content"] == "Step B"

    @pytest.mark.asyncio
    async def test_apply_upsert_skips_failed_field_and_keeps_other_fields(self, monkeypatch):
        memory_type = "notes"
        uri = "viking://user/test/memories/notes/demo.md"

        schema = MemoryTypeSchema(
            memory_type=memory_type,
            description="notes",
            fields=[
                MemoryField(name="title", field_type=FieldType.STRING, merge_op=MergeOp.REPLACE),
                MemoryField(name="content", field_type=FieldType.STRING, merge_op=MergeOp.PATCH),
            ],
        )
        registry = MemoryTypeRegistry()
        registry.register(schema)

        store: dict[str, str] = {}
        mock_viking_fs = MagicMock()

        async def mock_read_file(uri, **kwargs):
            return store.get(uri)

        async def mock_write_file(uri, content, **kwargs):
            store[uri] = content

        mock_viking_fs.read_file = mock_read_file
        mock_viking_fs.write_file = mock_write_file

        updater = MemoryUpdater(registry=registry)
        updater._get_viking_fs = MagicMock(return_value=mock_viking_fs)

        patch_op = MagicMock()
        patch_op.apply.side_effect = ValueError("patch failed")
        replace_op = MagicMock()
        replace_op.apply.return_value = "Updated Title"

        def mock_from_field(field):
            if field.name == "content":
                return patch_op
            if field.name == "title":
                return replace_op
            raise AssertionError(f"unexpected field: {field.name}")

        trace_info = MagicMock()
        monkeypatch.setattr(
            "openviking.session.memory.memory_updater.MergeOpFactory.from_field",
            mock_from_field,
        )
        monkeypatch.setattr("openviking.session.memory.memory_updater.tracer.info", trace_info)

        op = ResolvedOperation(
            old_memory_file_content=MemoryFile(
                uri=uri,
                content="Original body",
                extra_fields={"title": "Original Title"},
            ),
            memory_fields={
                "title": "Updated Title",
                "content": StrPatch(blocks=[SearchReplaceBlock(search="old", replace="new")]),
            },
            memory_type=memory_type,
            uris=[uri],
        )

        await updater._apply_upsert(op, MagicMock())

        parsed = parse_memory_file_with_fields(store[uri])
        assert parsed["title"] == "Updated Title"
        assert parsed["content"] == "Original body"
        trace_info.assert_any_call(
            f"[memory_updater] Skipping field update after merge_op failure: uri={uri}, field=content, error=patch failed"
        )

    @pytest.mark.asyncio
    async def test_apply_upsert_logs_patch_failure_from_memory_updater_only(self, monkeypatch):
        memory_type = "notes"
        uri = "viking://user/test/memories/notes/demo.md"

        schema = MemoryTypeSchema(
            memory_type=memory_type,
            description="notes",
            fields=[
                MemoryField(name="content", field_type=FieldType.STRING, merge_op=MergeOp.PATCH),
            ],
        )
        registry = MemoryTypeRegistry()
        registry.register(schema)

        store: dict[str, str] = {}
        mock_viking_fs = MagicMock()

        async def mock_read_file(uri, **kwargs):
            return store.get(uri)

        async def mock_write_file(uri, content, **kwargs):
            store[uri] = content

        mock_viking_fs.read_file = mock_read_file
        mock_viking_fs.write_file = mock_write_file

        updater = MemoryUpdater(registry=registry)
        updater._get_viking_fs = MagicMock(return_value=mock_viking_fs)

        existing_content = MemoryFileUtils.write(MemoryFile(content="Original body"))
        store[uri] = existing_content

        trace_info = MagicMock()
        patch_warning = MagicMock()
        monkeypatch.setattr("openviking.session.memory.memory_updater.tracer.info", trace_info)
        monkeypatch.setattr("openviking.session.memory.merge_op.patch_handler.logger.warning", patch_warning)

        op = ResolvedOperation(
            old_memory_file_content=MemoryFile(
                uri=uri,
                content="Original body",
            ),
            memory_fields={
                "content": StrPatch(blocks=[SearchReplaceBlock(search="missing", replace="new")]),
            },
            memory_type=memory_type,
            uris=[uri],
        )

        await updater._apply_upsert(op, MagicMock())

        parsed = parse_memory_file_with_fields(store[uri])
        assert parsed["content"] == "Original body"
        patch_warning.assert_not_called()
        assert any(
            "[memory_updater] Skipping field update after merge_op failure" in call.args[0]
            and f"uri={uri}" in call.args[0]
            and "field=content" in call.args[0]
            for call in trace_info.call_args_list
        )

    @pytest.mark.asyncio
    async def test_two_patches_same_uri_second_sees_first_patch(self):
        """Two patches to the same URI: second SEARCH/REPLACE must apply
        against the result of the first, not the original content."""
        uri = "viking://user/test/memories/notes.md"
        memory_type = "notes"

        content_field = MemoryField(
            name="content",
            field_type=FieldType.STRING,
            merge_op=MergeOp.PATCH,
        )
        schema = MemoryTypeSchema(
            memory_type=memory_type,
            description="notes",
            fields=[content_field],
        )
        registry = MemoryTypeRegistry()
        registry.register(schema)

        # In-memory store with initial content
        initial_content = MemoryFileUtils.write(MemoryFile(content="alpha beta gamma"))
        store: dict[str, str] = {uri: initial_content}
        mock_viking_fs = MagicMock()

        async def mock_read_file(uri, **kwargs):
            return store.get(uri)

        async def mock_write_file(uri, content, **kwargs):
            store[uri] = content

        mock_viking_fs.read_file = mock_read_file
        mock_viking_fs.write_file = mock_write_file

        updater = MemoryUpdater(registry=registry)
        updater._get_viking_fs = MagicMock(return_value=mock_viking_fs)

        mock_ctx = MagicMock()

        # First patch: "alpha" -> "ALPHA"
        patch1 = StrPatch(blocks=[SearchReplaceBlock(search="alpha", replace="ALPHA")])
        op1 = ResolvedOperation(
            old_memory_file_content=MemoryFile(
                uri=uri,
                content="alpha beta gamma",
            ),
            memory_fields={"content": patch1},
            memory_type=memory_type,
            uris=[uri],
        )
        await updater._apply_upsert(op1, mock_ctx)

        # Second patch: "beta" -> "BETA"
        # old_memory_file_content still has "alpha beta gamma" (stale),
        # but _apply_upsert reads from disk which now has "ALPHA beta gamma"
        patch2 = StrPatch(blocks=[SearchReplaceBlock(search="beta", replace="BETA")])
        op2 = ResolvedOperation(
            old_memory_file_content=MemoryFile(
                uri=uri,
                content="alpha beta gamma",
            ),
            memory_fields={"content": patch2},
            memory_type=memory_type,
            uris=[uri],
        )
        await updater._apply_upsert(op2, mock_ctx)

        # Final content should have BOTH patches applied
        final_content = store[uri]
        parsed = parse_memory_file_with_fields(final_content)
        assert "ALPHA" in parsed["content"]
        assert "BETA" in parsed["content"]
        assert "gamma" in parsed["content"]

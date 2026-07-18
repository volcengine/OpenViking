# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
Tests for MemoryUpdater.
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openviking.message import Message
from openviking.message.part import TextPart
from openviking.server.identity import RequestContext, Role
from openviking.session.memory.dataclass import (
    MemoryField,
    MemoryFile,
    MemoryOperationSource,
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
from openviking.storage.transaction.lock_handle import LockHandle
from openviking_cli.exceptions import NotFoundError
from openviking_cli.session.user_id import UserIdentifier


class _TestTreeLockManager:
    def __init__(self):
        self._handles: dict[str, LockHandle] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._owned_paths: dict[str, list[str]] = {}
        self._path_lock = SimpleNamespace(_lock_expire=300.0)

    def create_handle(self) -> LockHandle:
        handle = LockHandle()
        self._handles[handle.id] = handle
        return handle

    def get_handle(self, handle_id: str) -> LockHandle | None:
        return self._handles.get(handle_id)

    async def acquire_tree_batch(self, handle: LockHandle, paths: list[str], timeout=None) -> bool:
        del timeout
        acquired = []
        for path in sorted(set(paths), key=lambda item: (len(item), item)):
            lock = self._locks.setdefault(path, asyncio.Lock())
            await lock.acquire()
            acquired.append(path)
            handle.add_lock(path)
        self._owned_paths[handle.id] = acquired
        return True

    async def release(self, handle: LockHandle) -> None:
        for path in reversed(self._owned_paths.pop(handle.id, [])):
            handle.remove_lock(path)
            self._locks[path].release()
        self._handles.pop(handle.id, None)


class _MemoryVikingFS:
    def __init__(self, files: dict[str, str] | None = None):
        self.files = dict(files or {})
        self.write_calls: list[tuple[str, object | None]] = []
        self.read_error_uris: set[str] = set()

    def _uri_to_path(self, uri: str, ctx=None) -> str:
        del ctx
        return f"/{uri.removeprefix('viking://')}"

    async def read_file(self, uri: str, ctx=None) -> str:
        del ctx
        await asyncio.sleep(0)
        if uri in self.read_error_uris:
            raise PermissionError(f"denied: {uri}")
        if uri not in self.files:
            raise NotFoundError(uri, "file")
        return self.files[uri]

    async def write_file(self, uri: str, content: str, ctx=None, lock_handle=None) -> None:
        del ctx
        await asyncio.sleep(0)
        self.files[uri] = content
        self.write_calls.append((uri, lock_handle))


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

    def test_extract_context_can_disable_long_text_message_split(self):
        text = "第一句很长很长很长很长很长很长很长很长很长很长很长。" * 8
        messages = [Message(id="1", role="user", parts=[TextPart(text=text)])]

        split_context = ExtractContext(messages)
        unsplit_context = ExtractContext(messages, split_long_text_messages=False)

        assert len(split_context.messages) > 1
        assert len(unsplit_context.messages) == 1
        assert unsplit_context.messages[0] is messages[0]
        assert unsplit_context.chunk_meta == {}

    def test_extract_context_resource_event_content_hides_add_resource_fields(self):
        resource_uri = "viking://resources/images/2026/06/12/yueqian_jpeg"
        extract_context = ExtractContext(
            messages=[
                Message(
                    id="1",
                    role="user",
                    parts=[
                        TextPart(
                            text=(
                                "## Resource Addition\n"
                                f"Resource URI: {resource_uri}\n"
                                "Source name: yueqian.jpeg\n"
                                "Added at: 2026-06-12T03:43:36.343325+00:00\n"
                                "Resource abstract: This directory contains an anime illustration.\n"
                                "User reason: 这是越前龙马的照片"
                            )
                        )
                    ],
                    created_at="2026-06-12T03:43:36.343325+00:00",
                )
            ]
        )

        content = extract_context.get_resource_event_content(
            "0",
            f"2026-06-12，用户保存了粉丝创作的越前龙马动漫插画资源，资源URI为{resource_uri}。",
        )

        assert (
            content == f"2026-06-12，[用户保存了粉丝创作的越前龙马动漫插画资源]({resource_uri})。"
        )
        assert "Resource URI" not in content
        assert "Added at" not in content
        assert "Resource abstract" not in content
        assert "User reason" not in content

    def test_extract_context_event_content_falls_back_to_range_when_summary_empty(self):
        extract_context = ExtractContext(
            messages=[
                Message(
                    id="1",
                    role="user",
                    parts=[TextPart(text="Gina can expand her clothing store now.")],
                    created_at="2023-02-01T00:48:00",
                )
            ]
        )

        content = extract_context.get_event_content("0", "")

        assert "Gina can expand her clothing store now." in content

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
    async def test_generate_overview_deletes_empty_overview_via_rm(self):
        schema = MemoryTypeSchema(
            memory_type="entities",
            description="entity memory",
            directory="viking://user/{{ user_space }}/memories/entities",
            filename_template="{{ name }}.md",
            fields=[],
            overview_template="overview",
        )
        registry = MagicMock()
        registry.get.return_value = schema

        class FakeVikingFS:
            def __init__(self):
                self.rm_calls = []

            async def ls(self, uri, show_all_hidden=False, ctx=None):
                return [{"name": ".overview.md", "isDir": False}]

            async def rm(self, uri, recursive=False, ctx=None, lock_handle=None):
                self.rm_calls.append((uri, recursive))

        viking_fs = FakeVikingFS()
        updater = MemoryUpdater(registry=registry)
        updater._get_viking_fs = MagicMock(return_value=viking_fs)
        ctx = RequestContext(user=UserIdentifier("acme", "alice"), role=Role.USER)

        await updater.generate_overview(
            "entities",
            "viking://user/alice/memories/entities/动漫角色",
            ctx,
        )

        assert viking_fs.rm_calls == [
            ("viking://user/alice/memories/entities/动漫角色/.overview.md", False),
            ("viking://user/alice/memories/entities/动漫角色", True),
        ]

    @pytest.mark.asyncio
    async def test_generate_overview_skips_deleted_directory(self):
        schema = MemoryTypeSchema(
            memory_type="entities",
            description="entity memory",
            directory="viking://user/{{ user_space }}/memories/entities",
            filename_template="{{ name }}.md",
            fields=[],
            overview_template="overview",
        )
        registry = MagicMock()
        registry.get.return_value = schema

        class FakeVikingFS:
            def __init__(self):
                self.rm_calls = []

            async def ls(self, uri, show_all_hidden=False, ctx=None):
                raise NotFoundError(uri, "directory")

            async def rm(self, uri, recursive=False, ctx=None, lock_handle=None):
                self.rm_calls.append((uri, recursive))

        viking_fs = FakeVikingFS()
        updater = MemoryUpdater(registry=registry)
        updater._get_viking_fs = MagicMock(return_value=viking_fs)
        ctx = RequestContext(user=UserIdentifier("acme", "alice"), role=Role.USER)

        await updater.generate_overview(
            "entities",
            "viking://user/alice/memories/entities/动漫角色",
            ctx,
        )

        assert viking_fs.rm_calls == []

    @pytest.mark.asyncio
    async def test_generate_events_overview_without_extract_context_uses_summary_or_filename(self):
        schema = MemoryTypeSchema(
            memory_type="events",
            description="event memory",
            directory="viking://user/{{ user_space }}/memories/events",
            filename_template="{{ event_name }}.md",
            fields=[],
            overview_template=(
                "# Events Overview\n"
                "{% for item in items %}\n"
                "- [{{ item.file_content.summary|default(item.file_name, true) }}](./{{ item.file_name }})\n"
                "{% endfor %}"
            ),
        )
        registry = MagicMock()
        registry.get.return_value = schema

        directory = "viking://user/alice/memories/events/2026/06/16"
        event_uri = f"{directory}/kept_event.md"
        plain_event_uri = f"{directory}/plain_event.md"
        overview_uri = f"{directory}/.overview.md"

        class FakeVikingFS:
            def __init__(self):
                self.store = {
                    event_uri: MemoryFileUtils.write(
                        MemoryFile(
                            uri=event_uri,
                            content="Summary: kept event",
                            memory_type="events",
                            extra_fields={
                                "event_name": "kept_event",
                                "summary": "kept event",
                                "ranges": "0",
                            },
                        )
                    ),
                    plain_event_uri: "Plain event first sentence. More detail on the same line.",
                    overview_uri: (
                        "# Events Overview\n"
                        "**Date:** 2026/06/16\n"
                        "- [deleted event](./deleted_event.md)\n"
                    ),
                }

            async def ls(self, uri, show_all_hidden=False, ctx=None):
                return [
                    {"name": "kept_event.md", "isDir": False},
                    {"name": "plain_event.md", "isDir": False},
                    {"name": ".overview.md", "isDir": False},
                ]

            async def read_file(self, uri, ctx=None):
                return self.store[uri]

            async def write_file(self, uri, content, ctx=None):
                self.store[uri] = content

        viking_fs = FakeVikingFS()
        updater = MemoryUpdater(registry=registry)
        updater._get_viking_fs = MagicMock(return_value=viking_fs)
        ctx = RequestContext(user=UserIdentifier("acme", "alice"), role=Role.USER)

        await updater.generate_overview("events", directory, ctx, extract_context=None)

        assert "**Date:**" not in viking_fs.store[overview_uri]
        assert "- [kept event](./kept_event.md)" in viking_fs.store[overview_uri]
        assert "- [plain_event.md](./plain_event.md)" in viking_fs.store[overview_uri]
        assert "deleted_event.md" not in viking_fs.store[overview_uri]

    @pytest.mark.asyncio
    async def test_generate_overview_template_fallbacks_for_preferences_and_entities(self):
        registry = MemoryTypeRegistry(load_schemas=False)
        registry.load_from_yaml("openviking/prompts/templates/memory/entities.yaml")
        registry.load_from_yaml("openviking/prompts/templates/memory/preferences.yaml")

        entity_dir = "viking://user/alice/memories/entities/动漫角色"
        entity_uri = f"{entity_dir}/越前龙马.md"
        entity_overview_uri = f"{entity_dir}/.overview.md"
        preference_dir = "viking://user/alice/memories/preferences/alice"
        preference_uri = f"{preference_dir}/workflow.md"
        preference_overview_uri = f"{preference_dir}/.overview.md"

        class FakeVikingFS:
            def __init__(self):
                self.store = {
                    entity_uri: "A tennis character.",
                    preference_uri: "Prefers concise updates.",
                }

            async def ls(self, uri, show_all_hidden=False, ctx=None):
                if uri == entity_dir:
                    return [{"name": "越前龙马.md", "isDir": False}]
                if uri == preference_dir:
                    return [{"name": "workflow.md", "isDir": False}]
                return []

            async def read_file(self, uri, ctx=None):
                return self.store[uri]

            async def write_file(self, uri, content, ctx=None):
                self.store[uri] = content

        viking_fs = FakeVikingFS()
        updater = MemoryUpdater(registry=registry)
        updater._get_viking_fs = MagicMock(return_value=viking_fs)
        ctx = RequestContext(user=UserIdentifier("acme", "alice"), role=Role.USER)

        await updater.generate_overview("entities", entity_dir, ctx, extract_context=None)
        await updater.generate_overview("preferences", preference_dir, ctx, extract_context=None)

        assert "**Category:** 动漫角色" in viking_fs.store[entity_overview_uri]
        assert "- [越前龙马.md](./越前龙马.md)" in viking_fs.store[entity_overview_uri]
        assert "**User:** alice" in viking_fs.store[preference_overview_uri]
        assert "**Topic:** workflow.md" in viking_fs.store[preference_overview_uri]
        assert "- [workflow.md](./workflow.md)" in viking_fs.store[preference_overview_uri]


class TestAddOnlyUriAllocation:
    @staticmethod
    def _schema(operation_mode: str = "add_only") -> MemoryTypeSchema:
        return MemoryTypeSchema(
            memory_type="events",
            description="event memory",
            directory="viking://user/{{ user_space }}/memories/events",
            filename_template="{{ event_name }}.md",
            operation_mode=operation_mode,
            fields=[
                MemoryField(
                    name="event_name",
                    field_type=FieldType.STRING,
                    merge_op=MergeOp.IMMUTABLE,
                ),
                MemoryField(
                    name="content",
                    field_type=FieldType.STRING,
                    merge_op=MergeOp.REPLACE,
                ),
            ],
        )

    @classmethod
    def _updater(cls, viking_fs: _MemoryVikingFS, operation_mode: str = "add_only"):
        registry = MemoryTypeRegistry(load_schemas=False)
        registry.register(cls._schema(operation_mode))
        updater = MemoryUpdater(registry=registry)
        updater._get_viking_fs = MagicMock(return_value=viking_fs)
        updater._sync_resource_refs_for_result = AsyncMock()
        updater._vectorize_memories = AsyncMock()
        updater._apply_links_to_existing_files = AsyncMock()
        updater.generate_overview = AsyncMock()
        return updater

    @staticmethod
    def _ctx() -> RequestContext:
        return RequestContext(
            user=UserIdentifier("acme", "alice"),
            role=Role.USER,
        )

    @pytest.mark.asyncio
    async def test_existing_numbered_files_allocate_next_uri_and_remap_links(self):
        canonical = "viking://user/alice/memories/events/name.md"
        canonical_2 = "viking://user/alice/memories/events/name_2.md"
        canonical_3 = "viking://user/alice/memories/events/name_3.md"
        case_uri = "viking://user/alice/memories/cases/case.md"
        existing_content = MemoryFileUtils.write(MemoryFile(content="existing"))
        viking_fs = _MemoryVikingFS(
            {canonical: existing_content, canonical_2: existing_content}
        )
        updater = self._updater(viking_fs)
        operation = ResolvedOperation(
            memory_fields={"event_name": "name", "content": "new"},
            memory_type="events",
            uris=[canonical],
            add_only_uri_bases={canonical: canonical},
        )
        operations = ResolvedOperations(
            upsert_operations=[operation],
            delete_file_contents=[],
            errors=[],
            resolved_links=[
                StoredLink(
                    from_uri=canonical,
                    to_uri=case_uri,
                    link_type="belongs_to",
                )
            ],
        )
        lock_manager = _TestTreeLockManager()

        with patch(
            "openviking.storage.transaction.get_lock_manager",
            return_value=lock_manager,
        ):
            result = await updater.apply_operations(operations, self._ctx())

        assert viking_fs.files[canonical] == existing_content
        assert viking_fs.files[canonical_2] == existing_content
        assert canonical_3 in viking_fs.files
        assert operation.uris == [canonical_3]
        assert result.written_uris == [canonical_3]
        assert result.edited_uris == []
        assert result.errors == []
        assert operations.resolved_links[0].from_uri == canonical_3
        written = MemoryFileUtils.read(viking_fs.files[canonical_3], uri=canonical_3)
        assert written.links[0]["from_uri"] == canonical_3
        assert viking_fs.write_calls[0][1] is not None
        assert updater._vectorize_memories.await_args.kwargs["uri_memory_type_map"] == {
            canonical_3: "events"
        }

    @pytest.mark.asyncio
    async def test_same_request_candidates_continue_from_canonical_base(self):
        canonical = "viking://user/alice/memories/events/name.md"
        canonical_2 = "viking://user/alice/memories/events/name_2.md"
        canonical_3 = "viking://user/alice/memories/events/name_3.md"
        existing_content = MemoryFileUtils.write(MemoryFile(content="existing"))
        viking_fs = _MemoryVikingFS({canonical: existing_content})
        updater = self._updater(viking_fs)
        first = ResolvedOperation(
            memory_fields={"event_name": "name", "content": "first"},
            memory_type="events",
            uris=[canonical],
            add_only_uri_bases={canonical: canonical},
        )
        second = ResolvedOperation(
            memory_fields={"event_name": "name", "content": "second"},
            memory_type="events",
            uris=[canonical_2],
            add_only_uri_bases={canonical_2: canonical},
        )
        operations = ResolvedOperations(
            upsert_operations=[first, second],
            delete_file_contents=[],
            errors=[],
        )

        with patch(
            "openviking.storage.transaction.get_lock_manager",
            return_value=_TestTreeLockManager(),
        ):
            result = await updater.apply_operations(operations, self._ctx())

        assert first.uris == [canonical_2]
        assert second.uris == [canonical_3]
        assert result.written_uris == [canonical_2, canonical_3]
        assert MemoryFileUtils.read(viking_fs.files[canonical_2]).content == "first"
        assert MemoryFileUtils.read(viking_fs.files[canonical_3]).content == "second"

    @pytest.mark.asyncio
    async def test_non_not_found_read_error_fails_without_writing(self):
        canonical = "viking://user/alice/memories/events/name.md"
        viking_fs = _MemoryVikingFS()
        viking_fs.read_error_uris.add(canonical)
        updater = self._updater(viking_fs)
        operation = ResolvedOperation(
            memory_fields={"event_name": "name", "content": "new"},
            memory_type="events",
            uris=[canonical],
        )
        operations = ResolvedOperations(
            upsert_operations=[operation],
            delete_file_contents=[],
            errors=[],
        )

        with patch(
            "openviking.storage.transaction.get_lock_manager",
            return_value=_TestTreeLockManager(),
        ):
            result = await updater.apply_operations(operations, self._ctx())

        assert result.written_uris == []
        assert len(result.errors) == 1
        assert isinstance(result.errors[0][1], PermissionError)
        assert viking_fs.write_calls == []

    @pytest.mark.asyncio
    async def test_concurrent_writers_allocate_distinct_uris(self):
        canonical = "viking://user/alice/memories/events/name.md"
        canonical_2 = "viking://user/alice/memories/events/name_2.md"
        viking_fs = _MemoryVikingFS()
        first_updater = self._updater(viking_fs)
        second_updater = self._updater(viking_fs)

        def operations(content: str) -> ResolvedOperations:
            return ResolvedOperations(
                upsert_operations=[
                    ResolvedOperation(
                        memory_fields={"event_name": "name", "content": content},
                        memory_type="events",
                        uris=[canonical],
                    )
                ],
                delete_file_contents=[],
                errors=[],
            )

        lock_manager = _TestTreeLockManager()
        with patch(
            "openviking.storage.transaction.get_lock_manager",
            return_value=lock_manager,
        ):
            first_result, second_result = await asyncio.gather(
                first_updater.apply_operations(operations("first"), self._ctx()),
                second_updater.apply_operations(operations("second"), self._ctx()),
            )

        assert set(first_result.written_uris + second_result.written_uris) == {
            canonical,
            canonical_2,
        }
        assert set(viking_fs.files) == {canonical, canonical_2}

    @pytest.mark.asyncio
    async def test_non_add_only_schema_keeps_canonical_uri(self):
        canonical = "viking://user/alice/memories/events/name.md"
        existing_content = MemoryFileUtils.write(MemoryFile(content="existing"))
        viking_fs = _MemoryVikingFS({canonical: existing_content})
        updater = self._updater(viking_fs, operation_mode="upsert")
        operation = ResolvedOperation(
            memory_fields={"event_name": "name", "content": "replacement"},
            memory_type="events",
            uris=[canonical],
        )
        operations = ResolvedOperations(
            upsert_operations=[operation],
            delete_file_contents=[],
            errors=[],
        )

        result = await updater.apply_operations(operations, self._ctx())

        assert operation.uris == [canonical]
        assert result.written_uris == [canonical]
        assert MemoryFileUtils.read(viking_fs.files[canonical]).content == "replacement"

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

        ctx = RequestContext(user=UserIdentifier("acme", "alice"), role=Role.USER)

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
        ctx = RequestContext(user=UserIdentifier("acme", "alice"), role=Role.USER)

        with pytest.raises(ValueError, match="missing resolved URIs"):
            await updater.apply_operations(operations=operations, ctx=ctx)

    @pytest.mark.asyncio
    async def test_apply_operations_matches_overview_directory_from_resolved_user_uri(self):
        """Overview generation should use the resolved user memory directory."""
        memory_type = "preferences"
        schema_directory = "viking://user/{{ user_space }}/memories/preferences"
        resolved_uri = "viking://user/alice/memories/preferences/theme.md"
        expected_directory = "viking://user/alice/memories/preferences"
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
            user=UserIdentifier("acme", "alice"),
            role=Role.USER,
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
        deleted_uri = "viking://user/user_sample_3/memories/experiences/old.md"
        written_uri = "viking://user/user_sample_3/memories/experiences/new.md"

        schema = MemoryTypeSchema(
            memory_type="experiences",
            description="experience memory",
            directory="viking://user/{{ user_space }}/memories/experiences",
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

        async def mock_read_file(uri, **kwargs):
            if uri == deleted_uri:
                raise AssertionError("deleted URI should not be read")
            return MemoryFileUtils.write(MemoryFile(uri=uri, content="new content"))

        mock_viking_fs.read_file = AsyncMock(side_effect=mock_read_file)
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
            delete_file_contents=[
                MemoryFile(uri=deleted_uri, extra_fields={"memory_type": "experiences"})
            ],
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

        ctx = RequestContext(user=UserIdentifier("acme", "alice"), role=Role.USER)

        result = await updater.apply_operations(operations=resolved, ctx=ctx)

        assert result.written_uris == [written_uri]
        assert result.deleted_uris == [deleted_uri]
        assert deleted_uri not in [
            call.args[0] for call in mock_viking_fs.read_file.await_args_list
        ]

    @pytest.mark.asyncio
    async def test_apply_operations_skips_case_only_delete_conflicting_with_upsert(self):
        written_uri = "viking://user/conv-26/memories/entities/person/melanie.md"
        deleted_uri = "viking://user/conv-26/memories/entities/person/Melanie.md"

        schema = MemoryTypeSchema(
            memory_type="entities",
            description="entity memory",
            directory="viking://user/{{ user_space }}/memories/entities",
            filename_template="{{ category }}/{{ name }}.md",
            fields=[],
            overview_template="overview",
        )
        registry = MagicMock()
        registry.get.return_value = schema

        updater = MemoryUpdater(registry=registry)
        updater._get_viking_fs = MagicMock(return_value=MagicMock())
        updater._apply_upsert = AsyncMock(return_value=None)
        updater._apply_delete = AsyncMock()
        updater._vectorize_memories = AsyncMock()
        updater.generate_overview = AsyncMock()

        resolved = ResolvedOperations(
            upsert_operations=[
                ResolvedOperation(
                    memory_fields={"category": "person", "name": "melanie"},
                    memory_type="entities",
                    uris=[written_uri],
                )
            ],
            delete_file_contents=[
                MemoryFile(uri=deleted_uri, extra_fields={"memory_type": "entities"})
            ],
            errors=[],
        )
        ctx = RequestContext(user=UserIdentifier("acme", "conv-26"), role=Role.USER)

        result = await updater.apply_operations(operations=resolved, ctx=ctx)

        assert result.written_uris == [written_uri]
        assert result.deleted_uris == []
        updater._apply_delete.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_apply_operations_remaps_deleted_links_to_replacement(self):
        deleted_uri = "viking://user/u/memories/preferences/Evan/hobby_preferences.md"
        replacement_uri = "viking://user/u/memories/preferences/Evan/hobbies.md"
        profile_uri = "viking://user/u/memories/profile.md"

        schema = MemoryTypeSchema(
            memory_type="preferences",
            description="preference memory",
            directory="viking://user/{{ user_space }}/memories/preferences",
            filename_template="{{ name }}.md",
            fields=[],
            overview_template="overview",
        )
        registry = MagicMock()
        registry.get.return_value = schema

        deleted_file = MemoryFile(
            uri=deleted_uri,
            content="old hobby preferences",
            memory_type="preferences",
            links=[
                {
                    "from_uri": deleted_uri,
                    "to_uri": profile_uri,
                    "link_type": "related_to",
                    "weight": 0.8,
                    "match_text": "hobby",
                    "description": "old link",
                }
            ],
        )
        replacement_file = MemoryFile(
            uri=replacement_uri,
            content="new hobbies",
            memory_type="preferences",
        )
        profile_file = MemoryFile(
            uri=profile_uri,
            content="profile",
            memory_type="profile",
        )
        files = {
            deleted_uri: MemoryFileUtils.write(deleted_file),
            replacement_uri: MemoryFileUtils.write(replacement_file),
            profile_uri: MemoryFileUtils.write(profile_file),
        }

        mock_viking_fs = MagicMock()
        mock_viking_fs.read_file = AsyncMock(side_effect=lambda uri, ctx=None: files[uri])

        async def write_file(uri, content, ctx=None):
            files[uri] = content

        mock_viking_fs.write_file = AsyncMock(side_effect=write_file)
        updater = MemoryUpdater(registry=registry)
        updater._get_viking_fs = MagicMock(return_value=mock_viking_fs)
        updater._apply_upsert = AsyncMock(return_value=None)
        updater._apply_delete = AsyncMock()
        updater._vectorize_memories = AsyncMock()
        updater.generate_overview = AsyncMock()

        resolved = ResolvedOperations(
            upsert_operations=[
                ResolvedOperation(
                    memory_fields={"name": "hobbies"},
                    memory_type="preferences",
                    uris=[replacement_uri],
                )
            ],
            delete_file_contents=[deleted_file],
            errors=[],
            resolved_links=[
                StoredLink(
                    from_uri=deleted_uri,
                    to_uri=profile_uri,
                    link_type="related_to",
                    weight=0.9,
                    match_text="hobby",
                    description="in-flight link",
                )
            ],
            delete_replacements={deleted_uri: replacement_uri},
        )

        ctx = RequestContext(user=UserIdentifier("acme", "alice"), role=Role.USER)

        result = await updater.apply_operations(operations=resolved, ctx=ctx)

        assert result.written_uris == [replacement_uri]
        assert result.deleted_uris == [deleted_uri]
        assert resolved.resolved_links[0].from_uri == replacement_uri
        profile = MemoryFileUtils.read(files[profile_uri], uri=profile_uri)
        assert profile.backlinks[0]["from_uri"] == replacement_uri
        assert profile.backlinks[0]["to_uri"] == profile_uri

    @pytest.mark.asyncio
    async def test_apply_operations_routes_backlinks_to_matching_uri_only(self):
        caroline_uri = (
            "viking://user/Caroline/memories/events/2023/05/08/career_education_planning.md"
        )
        melanie_uri = (
            "viking://user/Melanie/memories/events/2023/05/08/career_education_planning.md"
        )
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

        ctx = RequestContext(user=UserIdentifier("acme", "alice"), role=Role.USER)

        await updater.apply_operations(operations=operations, ctx=ctx)

        caroline = parse_memory_file_with_fields(store[caroline_uri])
        melanie = parse_memory_file_with_fields(store[melanie_uri])

        assert [link["to_uri"] for link in caroline["backlinks"]] == [caroline_uri]
        assert melanie.get("backlinks", []) == []

    @pytest.mark.asyncio
    async def test_apply_operations_does_not_write_backlinks_to_resource_targets(self):
        memory_uri = "viking://user/alice/memories/entities/wang.md"
        resource_uri = "viking://resources/id_card.pdf"

        schema = MemoryTypeSchema(
            memory_type="entities",
            description="entity memory",
            directory="viking://user/{{ user_space }}/memories/entities",
            filename_template="{{ name }}.md",
            fields=[],
            overview_template="overview",
        )
        registry = MagicMock()
        registry.get.return_value = schema

        store = {}
        mock_viking_fs = MagicMock()

        async def mock_read_file(uri, **kwargs):
            if uri == resource_uri:
                raise AssertionError("resource target should not be read as a memory file")
            return store.get(uri)

        async def mock_write_file(uri, content, **kwargs):
            if uri == resource_uri:
                raise AssertionError("resource target should not be written as a memory file")
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
                        "name": "王大锤",
                        "content": "王大锤的身份证资料见资源。",
                    },
                    memory_type="entities",
                    uris=[memory_uri],
                    page_id=100,
                )
            ],
            delete_file_contents=[],
            errors=[],
            resolved_links=[
                StoredLink(
                    from_uri=memory_uri,
                    to_uri=resource_uri,
                    link_type="references_resource",
                    match_text="资源",
                )
            ],
        )

        ctx = RequestContext(user=UserIdentifier("acme", "alice"), role=Role.USER)

        await updater.apply_operations(operations=operations, ctx=ctx)

        memory = parse_memory_file_with_fields(store[memory_uri])
        assert memory["links"][0]["to_uri"] == resource_uri
        assert resource_uri not in store

    @pytest.mark.asyncio
    async def test_apply_operations_syncs_markdown_resource_refs_before_vectorize(self):
        memory_uri = "viking://user/alice/memories/entities/fuji.md"
        resource_uri = "viking://resources/images/2026/06/11/fuji_jpeg"

        schema = MemoryTypeSchema(
            memory_type="entities",
            description="entity memory",
            directory="viking://user/{{ user_space }}/memories/entities",
            filename_template="{{ name }}.md",
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

        async def assert_vectorized_after_resource_ref_sync(*args, **kwargs):
            mf = MemoryFileUtils.read(store[memory_uri], uri=memory_uri)
            assert mf.extra_fields["resource_refs"][0]["source"] == "session.commit"

        mock_viking_fs.read_file = mock_read_file
        mock_viking_fs.write_file = mock_write_file

        updater = MemoryUpdater(registry=registry)
        updater._get_viking_fs = MagicMock(return_value=mock_viking_fs)
        updater._vectorize_memories = AsyncMock(
            side_effect=assert_vectorized_after_resource_ref_sync
        )
        updater.generate_overview = AsyncMock()

        operations = ResolvedOperations(
            upsert_operations=[
                ResolvedOperation(
                    memory_fields={
                        "name": "不二周助",
                        "content": f"用户保存了一张[不二周助]({resource_uri})的照片",
                    },
                    memory_type="entities",
                    uris=[memory_uri],
                    page_id=100,
                )
            ],
            delete_file_contents=[],
            errors=[],
        )
        ctx = RequestContext(user=UserIdentifier("acme", "alice"), role=Role.USER)

        await updater.apply_operations(operations=operations, ctx=ctx)

        mf = MemoryFileUtils.read(store[memory_uri], uri=memory_uri)
        assert mf.content == f"用户保存了一张[不二周助]({resource_uri})的照片"
        assert mf.links == []
        assert mf.extra_fields["resource_refs"] == [
            {
                "resource_uri": resource_uri,
                "source": "session.commit",
                "created_at": mf.extra_fields["resource_refs"][0]["created_at"],
                "match_text": "不二周助",
            }
        ]

    @pytest.mark.asyncio
    async def test_apply_operations_linkifies_bare_resource_uri(self):
        memory_uri = "viking://user/alice/memories/entities/fuji.md"
        resource_uri = "viking://resources/images/2026/06/11/fuji_jpeg"

        schema = MemoryTypeSchema(
            memory_type="entities",
            description="entity memory",
            directory="viking://user/{{ user_space }}/memories/entities",
            filename_template="{{ name }}.md",
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
                        "name": "不二周助",
                        "content": f"今天是清明节。用户保存了一张不二周助的照片 {resource_uri}",
                    },
                    memory_type="entities",
                    uris=[memory_uri],
                    page_id=100,
                )
            ],
            delete_file_contents=[],
            errors=[],
        )
        ctx = RequestContext(user=UserIdentifier("acme", "alice"), role=Role.USER)

        await updater.apply_operations(operations=operations, ctx=ctx)

        mf = MemoryFileUtils.read(store[memory_uri], uri=memory_uri)
        assert mf.content == f"今天是清明节。[用户保存了一张不二周助的照片]({resource_uri})"
        assert mf.extra_fields["resource_refs"][0]["resource_uri"] == resource_uri
        assert mf.extra_fields["resource_refs"][0]["source"] == "session.commit"
        assert mf.extra_fields["resource_refs"][0]["match_text"] == "用户保存了一张不二周助的照片"


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
    async def test_apply_upsert_persists_last_update_trace_id(self):
        updater = self._make_updater_with_registry()
        mock_viking_fs = MagicMock()
        mock_viking_fs.read_file = AsyncMock(side_effect=FileNotFoundError("missing"))
        written_content = None

        async def mock_write_file(uri, content, **kwargs):
            nonlocal written_content
            written_content = content

        mock_viking_fs.write_file = mock_write_file
        updater._get_viking_fs = MagicMock(return_value=mock_viking_fs)

        op = ResolvedOperation(
            memory_fields={"content": "Line 1"},
            memory_type="test",
            uris=["viking://test/test.md"],
            source=MemoryOperationSource(extraction_id="extract_1", trace_id="trace_1"),
        )
        await updater._apply_upsert(op, MagicMock())

        assert written_content is not None
        result = MemoryFileUtils.read(written_content)
        assert result.extra_fields["source_extraction_id"] == "extract_1"
        assert result.extra_fields["last_update_trace_id"] == "trace_1"

    @pytest.mark.asyncio
    async def test_apply_upsert_persists_content_field_when_template_renders_body(self):
        content_field = MemoryField(
            name="content",
            field_type=FieldType.STRING,
            merge_op=MergeOp.REPLACE,
        )
        schema = MemoryTypeSchema(
            memory_type="test_exp",
            description="test",
            fields=[content_field],
            content_template="{{ content }}\n\n# Rendered Section",
        )
        registry = MemoryTypeRegistry()
        registry.register(schema)
        updater = MemoryUpdater(registry=registry)

        mock_viking_fs = MagicMock()
        mock_viking_fs.read_file = AsyncMock(side_effect=FileNotFoundError("missing"))
        written_content = None

        async def mock_write_file(uri, content, **kwargs):
            nonlocal written_content
            written_content = content

        mock_viking_fs.write_file = mock_write_file
        updater._get_viking_fs = MagicMock(return_value=mock_viking_fs)

        op = ResolvedOperation(
            memory_fields={"content": "Raw repair content"},
            memory_type="test_exp",
            uris=["viking://user/u/memories/test_exp/demo.md"],
        )
        await updater._apply_upsert(op, MagicMock())

        assert written_content is not None
        assert "# Rendered Section" in written_content
        assert '"content": "Raw repair content"' in written_content
        result = MemoryFileUtils.read(written_content)
        assert result.content == "Raw repair content"

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
        assert parsed["version"] == 2

    @pytest.mark.asyncio
    async def test_apply_upsert_strips_user_id_and_sets_version(self):
        memory_type = "notes"
        uri = "viking://user/alice/memories/notes.md"
        schema = MemoryTypeSchema(
            memory_type=memory_type,
            description="notes",
            fields=[
                MemoryField(
                    name="content",
                    field_type=FieldType.STRING,
                    merge_op=MergeOp.PATCH,
                ),
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

        op = ResolvedOperation(
            old_memory_file_content=None,
            memory_fields={
                "content": "Step A",
                "user_id": "alice",
                "user_ids": ["alice", "bob"],
            },
            memory_type=memory_type,
            uris=[uri],
        )
        await updater._apply_upsert(op, MagicMock())

        parsed = parse_memory_file_with_fields(store[uri])
        assert parsed["content"] == "Step A"
        assert parsed["version"] == 1
        assert "user_id" not in parsed
        assert "user_ids" not in parsed

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
        monkeypatch.setattr(
            "openviking.session.memory.merge_op.patch_handler.logger.warning", patch_warning
        )

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

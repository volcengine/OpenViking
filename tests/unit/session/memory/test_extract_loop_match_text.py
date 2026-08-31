# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

from openviking.session.memory.dataclass import (
    MemoryField,
    MemoryFile,
    MemoryOperationSkip,
    MemoryOperationSkipCode,
    MemoryTypeSchema,
    ResolvedOperation,
    ResolvedOperations,
    WikiLink,
)
from openviking.session.memory.extract_loop import ExtractLoop
from openviking.session.memory.merge_op import FieldType, MergeOp
from openviking.session.memory.page_id_map import PageIdMap


class AttrDict(dict):
    __getattr__ = dict.get


class TestResolveOperations:
    @staticmethod
    def _event_schema():
        return MemoryTypeSchema(
            memory_type="events",
            description="event memory",
            directory="viking://user/{{ user_space }}/memories/events",
            filename_template="{{ event_name }}.md",
            operation_mode="add_only",
            fields=[
                MemoryField(
                    name="event_name",
                    field_type=FieldType.STRING,
                    merge_op=MergeOp.IMMUTABLE,
                ),
                MemoryField(
                    name="ranges",
                    field_type=FieldType.STRING,
                    merge_op=MergeOp.IMMUTABLE,
                ),
            ],
        )

    @pytest.mark.asyncio
    async def test_event_low_page_id_never_reuses_existing_profile(self):
        schema = self._event_schema()
        page_id_map = PageIdMap()
        for index in range(4):
            page_id_map.get_page_id(f"viking://user/alice/memories/filler-{index}.md")
        profile_uri = "viking://user/alice/memories/profile.md"
        assert page_id_map.get_page_id(profile_uri) == 5
        profile = MemoryFile(uri=profile_uri, memory_type="profile", content="profile")
        event_uri = "viking://user/alice/memories/events/2026/08/27/demo.md"

        context_provider = Mock()
        context_provider.get_memory_schemas.return_value = [schema]
        context_provider.read_file_contents = {profile_uri: profile}
        isolation_handler = Mock()
        isolation_handler.get_read_scope.return_value = None
        isolation_handler.fill_identity_fields.side_effect = lambda item, **kwargs: item
        isolation_handler.calculate_memory_uris.return_value = [event_uri]

        loop = ExtractLoop(
            vlm=Mock(model="test-model"),
            viking_fs=Mock(),
            context_provider=context_provider,
            isolation_handler=isolation_handler,
        )
        loop._extract_context = SimpleNamespace(page_id_map=page_id_map)

        operations, _ = await loop.resolve_operations(
            AttrDict(events=[{"event_name": "demo", "ranges": "0", "page_id": 5}])
        )

        operation = operations.upsert_operations[0]
        assert operation.page_id == 100
        assert operation.uris == [event_uri]
        assert operation.old_memory_file_content is None
        assert page_id_map.resolve(5) == profile_uri
        isolation_handler.calculate_memory_uris.assert_called_once()

    @pytest.mark.asyncio
    async def test_event_unknown_low_page_id_is_normalized_and_written(self):
        schema = self._event_schema()
        page_id_map = PageIdMap()
        event_uri = "viking://user/alice/memories/events/2026/08/27/demo.md"
        context_provider = Mock()
        context_provider.get_memory_schemas.return_value = [schema]
        context_provider.read_file_contents = {}
        isolation_handler = Mock()
        isolation_handler.get_read_scope.return_value = None
        isolation_handler.fill_identity_fields.side_effect = lambda item, **kwargs: item
        isolation_handler.calculate_memory_uris.return_value = [event_uri]

        loop = ExtractLoop(
            vlm=Mock(model="test-model"),
            viking_fs=Mock(),
            context_provider=context_provider,
            isolation_handler=isolation_handler,
        )
        loop._extract_context = SimpleNamespace(page_id_map=page_id_map)

        operations, _ = await loop.resolve_operations(
            AttrDict(events=[{"event_name": "demo", "ranges": "0", "page_id": 5}])
        )

        operation = operations.upsert_operations[0]
        assert operation.page_id == 100
        assert operation.uris == [event_uri]
        assert operation.resolution_skip is None

    @pytest.mark.asyncio
    async def test_existing_page_id_from_other_memory_type_is_rejected(self):
        schema = MemoryTypeSchema(
            memory_type="entities",
            directory="viking://user/{{ user_space }}/memories/entities",
            filename_template="{{ name }}.md",
            fields=[],
        )
        profile_uri = "viking://user/alice/memories/profile.md"
        profile = MemoryFile(uri=profile_uri, memory_type="profile", content="profile")
        page_id_map = PageIdMap()
        assert page_id_map.get_page_id(profile_uri) == 1
        context_provider = Mock()
        profile_schema = MemoryTypeSchema(
            memory_type="profile",
            directory="viking://user/{{ user_space }}/memories",
            filename_template="profile.md",
            fields=[],
        )
        context_provider.get_memory_schemas.return_value = [schema, profile_schema]
        context_provider.read_file_contents = {profile_uri: profile}
        isolation_handler = Mock()
        isolation_handler.get_read_scope.return_value = None
        isolation_handler.fill_identity_fields.side_effect = lambda item, **kwargs: item
        isolation_handler.render_schema_directories.side_effect = lambda current_schema: [
            (
                "viking://user/alice/memories/entities"
                if current_schema.memory_type == "entities"
                else "viking://user/alice/memories"
            )
        ]

        loop = ExtractLoop(
            vlm=Mock(model="test-model"),
            viking_fs=Mock(),
            context_provider=context_provider,
            isolation_handler=isolation_handler,
        )
        loop._extract_context = SimpleNamespace(page_id_map=page_id_map)

        operations, _ = await loop.resolve_operations(AttrDict(entities=[{"page_id": 1}]))

        operation = operations.upsert_operations[0]
        assert operation.uris == []
        assert operation.old_memory_file_content is None
        assert operation.resolution_skip is not None
        assert (
            operation.resolution_skip.reason_code == MemoryOperationSkipCode.PAGE_ID_TYPE_MISMATCH
        )
        isolation_handler.calculate_memory_uris.assert_not_called()

    @pytest.mark.asyncio
    async def test_type_mismatched_page_id_is_rejected_from_links_and_replacements(self):
        entity_schema = MemoryTypeSchema(
            memory_type="entities",
            directory="viking://user/{{ user_space }}/memories/entities",
            filename_template="{{ name }}.md",
            fields=[],
        )
        experience_schema = MemoryTypeSchema(
            memory_type="experiences",
            directory="viking://user/{{ user_space }}/memories/experiences",
            filename_template="{{ experience_name }}.md",
            fields=[],
        )
        profile_uri = "viking://user/alice/memories/profile.md"
        old_entity_uri = "viking://user/alice/memories/entities/old-project.md"
        experience_uri = "viking://user/alice/memories/experiences/trip.md"
        page_id_map = PageIdMap()
        assert page_id_map.get_page_id(profile_uri) == 1
        assert page_id_map.get_page_id(old_entity_uri) == 2

        context_provider = Mock()
        context_provider.get_memory_schemas.return_value = [entity_schema, experience_schema]
        context_provider.read_file_contents = {
            profile_uri: MemoryFile(uri=profile_uri, memory_type="profile", content="profile"),
            old_entity_uri: MemoryFile(
                uri=old_entity_uri,
                memory_type="entities",
                content="old entity",
            ),
        }
        isolation_handler = Mock()
        isolation_handler.get_read_scope.return_value = None
        isolation_handler.fill_identity_fields.side_effect = lambda item, **kwargs: item
        isolation_handler.render_schema_directories.side_effect = lambda schema: [
            schema.directory.replace("{{ user_space }}", "alice")
        ]
        isolation_handler.calculate_memory_uris.return_value = [experience_uri]

        loop = ExtractLoop(
            vlm=Mock(model="test-model"),
            viking_fs=Mock(),
            context_provider=context_provider,
            isolation_handler=isolation_handler,
        )
        loop._extract_context = SimpleNamespace(page_id_map=page_id_map)
        loop._link_enabled = True

        operations, raw_links = await loop.resolve_operations(
            AttrDict(
                entities=[{"page_id": 1, "name": "wrong-project"}],
                experiences=[{"page_id": 100, "experience_name": "trip"}],
                links=[WikiLink(f=1, t=100, link_type="related_to", match_text=None)],
                delete_ids=[{"delete_page_id": 2, "replacement_page_id": 1}],
            )
        )

        mismatched_operation = operations.upsert_operations[0]
        assert mismatched_operation.uris == []
        assert mismatched_operation.resolution_skip is not None
        assert (
            mismatched_operation.resolution_skip.reason_code
            == MemoryOperationSkipCode.PAGE_ID_TYPE_MISMATCH
        )
        assert raw_links == []
        assert operations.delete_file_contents == []
        assert operations.delete_replacements == {}

        await loop.finalize_operations(operations, raw_links)
        assert operations.resolved_links == []

    @pytest.mark.asyncio
    async def test_existing_page_id_uses_path_when_metadata_is_dirty(self):
        schema = MemoryTypeSchema(
            memory_type="profile",
            directory="viking://user/{{ user_space }}/memories",
            filename_template="profile.md",
            fields=[],
        )
        profile_uri = "viking://user/alice/memories/profile.md"
        dirty_profile = MemoryFile(
            uri=profile_uri,
            memory_type="events",
            content="profile",
        )
        page_id_map = PageIdMap()
        assert page_id_map.get_page_id(profile_uri) == 1
        context_provider = Mock()
        context_provider.get_memory_schemas.return_value = [schema]
        context_provider.read_file_contents = {profile_uri: dirty_profile}
        isolation_handler = Mock()
        isolation_handler.get_read_scope.return_value = None
        isolation_handler.fill_identity_fields.side_effect = lambda item, **kwargs: item
        isolation_handler.render_schema_directories.return_value = ["viking://user/alice/memories"]

        loop = ExtractLoop(
            vlm=Mock(model="test-model"),
            viking_fs=Mock(),
            context_provider=context_provider,
            isolation_handler=isolation_handler,
        )
        loop._extract_context = SimpleNamespace(page_id_map=page_id_map)

        operations, _ = await loop.resolve_operations(AttrDict(profile=[{"page_id": 1}]))

        operation = operations.upsert_operations[0]
        assert operation.uris == [profile_uri]
        assert operation.old_memory_file_content is dirty_profile
        assert operation.resolution_skip is None

    @pytest.mark.asyncio
    async def test_non_event_add_only_resolution_is_unchanged(self):
        schema = MemoryTypeSchema(
            memory_type="trajectories",
            directory="viking://user/{{ user_space }}/memories/trajectories",
            filename_template="{{ trajectory_name }}.md",
            operation_mode="add_only",
            fields=[],
        )
        trajectory_uri = "viking://user/alice/memories/trajectories/demo.md"
        context_provider = Mock()
        context_provider.get_memory_schemas.return_value = [schema]
        context_provider.read_file_contents = {}
        isolation_handler = Mock()
        isolation_handler.get_read_scope.return_value = None
        isolation_handler.fill_identity_fields.side_effect = lambda item, **kwargs: item
        isolation_handler.calculate_memory_uris.return_value = [trajectory_uri]

        loop = ExtractLoop(
            vlm=Mock(model="test-model"),
            viking_fs=Mock(),
            context_provider=context_provider,
            isolation_handler=isolation_handler,
        )
        loop._extract_context = SimpleNamespace(page_id_map=PageIdMap())

        operations, _ = await loop.resolve_operations(
            AttrDict(trajectories=[{"trajectory_name": "demo", "page_id": 105}])
        )

        operation = operations.upsert_operations[0]
        assert operation.page_id == 105
        assert operation.uris == [trajectory_uri]

    @pytest.mark.asyncio
    async def test_new_page_ids_are_unique_across_memory_types(self):
        event_schema = self._event_schema()
        profile_schema = MemoryTypeSchema(
            memory_type="profile",
            directory="viking://user/{{ user_space }}/memories",
            filename_template="profile.md",
            fields=[],
        )
        entity_schema = MemoryTypeSchema(
            memory_type="entities",
            directory="viking://user/{{ user_space }}/memories/entities",
            filename_template="{{ name }}.md",
            fields=[],
        )
        uris = {
            "events": "viking://user/alice/memories/events/demo.md",
            "profile": "viking://user/alice/memories/profile.md",
            "entities": "viking://user/alice/memories/entities/target.md",
        }
        context_provider = Mock()
        context_provider.get_memory_schemas.return_value = [
            event_schema,
            profile_schema,
            entity_schema,
        ]
        context_provider.read_file_contents = {}
        isolation_handler = Mock()
        isolation_handler.get_read_scope.return_value = None
        isolation_handler.fill_identity_fields.side_effect = lambda item, **kwargs: item
        isolation_handler.calculate_memory_uris.side_effect = lambda **kwargs: [
            uris[kwargs["memory_type_schema"].memory_type]
        ]

        loop = ExtractLoop(
            vlm=Mock(model="test-model"),
            viking_fs=Mock(),
            context_provider=context_provider,
            isolation_handler=isolation_handler,
        )
        loop._extract_context = SimpleNamespace(page_id_map=PageIdMap())
        loop._link_enabled = True
        raw_operations = AttrDict(
            events=[{"event_name": "demo", "ranges": "0", "page_id": 100}],
            profile=[{"summary": "profile", "page_id": 100}],
            entities=[{"name": "target", "page_id": 101}],
            links=[WikiLink(f=100, t=101, match_text=None)],
        )

        operations, raw_links = await loop.resolve_operations(raw_operations)
        assert [operation.page_id for operation in operations.upsert_operations] == [102, 100, 101]
        assert raw_links == []

        repeated_operations, repeated_links = await loop.resolve_operations(raw_operations)
        assert [operation.page_id for operation in repeated_operations.upsert_operations] == [
            102,
            100,
            101,
        ]
        assert repeated_links == []

        await loop.finalize_operations(operations, raw_links)
        assert operations.resolved_links == []

    @pytest.mark.asyncio
    async def test_duplicate_non_event_page_ids_are_normalized(self):
        profile_schema = MemoryTypeSchema(
            memory_type="profile",
            directory="viking://user/{{ user_space }}/memories",
            filename_template="profile.md",
            fields=[],
        )
        entity_schema = MemoryTypeSchema(
            memory_type="entities",
            directory="viking://user/{{ user_space }}/memories/entities",
            filename_template="{{ name }}.md",
            fields=[],
        )
        context_provider = Mock()
        context_provider.get_memory_schemas.return_value = [profile_schema, entity_schema]
        context_provider.read_file_contents = {}
        isolation_handler = Mock()
        isolation_handler.get_read_scope.return_value = None
        isolation_handler.fill_identity_fields.side_effect = lambda item, **kwargs: item
        isolation_handler.calculate_memory_uris.side_effect = lambda **kwargs: [
            (
                "viking://user/alice/memories/profile.md"
                if kwargs["memory_type_schema"].memory_type == "profile"
                else f"viking://user/alice/memories/entities/{kwargs['operation'].memory_fields['name']}.md"
            )
        ]

        loop = ExtractLoop(
            vlm=Mock(model="test-model"),
            viking_fs=Mock(),
            context_provider=context_provider,
            isolation_handler=isolation_handler,
        )
        loop._extract_context = SimpleNamespace(page_id_map=PageIdMap())

        operations, raw_links = await loop.resolve_operations(
            AttrDict(
                profile=[{"summary": "profile", "page_id": 100}],
                entities=[
                    {"name": "duplicate", "page_id": 100},
                    {"name": "target", "page_id": 102},
                ],
                links=[WikiLink(f=100, t=102, match_text=None)],
            )
        )

        assert [operation.page_id for operation in operations.upsert_operations] == [100, 101, 102]
        assert raw_links == []

    @pytest.mark.asyncio
    async def test_delete_with_ambiguous_new_replacement_page_id_is_skipped(self):
        profile_schema = MemoryTypeSchema(
            memory_type="profile",
            directory="viking://user/{{ user_space }}/memories",
            filename_template="profile.md",
            fields=[],
        )
        entity_schema = MemoryTypeSchema(
            memory_type="entities",
            directory="viking://user/{{ user_space }}/memories/entities",
            filename_template="{{ name }}.md",
            fields=[],
        )
        deleted_uri = "viking://user/alice/memories/entities/deleted.md"
        deleted_file = MemoryFile(uri=deleted_uri, memory_type="entities", content="deleted")
        page_id_map = PageIdMap()
        assert page_id_map.get_page_id(deleted_uri) == 1
        context_provider = Mock()
        context_provider.get_memory_schemas.return_value = [profile_schema, entity_schema]
        context_provider.read_file_contents = {deleted_uri: deleted_file}
        isolation_handler = Mock()
        isolation_handler.get_read_scope.return_value = None
        isolation_handler.fill_identity_fields.side_effect = lambda item, **kwargs: item
        isolation_handler.calculate_memory_uris.side_effect = lambda **kwargs: [
            (
                "viking://user/alice/memories/profile.md"
                if kwargs["memory_type_schema"].memory_type == "profile"
                else "viking://user/alice/memories/entities/replacement.md"
            )
        ]

        loop = ExtractLoop(
            vlm=Mock(model="test-model"),
            viking_fs=Mock(),
            context_provider=context_provider,
            isolation_handler=isolation_handler,
        )
        loop._extract_context = SimpleNamespace(page_id_map=page_id_map)

        operations, _ = await loop.resolve_operations(
            AttrDict(
                profile=[{"summary": "profile", "page_id": 100}],
                entities=[{"name": "replacement", "page_id": 100}],
                delete_ids=[{"delete_page_id": 1, "replacement_page_id": 100}],
            )
        )

        assert operations.delete_file_contents == []
        assert operations.delete_replacements == {}

    @pytest.mark.asyncio
    async def test_existing_page_id_keeps_existing_uri_and_identity_fields(self):
        schema = MemoryTypeSchema(
            memory_type="entities",
            description="entity memory",
            directory="viking://user/{{ user_space }}/memories/entities",
            filename_template="{{ name }}.md",
            fields=[
                MemoryField(name="name", field_type=FieldType.STRING, merge_op=MergeOp.IMMUTABLE),
                MemoryField(name="owner", field_type=FieldType.STRING, merge_op=MergeOp.REPLACE),
                MemoryField(name="count", field_type=FieldType.INT64, merge_op=MergeOp.SUM),
                MemoryField(name="content", field_type=FieldType.STRING, merge_op=MergeOp.PATCH),
            ],
        )
        existing_uri = "viking://user/alice/memories/entities/Melanie.md"
        old_file = MemoryFile(
            uri=existing_uri,
            content="old content",
            memory_type="entities",
            extra_fields={"name": "Melanie", "owner": "Alice", "count": 2},
        )

        context_provider = Mock()
        context_provider.get_memory_schemas.return_value = [schema]
        context_provider._get_registry.return_value = Mock(get=Mock(return_value=schema))
        context_provider.read_file_contents = {existing_uri: old_file}

        isolation_handler = Mock()
        isolation_handler.get_read_scope.return_value = None
        isolation_handler.fill_identity_fields.side_effect = lambda item, role_scope=None: item

        loop = ExtractLoop(
            vlm=Mock(model="test-model"),
            viking_fs=Mock(),
            context_provider=context_provider,
            isolation_handler=isolation_handler,
        )
        loop._extract_context = SimpleNamespace(
            page_id_map=SimpleNamespace(resolve=lambda page_id: existing_uri)
        )

        operations, _ = await loop.resolve_operations(
            AttrDict(
                entities=[{"content": "new content", "page_id": 7}],
                delete_uris=[],
            )
        )

        operation = operations.upsert_operations[0]
        assert operation.uris == [existing_uri]
        assert operation.old_memory_file_content is old_file
        assert operation.memory_fields["name"] == "Melanie"
        assert "owner" not in operation.memory_fields
        assert "count" not in operation.memory_fields
        assert operation.memory_fields["content"] == "new content"
        isolation_handler.calculate_memory_uris.assert_not_called()

    @pytest.mark.asyncio
    async def test_existing_page_id_keeps_new_replace_and_sum_values(self):
        schema = MemoryTypeSchema(
            memory_type="entities",
            description="entity memory",
            fields=[
                MemoryField(name="name", field_type=FieldType.STRING, merge_op=MergeOp.IMMUTABLE),
                MemoryField(name="owner", field_type=FieldType.STRING, merge_op=MergeOp.REPLACE),
                MemoryField(name="count", field_type=FieldType.INT64, merge_op=MergeOp.SUM),
            ],
        )
        existing_uri = "viking://user/alice/memories/entities/Melanie.md"
        old_file = MemoryFile(
            uri=existing_uri,
            content="old content",
            memory_type="entities",
            extra_fields={"name": "Melanie", "owner": "Alice", "count": 2},
        )

        context_provider = Mock()
        context_provider.get_memory_schemas.return_value = [schema]
        context_provider.read_file_contents = {existing_uri: old_file}

        isolation_handler = Mock()
        isolation_handler.get_read_scope.return_value = None
        isolation_handler.fill_identity_fields.side_effect = lambda item, role_scope=None: item

        loop = ExtractLoop(
            vlm=Mock(model="test-model"),
            viking_fs=Mock(),
            context_provider=context_provider,
            isolation_handler=isolation_handler,
        )
        loop._extract_context = SimpleNamespace(
            page_id_map=SimpleNamespace(resolve=lambda page_id: existing_uri)
        )

        operations, _ = await loop.resolve_operations(
            AttrDict(
                entities=[
                    {"name": "Ignored", "owner": "Bob", "count": 3, "page_id": 7}
                ]
            )
        )

        operation = operations.upsert_operations[0]
        assert operation.memory_fields["name"] == "Melanie"
        assert operation.memory_fields["owner"] == "Bob"
        assert operation.memory_fields["count"] == 3

    def test_unresolved_page_ids_are_ignored(self):
        loop = ExtractLoop(vlm=Mock(model="test-model"), viking_fs=Mock(), context_provider=Mock())
        loop._extract_context = Mock()
        loop._extract_context.page_id_map = Mock()
        loop._extract_context.page_id_map._id_to_uri = {
            100: "viking://user/user_sample_0/memories/trajectories/a.md"
        }
        loop._extract_context.page_id_map.resolve.side_effect = lambda page_id: {
            100: "viking://user/user_sample_0/memories/trajectories/a.md"
        }.get(page_id)
        loop._extract_context.page_id_map.register_new_page_id = Mock()

        raw_links = [WikiLink(f=100, t=102, match_text="trip")]

        resolved = loop._resolve_links(raw_links, upsert_operations=[])

        assert resolved == []


class TestResolveLinksMultiUri:
    def test_normalize_operation_links_remaps_unregistered_low_page_id(self):
        page_id_map = PageIdMap()
        links = [WikiLink(f=5, t=7, match_text="event")]

        normalized = ExtractLoop._normalize_operation_links(
            links,
            {5: [100]},
            page_id_map,
        )

        assert len(normalized) == 1
        assert normalized[0].f == 100
        assert normalized[0].t == 7

    def test_normalize_operation_links_drops_collision_with_existing_page(self):
        page_id_map = PageIdMap()
        for index in range(5):
            page_id_map.get_page_id(f"viking://user/alice/memories/existing-{index}.md")
        links = [WikiLink(f=5, t=7, match_text="ambiguous")]

        normalized = ExtractLoop._normalize_operation_links(
            links,
            {5: [100]},
            page_id_map,
        )

        assert normalized == []

    def test_shared_page_id_pairs_matching_user_uris_only(self):
        loop = ExtractLoop(vlm=Mock(model="test-model"), viking_fs=Mock(), context_provider=Mock())
        loop._extract_context = Mock()
        loop._extract_context.page_id_map = Mock()
        loop._extract_context.page_id_map._id_to_uri = {}
        loop._extract_context.page_id_map.resolve.return_value = None
        loop._extract_context.page_id_map.register_new_page_id = Mock()

        raw_links = [WikiLink(f=100, t=101, match_text="trip")]
        upsert_operations = [
            ResolvedOperation(
                memory_fields={},
                memory_type="experiences",
                uris=[
                    "viking://user/a/memories/experiences/source.md",
                    "viking://user/b/memories/experiences/source.md",
                ],
                page_id=100,
            ),
            ResolvedOperation(
                memory_fields={},
                memory_type="experiences",
                uris=[
                    "viking://user/a/memories/experiences/target.md",
                    "viking://user/b/memories/experiences/target.md",
                ],
                page_id=101,
            ),
        ]

        resolved = loop._resolve_links(raw_links, upsert_operations=upsert_operations)

        assert {(link.from_uri, link.to_uri) for link in resolved} == {
            (
                "viking://user/a/memories/experiences/source.md",
                "viking://user/a/memories/experiences/target.md",
            ),
            (
                "viking://user/b/memories/experiences/source.md",
                "viking://user/b/memories/experiences/target.md",
            ),
        }

    def test_shared_page_id_self_link_is_ignored(self):
        loop = ExtractLoop(vlm=Mock(model="test-model"), viking_fs=Mock(), context_provider=Mock())
        loop._extract_context = Mock()
        loop._extract_context.page_id_map = Mock()
        loop._extract_context.page_id_map._id_to_uri = {}
        loop._extract_context.page_id_map.resolve.return_value = None

        raw_links = [WikiLink(f=100, t=100, match_text="trip")]
        upsert_operations = [
            ResolvedOperation(
                memory_fields={},
                memory_type="experiences",
                uris=[
                    "viking://user/a/memories/experiences/source.md",
                    "viking://user/b/memories/experiences/source.md",
                ],
                page_id=100,
            )
        ]

        assert loop._resolve_links(raw_links, upsert_operations=upsert_operations) == []


class TestResolutionRepair:
    @staticmethod
    def _loop(ctx=None):
        return ExtractLoop(
            vlm=Mock(model="test-model"),
            viking_fs=Mock(),
            context_provider=Mock(),
            isolation_handler=Mock(),
            ctx=ctx,
        )

    def test_retryable_resolution_issues_include_ranges(self):
        operations = ResolvedOperations(
            upsert_operations=[
                ResolvedOperation(
                    memory_fields={"ranges": "99"},
                    memory_type="events",
                    uris=[],
                    page_id=100,
                    resolution_skip=MemoryOperationSkip(
                        reason_code=MemoryOperationSkipCode.INVALID_RANGES,
                        reason="Message ranges are malformed or out of bounds",
                    ),
                )
            ],
            delete_file_contents=[],
            errors=[],
        )

        issues = self._loop()._retryable_resolution_issues(operations)

        assert issues == [
            {
                "memory_type": "events",
                "page_id": 100,
                "reason_code": "invalid_ranges",
                "reason": "Message ranges are malformed or out of bounds",
                "operation": {"ranges": "99"},
            }
        ]
        instruction = ExtractLoop._build_resolution_repair_instruction(issues)
        assert "valid in-bounds message indexes" in instruction
        assert '"reason_code": "invalid_ranges"' in instruction
        assert "server has preserved all successful operations" in instruction

    def test_policy_skip_is_not_retried(self):
        operations = ResolvedOperations(
            upsert_operations=[
                ResolvedOperation(
                    memory_fields={},
                    memory_type="events",
                    uris=[],
                    page_id=100,
                    resolution_skip=MemoryOperationSkip(
                        reason_code=MemoryOperationSkipCode.PEER_MEMORY_DISABLED,
                        reason="Peer memory writes are disabled",
                    ),
                )
            ],
            delete_file_contents=[],
            errors=[],
        )

        assert self._loop()._retryable_resolution_issues(operations) == []

    def test_non_event_resolution_skip_is_not_retried(self):
        operations = ResolvedOperations(
            upsert_operations=[
                ResolvedOperation(
                    memory_fields={"ranges": "99"},
                    memory_type="trajectories",
                    uris=[],
                    page_id=100,
                    resolution_skip=MemoryOperationSkip(
                        reason_code=MemoryOperationSkipCode.INVALID_RANGES,
                        reason="Message ranges are malformed or out of bounds",
                    ),
                )
            ],
            delete_file_contents=[],
            errors=[],
        )

        assert self._loop()._retryable_resolution_issues(operations) == []

    def test_missing_request_context_is_not_retried(self):
        operations = ResolvedOperations(
            upsert_operations=[
                ResolvedOperation(
                    memory_fields={"ranges": "0"},
                    memory_type="events",
                    uris=[],
                    page_id=100,
                    resolution_skip=MemoryOperationSkip(
                        reason_code=MemoryOperationSkipCode.NO_WRITABLE_TARGET,
                        reason="No writable memory target could be resolved",
                    ),
                )
            ],
            delete_file_contents=[],
            errors=[],
        )

        assert self._loop(ctx=None)._retryable_resolution_issues(operations) == []

    @pytest.mark.asyncio
    async def test_run_repairs_only_failed_event_and_preserves_successful_profile(self):
        schema = TestResolveOperations._event_schema()
        profile_schema = MemoryTypeSchema(
            memory_type="profile",
            directory="viking://user/{{ user_space }}/memories",
            filename_template="profile.md",
            fields=[],
        )
        event_uri = "viking://user/alice/memories/events/2026/08/27/demo.md"
        profile_uri = "viking://user/alice/memories/profile.md"
        context_provider = Mock()
        context_provider.get_memory_schemas.return_value = [schema, profile_schema]
        context_provider.get_output_language.return_value = "zh-CN"
        context_provider.get_tools.return_value = []
        context_provider.get_extract_context.return_value = SimpleNamespace(page_id_map=PageIdMap())
        context_provider.prefetch = AsyncMock(return_value=[])
        context_provider.read_file_contents = {}
        context_provider.instruction.return_value = "base instruction"

        isolation_handler = Mock()
        isolation_handler.get_read_scope.return_value = None
        isolation_handler.fill_identity_fields.side_effect = lambda item, **kwargs: item
        event_resolution_attempts = 0

        def calculate_memory_uris(*, memory_type_schema, operation, **kwargs):
            nonlocal event_resolution_attempts
            if memory_type_schema.memory_type == "profile":
                return [profile_uri]
            event_resolution_attempts += 1
            if event_resolution_attempts == 1:
                operation.resolution_skip = MemoryOperationSkip(
                    reason_code=MemoryOperationSkipCode.INVALID_RANGES,
                    reason="Message ranges are malformed or out of bounds",
                )
                return []
            operation.resolution_skip = None
            return [event_uri]

        isolation_handler.calculate_memory_uris.side_effect = calculate_memory_uris

        loop = ExtractLoop(
            vlm=Mock(model="test-model"),
            viking_fs=Mock(),
            context_provider=context_provider,
            isolation_handler=isolation_handler,
        )
        loop._call_llm = AsyncMock(
            side_effect=[
                (
                    [],
                    AttrDict(
                        events=[{"event_name": "demo", "ranges": "99", "page_id": 5}],
                        profile=[{"page_id": 101, "summary": "keep me"}],
                    ),
                ),
                (
                    [],
                    AttrDict(events=[{"event_name": "demo", "ranges": "0", "page_id": 100}]),
                ),
            ]
        )
        loop._check_unread_existing_files = AsyncMock(return_value=[])
        loop._validate_patch_operations = AsyncMock(return_value=[])
        loop.finalize_operations = AsyncMock()

        with (
            patch("openviking.session.memory.extract_loop.get_openviking_config") as mock_config,
            patch(
                "openviking.session.memory.extract_loop.SchemaModelGenerator.generate_all_models"
            ),
            patch(
                "openviking.session.memory.extract_loop.SchemaModelGenerator.create_structured_operations_model"
            ) as mock_create_model,
        ):
            mock_config.return_value = SimpleNamespace(memory=SimpleNamespace(link_enabled=False))
            mock_create_model.return_value = SimpleNamespace(
                model_fields={"events": None},
                model_json_schema=lambda: {},
            )

            final_operations, _ = await loop.run()

        assert loop._call_llm.await_count == 2
        assert event_resolution_attempts == 2
        assert len(final_operations.upsert_operations) == 2
        assert final_operations.upsert_operations[0].uris == [event_uri]
        assert final_operations.upsert_operations[0].page_id == 100
        assert final_operations.upsert_operations[1].memory_type == "profile"
        assert final_operations.upsert_operations[1].uris == [profile_uri]
        assert final_operations.upsert_operations[1].memory_fields["summary"] == "keep me"
        repair_messages = [
            message["content"]
            for message in loop._call_llm.await_args_list[-1].args[0]
            if message["role"] == "user" and "Resolution issues:" in message["content"]
        ]
        assert len(repair_messages) == 1
        assert '"reason_code": "invalid_ranges"' in repair_messages[0]
        assert '"event_name": "demo"' in repair_messages[0]

    def test_merge_event_repair_ignores_unrequested_memory_types(self):
        original_profile = ResolvedOperation(
            memory_fields={"summary": "original"},
            memory_type="profile",
            uris=["viking://user/alice/memories/profile.md"],
            page_id=1,
        )
        failed_event = ResolvedOperation(
            memory_fields={"ranges": "99"},
            memory_type="events",
            uris=[],
            page_id=100,
            resolution_skip=MemoryOperationSkip(
                reason_code=MemoryOperationSkipCode.INVALID_RANGES,
                reason="invalid",
            ),
        )
        repaired_event = ResolvedOperation(
            memory_fields={"ranges": "0"},
            memory_type="events",
            uris=["viking://user/alice/memories/events/demo.md"],
            page_id=100,
        )
        hallucinated_profile = original_profile.model_copy(
            update={"memory_fields": {"summary": "changed"}}
        )

        merged = ExtractLoop._merge_event_resolution_repair(
            ResolvedOperations(
                upsert_operations=[original_profile, failed_event],
                delete_file_contents=[],
                errors=[],
            ),
            ResolvedOperations(
                upsert_operations=[repaired_event, hallucinated_profile],
                delete_file_contents=[],
                errors=[],
            ),
        )

        assert merged.upsert_operations == [original_profile, repaired_event]

    def test_event_repair_subset_filters_extra_events_and_preserves_failed_id(self):
        successful_event = ResolvedOperation(
            memory_fields={"event_name": "success", "ranges": "0", "summary": "success"},
            memory_type="events",
            uris=["viking://user/alice/memories/events/success.md"],
            page_id=100,
        )
        failed_event = ResolvedOperation(
            memory_fields={"event_name": "failed", "ranges": "99", "summary": "original"},
            memory_type="events",
            uris=[],
            page_id=101,
            resolution_skip=MemoryOperationSkip(
                reason_code=MemoryOperationSkipCode.INVALID_RANGES,
                reason="invalid",
            ),
        )
        original = ResolvedOperations(
            upsert_operations=[successful_event, failed_event],
            delete_file_contents=[],
            errors=[],
        )

        subset = ExtractLoop._event_resolution_repair_subset(
            AttrDict(
                events=[
                    {
                        "event_name": "success",
                        "ranges": "0",
                        "summary": "duplicate",
                        "page_id": 100,
                    },
                    {
                        "event_name": "failed",
                        "ranges": "0",
                        "summary": "changed",
                        "page_id": 999,
                    },
                    {
                        "event_name": "failed",
                        "ranges": "0",
                        "summary": "changed",
                        "page_id": 101,
                    },
                ],
                profile=[{"page_id": 1, "summary": "ignore"}],
            ),
            original,
        )

        assert subset.events == [
            {
                "event_name": "failed",
                "ranges": "0",
                "summary": "original",
                "page_id": 101,
            }
        ]
        assert subset.delete_ids == []
        assert subset.links == []

    def test_merge_event_repair_rejects_changed_or_duplicate_page_id(self):
        failed_event = ResolvedOperation(
            memory_fields={"event_name": "failed", "ranges": "99"},
            memory_type="events",
            uris=[],
            page_id=101,
            resolution_skip=MemoryOperationSkip(
                reason_code=MemoryOperationSkipCode.INVALID_RANGES,
                reason="invalid",
            ),
        )
        changed_id = failed_event.model_copy(
            update={
                "uris": ["viking://user/alice/memories/events/changed.md"],
                "page_id": 102,
                "resolution_skip": None,
            }
        )
        duplicate_a = failed_event.model_copy(
            update={
                "uris": ["viking://user/alice/memories/events/a.md"],
                "resolution_skip": None,
            }
        )
        duplicate_b = duplicate_a.model_copy(
            update={"uris": ["viking://user/alice/memories/events/b.md"]}
        )
        original = ResolvedOperations(
            upsert_operations=[failed_event],
            delete_file_contents=[],
            errors=[],
        )

        changed_id_result = ExtractLoop._merge_event_resolution_repair(
            original,
            ResolvedOperations(
                upsert_operations=[changed_id],
                delete_file_contents=[],
                errors=[],
            ),
        )
        duplicate_result = ExtractLoop._merge_event_resolution_repair(
            original,
            ResolvedOperations(
                upsert_operations=[duplicate_a, duplicate_b],
                delete_file_contents=[],
                errors=[],
            ),
        )

        assert changed_id_result.upsert_operations == [failed_event]
        assert duplicate_result.upsert_operations == [failed_event]

    @pytest.mark.asyncio
    async def test_run_keeps_first_pass_when_event_repair_cannot_be_parsed(self):
        event_schema = TestResolveOperations._event_schema()
        profile_schema = MemoryTypeSchema(
            memory_type="profile",
            directory="viking://user/{{ user_space }}/memories",
            filename_template="profile.md",
            fields=[],
        )
        profile_uri = "viking://user/alice/memories/profile.md"
        context_provider = Mock()
        context_provider.get_memory_schemas.return_value = [event_schema, profile_schema]
        context_provider.get_output_language.return_value = "zh-CN"
        context_provider.get_tools.return_value = []
        context_provider.get_extract_context.return_value = SimpleNamespace(
            page_id_map=PageIdMap()
        )
        context_provider.prefetch = AsyncMock(return_value=[])
        context_provider.read_file_contents = {}
        context_provider.instruction.return_value = "base instruction"

        isolation_handler = Mock()
        isolation_handler.get_read_scope.return_value = None
        isolation_handler.fill_identity_fields.side_effect = lambda item, **kwargs: item

        def calculate_memory_uris(*, memory_type_schema, operation, **kwargs):
            if memory_type_schema.memory_type == "profile":
                return [profile_uri]
            operation.resolution_skip = MemoryOperationSkip(
                reason_code=MemoryOperationSkipCode.INVALID_RANGES,
                reason="Message ranges are malformed or out of bounds",
            )
            return []

        isolation_handler.calculate_memory_uris.side_effect = calculate_memory_uris
        loop = ExtractLoop(
            vlm=Mock(model="test-model"),
            viking_fs=Mock(),
            context_provider=context_provider,
            isolation_handler=isolation_handler,
            max_iterations=1,
        )
        loop._call_llm = AsyncMock(
            side_effect=[
                (
                    [],
                    AttrDict(
                        events=[{"event_name": "demo", "ranges": "99", "page_id": 5}],
                        profile=[{"page_id": 101, "summary": "keep me"}],
                    ),
                ),
                ([], None),
                ([], None),
            ]
        )
        loop.finalize_operations = AsyncMock()

        with (
            patch("openviking.session.memory.extract_loop.get_openviking_config") as mock_config,
            patch(
                "openviking.session.memory.extract_loop.SchemaModelGenerator.generate_all_models"
            ),
            patch(
                "openviking.session.memory.extract_loop.SchemaModelGenerator.create_structured_operations_model"
            ) as mock_create_model,
        ):
            mock_config.return_value = SimpleNamespace(memory=SimpleNamespace(link_enabled=False))
            mock_create_model.return_value = SimpleNamespace(
                model_fields={"events": None, "profile": None},
                model_json_schema=lambda: {},
            )

            final_operations, _ = await loop.run()

        assert loop._call_llm.await_count == 3
        assert len(final_operations.upsert_operations) == 2
        assert final_operations.upsert_operations[0].memory_type == "events"
        assert (
            final_operations.upsert_operations[0].resolution_skip.reason_code
            == MemoryOperationSkipCode.INVALID_RANGES
        )
        assert final_operations.upsert_operations[1].memory_type == "profile"
        assert final_operations.upsert_operations[1].uris == [profile_uri]
        assert final_operations.errors == []


class TestPageIdInstruction:
    @pytest.mark.asyncio
    async def test_run_always_includes_page_id_rules_when_links_disabled(self):
        context_provider = Mock()
        context_provider.get_memory_schemas.return_value = [
            SimpleNamespace(memory_type="experiences")
        ]
        context_provider.get_output_language.return_value = "zh-CN"
        context_provider.get_tools.return_value = []
        extract_context = Mock()
        extract_context.page_id_map = PageIdMap()
        context_provider.get_extract_context.return_value = extract_context
        context_provider.prefetch = AsyncMock(return_value=[])
        context_provider.read_file_contents = {}
        context_provider.instruction.return_value = "base instruction"
        context_provider._get_registry.return_value = Mock()

        isolation_handler = Mock()
        isolation_handler.get_read_scope.return_value = None
        isolation_handler.fill_identity_fields.side_effect = lambda item, role_scope=None: item
        isolation_handler.calculate_memory_uris.return_value = [
            "viking://user/alice/memories/experiences/chat.md"
        ]

        loop = ExtractLoop(
            vlm=Mock(model="test-model"),
            viking_fs=Mock(),
            context_provider=context_provider,
            isolation_handler=isolation_handler,
        )
        loop._call_llm = AsyncMock(
            return_value=(
                [],
                AttrDict(
                    experiences=[{"experience_name": "chat", "content": "updated", "page_id": 100}]
                ),
            )
        )
        loop._check_unread_existing_files = AsyncMock(return_value=[])
        loop.finalize_operations = AsyncMock()

        captured_messages = []

        def capture_messages(messages):
            captured_messages.extend(messages)

        with (
            patch("openviking.session.memory.extract_loop.get_openviking_config") as mock_config,
            patch("openviking.session.memory.extract_loop.pretty_print_messages", capture_messages),
            patch(
                "openviking.session.memory.extract_loop.SchemaModelGenerator.generate_all_models"
            ),
            patch(
                "openviking.session.memory.extract_loop.SchemaModelGenerator.create_structured_operations_model"
            ) as mock_create_model,
        ):
            mock_config.return_value = SimpleNamespace(memory=SimpleNamespace(link_enabled=False))
            mock_create_model.return_value = SimpleNamespace(model_json_schema=lambda: {})

            await loop.run()

        system_content = captured_messages[0]["content"]
        assert "## Page ID Rules" in system_content
        assert "## Read Format Rules" in system_content
        assert 'Every memory item you create or edit MUST include "page_id".' in system_content
        assert (
            "The read tool accepts `uri`, optional `offset` (0-indexed), and optional `limit`."
            in system_content
        )
        assert "each visible line is prefixed with `line_number<TAB>`" in system_content
        assert (
            "Never include the line-number prefix itself in `search` or `replace`."
            in system_content
        )
        assert "For existing items, use the page_id shown in read/search results." in system_content
        assert "For new items, assign a unique page_id >= 100." in system_content
        assert "When editing an existing item, reuse its existing page_id." in system_content
        assert "Link fields" not in system_content

    @pytest.mark.asyncio
    async def test_run_includes_link_page_id_rule_when_links_enabled(self):
        context_provider = Mock()
        context_provider.get_memory_schemas.return_value = [
            SimpleNamespace(memory_type="experiences")
        ]
        context_provider.get_output_language.return_value = "zh-CN"
        context_provider.get_tools.return_value = []
        extract_context = Mock()
        extract_context.page_id_map = PageIdMap()
        context_provider.get_extract_context.return_value = extract_context
        context_provider.prefetch = AsyncMock(return_value=[])
        context_provider.read_file_contents = {}
        context_provider.instruction.return_value = "base instruction"
        context_provider._get_registry.return_value = Mock()

        isolation_handler = Mock()
        isolation_handler.get_read_scope.return_value = None
        isolation_handler.fill_identity_fields.side_effect = lambda item, role_scope=None: item
        isolation_handler.calculate_memory_uris.return_value = [
            "viking://user/alice/memories/experiences/chat.md"
        ]

        loop = ExtractLoop(
            vlm=Mock(model="test-model"),
            viking_fs=Mock(),
            context_provider=context_provider,
            isolation_handler=isolation_handler,
        )
        loop._call_llm = AsyncMock(
            return_value=(
                [],
                AttrDict(
                    experiences=[{"experience_name": "chat", "content": "updated", "page_id": 100}],
                    links=[],
                ),
            )
        )
        loop._check_unread_existing_files = AsyncMock(return_value=[])
        loop.finalize_operations = AsyncMock()

        captured_messages = []

        def capture_messages(messages):
            captured_messages.extend(messages)

        with (
            patch("openviking.session.memory.extract_loop.get_openviking_config") as mock_config,
            patch("openviking.session.memory.extract_loop.pretty_print_messages", capture_messages),
            patch(
                "openviking.session.memory.extract_loop.SchemaModelGenerator.generate_all_models"
            ),
            patch(
                "openviking.session.memory.extract_loop.SchemaModelGenerator.create_structured_operations_model"
            ) as mock_create_model,
        ):
            mock_config.return_value = SimpleNamespace(memory=SimpleNamespace(link_enabled=True))
            mock_create_model.return_value = SimpleNamespace(model_json_schema=lambda: {})

            await loop.run()

        system_content = captured_messages[0]["content"]
        assert "## Page ID Rules" in system_content
        assert "## Read Format Rules" in system_content
        assert "## Link Rules" in system_content
        assert "Link fields `f` and `t` must reference these page_id values." in system_content
        assert "each visible line is prefixed with `line_number<TAB>`" in system_content
        assert "Only create links when the relationship is meaningful" in system_content


class TestFinalOperationsHydration:
    @pytest.mark.asyncio
    async def test_run_logs_final_operations_after_old_memory_file_is_hydrated(self):
        old_file = MemoryFile(
            uri="viking://user/Caroline/memories/experiences/chat.md", content="old"
        )

        context_provider = Mock()
        schema = SimpleNamespace(memory_type="experiences", fields=[])
        context_provider.get_memory_schemas.return_value = [schema]
        context_provider.get_output_language.return_value = "zh-CN"
        context_provider.get_tools.return_value = []
        extract_context = Mock()
        extract_context.page_id_map = PageIdMap()
        extract_context.page_id_map.get_page_id(old_file.uri)
        context_provider.get_extract_context.return_value = extract_context
        context_provider.prefetch = AsyncMock(return_value=[])
        context_provider.read_file_contents = {old_file.uri: old_file}
        context_provider.instruction.return_value = "test instruction"
        context_provider._get_registry.return_value = Mock()

        isolation_handler = Mock()
        isolation_handler.get_read_scope.return_value = "user://Caroline"
        isolation_handler.fill_identity_fields.side_effect = lambda item, role_scope=None: item

        loop = ExtractLoop(
            vlm=Mock(model="test-model"),
            viking_fs=Mock(),
            context_provider=context_provider,
            isolation_handler=isolation_handler,
        )
        loop._call_llm = AsyncMock(
            return_value=(
                [],
                AttrDict(
                    experiences=[{"experience_name": "chat", "content": "updated", "page_id": 1}]
                ),
            )
        )
        loop._check_unread_existing_files = AsyncMock(return_value=[])
        loop.finalize_operations = AsyncMock()

        with (
            patch("openviking.session.memory.extract_loop.get_openviking_config") as mock_config,
            patch(
                "openviking.session.memory.extract_loop.SchemaModelGenerator.generate_all_models"
            ),
            patch(
                "openviking.session.memory.extract_loop.SchemaModelGenerator.create_structured_operations_model"
            ) as mock_create_model,
            patch("openviking.session.memory.extract_loop.tracer.info") as mock_tracer_info,
        ):
            mock_config.return_value = SimpleNamespace(memory=SimpleNamespace(link_enabled=False))
            mock_create_model.return_value = SimpleNamespace(model_json_schema=lambda: {})

            final_operations, _ = await loop.run()

        assert extract_context.page_id_map.resolve(1) == old_file.uri

        op = final_operations.upsert_operations[0]
        assert op.page_id == 1
        assert op.old_memory_file_content is old_file
        assert final_operations.resolved_links == []
        logged_messages = [call.args[0] for call in mock_tracer_info.call_args_list]
        final_log = next(
            message for message in logged_messages if message.startswith("final_operations=")
        )
        assert '"old_memory_file_content":null' not in final_log

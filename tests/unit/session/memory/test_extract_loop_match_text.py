# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

from openviking.session.memory.dataclass import (
    MemoryField,
    MemoryFile,
    MemoryTypeSchema,
    ResolvedOperation,
    WikiLink,
)
from openviking.session.memory.extract_loop import ExtractLoop
from openviking.session.memory.merge_op import FieldType, MergeOp
from openviking.session.memory.page_id_map import PageIdMap


class AttrDict(dict):
    __getattr__ = dict.get


class TestResolveOperations:
    @pytest.mark.asyncio
    async def test_existing_page_id_keeps_existing_uri_and_identity_fields(self):
        schema = MemoryTypeSchema(
            memory_type="entities",
            description="entity memory",
            directory="viking://user/{{ user_space }}/memories/entities",
            filename_template="{{ name }}.md",
            fields=[
                MemoryField(name="name", field_type=FieldType.STRING, merge_op=MergeOp.REPLACE),
                MemoryField(name="content", field_type=FieldType.STRING, merge_op=MergeOp.PATCH),
            ],
        )
        existing_uri = "viking://user/alice/memories/entities/Melanie.md"
        old_file = MemoryFile(
            uri=existing_uri,
            content="old content",
            memory_type="entities",
            extra_fields={"name": "Melanie"},
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
                entities=[{"name": "WrongName", "content": "new content", "page_id": 7}],
                delete_uris=[],
            )
        )

        operation = operations.upsert_operations[0]
        assert operation.uris == [existing_uri]
        assert operation.old_memory_file_content is old_file
        assert operation.memory_fields["name"] == "Melanie"
        assert operation.memory_fields["content"] == "new content"
        isolation_handler.calculate_memory_uris.assert_not_called()

    def test_unresolved_page_ids_logs_at_info(self):
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

        with (
            patch("openviking.session.memory.extract_loop.tracer.info") as mock_info,
            patch("openviking.session.memory.extract_loop.tracer.error") as mock_error,
        ):
            resolved = loop._resolve_links(raw_links, upsert_operations=[])

        assert resolved == []
        mock_error.assert_not_called()
        mock_info.assert_any_call(
            "Skipping link with unresolved page_ids: f=100, t=102, "
            "from_uri=viking://user/user_sample_0/memories/trajectories/a.md, to_uri=None, "
            "op_page_map_keys=[]"
        )


class TestResolveLinksMultiUri:
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
        loop._mark_cache_breakpoint = AsyncMock()
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
        loop._mark_cache_breakpoint = AsyncMock()
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
        loop._mark_cache_breakpoint = AsyncMock()
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

# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

from openviking.prompts.manager import PromptManager
from openviking.session.memory.dataclass import MemoryField, MemoryFile
from openviking.session.memory.memory_type_registry import MemoryTypeRegistry
from openviking.session.memory.memory_updater import MemoryUpdater, MemoryUpdateResult
from openviking.session.memory.merge_op.base import FieldType
from openviking.session.memory.utils.memory_file_utils import MemoryFileUtils
from openviking.storage.queuefs.embedding_msg_converter import EmbeddingMsgConverter


class TestMemoryFieldSchema:
    def test_memory_field_no_longer_has_searchable(self):
        field = MemoryField(name="test", field_type=FieldType.STRING, description="test")
        assert not hasattr(field, "searchable")


class TestEmbeddingTemplateYamlParsing:
    @pytest.fixture(autouse=True)
    def setup(self):
        memory_dir = PromptManager._get_bundled_templates_dir() / "memory"
        self.registry = MemoryTypeRegistry(load_schemas=False)
        self.registry.load_from_yaml(str(memory_dir / "events.yaml"))
        self.registry.load_from_yaml(str(memory_dir / "preferences.yaml"))
        self.registry.load_from_yaml(str(memory_dir / "entities.yaml"))
        self.registry.load_from_yaml(str(memory_dir / "trajectories.yaml"))

    def test_events_exposes_embedding_template(self):
        schema = self.registry.get("events")
        assert schema.embedding_template == "{{ event_name }}\n\n{{ goal }}\n\n{{ content }}"

    def test_preferences_exposes_embedding_template(self):
        schema = self.registry.get("preferences")
        assert schema.embedding_template == "{{ user }}\n\n{{ topic }}\n\n{{ content }}"

    def test_entities_exposes_embedding_template(self):
        schema = self.registry.get("entities")
        assert schema.embedding_template == "{{ category }}\n\n{{ name }}\n\n{{ content }}"

    def test_trajectories_exposes_retrieval_anchor_template(self):
        schema = self.registry.get("trajectories")
        assert schema.embedding_template == "{{ trajectory_name }}\n\n{{ retrieval_anchor }}"


class TestContentTemplateRendering:
    def test_content_template_can_access_content_extra_fields_and_extract_context(self):
        memory_file = MemoryFile(
            content="Body content",
            extra_fields={"title": "Road Trip", "ranges": "0-1"},
        )
        extract_context = SimpleNamespace(get_year=lambda ranges: "2026")

        rendered = MemoryFileUtils.write(
            memory_file,
            content_template="{{ title }} | {{ content }} | {{ extract_context.get_year(ranges) }}",
            extract_context=extract_context,
        )

        assert rendered.startswith("Road Trip | Body content | 2026")


class TestEmbeddingTextConstruction:
    @pytest.mark.asyncio
    async def test_logs_final_embedding_text_before_vectorization(self):
        registry = MemoryTypeRegistry(load_schemas=False)
        registry._types["entities"] = registry._parse_memory_type(
            {
                "memory_type": "entities",
                "directory": "viking://user/{{ user_space }}/memories/entities",
                "filename_template": "{{ name }}.md",
                "embedding_template": "{{ category }} -> {{ name }} -> {{ content }}",
                "fields": [
                    {"name": "category", "type": "string"},
                    {"name": "name", "type": "string"},
                    {"name": "content", "type": "string"},
                ],
            }
        )

        updater = MemoryUpdater(registry=registry, vikingdb=Mock())
        updater._viking_fs = Mock()
        updater._viking_fs.read_file = AsyncMock(
            return_value=MemoryFileUtils.write(
                MemoryFile(
                    uri="viking://user/alice/memories/entities/person/alice.md",
                    memory_type="entities",
                    content="Plain body",
                    extra_fields={"category": "person", "name": "alice"},
                )
            )
        )
        updater._vikingdb.enqueue_embedding_msg = AsyncMock(return_value=True)

        result = MemoryUpdateResult()
        result.add_written("viking://user/alice/memories/entities/person/alice.md")
        ctx = SimpleNamespace(user=None, account_id="default")

        with (
            patch.object(EmbeddingMsgConverter, "from_context") as mock_from_context,
            patch("openviking.session.memory.memory_updater.logger.error") as mock_logger_error,
        ):
            mock_from_context.side_effect = lambda context: SimpleNamespace(
                telemetry_id=None, id="msg-1", message=context.get_vectorization_text()
            )
            await updater._vectorize_memories(
                result,
                ctx,
                extract_context=None,
                uri_memory_type_map={
                    "viking://user/alice/memories/entities/person/alice.md": "entities"
                },
            )

        mock_logger_error.assert_not_called()

    @pytest.mark.asyncio
    async def test_embedding_template_overrides_plain_content(self):
        registry = MemoryTypeRegistry(load_schemas=False)
        registry._types["entities"] = registry._parse_memory_type(
            {
                "memory_type": "entities",
                "directory": "viking://user/{{ user_space }}/memories/entities",
                "filename_template": "{{ name }}.md",
                "embedding_template": "{{ category }} -> {{ name }} -> {{ content }}",
                "fields": [
                    {"name": "category", "type": "string"},
                    {"name": "name", "type": "string"},
                    {"name": "content", "type": "string"},
                ],
            }
        )

        updater = MemoryUpdater(registry=registry, vikingdb=Mock())
        updater._viking_fs = Mock()
        updater._viking_fs.read_file = AsyncMock(
            return_value=MemoryFileUtils.write(
                MemoryFile(
                    uri="viking://user/alice/memories/entities/person/alice.md",
                    memory_type="entities",
                    content="Plain body",
                    extra_fields={"category": "person", "name": "alice"},
                )
            )
        )
        updater._vikingdb.enqueue_embedding_msg = AsyncMock(return_value=True)

        result = MemoryUpdateResult()
        result.add_written("viking://user/alice/memories/entities/person/alice.md")
        ctx = SimpleNamespace(user=None, account_id="default")

        with patch.object(EmbeddingMsgConverter, "from_context") as mock_from_context:
            mock_from_context.side_effect = lambda context: SimpleNamespace(
                telemetry_id=None, id="msg-1", message=context.get_vectorization_text()
            )
            await updater._vectorize_memories(
                result,
                ctx,
                extract_context=None,
                uri_memory_type_map={
                    "viking://user/alice/memories/entities/person/alice.md": "entities"
                },
            )

        vector_text = mock_from_context.call_args[0][0].get_vectorization_text()
        assert vector_text == "person -> alice -> Plain body"

    @pytest.mark.asyncio
    async def test_plain_content_fallback_still_works(self):
        registry = MemoryTypeRegistry(load_schemas=False)
        registry._types["notes"] = registry._parse_memory_type(
            {
                "memory_type": "notes",
                "directory": "viking://user/{{ user_space }}/memories/notes",
                "filename_template": "{{ slug }}.md",
                "fields": [
                    {"name": "slug", "type": "string"},
                    {"name": "content", "type": "string"},
                ],
            }
        )

        updater = MemoryUpdater(registry=registry, vikingdb=Mock())
        updater._viking_fs = Mock()
        updater._viking_fs.read_file = AsyncMock(
            return_value=MemoryFileUtils.write(
                MemoryFile(
                    uri="viking://user/alice/memories/notes/example.md",
                    memory_type="notes",
                    content="Fallback plain body",
                    extra_fields={"slug": "example"},
                )
            )
        )
        updater._vikingdb.enqueue_embedding_msg = AsyncMock(return_value=True)

        result = MemoryUpdateResult()
        result.add_written("viking://user/alice/memories/notes/example.md")
        ctx = SimpleNamespace(user=None, account_id="default")

        with patch.object(EmbeddingMsgConverter, "from_context") as mock_from_context:
            mock_from_context.side_effect = lambda context: SimpleNamespace(
                telemetry_id=None, id="msg-1", message=context.get_vectorization_text()
            )
            await updater._vectorize_memories(
                result,
                ctx,
                extract_context=None,
                uri_memory_type_map={"viking://user/alice/memories/notes/example.md": "notes"},
            )

        vector_text = mock_from_context.call_args[0][0].get_vectorization_text()
        assert vector_text == "Fallback plain body"

    @pytest.mark.asyncio
    async def test_embedding_template_receives_extract_context(self):
        registry = MemoryTypeRegistry(load_schemas=False)
        registry._types["events"] = registry._parse_memory_type(
            {
                "memory_type": "events",
                "directory": "viking://user/{{ user_space }}/memories/events",
                "filename_template": "{{ event_name }}.md",
                "embedding_template": "{{ extract_context.get_year(ranges) }} {{ content }}",
                "fields": [
                    {"name": "event_name", "type": "string"},
                    {"name": "ranges", "type": "string"},
                    {"name": "content", "type": "string"},
                ],
            }
        )

        updater = MemoryUpdater(registry=registry, vikingdb=Mock())
        updater._viking_fs = Mock()
        updater._viking_fs.read_file = AsyncMock(
            return_value=MemoryFileUtils.write(
                MemoryFile(
                    uri="viking://user/alice/memories/events/trip.md",
                    memory_type="events",
                    content="Trip summary",
                    extra_fields={"event_name": "trip", "ranges": "0-1"},
                )
            )
        )
        updater._vikingdb.enqueue_embedding_msg = AsyncMock(return_value=True)

        result = MemoryUpdateResult()
        result.add_written("viking://user/alice/memories/events/trip.md")
        ctx = SimpleNamespace(user=None, account_id="default")
        extract_context = SimpleNamespace(get_year=lambda ranges: "2026")

        with patch.object(EmbeddingMsgConverter, "from_context") as mock_from_context:
            mock_from_context.side_effect = lambda context: SimpleNamespace(
                telemetry_id=None, id="msg-1", message=context.get_vectorization_text()
            )
            await updater._vectorize_memories(
                result,
                ctx,
                extract_context=extract_context,
                uri_memory_type_map={"viking://user/alice/memories/events/trip.md": "events"},
            )

        vector_text = mock_from_context.call_args[0][0].get_vectorization_text()
        assert vector_text == "2026 Trip summary"

    @pytest.mark.asyncio
    async def test_embedding_template_render_failure_falls_back_to_plain_content(self):
        registry = MemoryTypeRegistry(load_schemas=False)
        registry._types["events"] = registry._parse_memory_type(
            {
                "memory_type": "events",
                "directory": "viking://user/{{ user_space }}/memories/events",
                "filename_template": "{{ event_name }}.md",
                "embedding_template": "{{ extract_context.get_year(ranges) }} {{ content }}",
                "fields": [
                    {"name": "event_name", "type": "string"},
                    {"name": "ranges", "type": "string"},
                    {"name": "content", "type": "string"},
                ],
            }
        )

        updater = MemoryUpdater(registry=registry, vikingdb=Mock())
        updater._viking_fs = Mock()
        updater._viking_fs.read_file = AsyncMock(
            return_value=MemoryFileUtils.write(
                MemoryFile(
                    uri="viking://user/alice/memories/events/trip.md",
                    memory_type="events",
                    content="Trip summary",
                    extra_fields={"event_name": "trip", "ranges": "0-1"},
                )
            )
        )
        updater._vikingdb.enqueue_embedding_msg = AsyncMock(return_value=True)

        result = MemoryUpdateResult()
        result.add_written("viking://user/alice/memories/events/trip.md")
        ctx = SimpleNamespace(user=None, account_id="default")

        with patch.object(EmbeddingMsgConverter, "from_context") as mock_from_context:
            mock_from_context.side_effect = lambda context: SimpleNamespace(
                telemetry_id=None, id="msg-1", message=context.get_vectorization_text()
            )
            await updater._vectorize_memories(
                result,
                ctx,
                extract_context=None,
                uri_memory_type_map={"viking://user/alice/memories/events/trip.md": "events"},
            )

        vector_text = mock_from_context.call_args[0][0].get_vectorization_text()
        assert vector_text == "Trip summary"

    @pytest.mark.asyncio
    async def test_embedding_template_missing_variable_falls_back_to_plain_content(self):
        registry = MemoryTypeRegistry(load_schemas=False)
        registry._types["events"] = registry._parse_memory_type(
            {
                "memory_type": "events",
                "directory": "viking://user/{{ user_space }}/memories/events",
                "filename_template": "{{ event_name }}.md",
                "embedding_template": "{{ missing_field }} {{ content }}",
                "fields": [
                    {"name": "event_name", "type": "string"},
                    {"name": "content", "type": "string"},
                ],
            }
        )

        updater = MemoryUpdater(registry=registry, vikingdb=Mock())
        updater._viking_fs = Mock()
        updater._viking_fs.read_file = AsyncMock(
            return_value=MemoryFileUtils.write(
                MemoryFile(
                    uri="viking://user/alice/memories/events/trip.md",
                    memory_type="events",
                    content="Trip summary",
                    extra_fields={"event_name": "trip"},
                )
            )
        )
        updater._vikingdb.enqueue_embedding_msg = AsyncMock(return_value=True)

        result = MemoryUpdateResult()
        result.add_written("viking://user/alice/memories/events/trip.md")
        ctx = SimpleNamespace(user=None, account_id="default")

        with patch.object(EmbeddingMsgConverter, "from_context") as mock_from_context:
            mock_from_context.side_effect = lambda context: SimpleNamespace(
                telemetry_id=None, id="msg-1", message=context.get_vectorization_text()
            )
            await updater._vectorize_memories(
                result,
                ctx,
                extract_context=None,
                uri_memory_type_map={"viking://user/alice/memories/events/trip.md": "events"},
            )

        vector_text = mock_from_context.call_args[0][0].get_vectorization_text()
        assert vector_text == "Trip summary"

# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

from openviking.session.memory.dataclass import MemoryFile, WikiLink
from openviking.session.memory.extract_loop import ExtractLoop


class AttrDict(dict):
    __getattr__ = dict.get


class TestValidateMatchText:
    """Tests for ExtractLoop._validate_match_text static method."""

    def test_none_match_text_allowed(self):
        assert ExtractLoop._validate_match_text(None, "some content") is True

    def test_empty_match_text_allowed(self):
        assert ExtractLoop._validate_match_text("", "some content") is True

    def test_single_word_found_in_conversation(self):
        assert ExtractLoop._validate_match_text("Python", "I love Python programming") is True

    def test_chinese_word_found_in_conversation(self):
        assert ExtractLoop._validate_match_text("机器学习", "我在研究机器学习技术") is True

    def test_chinese_compound_word(self):
        # Chinese compound words (no spaces) are single words
        assert ExtractLoop._validate_match_text("深度学习", "我们训练了一个深度学习模型") is True

    def test_multi_word_phrase_rejected(self):
        # "machine learning" is a phrase (contains space), not a single word
        assert (
            ExtractLoop._validate_match_text(
                "machine learning", "I study machine learning at school"
            )
            is False
        )

    def test_multi_word_chinese_phrase_with_space_rejected(self):
        # Chinese phrase with space is rejected
        assert ExtractLoop._validate_match_text("机器 学习", "我在研究机器学习技术") is False

    def test_not_found_in_conversation(self):
        assert ExtractLoop._validate_match_text("Java", "I love Python programming") is False

    def test_not_found_in_empty_conversation(self):
        assert ExtractLoop._validate_match_text("Python", "") is False

    def test_word_with_tab_rejected(self):
        # Tab character means it's not a single word
        assert ExtractLoop._validate_match_text("hello\tworld", "hello\tworld here") is False

    def test_word_with_newline_rejected(self):
        assert ExtractLoop._validate_match_text("hello\nworld", "hello\nworld here") is False

    def test_word_without_punctuation(self):
        assert ExtractLoop._validate_match_text("API", "The API is great") is True

    def test_word_with_hyphen(self):
        # Hyphenated word (no spaces) is a single word
        assert ExtractLoop._validate_match_text("self-care", "I practice self-care daily") is True

    def test_word_with_period(self):
        # Word with period (no spaces) is a single word
        assert ExtractLoop._validate_match_text("Python.", "I use Python. daily") is True


class TestResolveLinksLogging:
    def test_unresolved_page_ids_logs_at_info(self):
        loop = ExtractLoop(vlm=Mock(model="test-model"), viking_fs=Mock(), context_provider=Mock())
        loop._page_id_map = Mock()
        loop._page_id_map._id_to_uri = {100: "viking://agent/agent_sample_0/memories/trajectories/a.md"}
        loop._page_id_map.resolve.side_effect = lambda page_id: {
            100: "viking://agent/agent_sample_0/memories/trajectories/a.md"
        }.get(page_id)
        loop._page_id_map.register_new_page_id = Mock()

        raw_links = [WikiLink(f=100, t=102, match_text="trip")]

        with patch("openviking.session.memory.extract_loop.tracer.info") as mock_info, patch(
            "openviking.session.memory.extract_loop.tracer.error"
        ) as mock_error:
            resolved = loop._resolve_links(raw_links, upsert_operations=[])

        assert resolved == []
        mock_error.assert_not_called()
        mock_info.assert_any_call(
            "Skipping link with unresolved page_ids: f=100, t=102, "
            "from_uri=viking://agent/agent_sample_0/memories/trajectories/a.md, to_uri=None, "
            "op_page_map_keys=[]"
        )


class TestFinalOperationsHydration:
    @pytest.mark.asyncio
    async def test_run_logs_final_operations_after_old_memory_file_is_hydrated(self):
        old_file = MemoryFile(uri="viking://user/Caroline/memories/experiences/chat.md", content="old")

        context_provider = Mock()
        schema = SimpleNamespace(memory_type="experiences")
        context_provider.get_memory_schemas.return_value = [schema]
        context_provider.get_output_language.return_value = "zh-CN"
        context_provider.get_tools.return_value = []
        context_provider.get_extract_context.return_value = Mock()
        context_provider.prefetch = AsyncMock(return_value=[])
        context_provider.read_file_contents = {old_file.uri: old_file}
        context_provider.instruction.return_value = "test instruction"
        context_provider.set_page_id_map = Mock()
        context_provider._get_registry.return_value = Mock()

        isolation_handler = Mock()
        isolation_handler.get_read_scope.return_value = "user://Caroline"
        isolation_handler.fill_role_ids.side_effect = lambda item, role_scope=None: item

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
            patch(
                "openviking.session.memory.extract_loop.supplement_operation_uris"
            ) as mock_supplement_operation_uris,
            patch("openviking.session.memory.extract_loop.tracer.info") as mock_tracer_info,
        ):
            mock_config.return_value = SimpleNamespace(memory=SimpleNamespace(link_enabled=False))
            mock_create_model.return_value = SimpleNamespace(model_json_schema=lambda: {})

            def hydrate_existing_uri(operations, registry, extract_context, isolation_handler):
                operations.upsert_operations[0].uris = [old_file.uri]

            mock_supplement_operation_uris.side_effect = hydrate_existing_uri

            final_operations, _ = await loop.run()

        op = final_operations.upsert_operations[0]
        assert op.old_memory_file_content is old_file
        logged_messages = [call.args[0] for call in mock_tracer_info.call_args_list]
        final_log = next(message for message in logged_messages if message.startswith("final_operations="))
        assert '"old_memory_file_content":null' not in final_log

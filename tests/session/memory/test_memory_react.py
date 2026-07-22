# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
Tests for memory ExtractLoop orchestrator.
"""

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from openviking.session.memory.dataclass import (
    MemoryField,
    MemoryTypeSchema,
)
from openviking.session.memory.extract_loop import (
    ExtractLoop,
    PostValidationRetryDecision,
)
from openviking.session.memory.memory_type_registry import MemoryTypeRegistry
from openviking.session.memory.memory_updater import ExtractContext
from openviking.session.memory.merge_op.base import FieldType, MergeOp


class TestPreFetchFileFiltering:
    """Tests for the file filtering logic in pre-fetch."""

    def test_only_abstract_and_overview_are_read_when_both_exist(self):
        """Test that from a directory listing, only .abstract.md and .overview.md are selected when both exist."""
        # Mock directory entries - both .abstract.md and .overview.md exist
        test_entries = [
            {"name": ".abstract.md", "isDir": False},
            {"name": ".overview.md", "isDir": False},
            {"name": "regular-file.md", "isDir": False},
            {"name": "another-file.md", "isDir": False},
            {"name": "subdir", "isDir": True},
            {"name": ".gitkeep", "isDir": False},
            {"name": "data.json", "isDir": False},
        ]

        dir_uri = "viking://user/default/memories/preferences"
        single_file_schemas = set()

        # Apply the filtering logic manually (replicate what _pre_fetch_context does)
        md_files = list(single_file_schemas)

        for entry in test_entries:
            name = entry.get("name", "")
            if not entry.get("isDir", False):
                # Only read .abstract.md and .overview.md from multi-file schema directories
                # (only if they actually exist in the directory listing)
                if name == ".abstract.md" or name == ".overview.md":
                    file_uri = f"{dir_uri}/{name}"
                    if file_uri not in md_files:
                        md_files.append(file_uri)

        # Verify only the two special files are included
        assert len(md_files) == 2
        assert f"{dir_uri}/.abstract.md" in md_files
        assert f"{dir_uri}/.overview.md" in md_files

        # Verify regular .md files are NOT included
        assert f"{dir_uri}/regular-file.md" not in md_files
        assert f"{dir_uri}/another-file.md" not in md_files

    def test_only_read_existing_files(self):
        """Test that only existing files are read - when only one exists or none exist."""
        dir_uri = "viking://user/default/memories/preferences"
        single_file_schemas = set()

        # Case 1: Only .abstract.md exists
        test_entries1 = [
            {"name": ".abstract.md", "isDir": False},
            {"name": "regular-file.md", "isDir": False},
        ]
        md_files1 = list(single_file_schemas)
        for entry in test_entries1:
            name = entry.get("name", "")
            if not entry.get("isDir", False):
                if name == ".abstract.md" or name == ".overview.md":
                    file_uri = f"{dir_uri}/{name}"
                    if file_uri not in md_files1:
                        md_files1.append(file_uri)
        assert len(md_files1) == 1
        assert f"{dir_uri}/.abstract.md" in md_files1
        assert f"{dir_uri}/.overview.md" not in md_files1

        # Case 2: Only .overview.md exists
        test_entries2 = [
            {"name": ".overview.md", "isDir": False},
            {"name": "regular-file.md", "isDir": False},
        ]
        md_files2 = list(single_file_schemas)
        for entry in test_entries2:
            name = entry.get("name", "")
            if not entry.get("isDir", False):
                if name == ".abstract.md" or name == ".overview.md":
                    file_uri = f"{dir_uri}/{name}"
                    if file_uri not in md_files2:
                        md_files2.append(file_uri)
        assert len(md_files2) == 1
        assert f"{dir_uri}/.overview.md" in md_files2
        assert f"{dir_uri}/.abstract.md" not in md_files2

        # Case 3: Neither exists
        test_entries3 = [
            {"name": "regular-file.md", "isDir": False},
        ]
        md_files3 = list(single_file_schemas)
        for entry in test_entries3:
            name = entry.get("name", "")
            if not entry.get("isDir", False):
                if name == ".abstract.md" or name == ".overview.md":
                    file_uri = f"{dir_uri}/{name}"
                    if file_uri not in md_files3:
                        md_files3.append(file_uri)
        assert len(md_files3) == 0

    def test_schema_type_detection_logic(self):
        """Test the logic for determining if a schema is multi-file or single-file."""
        # Test cases: (filename_template, expected_has_variables)
        test_cases = [
            ("{topic}.md", True),
            ("static.md", False),
            ("{tool_name}.md", True),
            ("profile.md", False),
            ("", False),  # empty template
            ("{entity_name}-details.md", True),
            ("fixed-filename.md", False),
            ("{a}/{b}.md", True),
        ]

        for filename_template, expected_has_variables in test_cases:
            # Replicate the logic from _pre_fetch_context
            has_variables = False
            if filename_template:
                has_variables = "{" in filename_template and "}" in filename_template

            assert has_variables == expected_has_variables, (
                f"Template '{filename_template}': expected has_variables={expected_has_variables}"
            )


class TestAllowedDirectoriesList:
    """Tests for _get_allowed_directories_list method."""

    @pytest.fixture
    def mock_vlm(self):
        """Create a mock VLM."""
        vlm = MagicMock()
        vlm.model = "test-model"
        vlm.max_retries = 2
        vlm.get_completion_async = AsyncMock()
        return vlm

    @pytest.fixture
    def mock_viking_fs(self):
        """Create a mock VikingFS."""
        return MagicMock()

    def test_get_allowed_directories_list(self, mock_vlm, mock_viking_fs):
        """Test that allowed directories list is properly formatted."""
        registry = MemoryTypeRegistry(load_schemas=False)

        schema1 = MemoryTypeSchema(
            memory_type="preferences",
            description="Preferences",
            directory="viking://user/{{ user_space }}/memories/preferences",
            filename_template="{{ topic }}.md",
            fields=[],
        )
        schema2 = MemoryTypeSchema(
            memory_type="tools",
            description="Tools",
            directory="viking://user/{{ user_space }}/memories/tools",
            filename_template="{{ tool_name }}.md",
            fields=[],
        )

        registry.register(schema1)
        registry.register(schema2)

        result = registry.list_search_uris(user_space="default")

        assert "viking://user/default/memories/preferences" in result
        assert "viking://user/default/memories/tools" in result


class TestExtractLoopFinalJsonRetry:
    def test_final_instruction_includes_schema_aware_empty_json(self):
        extract_loop = object.__new__(ExtractLoop)
        extract_loop._expected_fields = ["preferences", "tools"]

        instruction = extract_loop._build_final_operations_instruction()

        assert "ONLY a valid JSON object" in instruction
        assert '"delete_ids": []' in instruction
        assert '"preferences": []' in instruction
        assert '"tools": []' in instruction

    def test_final_skeleton_always_includes_delete_ids(self):
        extract_loop = object.__new__(ExtractLoop)
        extract_loop._expected_fields = ["preferences"]

        assert extract_loop._build_final_operations_skeleton() == {
            "delete_ids": [],
            "preferences": [],
        }

    @pytest.mark.asyncio
    async def test_final_unparseable_response_raises_instead_of_empty_success(self):
        class FakeVLM:
            model = "test-model"

            def __init__(self):
                self.seen_messages = []

            async def get_completion_async(self, **kwargs):
                self.seen_messages.append(list(kwargs["messages"]))
                return "this is not json"

        class FakeContextProvider:
            read_file_contents = {}

            def get_memory_schemas(self, ctx):
                return [
                    MemoryTypeSchema(
                        memory_type="preferences",
                        description="Preferences",
                        directory="viking://user/{user_space}/memories/preferences",
                        filename_template="{topic}.md",
                        fields=[],
                    )
                ]

            def get_tools(self):
                return []

            def get_extract_context(self):
                return MagicMock()

            def get_output_language(self):
                return "en"

            def instruction(self):
                return "Extract memory operations."

            async def prefetch(self):
                return []

        vlm = FakeVLM()
        extract_loop = ExtractLoop(
            vlm=vlm,
            viking_fs=MagicMock(),
            context_provider=FakeContextProvider(),
            max_iterations=1,
        )

        result, _ = await extract_loop.run()
        assert result.errors
        assert "Final response could not be parsed" in result.errors[0]

        final_prompts = [
            message["content"]
            for messages in vlm.seen_messages
            for message in messages
            if message.get("role") == "user"
            and "maximum number of tool call iterations" in message.get("content", "")
        ]
        assert final_prompts
        assert '"delete_ids": []' in final_prompts[-1]
        assert '"preferences": []' in final_prompts[-1]


class TestExtractLoopPostValidationHook:
    @staticmethod
    def _preference_schema() -> MemoryTypeSchema:
        return MemoryTypeSchema(
            memory_type="preferences",
            description="Preferences",
            directory="viking://user/{user_space}/memories/preferences",
            filename_template="{topic}.md",
            fields=[
                MemoryField(
                    name="topic",
                    field_type=FieldType.STRING,
                    description="topic",
                    merge_op=MergeOp.IMMUTABLE,
                ),
                MemoryField(
                    name="content",
                    field_type=FieldType.STRING,
                    description="content",
                    merge_op=MergeOp.PATCH,
                ),
            ],
        )

    @staticmethod
    def _preference_ops(content: str) -> str:
        return json.dumps(
            {
                "preferences": [
                    {
                        "page_id": 100,
                        "topic": "color",
                        "content": content,
                    }
                ],
                "delete_ids": [],
            }
        )

    class _FakeContextProvider:
        read_file_contents = {}

        def __init__(self, schemas):
            self._schemas = schemas
            self._extract_context = ExtractContext([], split_long_text_messages=False)
            self._registry = MemoryTypeRegistry(load_schemas=False)
            for schema in schemas:
                self._registry.register(schema)

        def _get_registry(self):
            return self._registry

        def get_memory_schemas(self, ctx):
            return self._schemas

        def get_tools(self):
            return []

        def get_extract_context(self):
            return self._extract_context

        def get_output_language(self):
            return "en"

        def instruction(self):
            return "Extract memory operations."

        async def prefetch(self):
            return []

    class _FakeIsolationHandler:
        def get_read_scope(self):
            return None

        def fill_identity_fields(self, item_dict, role_scope=None, memory_type_schema=None):
            return None

        def calculate_memory_uris(self, memory_type_schema, operation, extract_context):
            name = operation.memory_fields.get("topic", "memory")
            return [f"viking://user/default/memories/{memory_type_schema.memory_type}/{name}.md"]

    class _SequenceVLM:
        model = "test-model"

        def __init__(self, responses):
            self.responses = list(responses)
            self.seen_calls = []

        async def get_completion_async(self, **kwargs):
            self.seen_calls.append(kwargs)
            if not self.responses:
                raise AssertionError("No fake VLM response left")
            return self.responses.pop(0)

    @pytest.mark.asyncio
    async def test_post_validation_hook_can_append_latest_draft_and_retry(self):
        decisions = [
            PostValidationRetryDecision(
                retry=True,
                instruction="Rewrite the complete JSON object with a source-bound correction.",
                include_latest_draft=True,
            ),
            None,
        ]

        async def post_validation_hook(operations, retry_count, *, messages, latest_draft):
            expected_retry_count = 0 if len(decisions) == 2 else 1
            assert retry_count == expected_retry_count
            assert operations.upsert_operations
            assert messages
            assert latest_draft.preferences
            decision = decisions.pop(0)
            if decision is not None:
                focused_draft = latest_draft.model_copy(deep=True)
                focused_draft.preferences[0].content = "focused rejected draft"
                decision.latest_draft_override = focused_draft
            return decision

        vlm = self._SequenceVLM(
            [
                self._preference_ops("first draft"),
                self._preference_ops("second draft"),
            ]
        )
        extract_loop = ExtractLoop(
            vlm=vlm,
            viking_fs=MagicMock(),
            context_provider=self._FakeContextProvider([self._preference_schema()]),
            isolation_handler=self._FakeIsolationHandler(),
            post_validation_hook=post_validation_hook,
            max_iterations=1,
        )

        result, _ = await extract_loop.run()

        assert len(result.upsert_operations) == 1
        assert result.upsert_operations[0].memory_fields["content"] == "second draft"
        assert len(vlm.seen_calls) == 2
        retry_messages = vlm.seen_calls[1]["messages"]
        assert any(
            message["role"] == "assistant" and "focused rejected draft" in message["content"]
            for message in retry_messages
        )
        assert not any(
            message["role"] == "assistant" and "first draft" in message["content"]
            for message in retry_messages
        )
        assert any(
            message["role"] == "user" and "source-bound correction" in message["content"]
            for message in retry_messages
        )

    @pytest.mark.asyncio
    async def test_post_validation_hook_can_discard_without_exception(self):
        def post_validation_hook(operations, retry_count, *, messages, latest_draft):
            return PostValidationRetryDecision(discard=True)

        vlm = self._SequenceVLM([self._preference_ops("discard me")])
        extract_loop = ExtractLoop(
            vlm=vlm,
            viking_fs=MagicMock(),
            context_provider=self._FakeContextProvider([self._preference_schema()]),
            isolation_handler=self._FakeIsolationHandler(),
            post_validation_hook=post_validation_hook,
            max_iterations=1,
        )

        result, _ = await extract_loop.run()

        assert result.upsert_operations == []
        assert result.errors == []
        assert len(vlm.seen_calls) == 1

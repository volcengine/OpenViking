# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
Tests for memory ExtractLoop orchestrator.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from openviking.session.memory.dataclass import (
    MemoryFile,
    MemoryTypeSchema,
    ResolvedOperation,
    ResolvedOperations,
)
from openviking.session.memory.extract_loop import (
    ExtractLoop,
)
from openviking.session.memory.merge_op import SearchReplaceBlock, StrPatch
from openviking.session.memory.schema_model_generator import SchemaModelGenerator


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


class TestExtractLoopFinalJsonRetry:
    @pytest.mark.asyncio
    async def test_structured_parser_preserves_delete_ids(self):
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

        vlm = MagicMock()
        vlm.model = "test-model"
        vlm.get_completion_async = AsyncMock(
            return_value='{"delete_ids": [{"delete_page_id": 7, "replacement_page_id": 11}]}'
        )
        # decision_reasoning response retained for when the schema field is re-enabled:
        # return_value=(
        #     '{"delete_ids": [{"delete_page_id": 7, "replacement_page_id": 11}], '
        #     '"decision_reasoning": [{"page_id": 7, "remove": [], '
        #     '"has_unaffected_facts": false, "action": "DELETE"}]}'
        # )
        extract_loop = ExtractLoop(
            vlm=vlm,
            viking_fs=MagicMock(),
            context_provider=FakeContextProvider(),
            max_iterations=1,
        )
        resolved = ResolvedOperations(
            upsert_operations=[],
            delete_file_contents=[],
            errors=[],
        )
        extract_loop.resolve_operations = AsyncMock(return_value=(resolved, []))
        extract_loop._check_unread_existing_files = AsyncMock(return_value={})
        extract_loop._validate_patch_operations = AsyncMock(return_value=[])
        extract_loop.finalize_operations = AsyncMock()

        await extract_loop.run()

        parsed_operations = extract_loop.resolve_operations.await_args.args[0]
        assert len(parsed_operations.delete_ids) == 1
        assert parsed_operations.delete_ids[0].delete_page_id == 7
        assert parsed_operations.delete_ids[0].replacement_page_id == 11
        # assert parsed_operations.decision_reasoning[0].action == "DELETE"
        # assert parsed_operations.decision_reasoning[0].remove == []

    def test_add_only_contract_does_not_allow_delete_ids(self):
        schema = MemoryTypeSchema(
            memory_type="trajectories",
            description="Trajectories",
            directory="viking://agent/{agent_space}/memories/trajectories",
            filename_template="{task}.md",
            fields=[],
            operation_mode="add_only",
        )
        generator = SchemaModelGenerator([schema], template_context={"language": "en"})

        operations_model = generator.create_structured_operations_model()
        fields = operations_model.model_fields
        assert "decision_reasoning" not in fields
        assert "delete_ids" not in fields

        # decision_reasoning schema tests retained for when the field is re-enabled:
        # assert next(iter(fields)) == "decision_reasoning"
        # description = fields["decision_reasoning"].description
        # assert "one decision for every related read page" in description
        # decisions = operations_model.model_validate(
        #     {
        #         "decision_reasoning": [
        #             {
        #                 "page_id": 7,
        #                 "remove": ["- affected fact"],
        #                 "has_unaffected_facts": True,
        #                 "action": "UPDATE",
        #             }
        #         ]
        #     }
        # ).decision_reasoning
        # assert decisions[0].has_unaffected_facts is True
        # assert operations_model.model_validate({}).decision_reasoning == []
        # with pytest.raises(ValueError):
        #     operations_model.model_validate(
        #         {
        #             "decision_reasoning": [
        #                 {
        #                     "page_id": 7,
        #                     "remove": [],
        #                     "has_unaffected_facts": True,
        #                     "action": "REMOVE",
        #                 }
        #             ]
        #         }
        #     )
        # merge_fields = (
        #     SchemaModelGenerator([schema], include_decision_reasoning=False)
        #     .create_structured_operations_model()
        #     .model_fields
        # )
        # assert "decision_reasoning" not in merge_fields

    def test_final_instruction_includes_schema_aware_empty_json(self):
        extract_loop = object.__new__(ExtractLoop)
        extract_loop._expected_fields = ["preferences", "tools"]
        # extract_loop._expected_fields = ["preferences", "tools", "decision_reasoning"]

        instruction = extract_loop._build_final_operations_instruction()

        assert "ONLY a valid JSON object" in instruction
        assert '"delete_ids": []' in instruction
        assert '"preferences": []' in instruction
        assert '"tools": []' in instruction
        assert '"decision_reasoning": []' not in instruction
        # assert '"decision_reasoning": []' in instruction

    @pytest.mark.asyncio
    async def test_patch_validation_uses_plain_and_sequential_content(self):
        target_uri = "viking://user/default/memories/profile.md"
        old_file = MemoryFile(
            uri=target_uri,
            content="# A\n- [Shared](./shared.md)\n\n# B\n- Shared",
        )
        operation = ResolvedOperation(
            old_memory_file_content=old_file,
            memory_type="profile",
            uris=[target_uri],
            memory_fields={
                "content": StrPatch(
                    blocks=[
                        SearchReplaceBlock(
                            search="# A\n- Shared",
                            replace="# A\n- A-only",
                        ),
                        SearchReplaceBlock(search="- Shared", replace="- B-only"),
                        SearchReplaceBlock(search="- Missing", replace="- Added"),
                    ]
                )
            },
        )
        extract_loop = object.__new__(ExtractLoop)
        extract_loop.context_provider = MagicMock(read_file_contents={target_uri: old_file})

        errors = await extract_loop._validate_patch_operations(
            ResolvedOperations(
                upsert_operations=[operation],
                delete_file_contents=[],
                errors=[],
            )
        )

        assert errors == [
            {
                "uri": target_uri,
                "page_id": None,
                "field": "content",
                "block_index": 3,
                "search": "- Missing",
                "reason": "not_found",
                "match_count": 0,
                "found_in_other_uris": [],
            }
        ]

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

        system_prompts = [
            message["content"]
            for messages in vlm.seen_messages
            for message in messages
            if message.get("role") == "system"
        ]
        assert system_prompts
        initial_system_prompt = system_prompts[0]
        assert "`delete_ids` deletes the whole item" in initial_system_prompt
        assert "only if every substantive fact is in scope" in initial_system_prompt
        assert "otherwise MUST use DELETE blocks" in initial_system_prompt
        assert "not inferring scope from the file name/topic" in initial_system_prompt

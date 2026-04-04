# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
Tests for memory ExtractLoop orchestrator.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openviking.session.memory.dataclass import (
    MemoryTypeSchema,
)
from openviking.session.memory.extract_loop import (
    ExtractLoop,
)


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
        # Patch the registry loading so we can inject our own schemas
        with patch(
            "openviking.session.memory.extract_loop.MemoryTypeRegistry"
        ) as mock_registry_cls:
            mock_registry = MagicMock()

            # Create test schemas
            schema1 = MemoryTypeSchema(
                memory_type="preferences",
                description="Preferences",
                directory="viking://user/{user_space}/memories/preferences",
                filename_template="{topic}.md",
                fields=[],
            )
            schema2 = MemoryTypeSchema(
                memory_type="tools",
                description="Tools",
                directory="viking://agent/{agent_space}/memories/tools",
                filename_template="{tool_name}.md",
                fields=[],
            )

            mock_registry.list_all.return_value = [schema1, schema2]
            mock_registry_cls.return_value = mock_registry

            # Also patch schema_model_generator and schema_prompt_generator
            with (
                patch("openviking.session.memory.extract_loop.SchemaModelGenerator") as mock_smg,
                patch("openviking.session.memory.extract_loop.SchemaPromptGenerator") as mock_spg,
            ):
                mock_smg_instance = MagicMock()
                mock_smg_instance.generate_all_models = MagicMock()
                mock_smg_instance.get_llm_json_schema = MagicMock(return_value={})
                mock_smg.return_value = mock_smg_instance

                mock_spg.return_value = MagicMock()

                # Create ExtractLoop
                extract_loop = ExtractLoop(mock_vlm, mock_viking_fs)

                # Call the method
                result = extract_loop._get_allowed_directories_list()

                # Verify the result contains the expected directories with variables replaced
                assert "viking://user/default/memories/preferences" in result
                assert "viking://agent/default/memories/tools" in result

# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Tests for schema_models.py - dynamic Pydantic model generation."""

import tempfile
from pathlib import Path

import pytest
import yaml

from openviking.session.memory.dataclass import (
    MemoryField,
    MemoryTypeSchema,
    WikiLink,
)
from openviking.session.memory.memory_type_registry import (
    MemoryTypeRegistry,
    create_default_registry,
)
from openviking.session.memory.merge_op.base import FieldType, MergeOp
from openviking.session.memory.schema_model_generator import (
    SchemaModelGenerator,
    SchemaPromptGenerator,
    to_pascal_case,
)


class TestToPascalCase:
    """Tests for to_pascal_case helper function."""

    def test_snake_case(self):
        """Test converting snake_case to PascalCase."""
        assert to_pascal_case("profile_memory") == "ProfileMemory"

    def test_kebab_case(self):
        """Test converting kebab-case to PascalCase."""
        assert to_pascal_case("memory-type") == "MemoryType"

    def test_spaces(self):
        """Test converting space-separated to PascalCase."""
        assert to_pascal_case("user preferences") == "UserPreferences"

    def test_mixed(self):
        """Test mixed separators."""
        assert to_pascal_case("test-case_with spaces") == "TestCaseWithSpaces"


class TestSchemaModelGenerator:
    """Tests for SchemaModelGenerator."""

    @pytest.fixture
    def sample_memory_type(self):
        """Create a sample MemoryTypeSchema for testing."""
        return MemoryTypeSchema(
            memory_type="test_type",
            description="Test memory type",
            fields=[
                MemoryField(
                    name="field1",
                    field_type=FieldType.STRING,
                    description="First test field",
                    merge_op=MergeOp.PATCH,
                ),
                MemoryField(
                    name="field2",
                    field_type=FieldType.INT64,
                    description="Second test field",
                    merge_op=MergeOp.SUM,
                ),
            ],
            filename_template="test.md",
            directory="test://dir",
        )

    @pytest.fixture
    def registry_with_sample(self, sample_memory_type):
        """Create a registry with a sample memory type."""
        registry = MemoryTypeRegistry()
        registry.register(sample_memory_type)
        return registry

    @pytest.fixture
    def real_registry(self):
        """Create a registry with real schemas."""
        schemas_dir = (
            Path(__file__).parent.parent.parent.parent
            / "openviking"
            / "prompts"
            / "templates"
            / "memory"
        )
        return create_default_registry(str(schemas_dir))

    def test_render_description_template_with_language(self):
        memory_type = MemoryTypeSchema(
            memory_type="templated",
            description="Summary must be written in {{ language }}.",
            fields=[
                MemoryField(
                    name="content",
                    field_type=FieldType.STRING,
                    description="Write this field in {{ language }}.",
                    merge_op=MergeOp.PATCH,
                )
            ],
            filename_template="templated.md",
            directory="test://dir",
        )

        generator = SchemaModelGenerator([memory_type], template_context={"language": "zh-CN"})
        model = generator.create_flat_data_model(memory_type)
        ops_model = generator.create_structured_operations_model(role_scope=None)

        content_description = model.model_fields["content"].description
        memory_description = ops_model.model_fields["templated"].description

        assert "{{ language }}" not in content_description
        assert "{{ language }}" not in memory_description
        assert "zh-CN" in content_description
        assert "zh-CN" in memory_description

    def test_keep_plain_description_unchanged(self, sample_memory_type):
        generator = SchemaModelGenerator([sample_memory_type], template_context={"language": "ja"})
        model = generator.create_flat_data_model(sample_memory_type)

        assert "First test field" in model.model_fields["field1"].description

    def test_render_description_template_conditional_branch(self):
        memory_type = MemoryTypeSchema(
            memory_type="conditional",
            description="{% if language == 'en' %}English summary{% else %}{{ language }} summary{% endif %}",
            fields=[
                MemoryField(
                    name="topic",
                    field_type=FieldType.STRING,
                    description="{% if language == 'en' %}snake_case only{% else %}natural {{ language }} phrase{% endif %}",
                    merge_op=MergeOp.IMMUTABLE,
                )
            ],
            filename_template="conditional.md",
            directory="test://dir",
        )

        en_generator = SchemaModelGenerator([memory_type], template_context={"language": "en"})
        zh_generator = SchemaModelGenerator([memory_type], template_context={"language": "zh-CN"})

        en_model = en_generator.create_flat_data_model(memory_type)
        zh_model = zh_generator.create_flat_data_model(memory_type)
        zh_ops_model = zh_generator.create_structured_operations_model(role_scope=None)

        assert en_model.model_fields["topic"].description == "snake_case only"
        assert zh_model.model_fields["topic"].description == "natural zh-CN phrase"
        assert "zh-CN summary" in zh_ops_model.model_fields["conditional"].description

    def test_create_flat_data_model(self, sample_memory_type, registry_with_sample):
        """Test creating a flat data model for a single memory type."""
        generator = SchemaModelGenerator(registry_with_sample)
        model = generator.create_flat_data_model(sample_memory_type)

        # Check model name
        assert model.__name__ == "TestTypeData"

        # Check model has the memory_type field
        assert "memory_type" in model.model_fields
        # memory_type is a required field with literal type

        # Check business fields
        assert "field1" in model.model_fields
        assert "field2" in model.model_fields

        # Check metadata fields are present
        assert "uri" in model.model_fields
        assert "name" in model.model_fields
        assert "abstract" in model.model_fields
        assert "overview" in model.model_fields
        assert "content" in model.model_fields
        assert "tags" in model.model_fields
        assert "created_at" in model.model_fields
        assert "updated_at" in model.model_fields

    def test_page_id_field_is_emitted_before_mutable_content(self, registry_with_sample):
        """page_id should appear before mutable fields so the model anchors target page first."""
        from unittest.mock import patch

        generator = SchemaModelGenerator(registry_with_sample)
        with patch(
            "openviking_cli.utils.config.get_openviking_config"
        ) as mock_get_openviking_config:
            mock_get_openviking_config.return_value = type(
                "Config",
                (),
                {"memory": type("MemoryCfg", (), {"link_enabled": True})()},
            )()
            model = generator.create_flat_data_model(registry_with_sample.get("test_type"))

        field_names = list(model.model_fields.keys())

        assert "page_id" in field_names
        assert "field1" in field_names
        assert field_names.index("page_id") < field_names.index("field1")

    def test_page_id_field_is_emitted_when_links_disabled(self, registry_with_sample):
        """page_id anchors edits even when link extraction is disabled."""
        from unittest.mock import patch

        generator = SchemaModelGenerator(registry_with_sample)
        with patch(
            "openviking_cli.utils.config.get_openviking_config"
        ) as mock_get_openviking_config:
            mock_get_openviking_config.return_value = type(
                "Config",
                (),
                {"memory": type("MemoryCfg", (), {"link_enabled": False})()},
            )()
            model = generator.create_flat_data_model(registry_with_sample.get("test_type"))

        field_names = list(model.model_fields.keys())
        assert "page_id" in field_names
        assert field_names.index("page_id") < field_names.index("field1")

        page_id_field = model.model_fields["page_id"]
        assert page_id_field.is_required()
        assert (
            page_id_field.description == "Temporary page_id for identifying the target memory item."
        )
        assert model.model_validate({"page_id": 1, "field1": "value"}).page_id == 1
        with pytest.raises(ValueError):
            model.model_validate({"field1": "value"})

    def test_page_id_schema_is_required_and_short_when_links_enabled(self, registry_with_sample):
        from unittest.mock import patch

        generator = SchemaModelGenerator(registry_with_sample)
        with patch(
            "openviking_cli.utils.config.get_openviking_config"
        ) as mock_get_openviking_config:
            mock_get_openviking_config.return_value = type(
                "Config",
                (),
                {"memory": type("MemoryCfg", (), {"link_enabled": True})()},
            )()
            model = generator.create_flat_data_model(registry_with_sample.get("test_type"))

        page_id_field = model.model_fields["page_id"]
        assert page_id_field.is_required()
        assert (
            page_id_field.description == "Temporary page_id for identifying the target memory item."
        )

        schema = model.model_json_schema()
        assert "page_id" in schema["required"]
        assert schema["properties"]["page_id"]["description"] == (
            "Temporary page_id for identifying the target memory item."
        )

    def test_links_field_is_not_emitted_when_links_disabled(self, registry_with_sample):
        from unittest.mock import patch

        generator = SchemaModelGenerator([registry_with_sample.get("test_type")])
        with patch(
            "openviking_cli.utils.config.get_openviking_config"
        ) as mock_get_openviking_config:
            mock_get_openviking_config.return_value = type(
                "Config",
                (),
                {"memory": type("MemoryCfg", (), {"link_enabled": False})()},
            )()
            model = generator.create_structured_operations_model(role_scope=None)

        assert "links" not in model.model_fields

    def test_links_field_description_uses_shared_link_rules_when_links_enabled(
        self, registry_with_sample
    ):
        from unittest.mock import patch

        generator = SchemaModelGenerator([registry_with_sample.get("test_type")])
        with patch(
            "openviking_cli.utils.config.get_openviking_config"
        ) as mock_get_openviking_config:
            mock_get_openviking_config.return_value = type(
                "Config",
                (),
                {"memory": type("MemoryCfg", (), {"link_enabled": True})()},
            )()
            model = generator.create_structured_operations_model(role_scope=None)

        links_field = model.model_fields["links"]
        assert links_field.description == (
            "Links between memory pages. Follow the link rules above. "
            "Use page_ids for `f` and `t`. Use `weight` from 0 to 1 to rank competing links."
        )

    def test_generate_all_models(self, real_registry):
        """Test generating models for all real schemas."""
        generator = SchemaModelGenerator(real_registry)
        # Generate all models including disabled ones
        models = generator.generate_all_models(include_disabled=True)

        # Check we have models for all registered types (including disabled)
        assert len(models) == len(real_registry.list_all(include_disabled=True))

        # Check specific types exist
        assert "profile" in models
        assert "preferences" in models

        # Check profile model has 'content' field
        profile_model = models["profile"]
        assert "content" in profile_model.model_fields

    def test_create_discriminated_union_model(self, real_registry):
        """Test creating the union model wrapper."""
        generator = SchemaModelGenerator(real_registry)
        union_model = generator.create_discriminated_union_model()

        # The union model is a wrapper BaseModel
        assert hasattr(union_model, "model_fields")
        assert "data" in union_model.model_fields

    def test_get_llm_json_schema(self, real_registry):
        """Test getting the LLM JSON schema."""
        generator = SchemaModelGenerator(real_registry)
        json_schema = generator.get_llm_json_schema()

        # Check it's a valid JSON schema
        assert "$defs" in json_schema or "definitions" in json_schema
        assert "properties" in json_schema

        # Check it includes operations
        assert "write_uris" in json_schema["properties"]
        assert "edit_uris" in json_schema["properties"]
        assert "delete_uris" in json_schema["properties"]

        # Check delete_uris is an array of strings
        delete_props = json_schema["properties"]["delete_uris"]
        assert delete_props.get("items", {}).get("type") == "string"

    def test_get_memory_data_json_schema(self, real_registry):
        """Test getting just the MemoryData JSON schema."""
        generator = SchemaModelGenerator(real_registry)
        json_schema = generator.get_memory_data_json_schema()

        # Check it's a valid JSON schema
        assert "$defs" in json_schema or "definitions" in json_schema
        assert "properties" in json_schema

    def test_model_caching(self, registry_with_sample, sample_memory_type):
        """Test that models are cached."""
        generator = SchemaModelGenerator(registry_with_sample)

        # Create model twice
        model1 = generator.create_flat_data_model(sample_memory_type)
        model2 = generator.create_flat_data_model(sample_memory_type)

        # Should be the same object
        assert model1 is model2

    def test_dynamic_new_schema(self):
        """Test that adding a new schema at runtime works without code changes."""
        # Create a temporary YAML file for a new memory type
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            new_schema_path = tmp_path / "new_type.yaml"

            # Write a new schema
            new_schema = {
                "memory_type": "new_custom_type",
                "description": "A dynamically added custom type",
                "directory": "test://new",
                "filename_template": "custom_{name}.md",
                "fields": [
                    {
                        "name": "custom_field",
                        "type": "string",
                        "description": "Custom field description",
                        "merge_op": "patch",
                    }
                ],
            }

            with open(new_schema_path, "w", encoding="utf-8") as f:
                yaml.dump(new_schema, f)

            # Load it
            registry = MemoryTypeRegistry()
            registry.load_from_yaml(str(new_schema_path))

            # Verify it's loaded
            assert registry.get("new_custom_type") is not None

            # Generate model
            generator = SchemaModelGenerator(registry)
            model = generator.create_flat_data_model(registry.get("new_custom_type"))

            # Verify the model has the custom field
            assert "custom_field" in model.model_fields
            assert "memory_type" in model.model_fields
            assert "uri" in model.model_fields


class TestWikiLink:
    def test_invalid_link_type_keeps_freeform_short_label(self):
        link = WikiLink.model_validate(
            {
                "f": 1,
                "t": 2,
                "link_type": "inspired_by",
                "weight": 0.7,
                "match_text": "memory",
                "description": "derived by model",
            }
        )

        assert link.link_type == "inspired_by"

    def test_link_type_schema_is_open_string_without_enum(self):
        schema = WikiLink.model_json_schema()
        link_type_schema = schema["properties"]["link_type"]

        assert link_type_schema["type"] == "string"
        assert "enum" not in link_type_schema

    def test_schema_prompt_generator_renders_conditional_descriptions(self):
        memory_type = MemoryTypeSchema(
            memory_type="conditional_prompt",
            description="{% if language == 'en' %}English type{% else %}{{ language }} type{% endif %}",
            fields=[
                MemoryField(
                    name="topic",
                    field_type=FieldType.STRING,
                    description="{% if language == 'en' %}english topic{% else %}{{ language }} topic{% endif %}",
                    merge_op=MergeOp.IMMUTABLE,
                )
            ],
        )

        generator = SchemaPromptGenerator([memory_type], template_context={"language": "zh-CN"})

        type_descriptions = generator.generate_type_descriptions()
        field_descriptions = generator.generate_field_descriptions("conditional_prompt")

        assert "zh-CN type" in type_descriptions
        assert "zh-CN topic" in type_descriptions
        assert "zh-CN topic" in field_descriptions


class TestSchemaPromptGenerator:
    """Tests for SchemaPromptGenerator."""

    @pytest.fixture
    def real_registry(self):
        """Create a registry with real schemas."""
        schemas_dir = (
            Path(__file__).parent.parent.parent.parent
            / "openviking"
            / "prompts"
            / "templates"
            / "memory"
        )
        return create_default_registry(str(schemas_dir))

    def test_generate_type_descriptions(self, real_registry):
        """Test generating type descriptions."""
        generator = SchemaPromptGenerator(real_registry)
        descriptions = generator.generate_type_descriptions()

        # Check it's not empty
        assert len(descriptions) > 0

        # Check it contains the structure header
        assert "## Available Memory Types" in descriptions

        # Check for memory types that should always be enabled
        # (profile and preferences might be disabled, check for events or cards instead)
        assert "### events" in descriptions or "### cards" in descriptions

    def test_generate_field_descriptions(self, real_registry):
        """Test generating field descriptions for a specific type."""
        generator = SchemaPromptGenerator(real_registry)

        # Get profile fields
        profile_fields = generator.generate_field_descriptions("profile")
        assert profile_fields is not None
        assert "### profile Fields" in profile_fields
        assert "content" in profile_fields

        # Get preferences fields
        pref_fields = generator.generate_field_descriptions("preferences")
        assert pref_fields is not None
        assert "topic" in pref_fields
        assert "content" in pref_fields

        # Non-existent type returns None
        assert generator.generate_field_descriptions("non_existent") is None

    def test_get_full_prompt_context(self, real_registry):
        """Test getting the full prompt context."""
        generator = SchemaPromptGenerator(real_registry)
        context = generator.get_full_prompt_context()

        # Check structure
        assert "type_descriptions" in context
        assert "memory_types" in context

        # Check memory_types entries - should only include enabled types
        memory_types = context["memory_types"]
        assert len(memory_types) == len(real_registry.list_all())

        # Check each entry has expected fields
        for mt in memory_types:
            assert "memory_type" in mt
            assert "description" in mt
            assert "fields" in mt
            for field in mt["fields"]:
                assert "name" in field
                assert "type" in field
                assert "description" in field


class TestIntegration:
    """Integration tests for the complete schema system."""

    def test_end_to_end_model_generation_and_validation(self):
        """Test end-to-end: load schemas, generate models, validate data."""
        schemas_dir = (
            Path(__file__).parent.parent.parent.parent
            / "openviking"
            / "prompts"
            / "templates"
            / "memory"
        )
        registry = create_default_registry(str(schemas_dir))

        # Create generator
        generator = SchemaModelGenerator(registry)

        # Get the operations model
        generator.create_structured_operations_model()

        # Get JSON schema
        json_schema = generator.get_llm_json_schema()

        # Verify the schema includes descriptions from YAML
        # Check that $defs has entries
        defs = json_schema.get("$defs", {})
        assert len(defs) > 0, "No definitions found in JSON schema"

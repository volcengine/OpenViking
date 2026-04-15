# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
Dynamic Pydantic model generator based on YAML schemas.

Generates type-safe Pydantic models at runtime from MemoryTypeSchema
definitions, with discriminator support for polymorphic fields.
"""

import re
from typing import Any, Dict, List, Optional, Tuple, Type, Union

from pydantic import BaseModel, Field, create_model
from pydantic.config import ConfigDict

from openviking.session.memory.dataclass import FaultTolerantBaseModel, MemoryTypeSchema
from openviking.session.memory.merge_op import MergeOp, MergeOpFactory
from openviking.session.memory.merge_op.base import FieldType, StrPatch, get_python_type_for_field
from openviking_cli.utils import get_logger

logger = get_logger(__name__)


def to_pascal_case(s: str) -> str:
    """Convert snake_case or kebab-case to PascalCase."""
    # Replace non-alphanumeric with spaces
    s = re.sub(r"[^a-zA-Z0-9]+", " ", s)
    # Split and capitalize
    words = s.strip().split()
    return "".join(word.title() for word in words)


class SchemaModelGenerator:
    """
    Dynamic Pydantic model generator from memory type schemas.

    Creates type-safe models at runtime with discriminator support
    for polymorphic memory data.
    """

    # Generic overview edit model shared by all memory types
    _generic_overview_edit_model: Optional[Type[BaseModel]] = None

    def __init__(self, schemas: List[MemoryTypeSchema]):
        self.schemas = schemas
        self._model_cache: Dict[str, Type[BaseModel]] = {}
        self._flat_data_models: Dict[str, Type[BaseModel]] = {}
        self._overview_edit_models: Dict[str, Type[BaseModel]] = {}
        self._union_model: Optional[Type[BaseModel]] = None
        self._operations_model: Optional[Type[BaseModel]] = None

    def _map_field_type(self, field_type: FieldType) -> Type[Any]:
        """Map YAML field type to Python type."""
        return get_python_type_for_field(field_type)

    def create_flat_data_model(self, memory_type: MemoryTypeSchema) -> Type[BaseModel]:
        """
        Create a fully flat Pydantic model for a specific memory type.

        Note: memory_type field is NOT included since each type has its own
        output field in the structured operations model.

        Args:
            memory_type: The memory type schema

        Returns:
            Dynamically created flat Pydantic model class
        """
        cache_key = memory_type.memory_type

        if cache_key in self._flat_data_models:
            return self._flat_data_models[cache_key]

        model_name = f"{to_pascal_case(memory_type.memory_type)}Data"

        # Build field definitions - no memory_type field needed
        field_definitions: Dict[str, Tuple[Type[Any], Any]] = {}

        # Add business fields from schema
        for field in memory_type.fields:
            base_type = self._map_field_type(field.field_type)
            if field.merge_op == MergeOp.IMMUTABLE:
                # Immutable fields: only base type, required
                field_definitions[field.name] = (
                    base_type,
                    Field(..., description=field.description),
                )
            else:
                # Mutable fields: Union[base_type, patch_type], optional
                merge_op = MergeOpFactory.from_field(field)
                patch_type = merge_op.get_output_schema_type(field.field_type)
                union_type = Union[base_type, patch_type]
                desc = merge_op.get_output_schema_description(field.description)
                field_definitions[field.name] = (
                    Optional[union_type],
                    Field(None, description=desc),
                )
        # Create the model
        model = create_model(
            model_name,
            __config__=ConfigDict(extra="forbid"),
            **field_definitions,
        )

        # Store in cache
        self._flat_data_models[cache_key] = model
        return model

    def generate_all_models(self, include_disabled: bool = True) -> Dict[str, Type[BaseModel]]:
        """
        Generate flat data models for all registered memory types.

        Args:
            include_disabled: If True, include disabled memory types

        Returns:
            Dictionary mapping memory_type to generated model class
        """
        models: Dict[str, Type[BaseModel]] = {}
        for memory_type in self.schemas:
            models[memory_type.memory_type] = self.create_flat_data_model(memory_type)
        return models

    def create_overview_edit_model(self, memory_type: MemoryTypeSchema) -> Type[BaseModel]:
        """
        Create a simplified model for editing .overview.md files.

        The model includes:
        - memory_type (literal discriminator)
        - overview (Union[str, StrPatch])

        Args:
            memory_type: The memory type schema

        Returns:
            Dynamically created overview edit model class
        """
        # Use cached generic model
        if SchemaModelGenerator._generic_overview_edit_model is not None:
            return SchemaModelGenerator._generic_overview_edit_model

        # Create generic model with string memory_type (not Literal)
        model = create_model(
            "GenericOverviewEdit",
            __config__=ConfigDict(extra="forbid"),
            memory_type=(
                str,
                Field(..., description="Memory type to edit (e.g., 'profile', 'skills')"),
            ),
            overview=(
                Optional[Union[str, StrPatch]],
                Field(
                    None,
                    description="Overview content (L1). Use Markdown with internal links: [filename](filename.md), e.g., [python](python.md), [go](go.md). Supports direct string or patch format.",
                ),
            ),
        )

        SchemaModelGenerator._generic_overview_edit_model = model
        return model

    def create_discriminated_union_model(self) -> Type[BaseModel]:
        """
        Create a unified MemoryData model with discriminator support.

        The model uses 'memory_type' as the discriminator field to
        determine which fields model to use.

        Returns:
            Unified Pydantic model with discriminator (a wrapper model containing the union)
        """
        if self._union_model is not None:
            return self._union_model

        # Generate all flat data models first (including disabled for completeness)
        self.generate_all_models(include_disabled=True)

        # Build the annotated union with discriminator - only use enabled types
        if not self.schemas:
            raise ValueError("No memory types in schemas")

        # Create union of flat data models
        enabled_memory_types = self.schemas
        flat_model_union_types = tuple(
            self._flat_data_models[mt.memory_type] for mt in enabled_memory_types
        )

        if flat_model_union_types:
            FlatDataUnion = Union[tuple(flat_model_union_types)]  # type: ignore
        else:
            # Fallback if no types are enabled
            class GenericMemoryData(BaseModel):
                """Generic memory data (fallback)."""

                memory_type: str = Field(..., description="Memory type identifier")

            FlatDataUnion = GenericMemoryData  # type: ignore

        # Wrap the union in a BaseModel for JSON schema generation
        class MemoryDataWrapper(BaseModel):
            """Wrapper model for memory data union."""

            data: FlatDataUnion = Field(..., description="Memory data")  # type: ignore

            model_config = ConfigDict(extra="forbid")

        self._union_model = MemoryDataWrapper
        return self._union_model

    def _is_single_value_schema(self, schema: MemoryTypeSchema) -> bool:
        """
        Determine if a schema should output as single value (not list).

        Single value if filename_template does NOT contain {xxx} variable.
        For example:
        - "profile.md" -> single value
        - "{skill_name}.md" -> list
        """
        return "{" not in schema.filename_template

    def create_structured_operations_model(self) -> Type[BaseModel]:
        """
        Create a structured MemoryOperations model with type-safe write operations.

        Each memory_type gets its own field (mixed add + edit), with:
        - Single value if filename_template has no variable (e.g., profile)
        - List if filename_template has variable (e.g., {skill_name})

        Returns:
            Pydantic model for structured operations
        """
        if self._operations_model is not None:
            return self._operations_model

        # Generate all flat data models
        self.generate_all_models(include_disabled=True)

        # Get enabled memory types
        enabled_memory_types = self.schemas
        memory_type_fields = [mt.memory_type for mt in enabled_memory_types]

        # Build field definitions for each memory_type
        field_definitions: Dict[str, Tuple[Type[Any], Any]] = {}

        # field_definitions["reasoning"] = (
        #     str,
        #     Field("", description="reasoning"),
        # )

        for mt in enabled_memory_types:
            flat_model = self.create_flat_data_model(mt)
            is_single = self._is_single_value_schema(mt)

            if is_single:
                # Single value: Optional[FlatModel] = None
                field_definitions[mt.memory_type] = (
                    Optional[flat_model],  # type: ignore
                    Field(None, description=f"{mt.memory_type} memory (add or edit)"),
                )
            else:
                # List: List[FlatModel] = []
                field_definitions[mt.memory_type] = (
                    List[flat_model],  # type: ignore
                    Field(
                        default_factory=list, description=f"{mt.memory_type} memories (add or edit)"
                    ),
                )

        # Use single generic model for overview edit (same for all memory types)
        # generic_overview_edit = self.create_overview_edit_model(
        #     enabled_memory_types[0] if enabled_memory_types else None
        # )

        # field_definitions["edit_overview_uris"] = (
        #     List[generic_overview_edit],  # type: ignore
        #     Field(
        #         default_factory=list,
        #         description="Edit operations for .overview.md files using memory_type",
        #     ),
        # )

        field_definitions["delete_uris"] = (
            List[str],
            Field(default_factory=list, description="Delete operations as URI strings"),
        )

        # Create model using create_model
        StructuredMemoryOperations = create_model(
            "StructuredMemoryOperations",
            __config__=ConfigDict(extra="ignore"),
            __base__=FaultTolerantBaseModel,
            **field_definitions,
        )

        # Add custom methods
        def is_empty(self) -> bool:
            """Check if there are any operations."""
            for field_name in memory_type_fields:
                value = getattr(self, field_name, None)
                if value is not None:
                    if isinstance(value, list):
                        if len(value) > 0:
                            return False
                    else:
                        # Single value (not None)
                        return False
            return len(self.edit_overview_uris) == 0 and len(self.delete_uris) == 0

        def to_legacy_operations(self) -> Dict[str, Any]:
            """Convert new per-type structure to legacy write_uris/edit_uris format."""
            write_uris = []
            edit_uris = []

            for field_name in memory_type_fields:
                value = getattr(self, field_name, None)
                if value is None:
                    continue
                if isinstance(value, list):
                    for item in value:
                        if hasattr(item, "uri") and item.uri:
                            edit_uris.append(item)
                        else:
                            write_uris.append(item)
                else:
                    if hasattr(value, "uri") and value.uri:
                        edit_uris.append(value)
                    else:
                        write_uris.append(value)

            return {
                "write_uris": write_uris,
                "edit_uris": edit_uris,
                #"edit_overview_uris": self.edit_overview_uris,
                "delete_uris": self.delete_uris,
            }

        # Attach methods
        StructuredMemoryOperations.is_empty = is_empty
        StructuredMemoryOperations.to_legacy_operations = to_legacy_operations
        StructuredMemoryOperations._memory_type_fields = memory_type_fields  # type: ignore

        self._operations_model = StructuredMemoryOperations
        return self._operations_model

    def get_llm_json_schema(self) -> Dict[str, Any]:
        """
        Get the JSON schema for LLM structured output.

        Returns:
            JSON schema dictionary suitable for LLM API
        """
        operations_model = self.create_structured_operations_model()
        return operations_model.model_json_schema()

    def get_memory_data_json_schema(self) -> Dict[str, Any]:
        """
        Get the JSON schema just for the flat memory data union.

        Returns:
            JSON schema for MemoryData
        """
        memory_model = self.create_discriminated_union_model()
        return memory_model.model_json_schema()


class SchemaPromptGenerator:
    """
    Prompt generator that incorporates schema information into LLM prompts.

    Generates descriptive text about memory types and their fields
    based on the YAML schema definitions.
    """

    def __init__(self, schemas: List[MemoryTypeSchema]):
        self.schemas = schemas

    def generate_type_descriptions(self) -> str:
        """
        Generate descriptions of all memory types.

        Returns:
            Formatted string with all memory type descriptions
        """
        lines = ["## Available Memory Types"]

        for mt in self.schemas:
            lines.append(f"\n### {mt.memory_type}")
            lines.append(f"{mt.description}")

            # Add URI format information
            if mt.directory or mt.filename_template:
                lines.append("\n**URI Format:**")
                if mt.directory and mt.filename_template:
                    lines.append(f"- URI: `{mt.directory}/{mt.filename_template}`")
                elif mt.directory:
                    lines.append(f"- Directory: `{mt.directory}`")
                elif mt.filename_template:
                    lines.append(f"- Filename: `{mt.filename_template}`")

                # Add variable substitution info
                lines.append("\n**Variable Substitution:**")
                lines.append("- `{{ user_space }}` → 'default'")
                lines.append("- `{{ agent_space }}` → 'default'")
                if mt.fields:
                    for field in mt.fields:
                        lines.append(f"- `{{ {field.name} }}` → use value from fields")

            if mt.fields:
                lines.append("\n**Fields:**")
                for field in mt.fields:
                    lines.append(
                        f"- `{field.name}` ({field.field_type.value}): {field.description}"
                    )

        return "\n".join(lines)

    def generate_field_descriptions(self, memory_type: str) -> Optional[str]:
        """
        Generate descriptions for a specific memory type's fields.

        Args:
            memory_type: The memory type to describe

        Returns:
            Formatted string with field descriptions, or None if not found
        """
        mt = next((s for s in self.schemas if s.memory_type == memory_type), None)
        if not mt:
            return None

        lines = [f"### {mt.memory_type} Fields"]
        for field in mt.fields:
            lines.append(f"- `{field.name}`: {field.description}")

        return "\n".join(lines)

    def get_full_prompt_context(self) -> Dict[str, Any]:
        """
        Get the full prompt context including all schema information.

        Returns:
            Dictionary with all prompt context components
        """
        return {
            "type_descriptions": self.generate_type_descriptions(),
            "memory_types": [
                {
                    "memory_type": mt.memory_type,
                    "description": mt.description,
                    "fields": [
                        {
                            "name": f.name,
                            "type": f.field_type.value,
                            "description": f.description,
                            "merge_op": f.merge_op.value,
                        }
                        for f in mt.fields
                    ],
                }
                for mt in self.schemas
            ],
        }

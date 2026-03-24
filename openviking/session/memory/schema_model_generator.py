# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""
Dynamic Pydantic model generator based on YAML schemas.

Generates type-safe Pydantic models at runtime from MemoryTypeSchema
definitions, with discriminator support for polymorphic fields.
"""

import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple, Type, Union

from pydantic import BaseModel, Field, create_model
from pydantic.config import ConfigDict
from typing_extensions import Annotated, Literal

from openviking.session.memory.dataclass import MemoryTypeSchema
from openviking.session.memory.merge_op import MergeOp, MergeOpFactory
from openviking.session.memory.merge_op.base import FieldType, StrPatch, get_python_type_for_field
from openviking.session.memory.memory_type_registry import MemoryTypeRegistry
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

    def __init__(self, registry: MemoryTypeRegistry):
        self.registry = registry
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

        The model includes:
        - memory_type (literal discriminator)
        - All business fields (with Union[base_type, patch_type] for mutable fields)
        - Standard metadata fields (uri, name, abstract, overview, content, tags, created_at, updated_at)

        Args:
            memory_type: The memory type schema

        Returns:
            Dynamically created flat Pydantic model class
        """
        cache_key = memory_type.memory_type

        if cache_key in self._flat_data_models:
            return self._flat_data_models[cache_key]

        model_name = f"{to_pascal_case(memory_type.memory_type)}Data"

        # Build field definitions
        field_definitions: Dict[str, Tuple[Type[Any], Any]] = {}

        # Add memory_type as literal discriminator
        field_definitions["memory_type"] = (
            Literal[memory_type.memory_type],  # type: ignore
            Field(..., description=f"Memory type: {memory_type.memory_type}"),
        )

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
        for memory_type in self.registry.list_all(include_disabled=include_disabled):
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
        memory_types = self.registry.list_all(include_disabled=False)
        if not memory_types:
            raise ValueError("No memory types registered in registry")

        # Create union of flat data models
        enabled_memory_types = self.registry.list_all(include_disabled=False)
        flat_model_union_types = tuple(
            self._flat_data_models[mt.memory_type]
            for mt in enabled_memory_types
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

    def create_structured_operations_model(self) -> Type[BaseModel]:
        """
        Create a structured MemoryOperations model with type-safe write operations.

        This uses fully flat models for write_uris and edit_uris,
        and simple string URIs for delete_uris.

        Returns:
            Pydantic model for structured operations
        """
        if self._operations_model is not None:
            return self._operations_model

        # Generate all flat data models
        self.generate_all_models(include_disabled=True)

        # Get enabled memory types
        enabled_memory_types = self.registry.list_all(include_disabled=False)

        # Create union type for flat data models (used for both write and edit)
        flat_models: List[Type[BaseModel]] = []
        for mt in enabled_memory_types:
            flat_model = self.create_flat_data_model(mt)
            flat_models.append(flat_model)

        FlatDataUnion = Union[tuple(flat_models)]  # type: ignore

        # Use single generic model for overview edit (same for all memory types)
        generic_overview_edit = self.create_overview_edit_model(enabled_memory_types[0] if enabled_memory_types else None)

        # Create structured operations
        class StructuredMemoryOperations(BaseModel):
            """Final memory operations output from LLM with type safety."""

            reasoning: str = Field(
                '',
                description="reasoning",
            )
            write_uris: List[FlatDataUnion] = Field(  # type: ignore
                default_factory=list,
                description="Write operations with flat data format",
            )
            edit_uris: List[FlatDataUnion] = Field(  # type: ignore
                default_factory=list,
                description="Edit operations with flat data format",
            )
            edit_overview_uris: List[generic_overview_edit] = Field(  # type: ignore
                default_factory=list,
                description="Edit operations for .overview.md files using memory_type",
            )
            delete_uris: List[str] = Field(
                default_factory=list,
                description="Delete operations as URI strings",
            )

            def is_empty(self) -> bool:
                """Check if there are any operations."""
                return (
                    len(self.write_uris) == 0
                    and len(self.edit_uris) == 0
                    and len(self.edit_overview_uris) == 0
                    and len(self.delete_uris) == 0
                )

            model_config = ConfigDict(extra='ignore')

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

    def __init__(self, registry: MemoryTypeRegistry):
        self.registry = registry

    def generate_type_descriptions(self) -> str:
        """
        Generate descriptions of all memory types.

        Returns:
            Formatted string with all memory type descriptions
        """
        lines = ["## Available Memory Types"]

        for mt in self.registry.list_all():
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
                lines.append("- `{user_space}` → 'default'")
                lines.append("- `{agent_space}` → 'default'")
                if mt.fields:
                    for field in mt.fields:
                        lines.append(f"- `{field.name}` → use value from fields")

            if mt.fields:
                lines.append("\n**Fields:**")
                for field in mt.fields:
                    lines.append(f"- `{field.name}` ({field.field_type.value}): {field.description}")

        return "\n".join(lines)

    def generate_field_descriptions(self, memory_type: str) -> Optional[str]:
        """
        Generate descriptions for a specific memory type's fields.

        Args:
            memory_type: The memory type to describe

        Returns:
            Formatted string with field descriptions, or None if not found
        """
        mt = self.registry.get(memory_type)
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
                for mt in self.registry.list_all()
            ],
        }

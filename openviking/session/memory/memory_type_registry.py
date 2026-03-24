# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""
Memory type registry - loads YAML configurations.
"""

from pathlib import Path
from typing import Dict, List, Optional

import yaml

from openviking.session.memory.dataclass import MemoryField, MemoryTypeSchema
from openviking.session.memory.merge_op import MergeOp
from openviking.session.memory.merge_op.base import FieldType
from openviking_cli.utils import get_logger

logger = get_logger(__name__)


class MemoryTypeRegistry:
    """
    Registry for memory types.

    Loads memory type definitions from YAML files and provides
    access to memory type configurations.
    """

    def __init__(self):
        self._types: Dict[str, MemoryTypeSchema] = {}

    def register(self, memory_type: MemoryTypeSchema) -> None:
        """Register a memory type."""
        self._types[memory_type.memory_type] = memory_type
        logger.debug(f"Registered memory type: {memory_type.memory_type}")

    def get(self, name: str) -> Optional[MemoryTypeSchema]:
        """Get a memory type by name."""
        return self._types.get(name)

    def list_all(self, include_disabled: bool = False) -> List[MemoryTypeSchema]:
        """List all registered memory types.

        Args:
            include_disabled: If True, include disabled memory types

        Returns:
            List of memory type schemas
        """
        if include_disabled:
            return list(self._types.values())
        return [mt for mt in self._types.values() if mt.enabled]

    def list_names(self, include_disabled: bool = False) -> List[str]:
        """List all registered memory type names.

        Args:
            include_disabled: If True, include disabled memory types

        Returns:
            List of memory type names
        """
        if include_disabled:
            return list(self._types.keys())
        return [mt.memory_type for mt in self._types.values() if mt.enabled]

    def load_from_yaml(self, yaml_path: str) -> None:
        """
        Load memory type from a YAML file.

        Args:
            yaml_path: Path to YAML file
        """
        with open(yaml_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)

        memory_type = self._parse_memory_type(data)
        self.register(memory_type)

    def load_from_directory(self, dir_path: str) -> int:
        """
        Load all YAML files from a directory.

        Args:
            dir_path: Directory path

        Returns:
            Number of types loaded
        """
        count = 0
        dir_path_obj = Path(dir_path)

        if not dir_path_obj.exists():
            logger.warning(f"Directory not found: {dir_path}")
            return 0

        for yaml_file in dir_path_obj.glob("*.yaml"):
            try:
                self.load_from_yaml(str(yaml_file))
                count += 1
            except Exception as e:
                logger.error(f"Failed to load {yaml_file}: {e}")

        for yaml_file in dir_path_obj.glob("*.yml"):
            try:
                self.load_from_yaml(str(yaml_file))
                count += 1
            except Exception as e:
                logger.error(f"Failed to load {yaml_file}: {e}")

        return count

    def _parse_memory_type(self, data: dict) -> MemoryTypeSchema:
        """Parse memory type from YAML data."""
        fields_data = data.get("fields", [])
        fields = []

        for field_data in fields_data:
            field = MemoryField(
                name=field_data.get("name", ""),
                field_type=FieldType(field_data.get("type", "string")),
                description=field_data.get("description", ""),
                merge_op=MergeOp(field_data.get("merge_op", "patch")),
            )
            fields.append(field)

        return MemoryTypeSchema(
            memory_type=data.get("memory_type", data.get("name", "")),
            description=data.get("description", ""),
            fields=fields,
            filename_template=data.get("filename_template", ""),
            content_template=data.get("content_template"),
            directory=data.get("directory", ""),
            enabled=data.get("enabled", data.get("enable", True)),
        )



def create_default_registry(schemas_dir: Optional[str] = None) -> MemoryTypeRegistry:
    """
    Create a registry with built-in memory types.

    Args:
        schemas_dir: Optional directory to load schemas from

    Returns:
        MemoryTypeRegistry with built-in types
    """
    registry = MemoryTypeRegistry()

    # Register built-in types
    # These can also be loaded from YAML files
    _register_builtin_types(registry)

    # Load from schemas directory if provided
    if schemas_dir:
        registry.load_from_directory(schemas_dir)

    return registry


def _register_builtin_types(registry: MemoryTypeRegistry) -> None:
    """Register built-in memory types."""
    # Note: In production, these should be loaded from YAML files
    # This is just a placeholder for reference
    pass

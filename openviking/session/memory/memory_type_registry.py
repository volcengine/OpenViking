# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
Memory type registry - loads YAML configurations.
"""

from pathlib import Path
from typing import Any, Dict, List, Optional

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

    def __init__(self, load_schemas: bool = True):
        self._types: Dict[str, MemoryTypeSchema] = {}

        if load_schemas:
            self._load_schemas()

    def _load_schemas(self) -> None:
        """Load schemas from built-in and custom directories. Fails on error."""
        import os

        from openviking_cli.utils.config import get_openviking_config

        builtin_dir = os.path.join(
            os.path.dirname(__file__), "..", "..", "prompts", "templates", "memory"
        )
        config = get_openviking_config()
        custom_dir = config.memory.custom_templates_dir

        # Load from builtin directory (must succeed)
        if not os.path.exists(builtin_dir):
            raise RuntimeError(f"Builtin memory templates directory not found: {builtin_dir}")
        loaded = self.load_from_directory(builtin_dir)
        if loaded == 0:
            raise RuntimeError(f"No memory schemas loaded from builtin directory: {builtin_dir}")
        logger.info(f"Loaded {loaded} memory schemas from builtin: {builtin_dir}")

        # Load from custom directory (if configured)
        if custom_dir:
            custom_dir_expanded = os.path.expanduser(custom_dir)
            if os.path.exists(custom_dir_expanded):
                custom_loaded = self.load_from_directory(custom_dir_expanded)
                logger.info(
                    f"Loaded {custom_loaded} memory schemas from custom: {custom_dir_expanded}"
                )

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

    def list_search_uris(self, user_space: str, agent_space: str) -> List[str]:
        """List all directory URIs for search scope.

        Args:
            user_space: User space name
            agent_space: Agent space name

        Returns:
            List of directory URIs from enabled schemas
        """
        import jinja2

        uris = []
        for schema in self.list_all(include_disabled=False):
            if schema.directory:
                env = jinja2.Environment(autoescape=False)
                template = env.from_string(schema.directory)
                dir_path = template.render(user_space=user_space, agent_space=agent_space)
                uris.append(dir_path)
        return uris

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
                init_value=field_data.get("init_value"),
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
            operation_mode=data.get("operation_mode", "upsert"),
        )

    async def initialize_memory_files(self, ctx: Any) -> None:
        """
        Initialize memory files with init_value for fields that have it.

        Only initializes single-file templates (filename_template doesn't require external fields).
        Skip templates like entities.yaml where filename requires external parameters.

        Args:
            ctx: Request context (must have user with user_space_name and agent_space_name)
        """
        import jinja2

        from openviking.storage.viking_fs import get_viking_fs

        logger = get_logger(__name__)

        user_space = ctx.user.user_space_name() if ctx and ctx.user else "default"
        agent_space = ctx.user.agent_space_name() if ctx and ctx.user else "default"

        logger.info(
            f"[MemoryTypeRegistry] Starting memory files initialization for user={user_space}, agent={agent_space}"
        )

        env = jinja2.Environment(autoescape=False)
        viking_fs = get_viking_fs()

        for schema in self.list_all(include_disabled=False):
            # Must be enabled, have filename_template and content_template
            if not schema.enabled or not schema.filename_template or not schema.content_template:
                continue

            # Skip multi-file templates (filename requires external parameters like {{ name }})
            if "{{" in schema.filename_template:
                continue

            # Check if any field has init_value
            fields_with_init = {
                f.name: f.init_value for f in schema.fields if f.init_value is not None
            }
            if not fields_with_init:
                continue

            # Render directory and filename from schema
            try:
                directory = env.from_string(schema.directory).render(
                    user_space=user_space,
                    agent_space=agent_space,
                )
                filename = env.from_string(schema.filename_template).render(
                    user_space=user_space,
                    agent_space=agent_space,
                )
            except Exception:
                continue

            file_uri = f"{directory}/{filename}"

            # Check if file already exists
            try:
                await viking_fs.read_file(file_uri, ctx=ctx)
                continue
            except Exception:
                pass

            # Add MEMORY_FIELDS comment with field metadata
            # Template rendering is handled inside serialize_with_metadata
            from openviking.session.memory.utils.content import serialize_with_metadata

            metadata = {
                "memory_type": schema.memory_type,
                **fields_with_init,
                "content": "",  # content will come from content_template rendering
            }
            full_content = serialize_with_metadata(
                metadata,
                content_template=schema.content_template,
            )

            # Write the file
            try:
                await viking_fs.write_file(file_uri, full_content, ctx=ctx)
                logger.info(f"[MemoryTypeRegistry] Initialized memory file: {file_uri}")
            except Exception:
                pass


def create_default_registry() -> MemoryTypeRegistry:
    """
    Create a registry with memory types loaded at initialization.

    Returns:
        MemoryTypeRegistry with built-in types (loaded in __init__)
    """
    return MemoryTypeRegistry(load_schemas=True)

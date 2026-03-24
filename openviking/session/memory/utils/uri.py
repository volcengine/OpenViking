# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""
URI generation and validation utilities.
"""

import re
from typing import Any, Dict, List, Optional, Set, Tuple, Type

from openviking.session.memory.dataclass import MemoryTypeSchema
from openviking.session.memory.memory_type_registry import MemoryTypeRegistry
from openviking_cli.utils import get_logger

logger = get_logger(__name__)


def generate_uri(
    memory_type: MemoryTypeSchema,
    fields: Dict[str, Any],
    user_space: str = "default",
    agent_space: str = "default",
) -> str:
    """
    Generate a full URI from memory type schema and field values.

    Args:
        memory_type: The memory type schema with directory and filename_template
        fields: The field values to use for template replacement
        user_space: The user space to substitute for {user_space}
        agent_space: The agent space to substitute for {agent_space}

    Returns:
        The fully generated URI

    Raises:
        ValueError: If required template variables are missing from fields
    """
    # Build the URI template from directory and filename_template
    uri_template = ""
    if memory_type.directory:
        uri_template = memory_type.directory
    if memory_type.filename_template:
        if uri_template:
            uri_template = f"{uri_template}/{memory_type.filename_template}"
        else:
            uri_template = memory_type.filename_template

    if not uri_template:
        raise ValueError("Memory type has neither directory nor filename_template")

    # Build the replacement dictionary
    replacements = {
        "user_space": user_space,
        "agent_space": agent_space,
    }

    # Add all fields to replacements
    replacements.update(fields)

    # Replace all template variables
    def replace_var(match: re.Match) -> str:
        var_name = match.group(1)
        if var_name not in replacements:
            raise ValueError(f"Missing template variable '{var_name}' in fields")
        value = replacements[var_name]
        if value is None:
            raise ValueError(f"Template variable '{var_name}' has None value")
        return str(value)

    # Replace {variable} patterns
    uri = re.sub(r"\{([^}]+)\}", replace_var, uri_template)

    return uri


def validate_uri_template(memory_type: MemoryTypeSchema) -> bool:
    """
    Validate that a memory type's URI template is well-formed.

    Args:
        memory_type: The memory type schema to validate

    Returns:
        True if the template is valid, False otherwise
    """
    if not memory_type.directory and not memory_type.filename_template:
        return False

    # Check that all variables in filename_template exist in fields
    if memory_type.filename_template:
        field_names = {f.name for f in memory_type.fields}
        template_vars = set(re.findall(r"\{([^}]+)\}", memory_type.filename_template))

        # {user_space} and {agent_space} are built-in, not from fields
        built_in_vars = {"user_space", "agent_space"}
        required_field_vars = template_vars - built_in_vars

        for var in required_field_vars:
            if var not in field_names:
                return False

    return True


def collect_allowed_directories(
    schemas: List[MemoryTypeSchema],
    user_space: str = "default",
    agent_space: str = "default",
) -> Set[str]:
    """
    Collect all allowed directories from activated schemas.

    Args:
        schemas: List of activated memory type schemas
        user_space: User space to substitute for {user_space}
        agent_space: Agent space to substitute for {agent_space}

    Returns:
        Set of allowed directory paths with variables replaced
    """
    allowed_dirs = set()
    for schema in schemas:
        if schema.directory:
            dir_path = schema.directory.replace("{user_space}", user_space).replace("{agent_space}", agent_space)
            allowed_dirs.add(dir_path)
    return allowed_dirs


def collect_allowed_path_patterns(
    schemas: List[MemoryTypeSchema],
    user_space: str = "default",
    agent_space: str = "default",
) -> Set[str]:
    """
    Collect all allowed full path patterns from activated schemas.

    Args:
        schemas: List of activated memory type schemas
        user_space: User space to substitute for {user_space}
        agent_space: Agent space to substitute for {agent_space}

    Returns:
        Set of allowed path patterns with {user_space} and {agent_space} replaced
        (other variables like {topic}, {tool_name}, etc. remain as patterns)
    """
    allowed_patterns = set()
    for schema in schemas:
        if not schema.directory and not schema.filename_template:
            continue
        pattern_parts = []
        if schema.directory:
            pattern_parts.append(schema.directory)
        if schema.filename_template:
            pattern_parts.append(schema.filename_template)
        if pattern_parts:
            pattern = "/".join(pattern_parts)
            pattern = pattern.replace("{user_space}", user_space).replace("{agent_space}", agent_space)
            allowed_patterns.add(pattern)
    return allowed_patterns


def _pattern_matches_uri(pattern: str, uri: str) -> bool:
    """
    Check if a URI matches a pattern with variables like {topic}, {tool_name}, etc.

    The pattern matching is flexible:
    - {variable} matches any sequence of characters except '/'
    - * matches any sequence of characters except '/' (shell-style)
    - ** matches any sequence of characters including '/' (shell-style)

    Args:
        pattern: The pattern to match against (may contain {variables} or * wildcards)
        uri: The URI to check

    Returns:
        True if the URI matches the pattern
    """
    import re

    # First, convert the pattern to a regex
    # Escape regex special chars except {, }, *, /
    pattern = re.escape(pattern)
    # Unescape {, }, * that we need to handle specially
    pattern = pattern.replace(r"\{", "{").replace(r"\}", "}").replace(r"\*", "*")
    # Convert {variable} to [^/]+
    pattern = re.sub(r"\{[^}]+\}", r"[^/]+", pattern)
    # Convert ** to .* and * to [^/]*
    pattern = pattern.replace("**", ".*")
    pattern = pattern.replace("*", "[^/]*")
    # Anchor the pattern
    pattern = "^" + pattern + "$"

    return bool(re.match(pattern, uri))


def is_uri_allowed(
    uri: str,
    allowed_directories: Set[str],
    allowed_patterns: Set[str],
) -> bool:
    """
    Check if a URI is allowed based on allowed directories and patterns.

    Args:
        uri: The URI to check
        allowed_directories: Set of allowed directory paths
        allowed_patterns: Set of allowed path patterns

    Returns:
        True if the URI is allowed
    """
    # Check if URI starts with any allowed directory
    for dir_path in allowed_directories:
        if uri == dir_path or uri.startswith(dir_path + "/"):
            return True
    # Check if URI matches any allowed pattern
    for pattern in allowed_patterns:
        if _pattern_matches_uri(pattern, uri):
            return True
    return False


def is_uri_allowed_for_schema(
    uri: str,
    schemas: List[MemoryTypeSchema],
    user_space: str = "default",
    agent_space: str = "default",
) -> bool:
    """
    Check if a URI is allowed for the given activated schemas.

    Args:
        uri: The URI to check
        schemas: List of activated memory type schemas
        user_space: User space to substitute for {user_space}
        agent_space: Agent space to substitute for {agent_space}

    Returns:
        True if the URI is allowed
    """
    allowed_dirs = collect_allowed_directories(schemas, user_space, agent_space)
    allowed_patterns = collect_allowed_path_patterns(schemas, user_space, agent_space)
    return is_uri_allowed(uri, allowed_dirs, allowed_patterns)


from openviking.session.memory.utils.model import model_to_dict


def extract_uri_fields_from_flat_model(model: Any, schema: MemoryTypeSchema) -> Dict[str, Any]:
    """
    Extract URI-friendly fields from a flat model, ignoring patch objects.

    Args:
        model: Flat model instance (Pydantic model or dict)
        schema: Memory type schema to know which fields are part of the schema

    Returns:
        Dict with only primitive type values suitable for URI generation
    """
    # Convert model to dict if it's a Pydantic model
    model_dict = model_to_dict(model)

    uri_fields = {}
    # Only include fields that are in the schema
    schema_field_names = {f.name for f in schema.fields}
    for name, value in model_dict.items():
        if name in schema_field_names and isinstance(value, (str, int, float, bool)):
            uri_fields[name] = value
    return uri_fields


def resolve_flat_model_uri(
    flat_model: Any,
    registry: MemoryTypeRegistry,
    user_space: str = "default",
    agent_space: str = "default",
) -> str:
    """
    Resolve URI for a flat model (used for both write and edit operations).

    Args:
        flat_model: Flat model instance with memory_type and business fields
        registry: MemoryTypeRegistry to get schema
        user_space: User space for substitution
        agent_space: Agent space for substitution

    Returns:
        Resolved URI

    Raises:
        ValueError: If memory_type not found or URI generation fails
    """
    # Get memory_type from the model
    if hasattr(flat_model, 'memory_type'):
        memory_type_str = flat_model.memory_type
    elif isinstance(flat_model, dict) and 'memory_type' in flat_model:
        memory_type_str = flat_model['memory_type']
    else:
        raise ValueError("Flat model missing 'memory_type' field")

    schema = registry.get(memory_type_str)
    if not schema:
        raise ValueError(f"Unknown memory type: {memory_type_str}")

    # Check if model already has a uri field
    if hasattr(flat_model, 'uri') and flat_model.uri is not None:
        return flat_model.uri
    elif isinstance(flat_model, dict) and 'uri' in flat_model and flat_model['uri'] is not None:
        return flat_model['uri']

    # Extract URI fields and generate URI
    uri_fields = extract_uri_fields_from_flat_model(flat_model, schema)
    return generate_uri(schema, uri_fields, user_space, agent_space)


def resolve_overview_edit_uri(
    overview_model: Any,
    registry: MemoryTypeRegistry,
    user_space: str = "default",
    agent_space: str = "default",
) -> str:
    """
    Resolve URI for an overview edit operation.

    Args:
        overview_model: Overview edit model with memory_type and overview fields
        registry: MemoryTypeRegistry to get schema
        user_space: User space for substitution
        agent_space: Agent space for substitution

    Returns:
        Resolved URI for .overview.md file (e.g., viking://user/default/memories/.overview.md)

    Raises:
        ValueError: If memory_type not found or directory not found
    """
    # Get memory_type from model
    if hasattr(overview_model, 'memory_type'):
        memory_type_str = overview_model.memory_type
    elif isinstance(overview_model, dict):
        memory_type_str = overview_model.get('memory_type')
    else:
        raise ValueError("overview_model must have memory_type field")

    # Get schema from registry
    schema = registry.get(memory_type_str)
    if not schema:
        raise ValueError(f"Unknown memory type: {memory_type_str}")

    if not schema.directory:
        raise ValueError(f"Memory type {memory_type_str} has no directory configured")

    # Substitute user_space and agent_space in directory
    directory = schema.directory.replace("{user_space}", user_space).replace("{agent_space}", agent_space)

    # Return the .overview.md URI
    return f"{directory}/.overview.md"


class ResolvedOperations:
    """Operations with resolved URIs."""

    def __init__(self):
        self.write_operations: List[Tuple[Any, str]] = []  # (flat_model, resolved_uri)
        self.edit_operations: List[Tuple[Any, str]] = []  # (flat_model, resolved_uri)
        self.edit_overview_operations: List[Tuple[Any, str]] = []  # (overview_edit_model, overview_uri)
        self.delete_operations: List[Tuple[str, str]] = []  # (uri_str, uri_str) - just the uri
        self.errors: List[str] = []

    def has_errors(self) -> bool:
        return len(self.errors) > 0


def resolve_all_operations(
    operations: Any,
    registry: MemoryTypeRegistry,
    user_space: str = "default",
    agent_space: str = "default",
) -> ResolvedOperations:
    """
    Resolve URIs for all operations using the new flat model format.

    Args:
        operations: StructuredMemoryOperations with write_uris, edit_uris, delete_uris
        registry: MemoryTypeRegistry to get schemas
        user_space: User space for substitution
        agent_space: Agent space for substitution

    Returns:
        ResolvedOperations with all URIs resolved
    """
    resolved = ResolvedOperations()

    # Resolve write operations (flat models)
    if hasattr(operations, 'write_uris'):
        for op in operations.write_uris:
            try:
                uri = resolve_flat_model_uri(op, registry, user_space, agent_space)
                resolved.write_operations.append((op, uri))
            except Exception as e:
                resolved.errors.append(f"Failed to resolve write operation: {e}")

    # Resolve edit operations (flat models)
    if hasattr(operations, 'edit_uris'):
        for op in operations.edit_uris:
            try:
                uri = resolve_flat_model_uri(op, registry, user_space, agent_space)
                resolved.edit_operations.append((op, uri))
            except Exception as e:
                resolved.errors.append(f"Failed to resolve edit operation: {e}")

    # Resolve edit_overview operations (overview edit models)
    if hasattr(operations, 'edit_overview_uris'):
        for op in operations.edit_overview_uris:
            try:
                uri = resolve_overview_edit_uri(op, registry, user_space, agent_space)
                resolved.edit_overview_operations.append((op, uri))
            except Exception as e:
                resolved.errors.append(f"Failed to resolve edit_overview operation: {e}")

    # Resolve delete operations (already URI strings)
    if hasattr(operations, 'delete_uris'):
        for uri in operations.delete_uris:
            try:
                # Delete operations are already URIs, just pass them through
                resolved.delete_operations.append((uri, uri))
            except Exception as e:
                resolved.errors.append(f"Failed to resolve delete operation: {e}")

    return resolved


def validate_operations_uris(
    operations: Any,
    schemas: List[MemoryTypeSchema],
    registry: MemoryTypeRegistry,
    user_space: str = "default",
    agent_space: str = "default",
) -> Tuple[bool, List[str]]:
    """
    Validate that all URIs in StructuredMemoryOperations are allowed.

    Args:
        operations: The StructuredMemoryOperations to validate
        schemas: List of activated memory type schemas
        registry: MemoryTypeRegistry for URI resolution
        user_space: User space to substitute for {user_space}
        agent_space: Agent space to substitute for {agent_space}

    Returns:
        Tuple of (is_valid, list of error messages)
    """
    allowed_dirs = collect_allowed_directories(schemas, user_space, agent_space)
    allowed_patterns = collect_allowed_path_patterns(schemas, user_space, agent_space)

    errors = []

    # First resolve all URIs
    resolved = resolve_all_operations(operations, registry, user_space, agent_space)

    if resolved.has_errors():
        errors.extend(resolved.errors)
    else:
        # Validate resolved URIs
        for _op, uri in resolved.write_operations:
            if not is_uri_allowed(uri, allowed_dirs, allowed_patterns):
                errors.append(f"Write operation URI not allowed: {uri}")

        for _op, uri in resolved.edit_operations:
            if not is_uri_allowed(uri, allowed_dirs, allowed_patterns):
                errors.append(f"Edit operation URI not allowed: {uri}")

        for _op, uri in resolved.edit_overview_operations:
            if not is_uri_allowed(uri, allowed_dirs, allowed_patterns):
                errors.append(f"Edit overview operation URI not allowed: {uri}")

        for _uri_str, uri in resolved.delete_operations:
            if not is_uri_allowed(uri, allowed_dirs, allowed_patterns):
                errors.append(f"Delete operation URI not allowed: {uri}")

    return len(errors) == 0, errors

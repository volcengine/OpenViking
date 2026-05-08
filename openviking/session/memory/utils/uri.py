# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
URI generation and validation utilities.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Set, Tuple

if TYPE_CHECKING:
    from openviking.session.memory.memory_isolation_handler import MemoryIsolationHandler
    from openviking.session.memory.memory_updater import ExtractContext

import jinja2

from openviking.session.memory.dataclass import MemoryTypeSchema, ResolvedOperations
from openviking.session.memory.memory_type_registry import MemoryTypeRegistry
from openviking.session.memory.utils.model import model_to_dict
from openviking_cli.utils import get_logger

logger = get_logger(__name__)


def _render_jinja_template(template: str, context: Dict[str, Any]) -> str:
    """Render a Jinja2 template with the given context."""
    env = jinja2.Environment(
        autoescape=False,
        keep_trailing_newline=True,
    )
    jinja_template = env.from_string(template)
    return jinja_template.render(**context)


def render_template(
    template: str,
    fields: Dict[str, Any],
    extract_context: Any = None,
) -> str:
    """
    Generic Jinja2 template rendering method.

    This is the same method used for rendering content_template in memory_updater.py.
    Used for rendering filename_template, directory, etc.

    Args:
        template: The template string with Jinja2 placeholders
        fields: Dictionary of field values for substitution
        extract_context: ExtractContext instance for template access to message ranges

    Returns:
        Rendered template string
    """
    # 创建 Jinja2 环境，允许未定义的变量（打印警告但不报错）
    env = jinja2.Environment(autoescape=False, undefined=jinja2.DebugUndefined)

    # 创建模板变量
    template_vars = fields.copy()
    # 始终传入 extract_context，即使是 None，避免模板中访问时 undefined
    template_vars["extract_context"] = extract_context

    # 渲染模板
    jinja_template = env.from_string(template)
    return jinja_template.render(**template_vars).strip()





def generate_uri(
    memory_type: MemoryTypeSchema,
    fields: Dict[str, Any],
    user_space: str = "default",
    agent_space: str = "default",
    extract_context: Any = None,
) -> tuple[str, str]:
    """
    Generate a full URI from memory type schema and field values.

    Args:
        memory_type: The memory type schema with directory and filename_template
        fields: The field values to use for template replacement
        user_space: The user space to substitute for {{ user_space }}
        agent_space: The agent space to substitute for {{ agent_space }}
        extract_context: ExtractContext instance for template rendering (same as content_template)

    Returns:
        The fully generated URI

    Raises:
        ValueError: If required template variables are missing from fields
    """
    # Build the URI template from directory and filename_template

    dir_template = memory_type.directory
    uri_template = f"{dir_template}/{memory_type.filename_template}"
    # Build the context for Jinja2 rendering - include user_space and agent_space
    context = {
        "user_space": user_space,
        "agent_space": agent_space,
    }
    # Add all fields to context (uri_fields with actual values)
    context.update(fields)
    # Render using unified render_template method (same as content_template)
    uri = render_template(uri_template, context, extract_context)
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
        # Match Jinja2 {{ variable }} patterns
        template_vars = set(re.findall(r"\{\{\s*(\w+)\s*\}\}", memory_type.filename_template))

        # {{ user_space }} and {{ agent_space }} are built-in, not from fields
        built_in_vars = {"user_space", "agent_space"}
        required_field_vars = template_vars - built_in_vars

        for var in required_field_vars:
            if var not in field_names:
                return False

    return True





def _pattern_matches_uri(pattern: str, uri: str) -> bool:
    """
    Check if a URI matches a pattern with variables like {{ topic }}, {{ tool_name }}, etc.

    The pattern matching is flexible:
    - {{ variable }} matches any sequence of characters except '/'
    - * matches any sequence of characters except '/' (shell-style)
    - ** matches any sequence of characters including '/' (shell-style)

    Args:
        pattern: The pattern to match against (may contain {{ variables }} or * wildcards)
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
    # Convert {{ variable }} to [^/]+
    pattern = re.sub(r"\{\{\s*[^}]+\s*\}\}", r"[^/]+", pattern)
    # Also support legacy {variable} format
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





def supplement_operation_uris(
    operations: ResolvedOperations,
    registry: MemoryTypeRegistry,
    extract_context: ExtractContext = None,
    isolation_handler: MemoryIsolationHandler = None,
):

    logger.info(f"[supplement_operation_uris] isolation_handler: {isolation_handler}")
    for operation in operations.upsert_operations:
        memory_type_schema = registry.get(operation.memory_type)
        uris = isolation_handler.calculate_memory_uris(
            memory_type_schema=memory_type_schema,
            operation=operation,
            extract_context=extract_context,
        )
        operation.uris = uris

# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
URI generation and validation utilities.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any, Dict

from openviking.session.memory.dataclass import MemoryTypeSchema
from openviking.session.memory.utils.model import model_to_dict
from openviking.session.memory.utils.template_utils import TemplateUtils

_PORTABLE_SEGMENT_MARKER = "~ov~"
_PORTABLE_SEGMENT_HASH_LENGTH = 16
_WINDOWS_INVALID_CHARS = frozenset('<>:"\\|?*')
_WINDOWS_RESERVED_STEMS = frozenset(
    {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        *(f"COM{index}" for index in range(1, 10)),
        *(f"LPT{index}" for index in range(1, 10)),
    }
)


def _windows_reserved_stem(segment: str) -> str:
    """Return the Win32 device-name stem used for portability checks."""
    return segment.split(".", 1)[0].rstrip(" .").upper()


def _is_portable_uri_segment(segment: str) -> bool:
    """Return whether a rendered URI segment is safe as a Windows path component."""
    if not segment or segment in {".", ".."}:
        return False
    if segment.endswith((" ", ".")):
        return False
    if _PORTABLE_SEGMENT_MARKER in segment:
        # Reserve the generated marker so a literal segment cannot alias one.
        return False
    for character in segment:
        codepoint = ord(character)
        if (
            character in _WINDOWS_INVALID_CHARS
            or codepoint < 32
            or codepoint == 127
            or 0xD800 <= codepoint <= 0xDFFF
        ):
            return False
    return _windows_reserved_stem(segment) not in _WINDOWS_RESERVED_STEMS


def _split_safe_extension(segment: str) -> tuple[str, str]:
    """Split a conventional final extension so rewritten memory files keep it."""
    stem, separator, extension = segment.rpartition(".")
    if not separator or not stem or not extension:
        return segment, ""
    if any(
        character in _WINDOWS_INVALID_CHARS
        or ord(character) < 32
        or ord(character) == 127
        or 0xD800 <= ord(character) <= 0xDFFF
        for character in extension
    ):
        return segment, ""
    return stem, f".{extension}"


def _portable_uri_segment(segment: str) -> str:
    """Rewrite one non-portable segment without collapsing distinct logical names."""
    if _is_portable_uri_segment(segment):
        return segment

    digest = hashlib.sha256(segment.encode("utf-8", errors="surrogatepass")).hexdigest()
    suffix = f"{_PORTABLE_SEGMENT_MARKER}{digest[:_PORTABLE_SEGMENT_HASH_LENGTH]}"

    cleaned = "".join(
        character
        if (
            character not in _WINDOWS_INVALID_CHARS
            and ord(character) >= 32
            and ord(character) != 127
            and not 0xD800 <= ord(character) <= 0xDFFF
        )
        else "_"
        for character in segment
    ).rstrip(" .")
    if not cleaned or cleaned in {".", ".."}:
        cleaned = "_"
    if _windows_reserved_stem(cleaned) in _WINDOWS_RESERVED_STEMS:
        cleaned = f"_{cleaned}"

    stem, extension = _split_safe_extension(cleaned)
    stem = stem.rstrip(" .") or "_"
    return f"{stem}{suffix}{extension}"


def _make_uri_path_segments_portable(uri: str) -> str:
    """Make every rendered memory URI segment portable on supported filesystems.

    Clean segments remain byte-identical. A non-portable segment keeps a readable
    safe prefix and receives a deterministic digest of its original value, so
    values such as ``Desktop``, ``Desktop ``, and ``Desktop.`` stay distinct.
    """
    scheme_separator = "://"
    if scheme_separator in uri:
        scheme, _, path = uri.partition(scheme_separator)
        prefix = f"{scheme}{scheme_separator}"
    else:
        prefix, path = "", uri

    if not path:
        return uri
    return prefix + "/".join(_portable_uri_segment(segment) for segment in path.split("/"))


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
    return TemplateUtils.render(
        template,
        fields,
        extract_context=extract_context,
        debug_undefined=True,
    )


def generate_uri(
    memory_type: MemoryTypeSchema,
    fields: Dict[str, Any],
    user_space: str = "default",
    extract_context: Any = None,
) -> str:
    """
    Generate a full URI from memory type schema and field values.

    Args:
        memory_type: The memory type schema with directory and filename_template
        fields: The field values to use for template replacement
        user_space: The user space to substitute for {{ user_space }}
        extract_context: ExtractContext instance for template rendering (same as content_template)

    Returns:
        The fully generated URI

    Raises:
        ValueError: If required template variables are missing from fields
    """
    # Build the URI template from directory and filename_template

    dir_template = memory_type.directory
    filename_template = memory_type.filename_template
    if dir_template and filename_template:
        uri_template = f"{dir_template.rstrip('/')}/{filename_template.lstrip('/')}"
    else:
        uri_template = dir_template or filename_template
    context = {"user_space": user_space}
    # Add all fields to context (uri_fields with actual values)
    context.update(fields)
    template_vars = set(re.findall(r"\{\{\s*(\w+)\s*\}\}", uri_template))
    for var in template_vars:
        if var not in context:
            raise ValueError(f"Missing template variable: {var}")
        if context[var] is None:
            raise ValueError(f"Template variable '{var}' has None value")
    # Render using unified render_template method (same as content_template)
    uri = render_template(uri_template, context, extract_context)
    return _make_uri_path_segments_portable(uri)


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

        built_in_vars = {"user_space"}
        required_field_vars = template_vars - built_in_vars

        for var in required_field_vars:
            if var not in field_names:
                return False

    return True


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

# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""MCP to Skill converter."""

import json
from typing import Any, Dict

_MODEL_FACING_CONSTRAINTS = (
    "minimum",
    "maximum",
    "default",
    "minLength",
    "maxLength",
    "pattern",
    "minItems",
    "maxItems",
    "uniqueItems",
)


def _format_constraints(schema: Dict[str, Any]) -> str:
    """Format common JSON Schema constraints for model-facing Skill text."""
    constraints = []
    for key in _MODEL_FACING_CONSTRAINTS:
        if key not in schema:
            continue
        try:
            value = json.dumps(
                schema[key],
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
        except (TypeError, ValueError):
            continue
        constraints.append(f"{key}: {value}")
    return "; ".join(constraints)


def mcp_to_skill(mcp_config: Dict[str, Any]) -> Dict[str, Any]:
    """Convert MCP tool definition to Skill format with YAML frontmatter."""
    name = mcp_config.get("name", "unnamed-tool").replace("_", "-")
    description = mcp_config.get("description", "")
    input_schema = mcp_config.get("inputSchema", {})

    # Build YAML frontmatter
    frontmatter_parts = [
        "---\n",
        f"name: {name}\n",
        f"description: {description}\n",
        "---\n\n",
    ]

    # Build markdown body
    body_parts = [f"# {name}\n\n"]

    if description:
        body_parts.append(f"{description}\n")

    # Add parameters section
    if input_schema and input_schema.get("properties"):
        body_parts.append("\n## Parameters\n\n")
        properties = input_schema.get("properties", {})
        required = input_schema.get("required", [])

        for param_name, param_info in properties.items():
            param_type = param_info.get("type", "any")
            param_desc = param_info.get("description", "")
            is_required = param_name in required

            required_str = " (required)" if is_required else " (optional)"
            details = param_desc
            constraints = _format_constraints(param_info)
            if constraints:
                details += (" " if details else "") + f"Constraints: {constraints}."
            body_parts.append(f"- **{param_name}** ({param_type}){required_str}: {details}\n")

    # Add usage section
    body_parts.append("\n## Usage\n\n")
    body_parts.append(f"This tool wraps the MCP tool `{name}`. ")
    body_parts.append(
        "Call this when the user needs functionality matching the description above.\n"
    )

    content = "".join(frontmatter_parts) + "".join(body_parts)

    return {
        "name": name,
        "description": description,
        "content": content,
    }


def is_mcp_format(data: Dict[str, Any]) -> bool:
    """Check if dict is in MCP tool format."""
    # MCP tools have "inputSchema" field
    return isinstance(data, dict) and "inputSchema" in data

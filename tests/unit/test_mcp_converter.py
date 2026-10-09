# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Tests for model-facing MCP tool conversion."""

from openviking.core.mcp_converter import mcp_to_skill


def test_mcp_to_skill_preserves_existing_unconstrained_parameter_output():
    result = mcp_to_skill(
        {
            "name": "search_web",
            "description": "Search the web",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query"},
                },
                "required": ["query"],
            },
        }
    )

    assert "- **query** (string) (required): Search query\n" in result["content"]


def test_mcp_to_skill_renders_common_json_schema_constraints():
    result = mcp_to_skill(
        {
            "name": "bounded_search",
            "description": "Search with bounded input.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "timeout_ms": {
                        "type": "integer",
                        "description": "Maximum wait in milliseconds.",
                        "minimum": 0,
                        "maximum": 30000,
                        "default": 0,
                    },
                    "query": {
                        "type": "string",
                        "minLength": 3,
                        "maxLength": 80,
                        "pattern": "^[A-Za-z0-9 ]+$",
                    },
                    "tags": {
                        "type": "array",
                        "description": "Unique result tags.",
                        "items": {"type": "string"},
                        "minItems": 1,
                        "maxItems": 4,
                        "uniqueItems": True,
                    },
                },
                "required": ["query"],
            },
        }
    )

    assert (
        "- **timeout_ms** (integer) (optional): Maximum wait in milliseconds. "
        "Constraints: minimum: 0; maximum: 30000; default: 0.\n" in result["content"]
    )
    assert (
        "- **query** (string) (required): Constraints: minLength: 3; maxLength: 80; "
        'pattern: "^[A-Za-z0-9 ]+$".\n' in result["content"]
    )
    assert (
        "- **tags** (array) (optional): Unique result tags. "
        "Constraints: minItems: 1; maxItems: 4; uniqueItems: true.\n" in result["content"]
    )

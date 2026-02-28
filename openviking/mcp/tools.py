# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Tool definitions and dispatcher for OpenViking MCP server."""

from __future__ import annotations

import copy
import json
from dataclasses import asdict, is_dataclass
from enum import Enum
from typing import Any, Dict, List

from .permissions import MCPAccessLevel, access_level_name, can_access, parse_access_level

MAX_READ_LIMIT = 2000
DEFAULT_READ_LIMIT = 200
MAX_FIND_LIMIT = 50
DEFAULT_FIND_LIMIT = 10
MAX_TREE_ABS_LIMIT = 4096
DEFAULT_TREE_ABS_LIMIT = 128
MAX_TREE_NODE_LIMIT = 5000
DEFAULT_TREE_NODE_LIMIT = 1000
ALLOWED_SESSION_ROLES = {"user", "assistant", "system", "tool"}


def _tool(
    name: str,
    description: str,
    input_schema: Dict[str, Any],
    min_access_level: str = "readonly",
) -> Dict[str, Any]:
    return {
        "name": name,
        "description": description,
        "inputSchema": input_schema,
        "minAccessLevel": min_access_level,
    }


READ_TOOL_DEFINITIONS = [
    _tool(
        "openviking_find",
        "Semantic search in OpenViking context database.",
        {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query."},
                "uri": {
                    "type": "string",
                    "description": "Target URI scope. Default is global search.",
                    "default": "",
                },
                "limit": {
                    "type": "integer",
                    "description": f"Max number of results (1-{MAX_FIND_LIMIT}).",
                    "default": DEFAULT_FIND_LIMIT,
                    "minimum": 1,
                    "maximum": MAX_FIND_LIMIT,
                },
                "threshold": {"type": "number", "description": "Optional score threshold."},
            },
            "required": ["query"],
        },
    ),
    _tool(
        "openviking_search",
        "Context-aware retrieval in OpenViking.",
        {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query."},
                "uri": {
                    "type": "string",
                    "description": "Target URI scope. Default is global search.",
                    "default": "",
                },
                "session_id": {
                    "type": "string",
                    "description": "Optional session ID for context-aware retrieval.",
                },
                "limit": {
                    "type": "integer",
                    "description": f"Max number of results (1-{MAX_FIND_LIMIT}).",
                    "default": DEFAULT_FIND_LIMIT,
                    "minimum": 1,
                    "maximum": MAX_FIND_LIMIT,
                },
                "threshold": {"type": "number", "description": "Optional score threshold."},
            },
            "required": ["query"],
        },
    ),
    _tool(
        "openviking_read",
        "Read content from OpenViking (L2).",
        {
            "type": "object",
            "properties": {
                "uri": {"type": "string", "description": "Resource URI."},
                "offset": {
                    "type": "integer",
                    "description": "Starting line number, 0-indexed.",
                    "default": 0,
                    "minimum": 0,
                },
                "limit": {
                    "type": "integer",
                    "description": (
                        f"Number of lines to read. "
                        f"Default {DEFAULT_READ_LIMIT}, max {MAX_READ_LIMIT}."
                    ),
                    "default": DEFAULT_READ_LIMIT,
                    "minimum": 1,
                    "maximum": MAX_READ_LIMIT,
                },
            },
            "required": ["uri"],
        },
    ),
    _tool(
        "openviking_ls",
        "List directory contents in OpenViking.",
        {
            "type": "object",
            "properties": {
                "uri": {
                    "type": "string",
                    "description": "Directory URI. Default is viking://.",
                    "default": "viking://",
                },
                "simple": {
                    "type": "boolean",
                    "description": "Whether to return simple path list.",
                    "default": False,
                },
                "recursive": {
                    "type": "boolean",
                    "description": "Whether to list subdirectories recursively.",
                    "default": False,
                },
                "output": {
                    "type": "string",
                    "description": "Output format, either 'agent' or 'original'.",
                    "default": "agent",
                },
                "abs_limit": {
                    "type": "integer",
                    "description": f"Abstract content limit (0-{MAX_TREE_ABS_LIMIT}).",
                    "default": 256,
                    "minimum": 0,
                    "maximum": MAX_TREE_ABS_LIMIT,
                },
                "show_all_hidden": {
                    "type": "boolean",
                    "description": "Whether to include all hidden entries.",
                    "default": False,
                },
                "node_limit": {
                    "type": "integer",
                    "description": f"Maximum nodes in output (1-{MAX_TREE_NODE_LIMIT}).",
                    "default": DEFAULT_TREE_NODE_LIMIT,
                    "minimum": 1,
                    "maximum": MAX_TREE_NODE_LIMIT,
                },
            },
            "required": [],
        },
    ),
    _tool(
        "openviking_abstract",
        "Read L0 abstract (.abstract.md) for a directory URI.",
        {
            "type": "object",
            "properties": {"uri": {"type": "string", "description": "Directory URI."}},
            "required": ["uri"],
        },
    ),
    _tool(
        "openviking_overview",
        "Read L1 overview (.overview.md) for a directory URI.",
        {
            "type": "object",
            "properties": {"uri": {"type": "string", "description": "Directory URI."}},
            "required": ["uri"],
        },
    ),
    _tool(
        "openviking_wait_processed",
        "Wait until queued async processing completes.",
        {
            "type": "object",
            "properties": {"timeout": {"type": "number", "description": "Optional timeout in seconds."}},
            "required": [],
        },
    ),
    _tool(
        "openviking_stat",
        "Get resource metadata and status.",
        {
            "type": "object",
            "properties": {"uri": {"type": "string", "description": "Resource URI."}},
            "required": ["uri"],
        },
    ),
    _tool(
        "openviking_tree",
        "Get directory tree in agent-friendly format.",
        {
            "type": "object",
            "properties": {
                "uri": {"type": "string", "description": "Directory URI."},
                "abs_limit": {
                    "type": "integer",
                    "description": f"Abstract content limit (0-{MAX_TREE_ABS_LIMIT}).",
                    "default": DEFAULT_TREE_ABS_LIMIT,
                    "minimum": 0,
                    "maximum": MAX_TREE_ABS_LIMIT,
                },
                "show_all_hidden": {
                    "type": "boolean",
                    "description": "Whether to include all hidden entries.",
                    "default": False,
                },
                "node_limit": {
                    "type": "integer",
                    "description": f"Maximum nodes in output (1-{MAX_TREE_NODE_LIMIT}).",
                    "default": DEFAULT_TREE_NODE_LIMIT,
                    "minimum": 1,
                    "maximum": MAX_TREE_NODE_LIMIT,
                },
            },
            "required": ["uri"],
        },
    ),
    _tool(
        "openviking_grep",
        "Search text pattern in files under a URI.",
        {
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "Search pattern."},
                "uri": {"type": "string", "description": "Search root URI.", "default": "viking://"},
                "ignore_case": {
                    "type": "boolean",
                    "description": "Case-insensitive matching.",
                    "default": False,
                },
            },
            "required": ["pattern"],
        },
    ),
    _tool(
        "openviking_glob",
        "Search files by glob pattern under a URI.",
        {
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "Glob pattern."},
                "uri": {"type": "string", "description": "Search root URI.", "default": "viking://"},
            },
            "required": ["pattern"],
        },
    ),
    _tool("openviking_status", "Get OpenViking component status.", {"type": "object", "properties": {}, "required": []}),
    _tool("openviking_health", "Get quick health check result.", {"type": "object", "properties": {}, "required": []}),
    _tool("openviking_session_list", "List sessions.", {"type": "object", "properties": {}, "required": []}),
    _tool(
        "openviking_session_get",
        "Get session details.",
        {
            "type": "object",
            "properties": {"session_id": {"type": "string", "description": "Session ID."}},
            "required": ["session_id"],
        },
    ),
    _tool(
        "openviking_relation_list",
        "List relations of a resource.",
        {
            "type": "object",
            "properties": {"uri": {"type": "string", "description": "Resource URI."}},
            "required": ["uri"],
        },
    ),
]

INGEST_TOOL_DEFINITIONS = [
    _tool(
        "openviking_session_create",
        "Create a new session.",
        {"type": "object", "properties": {}, "required": []},
        min_access_level="ingest",
    ),
    _tool(
        "openviking_session_add_message",
        "Add a message to a session.",
        {
            "type": "object",
            "properties": {
                "session_id": {"type": "string", "description": "Session ID."},
                "role": {"type": "string", "description": "Message role: user, assistant, system, or tool."},
                "content": {
                    "type": "string",
                    "description": "Optional text content. Required when parts is absent.",
                },
                "parts": {
                    "type": "array",
                    "description": "Optional message parts. Required when content is absent.",
                    "items": {"type": "object"},
                },
            },
            "required": ["session_id", "role"],
        },
        min_access_level="ingest",
    ),
    _tool(
        "openviking_session_commit",
        "Commit a session (archive and extract memories).",
        {
            "type": "object",
            "properties": {"session_id": {"type": "string", "description": "Session ID."}},
            "required": ["session_id"],
        },
        min_access_level="ingest",
    ),
    _tool(
        "openviking_resource_add",
        "Add local path or URL as resource into OpenViking.",
        {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Local path or URL to import."},
                "to": {"type": "string", "description": "Optional target URI."},
                "reason": {"type": "string", "description": "Optional import reason.", "default": ""},
                "instruction": {
                    "type": "string",
                    "description": "Optional additional instruction.",
                    "default": "",
                },
                "wait": {
                    "type": "boolean",
                    "description": "Wait until processing completes.",
                    "default": False,
                },
                "timeout": {"type": "number", "description": "Optional wait timeout in seconds."},
            },
            "required": ["path"],
        },
        min_access_level="ingest",
    ),
    _tool(
        "openviking_add_resource",
        "[Deprecated alias] Add local path or URL as resource into OpenViking. Use openviking_resource_add.",
        {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Local path or URL to import."},
                "to": {"type": "string", "description": "Optional target URI."},
                "reason": {"type": "string", "description": "Optional import reason.", "default": ""},
                "instruction": {
                    "type": "string",
                    "description": "Optional additional instruction.",
                    "default": "",
                },
                "wait": {
                    "type": "boolean",
                    "description": "Wait until processing completes.",
                    "default": False,
                },
                "timeout": {"type": "number", "description": "Optional wait timeout in seconds."},
            },
            "required": ["path"],
        },
        min_access_level="ingest",
    ),
    _tool(
        "openviking_resource_add_skill",
        "Add a skill into OpenViking.",
        {
            "type": "object",
            "properties": {
                "data": {"type": "string", "description": "Skill directory, SKILL.md, or raw content."},
                "wait": {"type": "boolean", "description": "Wait until processing completes.", "default": False},
                "timeout": {"type": "number", "description": "Optional wait timeout in seconds."},
            },
            "required": ["data"],
        },
        min_access_level="ingest",
    ),
]

MUTATE_TOOL_DEFINITIONS = [
    _tool(
        "openviking_relation_link",
        "Create relation links from one URI to one or more targets.",
        {
            "type": "object",
            "properties": {
                "from_uri": {"type": "string", "description": "Source URI."},
                "uris": {
                    "anyOf": [{"type": "string"}, {"type": "array", "items": {"type": "string"}}],
                    "description": "Target URI or list of target URIs.",
                },
                "reason": {"type": "string", "description": "Reason for linking.", "default": ""},
            },
            "required": ["from_uri", "uris"],
        },
        min_access_level="mutate",
    ),
    _tool(
        "openviking_relation_unlink",
        "Remove a relation link.",
        {
            "type": "object",
            "properties": {
                "from_uri": {"type": "string", "description": "Source URI."},
                "uri": {"type": "string", "description": "Target URI to unlink."},
            },
            "required": ["from_uri", "uri"],
        },
        min_access_level="mutate",
    ),
    _tool(
        "openviking_fs_mkdir",
        "Create a directory.",
        {
            "type": "object",
            "properties": {"uri": {"type": "string", "description": "Directory URI to create."}},
            "required": ["uri"],
        },
        min_access_level="mutate",
    ),
    _tool(
        "openviking_fs_mv",
        "Move or rename a resource.",
        {
            "type": "object",
            "properties": {
                "from_uri": {"type": "string", "description": "Source URI."},
                "to_uri": {"type": "string", "description": "Target URI."},
            },
            "required": ["from_uri", "to_uri"],
        },
        min_access_level="mutate",
    ),
]

ADMIN_TOOL_DEFINITIONS = [
    _tool(
        "openviking_session_delete",
        "Delete a session.",
        {
            "type": "object",
            "properties": {"session_id": {"type": "string", "description": "Session ID."}},
            "required": ["session_id"],
        },
        min_access_level="admin",
    ),
    _tool(
        "openviking_fs_rm",
        "Remove a resource.",
        {
            "type": "object",
            "properties": {
                "uri": {"type": "string", "description": "Viking URI to remove."},
                "recursive": {"type": "boolean", "description": "Remove recursively.", "default": False},
            },
            "required": ["uri"],
        },
        min_access_level="admin",
    ),
    _tool(
        "openviking_pack_export",
        "Export context as .ovpack.",
        {
            "type": "object",
            "properties": {
                "uri": {"type": "string", "description": "Source URI."},
                "to": {"type": "string", "description": "Output .ovpack file path."},
            },
            "required": ["uri", "to"],
        },
        min_access_level="admin",
    ),
    _tool(
        "openviking_pack_import",
        "Import .ovpack into target URI.",
        {
            "type": "object",
            "properties": {
                "file_path": {"type": "string", "description": "Input .ovpack file path."},
                "target_uri": {"type": "string", "description": "Target parent URI."},
                "force": {"type": "boolean", "description": "Overwrite when conflicts exist.", "default": False},
                "vectorize": {
                    "type": "boolean",
                    "description": "Whether to trigger vectorization after import.",
                    "default": True,
                },
            },
            "required": ["file_path", "target_uri"],
        },
        min_access_level="admin",
    ),
]

TOOL_ALIASES = {"openviking_add_resource": "openviking_resource_add"}

TOOL_DEFINITIONS = (
    READ_TOOL_DEFINITIONS + INGEST_TOOL_DEFINITIONS + MUTATE_TOOL_DEFINITIONS + ADMIN_TOOL_DEFINITIONS
)
TOOL_REQUIRED_LEVELS = {
    tool["name"]: parse_access_level(tool["minAccessLevel"]) for tool in TOOL_DEFINITIONS
}


class ToolArgumentError(ValueError):
    """Raised when MCP tool arguments are invalid."""


def get_tool_definitions(access_level: MCPAccessLevel | str = MCPAccessLevel.READONLY) -> List[Dict[str, Any]]:
    """Return MCP tool definitions filtered by access level."""
    level = parse_access_level(access_level)
    definitions = [
        tool for tool in TOOL_DEFINITIONS if can_access(level, parse_access_level(tool["minAccessLevel"]))
    ]
    return [copy.deepcopy(tool) for tool in definitions]


def _to_jsonable(value: Any) -> Any:
    """Convert values into JSON-serializable structures."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {str(k): _to_jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_to_jsonable(item) for item in value]
    if hasattr(value, "to_dict") and callable(value.to_dict):
        return _to_jsonable(value.to_dict())
    if hasattr(value, "model_dump") and callable(value.model_dump):
        return _to_jsonable(value.model_dump())
    if is_dataclass(value):
        return _to_jsonable(asdict(value))
    if hasattr(value, "__dict__"):
        data = {k: v for k, v in vars(value).items() if not k.startswith("_")}
        return _to_jsonable(data)
    return str(value)


def _json_ok(result: Any) -> str:
    return json.dumps({"ok": True, "result": _to_jsonable(result)}, ensure_ascii=False)


def _json_error(code: str, message: str, details: Dict[str, Any] | None = None) -> str:
    payload: Dict[str, Any] = {"ok": False, "error": {"code": code, "message": message}}
    if details:
        payload["error"]["details"] = _to_jsonable(details)
    return json.dumps(payload, ensure_ascii=False)


def _expect_dict(arguments: Any) -> Dict[str, Any]:
    if arguments is None:
        return {}
    if not isinstance(arguments, dict):
        raise ToolArgumentError("arguments must be a JSON object")
    return arguments


def _require_str(arguments: Dict[str, Any], key: str) -> str:
    value = arguments.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ToolArgumentError(f"'{key}' must be a non-empty string")
    return value


def _optional_str(arguments: Dict[str, Any], key: str, default: str) -> str:
    value = arguments.get(key, default)
    if value is None:
        return default
    if not isinstance(value, str):
        raise ToolArgumentError(f"'{key}' must be a string")
    return value


def _optional_nullable_str(arguments: Dict[str, Any], key: str) -> str | None:
    if key not in arguments:
        return None
    value = arguments.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ToolArgumentError(f"'{key}' must be a string or null")
    return value


def _optional_int(arguments: Dict[str, Any], key: str, default: int) -> int:
    value = arguments.get(key, default)
    if not isinstance(value, int) or isinstance(value, bool):
        raise ToolArgumentError(f"'{key}' must be an integer")
    return value


def _optional_bool(arguments: Dict[str, Any], key: str, default: bool) -> bool:
    value = arguments.get(key, default)
    if not isinstance(value, bool):
        raise ToolArgumentError(f"'{key}' must be a boolean")
    return value


def _optional_float(arguments: Dict[str, Any], key: str) -> float | None:
    if key not in arguments:
        return None
    value = arguments.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ToolArgumentError(f"'{key}' must be a number")
    return float(value)


def _optional_parts(arguments: Dict[str, Any], key: str) -> list[dict] | None:
    if key not in arguments:
        return None
    value = arguments.get(key)
    if value is None:
        return None
    if not isinstance(value, list):
        raise ToolArgumentError(f"'{key}' must be an array of objects")
    if not value:
        raise ToolArgumentError(f"'{key}' must not be an empty array")
    for item in value:
        if not isinstance(item, dict):
            raise ToolArgumentError(f"'{key}' must be an array of objects")
    return value


def _require_str_or_str_list(arguments: Dict[str, Any], key: str) -> str | list[str]:
    value = arguments.get(key)
    if isinstance(value, str):
        if not value.strip():
            raise ToolArgumentError(f"'{key}' must be a non-empty string or non-empty string array")
        return value
    if isinstance(value, list):
        if not value:
            raise ToolArgumentError(f"'{key}' must be a non-empty string or non-empty string array")
        normalized: list[str] = []
        for item in value:
            if not isinstance(item, str) or not item.strip():
                raise ToolArgumentError(f"'{key}' must contain non-empty strings")
            normalized.append(item)
        return normalized
    raise ToolArgumentError(f"'{key}' must be a non-empty string or non-empty string array")


def _permission_denied_error(name: str, required: MCPAccessLevel, current: MCPAccessLevel) -> str:
    return _json_error(
        "PERMISSION_DENIED",
        f"Tool '{name}' requires access level '{access_level_name(required)}'",
        details={
            "tool": name,
            "required": access_level_name(required),
            "current": access_level_name(current),
        },
    )

def dispatch_tool(
    name: str,
    arguments: Any,
    client: Any,
    access_level: MCPAccessLevel | str = MCPAccessLevel.READONLY,
) -> str:
    """Dispatch an MCP tool call and return a JSON payload string."""
    try:
        args = _expect_dict(arguments)
        current_level = parse_access_level(access_level)
        normalized_name = TOOL_ALIASES.get(name, name)

        required = TOOL_REQUIRED_LEVELS.get(normalized_name)
        if required is None:
            return _json_error("TOOL_NOT_FOUND", f"Unknown tool: {name}")
        if not can_access(current_level, required):
            return _permission_denied_error(name, required, current_level)

        if normalized_name == "openviking_find":
            query = _require_str(args, "query")
            target_uri = _optional_str(args, "uri", "")
            limit = _optional_int(args, "limit", DEFAULT_FIND_LIMIT)
            if limit < 1 or limit > MAX_FIND_LIMIT:
                raise ToolArgumentError(
                    f"'limit' must be between 1 and {MAX_FIND_LIMIT} for openviking_find"
                )
            threshold = _optional_float(args, "threshold")
            return _json_ok(
                client.find(
                    query=query,
                    target_uri=target_uri,
                    limit=limit,
                    score_threshold=threshold,
                )
            )

        if normalized_name == "openviking_search":
            query = _require_str(args, "query")
            target_uri = _optional_str(args, "uri", "")
            session_id = _optional_nullable_str(args, "session_id")
            limit = _optional_int(args, "limit", DEFAULT_FIND_LIMIT)
            if limit < 1 or limit > MAX_FIND_LIMIT:
                raise ToolArgumentError(
                    f"'limit' must be between 1 and {MAX_FIND_LIMIT} for openviking_search"
                )
            threshold = _optional_float(args, "threshold")
            return _json_ok(
                client.search(
                    query=query,
                    target_uri=target_uri,
                    session_id=session_id,
                    limit=limit,
                    score_threshold=threshold,
                )
            )

        if normalized_name == "openviking_read":
            uri = _require_str(args, "uri")
            offset = _optional_int(args, "offset", 0)
            if offset < 0:
                raise ToolArgumentError("'offset' must be >= 0")
            limit = _optional_int(args, "limit", DEFAULT_READ_LIMIT)
            if limit < 1 or limit > MAX_READ_LIMIT:
                raise ToolArgumentError(
                    f"'limit' must be between 1 and {MAX_READ_LIMIT} for openviking_read"
                )
            return _json_ok(client.read(uri=uri, offset=offset, limit=limit))

        if normalized_name == "openviking_ls":
            uri = _optional_str(args, "uri", "viking://")
            simple = _optional_bool(args, "simple", False)
            recursive = _optional_bool(args, "recursive", False)
            output = _optional_str(args, "output", "agent")
            if output not in {"agent", "original"}:
                raise ToolArgumentError("'output' must be either 'agent' or 'original'")
            abs_limit = _optional_int(args, "abs_limit", 256)
            if abs_limit < 0 or abs_limit > MAX_TREE_ABS_LIMIT:
                raise ToolArgumentError(
                    f"'abs_limit' must be between 0 and {MAX_TREE_ABS_LIMIT} for openviking_ls"
                )
            show_all_hidden = _optional_bool(args, "show_all_hidden", False)
            node_limit = _optional_int(args, "node_limit", DEFAULT_TREE_NODE_LIMIT)
            if node_limit < 1 or node_limit > MAX_TREE_NODE_LIMIT:
                raise ToolArgumentError(
                    f"'node_limit' must be between 1 and {MAX_TREE_NODE_LIMIT} for openviking_ls"
                )
            return _json_ok(
                client.ls(
                    uri=uri,
                    simple=simple,
                    recursive=recursive,
                    output=output,
                    abs_limit=abs_limit,
                    show_all_hidden=show_all_hidden,
                    node_limit=node_limit,
                )
            )

        if normalized_name == "openviking_abstract":
            return _json_ok(client.abstract(uri=_require_str(args, "uri")))

        if normalized_name == "openviking_overview":
            return _json_ok(client.overview(uri=_require_str(args, "uri")))

        if normalized_name == "openviking_wait_processed":
            return _json_ok(client.wait_processed(timeout=_optional_float(args, "timeout")))

        if normalized_name == "openviking_stat":
            return _json_ok(client.stat(uri=_require_str(args, "uri")))

        if normalized_name == "openviking_tree":
            uri = _require_str(args, "uri")
            abs_limit = _optional_int(args, "abs_limit", DEFAULT_TREE_ABS_LIMIT)
            if abs_limit < 0 or abs_limit > MAX_TREE_ABS_LIMIT:
                raise ToolArgumentError(
                    f"'abs_limit' must be between 0 and {MAX_TREE_ABS_LIMIT} for openviking_tree"
                )
            show_all_hidden = _optional_bool(args, "show_all_hidden", False)
            node_limit = _optional_int(args, "node_limit", DEFAULT_TREE_NODE_LIMIT)
            if node_limit < 1 or node_limit > MAX_TREE_NODE_LIMIT:
                raise ToolArgumentError(
                    f"'node_limit' must be between 1 and {MAX_TREE_NODE_LIMIT} for openviking_tree"
                )
            return _json_ok(
                client.tree(
                    uri=uri,
                    output="agent",
                    abs_limit=abs_limit,
                    show_all_hidden=show_all_hidden,
                    node_limit=node_limit,
                )
            )

        if normalized_name == "openviking_grep":
            return _json_ok(
                client.grep(
                    uri=_optional_str(args, "uri", "viking://"),
                    pattern=_require_str(args, "pattern"),
                    case_insensitive=_optional_bool(args, "ignore_case", False),
                )
            )

        if normalized_name == "openviking_glob":
            return _json_ok(
                client.glob(
                    pattern=_require_str(args, "pattern"),
                    uri=_optional_str(args, "uri", "viking://"),
                )
            )

        if normalized_name == "openviking_status":
            return _json_ok(client.get_status())

        if normalized_name == "openviking_health":
            return _json_ok({"healthy": bool(client.is_healthy())})

        if normalized_name == "openviking_session_create":
            return _json_ok(client.create_session())

        if normalized_name == "openviking_session_list":
            return _json_ok(client.list_sessions())

        if normalized_name == "openviking_session_get":
            return _json_ok(client.get_session(session_id=_require_str(args, "session_id")))

        if normalized_name == "openviking_session_delete":
            session_id = _require_str(args, "session_id")
            result = client.delete_session(session_id=session_id)
            return _json_ok({"session_id": session_id} if result is None else result)

        if normalized_name == "openviking_session_add_message":
            session_id = _require_str(args, "session_id")
            role = _require_str(args, "role").strip().lower()
            if role not in ALLOWED_SESSION_ROLES:
                raise ToolArgumentError(
                    f"'role' must be one of: {', '.join(sorted(ALLOWED_SESSION_ROLES))}"
                )
            content = _optional_nullable_str(args, "content")
            if content is not None and not content.strip():
                raise ToolArgumentError("'content' must be a non-empty string when provided")
            parts = _optional_parts(args, "parts")
            if content is None and parts is None:
                raise ToolArgumentError("either 'content' or 'parts' must be provided")
            return _json_ok(
                client.add_message(
                    session_id=session_id,
                    role=role,
                    content=content,
                    parts=parts,
                )
            )

        if normalized_name == "openviking_session_commit":
            return _json_ok(client.commit_session(session_id=_require_str(args, "session_id")))

        if normalized_name == "openviking_resource_add":
            return _json_ok(
                client.add_resource(
                    path=_require_str(args, "path"),
                    target=_optional_nullable_str(args, "to"),
                    reason=_optional_str(args, "reason", ""),
                    instruction=_optional_str(args, "instruction", ""),
                    wait=_optional_bool(args, "wait", False),
                    timeout=_optional_float(args, "timeout"),
                )
            )

        if normalized_name == "openviking_resource_add_skill":
            return _json_ok(
                client.add_skill(
                    data=_require_str(args, "data"),
                    wait=_optional_bool(args, "wait", False),
                    timeout=_optional_float(args, "timeout"),
                )
            )

        if normalized_name == "openviking_relation_list":
            return _json_ok(client.relations(uri=_require_str(args, "uri")))

        if normalized_name == "openviking_relation_link":
            from_uri = _require_str(args, "from_uri")
            uris = _require_str_or_str_list(args, "uris")
            reason = _optional_str(args, "reason", "")
            result = client.link(from_uri=from_uri, uris=uris, reason=reason)
            return _json_ok({"from": from_uri, "to": uris, "reason": reason} if result is None else result)

        if normalized_name == "openviking_relation_unlink":
            from_uri = _require_str(args, "from_uri")
            uri = _require_str(args, "uri")
            result = client.unlink(from_uri=from_uri, uri=uri)
            return _json_ok({"from": from_uri, "to": uri} if result is None else result)

        if normalized_name == "openviking_fs_mkdir":
            uri = _require_str(args, "uri")
            result = client.mkdir(uri=uri)
            return _json_ok({"uri": uri} if result is None else result)

        if normalized_name == "openviking_fs_mv":
            from_uri = _require_str(args, "from_uri")
            to_uri = _require_str(args, "to_uri")
            result = client.mv(from_uri=from_uri, to_uri=to_uri)
            return _json_ok({"from": from_uri, "to": to_uri} if result is None else result)

        if normalized_name == "openviking_fs_rm":
            uri = _require_str(args, "uri")
            recursive = _optional_bool(args, "recursive", False)
            result = client.rm(uri=uri, recursive=recursive)
            return _json_ok({"uri": uri, "recursive": recursive} if result is None else result)

        if normalized_name == "openviking_pack_export":
            return _json_ok(
                {"file": client.export_ovpack(uri=_require_str(args, "uri"), to=_require_str(args, "to"))}
            )

        if normalized_name == "openviking_pack_import":
            return _json_ok(
                {
                    "uri": client.import_ovpack(
                        file_path=_require_str(args, "file_path"),
                        target=_require_str(args, "target_uri"),
                        force=_optional_bool(args, "force", False),
                        vectorize=_optional_bool(args, "vectorize", True),
                    )
                }
            )

        return _json_error("TOOL_NOT_FOUND", f"Unknown tool: {name}")

    except ToolArgumentError as exc:
        return _json_error("INVALID_ARGUMENT", str(exc))
    except Exception as exc:  # noqa: BLE001
        return _json_error(
            "INTERNAL",
            "Tool execution failed",
            details={"tool": name, "exception": type(exc).__name__, "message": str(exc)},
        )

# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Tool definitions and dispatcher for OpenViking MCP server."""

from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from enum import Enum
from typing import Any, Dict

MAX_READ_LIMIT = 2000
DEFAULT_READ_LIMIT = 200
MAX_FIND_LIMIT = 50
DEFAULT_FIND_LIMIT = 10

TOOL_DEFINITIONS = [
    {
        "name": "openviking_find",
        "description": "Semantic search in OpenViking context database.",
        "inputSchema": {
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
                "threshold": {
                    "type": "number",
                    "description": "Optional score threshold.",
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "openviking_read",
        "description": "Read content from OpenViking (L2).",
        "inputSchema": {
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
    },
    {
        "name": "openviking_ls",
        "description": "List directory contents in OpenViking.",
        "inputSchema": {
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
            },
            "required": [],
        },
    },
    {
        "name": "openviking_abstract",
        "description": "Read L0 abstract (.abstract.md) for a directory URI.",
        "inputSchema": {
            "type": "object",
            "properties": {"uri": {"type": "string", "description": "Directory URI."}},
            "required": ["uri"],
        },
    },
    {
        "name": "openviking_overview",
        "description": "Read L1 overview (.overview.md) for a directory URI.",
        "inputSchema": {
            "type": "object",
            "properties": {"uri": {"type": "string", "description": "Directory URI."}},
            "required": ["uri"],
        },
    },
]


class ToolArgumentError(ValueError):
    """Raised when MCP tool arguments are invalid."""


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
    value = arguments.get(key)
    if value is None:
        return None
    if not isinstance(value, (int, float)):
        raise ToolArgumentError(f"'{key}' must be a number")
    return float(value)


def dispatch_tool(name: str, arguments: Any, client: Any) -> str:
    """Dispatch an MCP tool call and return a JSON payload string."""
    try:
        args = _expect_dict(arguments)

        if name == "openviking_find":
            query = _require_str(args, "query")
            target_uri = _optional_str(args, "uri", "")
            limit = _optional_int(args, "limit", DEFAULT_FIND_LIMIT)
            if limit < 1 or limit > MAX_FIND_LIMIT:
                raise ToolArgumentError(
                    f"'limit' must be between 1 and {MAX_FIND_LIMIT} for openviking_find"
                )
            threshold = _optional_float(args, "threshold")
            result = client.find(
                query=query,
                target_uri=target_uri,
                limit=limit,
                score_threshold=threshold,
            )
            return _json_ok(result)

        if name == "openviking_read":
            uri = _require_str(args, "uri")
            offset = _optional_int(args, "offset", 0)
            if offset < 0:
                raise ToolArgumentError("'offset' must be >= 0")
            limit = _optional_int(args, "limit", DEFAULT_READ_LIMIT)
            if limit < 1 or limit > MAX_READ_LIMIT:
                raise ToolArgumentError(
                    f"'limit' must be between 1 and {MAX_READ_LIMIT} for openviking_read"
                )
            result = client.read(uri=uri, offset=offset, limit=limit)
            return _json_ok(result)

        if name == "openviking_ls":
            uri = _optional_str(args, "uri", "viking://")
            simple = _optional_bool(args, "simple", False)
            recursive = _optional_bool(args, "recursive", False)
            result = client.ls(uri=uri, simple=simple, recursive=recursive, output="agent")
            return _json_ok(result)

        if name == "openviking_abstract":
            uri = _require_str(args, "uri")
            result = client.abstract(uri=uri)
            return _json_ok(result)

        if name == "openviking_overview":
            uri = _require_str(args, "uri")
            result = client.overview(uri=uri)
            return _json_ok(result)

        return _json_error("TOOL_NOT_FOUND", f"Unknown tool: {name}")

    except ToolArgumentError as exc:
        return _json_error("INVALID_ARGUMENT", str(exc))
    except Exception as exc:  # noqa: BLE001
        return _json_error(
            "INTERNAL",
            "Tool execution failed",
            details={"tool": name, "exception": type(exc).__name__, "message": str(exc)},
        )

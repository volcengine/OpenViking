# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Freeze MCP tool definitions with small gateway-specific overrides."""

import copy

import orjson

from .notices import clip, tool_head
from .protocols import replays_reasoning
from .tool_protocols import tool_protocol
from .tool_protocols.common import PREFIX
from .windows import native_definitions

# Only what the gateway adds to MCP: the arguments a notice names, in order of
# preference, and the attachment upload hook with its usage note.
TOOL_OVERRIDES = {
    "find": {"notice": ("query",)},
    "search": {"notice": ("query",)},
    "read": {"notice": ("uris",)},
    "list": {"notice": ("uri",)},
    "write": {"notice": ("uri",)},
    "add_resource": {
        "notice": ("path", "attachment_index"),
        "attachment": True,
        "description": (
            " For a file attached to this conversation, set attachment_index (zero-based). "
            "For a local path, follow the returned upload instructions using the client's "
            "shell tool. The gateway cannot read local paths."
        ),
    },
    "add_skill": {
        "notice": ("path", "target_uri", "attachment_index", "data"),
        "attachment": True,
        "description": (
            " For a file attached to this conversation, set attachment_index (zero-based). "
            "Local directories must be zipped and uploaded using the client's shell tool "
            "and the returned signed instructions. The gateway cannot read local paths."
        ),
    },
}


def reply_block_reason(body):
    """Why the reply is not a single text completion the gateway may add to, or ``""``."""
    if body.get("n", 1) != 1:
        return "tools_multiple_choices"
    output_format = (
        body.get("response_format")
        or body.get("text", {}).get("format")
        or body.get("output_config", {}).get("format")
        or {}
    )
    if output_format.get("type", "text") != "text":
        return "tools_structured_output"
    return ""


def tool_block_reason(body, protocol, upstream, restores_reasoning=True):
    """Why the request gets no gateway tools, or ``""``.

    ``restores_reasoning`` is false when this request's replies go upstream without
    the reasoning the gateway would otherwise restore.
    """
    adapter = tool_protocol(protocol)
    if not adapter.enhanced_supported(body):
        return "tools_require_full_history"
    if not upstream.get("allow_gateway_tools", True):
        return "upstream_tools_disabled"
    reason = reply_block_reason(body) or adapter.block_reason(body)
    if reason:
        return reason
    # With tools, DeepSeek rejects history whose replies lack their reasoning.
    if upstream.get("vendor") == "deepseek" and not (
        restores_reasoning and replays_reasoning(upstream)
    ):
        thinking = body.get("thinking") or {}
        if thinking.get("type") != "disabled":
            return "deepseek_reasoning_history_required"
    return ""


def select_tools(catalog, policy):
    """Freeze the MCP tools the policy leaves enabled, then any native tools, as functions."""
    disabled = set(policy.get("disabled_tools", []))
    selected = []
    for tool in catalog:
        name = tool["name"]
        if name in disabled:
            continue
        override = TOOL_OVERRIDES.get(name, {})
        schema = copy.deepcopy(tool["inputSchema"])
        if override.get("attachment"):
            schema.setdefault("properties", {})["attachment_index"] = {
                "type": "integer",
                "minimum": 0,
                "description": "Zero-based index of a file attached to this conversation.",
            }
        selected.append(
            {
                "type": "function",
                "function": {
                    "name": PREFIX + name,
                    "description": tool.get("description", "") + override.get("description", ""),
                    "parameters": schema,
                },
            }
        )
    return [*selected, *native_definitions(selected, policy)]


def notice_head(item):
    """The visible line for one gateway-run call, shown before it runs."""
    short = item["function"]["name"].removeprefix(PREFIX)
    try:
        args = orjson.loads(item["function"].get("arguments") or "{}")
    except (TypeError, ValueError):
        args = {}
    args = args if isinstance(args, dict) else {}
    target = ""
    for key in TOOL_OVERRIDES.get(short, {}).get("notice", ()):
        value = args.get(key)
        if key == "attachment_index":
            target = f"attachment {value}" if type(value) is int else ""
        elif key == "uris" and isinstance(value, list):
            uris = [clip(u) for u in value if isinstance(u, str) and u.strip()]
            more = f" (+{len(uris) - 1} more)" if len(uris) > 1 else ""
            target = uris[0] + more if uris else ""
        elif isinstance(value, str) and value.strip():
            target = clip(value)
            if key == "query":
                target = f'"{target}"'
            elif key == "data":
                target = "SKILL.md text"
        if target:
            break
    return tool_head(short, target)

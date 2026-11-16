# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Shared Live Context guidance for nodes and agent-facing tool descriptions."""

LIVE_CONTEXT_INSTRUCTION = """## Instruction

Reading returns this node's definition; fetch live data by invoking this node's URI through OpenViking.
"""

LIVE_CONTEXT_READ_INSTRUCTION = (
    "\n\nInstruction for Live Context:\n"
    "Reading a node returns its stored definition, default arguments, and node-specific "
    "guidance without executing tools. To fetch live data, find the current integration's "
    "capability for invoking an OpenViking-managed MCP Tool or Live Context node by URI. "
    "Tool names may be prefixed or renamed: identify "
    "this capability by its description and input schema, then use its actual exposed "
    "name and parameter schema. Follow that invocation tool's instructions. "
    "If the capability is unavailable, use the CLI only when ov is available, after "
    "checking the referenced Tool schema and annotations and using node defaults "
    "within the schema and node-specific constraints: "
    "ov mcps invoke <node-uri> "
    "(omit --args and --timeout-seconds to use node defaults). Otherwise, report that this "
    "integration cannot fetch live data. Never invent tool names or bypass OpenViking "
    "by calling an upstream MCP tool directly. Repeated reads only return the definition; "
    "summaries support discovery, not invocation."
)

LIVE_CONTEXT_INVOKE_INSTRUCTION = (
    "\n\nInstruction:\n"
    "Pass either a Tool URI or a Live Context node URI as tool_uri. For a node, omit "
    "arguments and timeout_seconds to use its stored x-openviking.args and timeout. "
    "Explicit arguments override only matching top-level defaults; nested objects are replaced. "
    "Timeout precedence is explicit value, node value, then the server default of 30 seconds. "
    "Read the referenced x-openviking.definition Tool if its "
    "tool.inputSchema and annotations are not already available. Adjust defaults only "
    "when the task requires it, within tool.inputSchema and node-specific constraints. "
    "Any fixed preset_arguments in the Tool definition must remain unchanged. "
    "For a direct Tool URI, omitted arguments mean an empty object. "
    "The server's parameter names are tool_uri, arguments, and timeout_seconds. "
    "Inspect the Tool annotations because the upstream tool may have external side effects. "
    "OpenViking resolves credentials, validates arguments, and returns native MCP results. "
    "Use the invocation result as live data. Calls are not automatically retried; "
    "retry metadata is not an execution policy. The node definition and Tool schema "
    "are the invocation contract, not their summaries."
)

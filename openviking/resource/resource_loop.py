# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
ReAct tool-calling loop for resource tools.

Reference: session/memory/extract_loop.py
"""

import asyncio
import json
from typing import Any, Dict, List, Optional

from openviking.resource.tools import (
    execute_resource_tool,
    get_resource_tool,
    get_resource_tool_schemas,
)
from openviking.telemetry import tracer
from openviking_cli.utils import get_logger

logger = get_logger(__name__)

_FINAL_INSTRUCTION = (
    "You have reached the final iteration. Using the information gathered "
    "so far, produce the final overview now. Do not call any more tools — "
    "return only the Markdown overview following the structure specified "
    "above."
)


def _add_tool_call_pair_to_messages(
    messages: List[Dict[str, Any]],
    call_id: str,
    tool_name: str,
    params: Dict[str, Any],
    result: str,
) -> None:
    """
    Append a tool-call/result pair to the message history.

    Encoded as a single user-role JSON message to stay backend-agnostic
    (works with providers that do not support native tool_result roles).
    """
    messages.append(
        {
            "role": "user",
            "content": json.dumps(
                {"tool_call_name": tool_name, "args": params, "result": result},
                ensure_ascii=False,
            ),
        }
    )


async def run_resource_tool_loop(
    *,
    vlm,
    prompt: str,
    max_iterations: int = 3,
    max_total_tool_chars: int = 60000,
    tool_timeout: float = 30.0,
    thinking: bool = False,
) -> str:
    """
    Run the ReAct loop with resource tools and return the final overview text.

    Args:
        vlm: VLM client exposing ``get_completion_async``.
        prompt: The rendered overview_generation prompt.
        max_iterations: Initial max number of VLM round-trips. The budget
            is dynamically extended (up to 2x) when the LLM calls tools,
            so it has a chance to integrate results before being forced
            to finalize.
        max_total_tool_chars: Hard cap on cumulative tool-result characters.
        tool_timeout: Per-tool execution timeout in seconds.
        thinking: Whether to enable VLM extended thinking.

    Returns:
        The final overview markdown text from the VLM.

    Raises:
        RuntimeError: if the loop exhausts all iterations (including
            extensions) without a final content response, or if total
            tool-result chars exceed budget.
    """
    schemas = get_resource_tool_schemas()
    if not schemas:
        logger.debug("No resource tools exposed, running plain completion")
        response = await vlm.get_completion_async(prompt, thinking=thinking)
        return response.content or ""

    messages: List[Dict[str, Any]] = [{"role": "user", "content": prompt}]
    total_tool_chars = 0
    iteration = 0
    # Hard cap prevents unbounded extension. Allow at most 2x the initial
    # budget (with a floor of max_iterations + 2 for small configs).
    hard_cap = max(max_iterations * 2, max_iterations + 2)
    effective_max = max_iterations
    disable_tools_next = False

    while iteration < effective_max:
        iteration += 1
        is_last = iteration >= effective_max
        can_extend = effective_max < hard_cap

        # On the last iteration before the hard cap, nudge the LLM to
        # finalize but still allow tool calls (which trigger extension).
        # At the hard cap, withhold tools entirely to force a text answer.
        if is_last:
            messages.append({"role": "user", "content": _FINAL_INSTRUCTION})

        force_no_tools = (is_last and not can_extend) or disable_tools_next
        call_tools: Optional[List[Dict[str, Any]]] = None if force_no_tools else schemas

        logger.debug(
            "Resource tool loop iteration %d/%d (tools=%s, force=%s)",
            iteration, effective_max,
            "off" if call_tools is None else "on",
            force_no_tools,
        )

        try:
            response = await vlm.get_completion_async(
                prompt=None,
                thinking=thinking,
                tools=call_tools,
                messages=messages,
            )
        except Exception as e:
            tracer.error(f"VLM call failed at iteration {iteration}: {e}")
            raise

        # Reset for the next round.
        disable_tools_next = False

        if not response.has_tool_calls:
            return response.content or ""

        if not response.tool_calls:
            logger.warning(
                "VLM reported has_tool_calls=True with empty list at "
                "iteration %d; treating content as final",
                iteration,
            )
            return response.content or ""

        # At the hard cap with tools withheld: salvage any text content;
        # otherwise surface the failure.
        if is_last and not can_extend:
            content = response.content or ""
            if content:
                logger.warning(
                    "LLM returned tool_calls at hard cap (iteration %d); "
                    "salvaging available content",
                    iteration,
                )
                return content
            raise RuntimeError(
                f"Resource tool loop exhausted {iteration} iterations; "
                f"LLM continued requesting tools at the hard cap."
            )

        saw_unknown_tool = False
        for call in response.tool_calls or []:
            call_id = call.id if hasattr(call, "id") else str(iteration)
            tool_name = call.name
            params = call.arguments or {}

            logger.debug(
                "Executing tool '%s' (call_id=%s, args=%s)",
                tool_name, call_id, params,
            )

            if get_resource_tool(tool_name) is None:
                saw_unknown_tool = True
                result = json.dumps(
                    {"error": f"Unknown tool: {tool_name}"},
                    ensure_ascii=False,
                )
                logger.warning(
                    "Unknown tool called: %s (iteration %d)",
                    tool_name, iteration,
                )
            else:
                try:
                    result = await asyncio.wait_for(
                        execute_resource_tool(tool_name, **params),
                        timeout=tool_timeout,
                    )
                except asyncio.TimeoutError:
                    result = json.dumps(
                        {"error": f"Tool '{tool_name}' timed out after {tool_timeout}s"},
                        ensure_ascii=False,
                    )
                    logger.warning(
                        "Tool '%s' timed out after %ss",
                        tool_name, tool_timeout,
                    )

            total_tool_chars += len(result)
            if total_tool_chars > max_total_tool_chars:
                tracer.error(
                    "Total tool-result chars (%d) exceeded budget (%d)",
                    total_tool_chars, max_total_tool_chars,
                )
                raise RuntimeError(
                    f"Resource tool loop exceeded total tool char budget "
                    f"({total_tool_chars}/{max_total_tool_chars})"
                )

            _add_tool_call_pair_to_messages(
                messages, call_id, tool_name, params, result,
            )

        if saw_unknown_tool:
            disable_tools_next = True
            logger.info(
                "Unknown tool called at iteration %d; tools will be "
                "withheld next iteration",
                iteration,
            )

        # Dynamic extension: if the LLM called tools on the last iteration
        # and we haven't hit the hard cap, extend the budget by one so the
        # LLM can integrate the results before being forced to finalize.
        if is_last and can_extend:
            effective_max += 1
            tracer.info(
                "Extended max_iterations to %d for tool integration",
                effective_max,
            )

    raise RuntimeError(
        f"Resource tool loop exhausted {iteration} iterations without "
        f"a final content response"
    )

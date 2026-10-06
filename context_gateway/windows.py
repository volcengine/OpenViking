# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Experimental agent-managed context windows: native tools, window header and signals.

The model starts a fresh window itself with openviking_new_context and writes
its own hand-off notes; nothing is summarized. The hidden tool loop switches to
the new window at once, and persist stores the cut as a REPLACEMENT that later
requests replay like a compaction.
"""

import copy
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from .blocks import block, token_estimate
from .compaction import active_cut, cut_hint, opening_block
from .models import Policy
from .protocols import clean_text, is_user, text_content, unwrap_client
from .records import RecordKind as K
from .tool_protocols import tool_protocol

NEW_CONTEXT = "openviking_new_context"
ALONE = (
    "openviking_new_context must be the only tool call in its response, so no call in this "
    "response ran. Call it again on its own."
)
STAY = "A new context window cannot start at this point; continue in the current one."
SEARCHABLE = " Earlier windows stay searchable with openviking_grep and openviking_read."
REMINDERS = {
    "soft": "[context-reminder] This context window is {} full. At the next natural boundary, "
    "finish or checkpoint the current step, then call openviking_new_context with complete notes.",
    "hard": "[context-reminder] This context window is {} full. Call openviking_new_context now "
    "with complete notes, before the window runs out.",
}


@dataclass(frozen=True)
class NativeTool:
    description: str
    parameters: dict
    handler: Callable[..., Awaitable[dict]]


def native_definitions(tools, policy):
    """Native tools to freeze after the selected MCP tools when agent windows are on."""
    if not policy.get("agent_windows", False):
        return []
    names = {t["function"]["name"] for t in tools}
    searchable = policy.get("capture", True) and {"openviking_grep", "openviking_read"} <= names
    return [
        {
            "type": "function",
            "function": {
                "name": name,
                "description": tool.description
                + (SEARCHABLE if name == NEW_CONTEXT and searchable else ""),
                "parameters": copy.deepcopy(tool.parameters),
            },
        }
        for name, tool in NATIVE_TOOLS.items()
    ]


def active(request):
    """Windows apply to a user or tool-result request that offers the frozen native tools."""
    return (
        request.tools_active
        and not request.disabled
        and request.kind in {"user", "continuation"}
        and any(t["function"]["name"] == NEW_CONTEXT for t in request.root["tools"])
    )


def window_number(request, end=None):
    """The number of the window in force before ``end``: one more than the window cuts."""
    return 1 + sum(
        request.records.get((K.REPLACEMENT, anchor), {}).get("source") == "window"
        for anchor in request.capture_chain[:end]
    )


def amount(tokens):
    for unit, size in (("M", 1_000_000), ("k", 1000)):
        if tokens >= size:
            return f"{tokens / size:.1f}".removesuffix(".0") + unit
    return str(tokens)


def since(request):
    """Time since the user's previous message, or "" before the first one completed."""
    at = request.observation.value.get("user_at")
    if not at:
        return ""
    seconds = max(0, time.time() - at)
    for unit, size in (("d", 86400), ("h", 3600), ("m", 60)):
        if seconds >= size:
            return f"{int(seconds // size)}{unit}"
    return f"{int(seconds)}s"


def due(request, policy, index):
    """The reminder this request is due; each kind is sent at most once per window."""
    ratio = request.context_tokens / request.context_window
    sent = {
        request.records.get((K.INJECTION, anchor), {}).get("reminder")
        for anchor in request.chain[index + 1 :]
    }
    if ratio >= policy.window_hard_ratio and "hard" not in sent:
        return "hard"
    if ratio >= policy.window_soft_ratio and not sent & {"soft", "hard"}:
        return "soft"
    return ""


def status_line(request, policy):
    """The status line and any due reminder that end a user message's recall block."""
    if not active(request):
        return "", ""
    index, window = active_cut(request), window_number(request)
    percent = f"{request.context_tokens / request.context_window:.0%}"
    line = (
        f"[context-status] window w{window} · ~{amount(request.context_tokens)}/"
        f"{amount(request.context_window)} tokens ({percent})"
    )
    gap = since(request)
    if gap:
        line += f" · {gap} since your previous message"
    kind = due(request, policy, index)
    if kind:
        line += "\n" + REMINDERS[kind].format(percent)
        request.metrics["window_reminder"] = kind
    return line, kind


async def remind(store, request, policy):
    """Number the window, and append a due reminder to a continuation's tool result."""
    if not active(request):
        return
    index, window = active_cut(request), window_number(request)
    request.metrics["window"] = window
    anchor = (request.chain or [""])[-1]
    adapter = tool_protocol(request.protocol)
    field = adapter.field
    if (
        request.kind != "continuation"
        or not anchor
        or (K.INJECTION, anchor) in request.records
        or not adapter.accepts_context(request.body[field][-1])
    ):
        return
    kind = due(request, policy, index)
    if not kind:
        return
    percent = f"{request.context_tokens / request.context_window:.0%}"
    # Reminders cost nothing against the recall budget of the window.
    decision = await store.replay.put(
        request.scope,
        request.session,
        K.INJECTION,
        anchor,
        {
            "text": block("gateway-recall", REMINDERS[kind].format(percent)),
            "uris": [],
            "tokens": 0,
            "reason": "reminder",
            "reminder": kind,
        },
    )
    request.records[K.INJECTION, anchor] = decision
    adapter.append_context(request.body[field][-1], decision["text"])
    request.metrics["window_reminder"] = decision["reminder"]


async def new_context(request, credential, args):
    """Build the next window's header; the tool loop switches to it after this round."""
    reason, notes, steps = (args.get(key, "") for key in ("reason", "notes", "next_steps"))
    if not all(isinstance(v, str) for v in (reason, notes, steps)) or not (
        reason.strip() and notes.strip()
    ):
        return {"content": "reason and notes must be non-empty strings.", "failed": True}
    # The cut follows the last client message, so every later request can replay it.
    position = max((i for i, a in enumerate(request.capture_chain) if a), default=-1)
    # Without a client message there is nothing to cut at. A cut this request was built
    # on (a compaction just now, or a reset stored by an earlier attempt) keeps its
    # stored text, so a new window there could never be replayed.
    if position < 0 or request.capture_chain[position] not in request.body_chain:
        return {"content": STAY, "failed": True}
    window = window_number(request, position) + 1
    parts = [
        f"This is context window {window}. You started it by calling {NEW_CONTEXT}, and the "
        "gateway wrote this message from that call; the user did not write it. Files, "
        "processes and tools outside this conversation are unchanged. Continue from your "
        "notes, and if the user's most recent request is not finished, resume it now.",
        "Reason you gave: " + reason.strip(),
        "Your notes:\n" + notes.strip(),
    ]
    if steps.strip():
        parts.append("Next steps:\n" + steps.strip())
    latest = (
        clean_text(unwrap_client(text_content(request.messages[request.anchor])))
        if request.anchor >= 0
        else ""
    )
    if latest:
        parts.append("The user's most recent message, verbatim:\n" + latest)
    parts.append(
        cut_hint(request, credential["user_id"], Policy.model_validate(request.root["policy"]))
    )
    text = "\n\n".join(
        part
        for part in (
            block("gateway-window", "\n\n".join(p for p in parts if p)),
            opening_block(request.records, request.chain),
        )
        if part
    )
    value = {"source": "window", "text": text, "tokens": token_estimate(text)}
    return {
        "content": f"Context window {window} started.",
        "failed": False,
        "cut": (request.capture_chain[position], value),
    }


async def context_remaining(request, credential, args):
    policy = Policy.model_validate(request.root["policy"])
    index, window = active_cut(request), window_number(request)
    used, size = request.context_tokens, request.context_window
    ratio = used / size
    lines = [
        f"Context window {window}: ~{amount(used)} of {amount(size)} tokens used "
        f"({ratio:.0%}), ~{amount(max(size - used, 0))} left.",
        f"User messages in this window: {sum(is_user(m) for m in request.messages[index + 1 :])}.",
    ]
    gap = since(request)
    if gap:
        lines.append(f"Time since the user's previous message: {gap}.")
    if ratio >= policy.window_hard_ratio:
        advice = "call openviking_new_context now with complete notes."
    elif ratio >= policy.window_soft_ratio:
        advice = (
            "plan a new window: at the next natural boundary, finish or checkpoint the current "
            "step, then call openviking_new_context."
        )
    else:
        advice = "no action needed; keep working."
    lines.append("Advice: " + advice)
    return {"content": "\n".join(lines), "failed": False}


TEXT = {"type": "string"}
NATIVE_TOOLS = {
    NEW_CONTEXT: NativeTool(
        "Start a fresh context window for this conversation. Call it when a phase of work is "
        "finished, when the user turns to unrelated work, or when the [context-status] line or "
        "openviking_context_remaining shows the window filling up. Do not call it in the middle "
        "of an unverified change, right after a new window started, or to get away from an "
        "unsolved problem. Everything in the current window leaves your context: the new window "
        "starts from one message holding your reason, notes and next steps and the user's most "
        "recent message. Write the notes for a reader who knows nothing else: the goal, "
        "decisions and why, the current state, files and identifiers, open problems and next "
        "steps. Files, processes and tools are unchanged. It must be the only tool call in its "
        "response.",
        {
            "type": "object",
            "properties": {
                "reason": {**TEXT, "description": "Why a new window starts now, briefly."},
                "notes": {
                    **TEXT,
                    "description": "Everything the next window needs: goal, decisions, current "
                    "state, files and identifiers, open problems.",
                },
                "next_steps": {**TEXT, "description": "What to do first in the new window."},
            },
            "required": ["reason", "notes"],
        },
        new_context,
    ),
    "openviking_context_remaining": NativeTool(
        "Report how full the current context window is: the window number, estimated tokens "
        "used and left, user messages in this window, time since the user's previous message, "
        "and whether to start a new window. Takes no arguments.",
        {"type": "object", "properties": {}},
        context_remaining,
    ),
}

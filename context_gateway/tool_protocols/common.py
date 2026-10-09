# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Wire adapters preserve native history; only executable calls are normalized.

Adapters hold everything the gateway knows about one wire protocol. They assemble
upstream replies, for relayed responses as well as the hidden tool loop, and
project the loop's visible events. They do not execute tools, own budgets, or
access storage. The shared loop owns those steps.
"""

import copy
import functools
import itertools
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass

import orjson

from ..notices import TOOL_NOTICE, without_recall_notice
from ..protocols import prefix_chain, usage_of

PREFIX = "openviking_"
# None is the Chat Completions [DONE] marker; all other events are native JSON.
StreamEvent = dict | None
# Reasoning counts against these output caps, and some models reason without being
# asked, so a summary request first asks for this room beyond its own text. Models
# with a smaller output limit reject that cap, and the kernel retries without it.
SUMMARY_HEADROOM = 16000


class ToolLoopError(Exception):
    def __init__(self, reason, status=502, content=None, headers=None):
        super().__init__(reason)
        self.status, self.content, self.headers = status, content, headers or {}


class SummaryError(Exception):
    """A summary request that produced no usable summary; ``reason`` becomes a metric."""

    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


def merge_delta(target, delta):
    for key, value in delta.items():
        if value is None:
            target.setdefault(key, None)
        elif isinstance(value, dict):
            if not isinstance(target.get(key), dict):
                target[key] = {}
            merge_delta(target[key], value)
        elif isinstance(value, str) and key not in {"role", "type", "id"}:
            target[key] = (target.get(key) or "") + value
        elif isinstance(value, list):
            target.setdefault(key, []).extend(copy.deepcopy(value))
        else:
            target[key] = copy.deepcopy(value)


def add_usage(total, usage):
    for key, value in usage.items():
        if isinstance(value, dict):
            add_usage(total.setdefault(key, {}), value)
        elif isinstance(value, (int, float)):
            total[key] = total.get(key, 0) + value
        else:
            total[key] = value


def without(value, path):
    """A copy of ``value`` without the dotted ``path``; everything else is shared."""
    key, _, rest = path.partition(".")
    if key not in value or (rest and not isinstance(value[key], dict)):
        return value
    if not rest:
        return {k: v for k, v in value.items() if k != key}
    return {**value, key: without(value[key], rest)}


def append_text(message, field, text, kind, joins_strings=False):
    """Append text to ``message[field]``; string content becomes text blocks unless joined."""
    content = message.get(field, "")
    if joins_strings and isinstance(content, str):
        message[field] = content + "\n\n" + text
        return
    if isinstance(content, str):
        content = [{"type": kind, "text": content}]
    message[field] = [*content, {"type": kind, "text": text}]


def sse(value):
    return b"data: " + orjson.dumps(value) + b"\n\n"


def call(identifier, name, arguments):
    return {
        "id": identifier,
        "type": "function",
        "function": {"name": name, "arguments": arguments},
    }


def edit_text(content, edit, first=False):
    """``content`` with ``edit`` applied to its text, or ``content`` itself if unchanged.

    A string is edited whole. In a list ``edit`` gets the text of every part that has some,
    or with ``first`` only of the first such part, and a part the edit empties is dropped.
    """
    if isinstance(content, str):
        edited = edit(content)
        return content if edited == content else edited
    if not isinstance(content, list):
        return content
    parts, changed, offered = [], False, False
    for part in content:
        text = part.get("text") if isinstance(part, dict) else None
        if isinstance(text, str) and not (first and offered):
            offered, edited = True, edit(text)
            if edited != text:
                changed = True
                if not edited.strip():
                    continue
                part = {**part, "text": edited}
        parts.append(part)
    return parts if changed else content


def edit_replies(messages, edit, rebuild, first=False):
    """``messages`` with ``edit`` applied to assistant text, or ``messages`` itself if unchanged.

    ``edit_text`` says which text ``edit`` gets. ``rebuild(message, content, previous)``
    returns an edited message with its new content, None to drop it, or ``message`` to keep
    the edit out; ``previous`` is the item before it, ``{}`` for the first.
    """
    result, changed = [], False
    for previous, message in itertools.pairwise([{}, *messages]):
        if isinstance(message, dict) and message.get("role") == "assistant":
            content = edit_text(message.get("content"), edit, first)
            if content is not message.get("content"):
                rebuilt = rebuild(message, content, previous)
                changed = changed or rebuilt is not message
                if rebuilt is None:
                    continue
                message = rebuilt
        result.append(message)
    return result if changed else messages


def strip_notices(messages):
    """Remove tool notices from echoed assistant text, dropping emptied text parts and messages."""

    def rebuild(message, content, previous):
        return {**message, "content": content} if content != [] else None

    return edit_replies(messages, lambda text: TOOL_NOTICE.sub("", text), rebuild)


@dataclass(frozen=True)
class ToolRound:
    """The validated result of one upstream call, independent of wire format."""

    output: list[dict]
    calls: list[dict]
    usage: dict
    # The upstream stopped to have tools run, so the round's calls are complete.
    tool_stop: bool


class ToolProtocol(ABC):
    """Native request/history rules and a per-request response assembler.

    ``accumulate`` and ``load`` assemble one upstream reply, and ``reply`` reads
    it; relayed responses and the hidden tool loop share them. ``visible`` is the
    cumulative client-visible history. ``end`` returns the completed round; the
    loop never inspects the assembler's partial state. Event producers return
    native JSON (or the Chat DONE marker); only encode turns events into bytes.
    Adapters never execute tools or access storage.
    """

    field = "messages"
    id_prefix = ""
    canonicalizes_history = False
    # Dotted body paths a summary request drops: output formats, stop sequences, stream options.
    summary_drops: tuple[str, ...] = ()
    # Finish reasons of a reply that ended the way the model meant it to, not cut off.
    reply_ends: frozenset[str] = frozenset()
    # Thinking signatures cover the history before them, so history the gateway
    # cannot resend exactly must go upstream without its thinking.
    signed_thinking = False

    def __init__(self, body: dict):
        self.body = body
        self.identifier = self.id_prefix + "ovcg-" + uuid.uuid4().hex
        self.visible: list[dict] = []
        self.started = False
        # Text the reply starts with, shown before any upstream content.
        self.lead = ""

    @classmethod
    def messages(cls, body: dict) -> list:
        """The body's history; may be malformed, which the caller checks."""
        return body.get(cls.field, [])

    @staticmethod
    def enhanced_supported(body: dict) -> bool:
        """Whether the body carries the whole history, which recall and replay need."""
        return True

    @staticmethod
    def auth_header(key: str) -> dict:
        return {"authorization": "Bearer " + key}

    @staticmethod
    def is_reply(message: dict) -> bool:
        """Whether a history item belongs to a model reply, so a reply can end at it."""
        return message.get("role") == "assistant"

    @staticmethod
    def accepts_context(message: dict) -> bool:
        """Whether append_context can extend the message: a user message or a tool result."""
        return message.get("role") == "user"

    @staticmethod
    def append_context(message: dict, text: str) -> None:
        """Append text to a user message or tool result as a trailing text block."""
        append_text(message, "content", text, "text")

    @staticmethod
    def thinking_as_text(message: dict) -> list[dict]:
        """Text blocks of a reply that may be its unsigned thinking, resent by the client as text."""
        return []

    @staticmethod
    def unsigned_thinking(output: list[dict]) -> list[str]:
        """Thinking the reply carries without a signature, which clients may resend as text."""
        return []

    @staticmethod
    def tool_choice(body: dict):
        return body.get("tool_choice", "auto")

    @classmethod
    def block_reason(cls, body: dict) -> str:
        return "" if cls.tool_choice(body) in ("auto", "none") else "tools_forced_choice"

    @staticmethod
    @abstractmethod
    def wire_tools(tools: list[dict]) -> list[dict]:
        """Encode the frozen function catalogue for this protocol."""

    @classmethod
    def add_tools(cls, body: dict, tools: list[dict]) -> None:
        body["tools"] = [*body.get("tools", []), *cls.wire_tools(tools)]

    @staticmethod
    def disable_tools(body: dict) -> None:
        body["tool_choice"] = "none"

    @staticmethod
    def history_chain(messages: list[dict]) -> list[str]:
        return prefix_chain(messages)

    @staticmethod
    def join_replayed_history(messages: list[dict]) -> list[dict]:
        return messages

    @staticmethod
    def split_reasoning(output: list[dict]) -> tuple[list[dict], dict[int, dict]]:
        """A reply as a client that drops reasoning resends it, and the reasoning to restore.

        The reasoning is keyed by the index, in that form, of the item that carries
        it or that it precedes.
        """
        return output, {}

    @staticmethod
    def restore_reasoning(previous: list[dict], message: dict, value: dict) -> list[dict]:
        """``message`` with recorded reasoning back, after the items ``previous`` holds.

        A message that still carries reasoning comes back unchanged.
        """
        return [message]

    @classmethod
    def strip_lead(cls, messages: list[dict]) -> list[dict]:
        """The history without the recall notice the gateway put at the start of replies.

        Only an assistant message's first text can hold this lead; a user quoting it keeps
        it. Returns ``messages`` itself when nothing changed.
        """

        def rebuild(message, content, previous):
            # A reply of tool calls alone had no content before the lead.
            if content == "" and message.get("tool_calls"):
                content = None
            return {**message, "content": content}

        return edit_replies(messages, without_recall_notice, rebuild, first=True)

    # Whether omit_hidden_history removes reasoning, so none is restored before it.
    omits_reasoning = False

    @staticmethod
    def omit_hidden_history(messages: list[dict]) -> list[dict]:
        """Drop what cannot be used without the omitted tool history."""
        return strip_notices(messages)

    @classmethod
    def summary_request(
        cls,
        body: dict,
        messages: list[dict],
        instruction: str,
        max_tokens: int,
        headroom: int = SUMMARY_HEADROOM,
    ) -> dict:
        """Ask for a summary of ``messages`` without streaming.

        System, tools and reasoning settings stay as the client sent them, so
        the upstream can reuse its cached prefix. Output settings a plain-text
        summary cannot follow are dropped, and a forced tool choice becomes none.
        """
        request = {
            **functools.reduce(without, cls.summary_drops, body),
            cls.field: [*messages, {"role": "user", "content": instruction}],
            "stream": False,
        }
        if cls.tool_choice(body) not in ("auto", "none"):
            cls.disable_tools(request)
        return request

    @staticmethod
    @abstractmethod
    def summary_text(response: dict) -> str:
        """Return the reply's text; raise SummaryError for tool calls or a cut-off reply."""

    def begin(self) -> None:
        self.output, self.calls, self.usage, self.envelope = [], [], {}, {}
        self.finish = None

    @abstractmethod
    def accumulate(self, value: dict) -> None:
        """Add an upstream event to the reply; events it does not know are ignored."""

    @abstractmethod
    def event(self, value: dict) -> list[dict]:
        """Accumulate an upstream event and return any client-visible events."""

    @abstractmethod
    def load(self, value: dict) -> None:
        """Consume a nonstreaming response."""

    @abstractmethod
    def assembled(self) -> tuple[dict | None, list[dict] | None]:
        """The reply so far as one message, and as output items where the protocol has them."""

    @staticmethod
    @abstractmethod
    def has_calls(output: list[dict]) -> bool:
        """Whether the reply calls tools."""

    def reply(self) -> dict:
        """The reply so far, as ``ResponseCapture`` holds it.

        Every path reads completion here. A reply that ended in tool calls hands
        the turn to the client's tools: it is finished, but the user's turn goes
        on in the next request. Any other reply that ended completes the turn.
        """
        message, items = self.assembled()
        output = items or ([message] if message else [])
        ended = self.finish in self.reply_ends
        handoff = ended and self.has_calls(output)
        return {
            "message": message,
            "output_items": items,
            "usage": usage_of({"usage": self.usage}) if self.usage else None,
            "response_id": self.envelope.get("id") or "",
            "complete": ended and not handoff,
            "handoff": handoff,
        }

    @abstractmethod
    def end(self) -> ToolRound:
        """Validate the assembled round before exposing executable calls."""

    @abstractmethod
    def publish_calls(self, client: list[dict]) -> list[dict]:
        """Add client-owned calls to visible history and return their events."""

    def open_notice(self) -> list[dict]:
        """Start visible text for the calls the gateway runs and return its events."""
        return []

    @abstractmethod
    def notice(self, text: str) -> list[dict]:
        """Append text to the open notice in visible history and return its events."""

    def close_notice(self) -> list[dict]:
        return []

    def lead_with(self, text: str) -> None:
        """Start the visible reply with ``text``; the adapter shows it before upstream content."""
        self.lead = text

    def lead_notice(self) -> list[dict]:
        """Put the pending lead text first in visible history and return its events, once."""
        text, self.lead = self.lead, ""
        if not text:
            return []
        return [*self.open_notice(), *self.notice(text), *self.close_notice()]

    @abstractmethod
    def results(self, receipts: list[dict]) -> list[dict]:
        """Encode executor receipts as native tool-result messages."""

    @abstractmethod
    def final(self, usage: dict) -> dict:
        """Build the combined client response."""

    @abstractmethod
    def terminal(self, final: dict) -> list[StreamEvent]:
        """Return the protocol's success terminal events."""

    def error(self, message: str) -> list[StreamEvent]:
        return [{"type": "error", "error": {"type": "gateway_tool_error", "message": message}}]

    def encode(self, value: StreamEvent) -> bytes:
        if value is None:
            return b"data: [DONE]\n\n"
        return b"event: " + value["type"].encode() + b"\n" + sse(value)

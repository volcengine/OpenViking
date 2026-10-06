# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Stateless Responses output items and a single numbered event stream."""

import copy
import uuid

from ..protocols import text_content
from .common import (
    PREFIX,
    SUMMARY_HEADROOM,
    SummaryError,
    ToolLoopError,
    ToolProtocol,
    ToolRound,
    append_text,
    call,
)

CLIENT_CALLS = {"function_call", "custom_tool_call"}
TOOL_OUTPUTS = {"function_call_output", "custom_tool_call_output"}


class ResponsesProtocol(ToolProtocol):
    field = "input"
    id_prefix = "resp_"
    summary_drops = ("text.format",)
    reply_ends = frozenset({"completed"})

    @classmethod
    def messages(cls, body):
        content = body.get("input", [])
        return [{"role": "user", "content": content}] if isinstance(content, str) else content

    @staticmethod
    def enhanced_supported(body):
        # Stored or server-side history is not in the body.
        return body.get("store") is False and not any(
            body.get(k) for k in ("previous_response_id", "conversation", "background")
        )

    @staticmethod
    def is_reply(message):
        return message.get("role") == "assistant" or message.get("type") in {
            "reasoning",
            *CLIENT_CALLS,
        }

    @staticmethod
    def accepts_context(message):
        return message.get("role") == "user" or message.get("type") in TOOL_OUTPUTS

    @staticmethod
    def append_context(message, text):
        if message.get("type") in TOOL_OUTPUTS:
            append_text(message, "output", text, "input_text", joins_strings=True)
        else:
            append_text(message, "content", text, "input_text")

    @staticmethod
    def wire_tools(tools):
        return [{"type": "function", **t["function"], "strict": False} for t in tools]

    @classmethod
    def block_reason(cls, body):
        if any(m.get("type") == "item_reference" for m in cls.messages(body)):
            return "tools_require_full_history"
        return ToolProtocol.block_reason(body)

    @classmethod
    def add_tools(cls, body, tools):
        super().add_tools(body, tools)
        body["include"] = list(
            dict.fromkeys([*body.get("include", []), "reasoning.encrypted_content"])
        )

    @staticmethod
    def split_reasoning(output):
        # Only plain-text reasoning can be resent; summaries and encrypted content cannot.
        visible, reasoning, pending = [], {}, []
        for item in output:
            if item.get("type") == "reasoning":
                content = item.get("content")
                if isinstance(content, list) and any(
                    isinstance(part, dict) and isinstance(part.get("text"), str) and part["text"]
                    for part in content
                ):
                    pending.append(item)
                continue
            if pending:
                reasoning[len(visible)] = {"items": pending}
                pending = []
            visible.append(item)
        return visible, reasoning

    @staticmethod
    def restore_reasoning(previous, message, value):
        if previous and previous[-1].get("type") == "reasoning":
            return [message]
        return [*value["items"], message]

    @classmethod
    def summary_request(cls, body, messages, instruction, max_tokens, headroom=SUMMARY_HEADROOM):
        request = super().summary_request(body, messages, instruction, max_tokens)
        # Some models reason without a reasoning field, so the headroom does not depend on it.
        request["max_output_tokens"] = max_tokens + headroom
        return request

    @staticmethod
    def summary_text(response):
        output = response.get("output") or []
        if any(item.get("type") in CLIENT_CALLS for item in output):
            raise SummaryError("summary_tool_call")
        if response.get("status") != "completed":
            raise SummaryError("summary_incomplete")
        return "\n".join(text_content(item) for item in output if item.get("type") == "message")

    def __init__(self, body):
        super().__init__(body)
        self.sequence = 0

    def begin(self):
        super().begin()
        self.indices, self.buffered, self.items = {}, {}, {}
        self.announce, self.closed = not self.started, set()
        self.completed = False

    def encode(self, value):
        value = {**value, "sequence_number": self.sequence}
        if "response_id" in value:
            value["response_id"] = self.identifier
        if "response" in value:
            value["response"] = self.public_response(value["response"])
        self.sequence += 1
        return super().encode(value)

    def public_response(self, value):
        value = {**value, "id": self.identifier}
        if "tools" in value:
            value["tools"] = [t for t in value["tools"] if not t.get("name", "").startswith(PREFIX)]
        return value

    def accumulate(self, value):
        kind = value.get("type")
        if kind in {"response.completed", "response.incomplete"}:
            self.envelope = copy.deepcopy(value["response"])
            self.finish = self.envelope.get("status")
            self.usage = self.envelope.get("usage") or {}
            self.output = self.envelope.get("output") or []
            self.completed = True
        elif kind in {"response.output_item.added", "response.output_item.done"}:
            index = value["output_index"]
            self.items[index] = copy.deepcopy(value["item"])
            if kind == "response.output_item.done":
                self.closed.add(index)

    def event(self, value):
        self.accumulate(value)
        kind = value["type"]
        if kind in {"response.created", "response.in_progress"}:
            if self.announce:
                self.envelope = copy.deepcopy(value["response"])
                self.started = True
                return [{**value, "response": {**value["response"], "output": []}}]
            return []
        if kind in {"response.completed", "response.incomplete"}:
            return []
        if kind == "response.failed":
            raise ToolLoopError("Model response failed")
        if "output_index" in value:
            index = value["output_index"]
            if kind == "response.output_item.added":
                item = self.items[index]
                if item["type"] in CLIENT_CALLS:
                    self.buffered[index] = []
                else:
                    self.indices[index] = len(self.visible)
                    self.visible.append(copy.deepcopy(item))
            if kind == "response.output_item.done" and index in self.indices:
                self.visible[self.indices[index]] = self.items[index]
            if index in self.buffered:
                self.buffered[index].append(value)
                return []
            return [{**value, "output_index": self.indices[index]}]
        # Events without an output item cannot expose a gateway function call.
        return [{**value, **({"response_id": self.identifier} if "response_id" in value else {})}]

    def load(self, value):
        self.envelope = copy.deepcopy(value)
        self.finish, self.usage = value.get("status"), value.get("usage") or {}
        self.output = copy.deepcopy(value.get("output") or [])
        self.visible.extend(item for item in self.output if item["type"] not in CLIENT_CALLS)
        self.completed = True

    def assembled(self):
        # Only the terminal response carries the whole output.
        if not self.completed:
            return None, None
        text = "\n".join(text_content(item) for item in self.output)
        return {"role": "assistant", "content": text}, self.output

    @staticmethod
    def has_calls(output):
        return any(item.get("type") in CLIENT_CALLS for item in output)

    def end(self):
        if not self.completed or self.finish not in {"completed", "incomplete"}:
            raise ToolLoopError("Incomplete Responses event stream")
        if self.body.get("stream"):
            # The terminal object is authoritative, including opaque reasoning
            # data that need not appear in deltas. Never reconstruct signatures.
            if set(self.items) != set(range(len(self.output))) or self.closed != set(self.items):
                raise ToolLoopError("Responses output items are missing")
            for index, item in enumerate(self.output):
                if index in self.indices:
                    self.visible[self.indices[index]] = item
        self.calls = [
            call(
                item["call_id"],
                (item.get("namespace", "") + "." if item.get("namespace") else "") + item["name"],
                item.get("arguments", item.get("input", "")),
            )
            for item in self.output
            if item["type"] in CLIENT_CALLS
        ]

        return ToolRound(self.output, self.calls, self.usage, self.finish == "completed")

    def publish_calls(self, client):
        ids, events = {c["id"] for c in client}, []
        for original_index, item in enumerate(self.output):
            if item["type"] not in CLIENT_CALLS or item["call_id"] not in ids:
                continue
            index = len(self.visible)
            self.visible.append(item)
            events.extend(
                {**event, "output_index": index} for event in self.buffered.get(original_index, [])
            )
        return events

    def open_notice(self):
        # Notices are a gateway-written assistant message, streamed like a model's.
        item = {
            "id": "msg_" + uuid.uuid4().hex,
            "type": "message",
            "status": "in_progress",
            "content": [],
            "role": "assistant",
        }
        part = {"type": "output_text", "annotations": [], "text": ""}
        index = len(self.visible)
        events = [
            {"type": "response.output_item.added", "output_index": index, "item": dict(item)},
            {
                "type": "response.content_part.added",
                "output_index": index,
                "item_id": item["id"],
                "content_index": 0,
                "part": dict(part),
            },
        ]
        self.visible.append({**item, "content": [part]})
        return events

    def notice(self, text):
        item = self.visible[-1]
        item["content"][0]["text"] += text
        return [
            {
                "type": "response.output_text.delta",
                "output_index": len(self.visible) - 1,
                "item_id": item["id"],
                "content_index": 0,
                "delta": text,
            }
        ]

    def close_notice(self):
        item = self.visible[-1]
        item["status"] = "completed"
        part = item["content"][0]
        place = {"output_index": len(self.visible) - 1, "item_id": item["id"], "content_index": 0}
        return [
            {"type": "response.output_text.done", **place, "text": part["text"]},
            {"type": "response.content_part.done", **place, "part": dict(part)},
            {
                "type": "response.output_item.done",
                "output_index": place["output_index"],
                "item": copy.deepcopy(item),
            },
        ]

    def results(self, results):
        return [
            {"type": "function_call_output", "call_id": r["tool_call_id"], "output": r["content"]}
            for r in results
        ]

    def final(self, usage):
        return self.public_response({**self.envelope, "output": self.visible, "usage": usage})

    def terminal(self, final):
        return [{"type": "response." + self.finish, "response": final}]

    def error(self, message):
        return [
            {
                "type": "response.failed",
                "response": {
                    **self.envelope,
                    "object": "response",
                    "model": self.body.get("model"),
                    "status": "failed",
                    "error": {"code": "server_error", "message": message},
                    "incomplete_details": None,
                    "output": self.visible,
                },
            }
        ]

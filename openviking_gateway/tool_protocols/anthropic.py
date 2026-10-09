# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Messages content blocks, preserving thinking/signatures in native history."""

import copy

import orjson

from ..protocols import strip_thinking, text_content
from .common import (
    SUMMARY_HEADROOM,
    SummaryError,
    ToolLoopError,
    ToolProtocol,
    ToolRound,
    call,
    strip_notices,
)

THINKING = {"thinking", "redacted_thinking"}


class AnthropicProtocol(ToolProtocol):
    id_prefix = "msg_"
    summary_drops = ("stop_sequences", "output_config.format")
    reply_ends = frozenset({"end_turn", "stop_sequence", "tool_use"})
    signed_thinking = True

    @staticmethod
    def auth_header(key):
        return {"x-api-key": key}

    @staticmethod
    def thinking_as_text(message):
        content = message.get("content")
        if message.get("role") != "assistant" or not isinstance(content, list):
            return []
        return [
            b for b in content if isinstance(b, dict) and b.get("type") == "text" and b.get("text")
        ]

    @staticmethod
    def unsigned_thinking(output):
        # DeepSeek through Ark returns thinking without a signature.
        return [
            block["thinking"]
            for message in output
            for block in (
                message.get("content") if isinstance(message.get("content"), list) else []
            )
            if isinstance(block, dict)
            and block.get("type") == "thinking"
            and not block.get("signature")
            and isinstance(block.get("thinking"), str)
            and block["thinking"].strip()
        ]

    @staticmethod
    def wire_tools(tools):
        return [
            {
                "name": t["function"]["name"],
                "description": t["function"]["description"],
                "input_schema": t["function"]["parameters"],
            }
            for t in tools
        ]

    @staticmethod
    def tool_choice(body):
        choice = body.get("tool_choice", {"type": "auto"})
        if isinstance(choice, dict):
            choice = choice.get("type")
        return choice

    @staticmethod
    def disable_tools(body):
        body["tool_choice"] = {"type": "none"}

    @staticmethod
    def join_replayed_history(messages):
        return merge_tool_results(messages)

    @staticmethod
    def repair_history(messages):
        return deduplicate_server_tool_history(messages)

    omits_reasoning = True

    @staticmethod
    def omit_hidden_history(messages):
        return strip_thinking(strip_notices(messages))

    @staticmethod
    def split_reasoning(output):
        reasoning = {}
        for index, message in enumerate(output):
            content = message.get("content")
            blocks = [
                [position, block]
                for position, block in enumerate(content if isinstance(content, list) else [])
                if isinstance(block, dict) and block.get("type") in THINKING
            ]
            if blocks:
                reasoning[index] = {"blocks": blocks}
        return output, reasoning

    @staticmethod
    def restore_reasoning(previous, message, value):
        content = message.get("content")
        if isinstance(content, str):
            content = [{"type": "text", "text": content}] if content else []
        if (
            message.get("role") != "assistant"
            or not isinstance(content, list)
            or any(isinstance(b, dict) and b.get("type") in THINKING for b in content)
        ):
            return [message]
        # Blocks go back where the reply had them, so the client's form must equal it without them.
        content = list(content)
        for position, block in value["blocks"]:
            content.insert(position, block)
        return [{**message, "content": content}]

    @classmethod
    def summary_request(cls, body, messages, instruction, max_tokens, headroom=SUMMARY_HEADROOM):
        request = super().summary_request(body, messages, instruction, max_tokens)
        thinking = body.get("thinking") or {}
        # Thinking spends from max_tokens, and a manual budget must stay below it.
        # Without a manual budget, leave headroom anyway: some models think by default.
        if thinking.get("type") == "enabled":
            max_tokens += thinking.get("budget_tokens", 0)
        else:
            max_tokens += headroom
        request["max_tokens"] = max_tokens
        return request

    @staticmethod
    def summary_text(response):
        content = response.get("content") or []
        if any(block.get("type") == "tool_use" for block in content):
            raise SummaryError("summary_tool_call")
        if response.get("stop_reason") != "end_turn":
            raise SummaryError("summary_incomplete")
        return text_content({"content": content})

    def __init__(self, body):
        super().__init__(body)
        self.visible = [{"role": "assistant", "content": []}]

    def begin(self):
        super().begin()
        self.blocks, self.arguments, self.indices = {}, {}, {}
        self.opened, self.stopped, self.closed = False, False, set()

    def accumulate(self, value):
        kind = value.get("type")
        if kind == "message_start":
            self.envelope = copy.deepcopy(value["message"])
            self.usage.update(self.envelope.get("usage") or {})
            self.opened = True
        elif kind == "content_block_start":
            self.blocks[value["index"]] = copy.deepcopy(value["content_block"])
        elif kind == "content_block_delta":
            index, delta = value["index"], value["delta"]
            block = self.blocks[index]
            if "partial_json" in delta:
                self.arguments[index] = self.arguments.get(index, "") + delta["partial_json"]
            else:
                for key in ("text", "thinking", "signature"):
                    if key in delta:
                        block[key] = block.get(key, "") + delta[key]
                if delta.get("type") == "citations_delta":
                    block.setdefault("citations", []).append(copy.deepcopy(delta["citation"]))
        elif kind == "content_block_stop":
            index = value["index"]
            self.closed.add(index)
            arguments = self.arguments.get(index, "")
            if arguments:
                self.blocks[index]["input"] = orjson.loads(arguments)
        elif kind == "message_delta":
            self.finish = value.get("delta", {}).get("stop_reason") or self.finish
            self.envelope.update(value.get("delta", {}))
            self.usage.update(value.get("usage") or {})
        elif kind == "message_stop":
            self.stopped = True

    def event(self, value):
        self.accumulate(value)
        kind = value["type"]
        if kind == "message_start":
            if not self.started:
                self.started = True
                return [
                    {**value, "message": {**self.envelope, "id": self.identifier, "content": []}},
                    # The lead is block 0; upstream blocks follow it through self.indices.
                    *self.lead_notice(),
                ]
        elif kind == "content_block_start":
            index = value["index"]
            block = self.blocks[index]
            # Buffer tool blocks until the full call is validated. All other
            # blocks stream immediately, including opaque/redacted thinking.
            if block["type"] != "tool_use":
                self.indices[index] = len(self.visible[0]["content"])
                self.visible[0]["content"].append(block)
                return [{**value, "index": self.indices[index]}]
        elif kind in {"content_block_delta", "content_block_stop"}:
            if value["index"] in self.indices:
                return [{**value, "index": self.indices[value["index"]]}]
        elif kind not in {"message_delta", "message_stop"}:
            return [value]
        return []

    def load(self, value):
        self.envelope = copy.deepcopy(value)
        self.finish, self.usage = value.get("stop_reason"), value.get("usage") or {}
        self.blocks = dict(enumerate(copy.deepcopy(value.get("content") or [])))
        self.lead_notice()
        self.visible[0]["content"].extend(
            b for b in self.blocks.values() if b["type"] != "tool_use"
        )
        self.opened, self.stopped, self.closed = True, True, set(self.blocks)

    def assembled(self):
        if not self.opened:
            return None, None
        return {"role": "assistant", "content": [self.blocks[i] for i in sorted(self.blocks)]}, None

    @staticmethod
    def has_calls(output):
        return any(
            isinstance(b, dict) and b.get("type") == "tool_use"
            for message in output
            for b in (message.get("content") if isinstance(message.get("content"), list) else [])
        )

    def end(self):
        if not self.stopped or not self.finish or self.closed != set(self.blocks):
            raise ToolLoopError("Incomplete Anthropic message")
        blocks = [self.blocks[i] for i in sorted(self.blocks)]
        self.output = [{"role": "assistant", "content": blocks}]
        self.calls = [
            call(b["id"], b["name"], orjson.dumps(b["input"]).decode())
            for b in blocks
            if b["type"] == "tool_use"
        ]

        return ToolRound(self.output, self.calls, self.usage, self.finish == "tool_use")

    def publish_calls(self, client):
        ids = {c["id"] for c in client}
        events = []
        for block in self.blocks.values():
            if block["type"] != "tool_use" or block["id"] not in ids:
                continue
            index = len(self.visible[0]["content"])
            self.visible[0]["content"].append(block)
            events.extend(
                [
                    {
                        "type": "content_block_start",
                        "index": index,
                        "content_block": {**block, "input": {}},
                    },
                    {
                        "type": "content_block_delta",
                        "index": index,
                        "delta": {
                            "type": "input_json_delta",
                            "partial_json": orjson.dumps(block["input"]).decode(),
                        },
                    },
                    {"type": "content_block_stop", "index": index},
                ]
            )
        return events

    def open_notice(self):
        content = self.visible[0]["content"]
        content.append({"type": "text", "text": ""})
        return [
            {
                "type": "content_block_start",
                "index": len(content) - 1,
                "content_block": {"type": "text", "text": ""},
            }
        ]

    def notice(self, text):
        content = self.visible[0]["content"]
        content[-1]["text"] += text
        return [
            {
                "type": "content_block_delta",
                "index": len(content) - 1,
                "delta": {"type": "text_delta", "text": text},
            }
        ]

    def close_notice(self):
        return [{"type": "content_block_stop", "index": len(self.visible[0]["content"]) - 1}]

    def results(self, results):
        return [
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": r["tool_call_id"],
                        "content": r["content"],
                        **({"is_error": True} if r.get("failed") else {}),
                    }
                    for r in results
                ],
            }
        ]

    def final(self, usage):
        return {
            **self.envelope,
            "id": self.identifier,
            "type": "message",
            "role": "assistant",
            "content": self.visible[0]["content"],
            "stop_reason": self.finish,
            "usage": usage,
        }

    def terminal(self, final):
        return [
            {
                "type": "message_delta",
                "delta": {"stop_reason": self.finish, "stop_sequence": final.get("stop_sequence")},
                "usage": final["usage"],
            },
            {"type": "message_stop"},
        ]


def merge_tool_results(messages):
    """Mixed calls must receive all results in the immediately following user message."""
    result = []
    for message in messages:
        prior = result[-1] if result else {}
        if (
            prior.get("role") == message.get("role") == "user"
            and isinstance(prior.get("content"), list)
            and isinstance(message.get("content"), list)
            and all(b.get("type") == "tool_result" for b in prior["content"])
            and any(b.get("type") == "tool_result" for b in message["content"])
        ):
            result[-1] = {**prior, **message, "content": [*prior["content"], *message["content"]]}
        else:
            result.append(message)
    return result


def deduplicate_server_tool_history(messages):
    """Remove exact duplicate server-call groups within an assistant turn.

    Claude Code compaction can repeat provider-owned blocks inside one assistant
    message. Anthropic rejects repeated ``server_tool_use`` IDs. Only identical
    call/result groups are safe to remove; conflicting copies remain untouched so the
    gateway never guesses which provider transcript is authoritative.
    """
    repaired, removed = [], 0
    for message in messages:
        content = message.get("content")
        if message.get("role") != "assistant" or not isinstance(content, list):
            repaired.append(message)
            continue

        seen_groups = {}
        result_repaired = []
        index = 0
        while index < len(content):
            block = content[index]
            if not isinstance(block, dict) or block.get("type") != "server_tool_use":
                result_repaired.append(block)
                index += 1
                continue

            identifier = block.get("id")
            if not isinstance(identifier, str) or not identifier:
                result_repaired.append(block)
                index += 1
                continue

            end = index + 1
            while end < len(content):
                result = content[end]
                result_type = result.get("type") if isinstance(result, dict) else None
                if not (
                    isinstance(result_type, str)
                    and result_type.endswith("_tool_result")
                    and result.get("tool_use_id") == identifier
                ):
                    break
                end += 1
            group = content[index:end]
            prior = seen_groups.get(identifier)
            if prior is not None and group == prior:
                removed += len(group)
            else:
                seen_groups.setdefault(identifier, group)
                result_repaired.extend(group)
            index = end

        repaired.append(
            {**message, "content": result_repaired}
            if len(result_repaired) != len(content)
            else message
        )
    return repaired, removed

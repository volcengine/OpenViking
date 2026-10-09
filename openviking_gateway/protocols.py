# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Protocol-independent request operations; unknown protocol fields survive unchanged.

What differs between wire protocols lives in their adapters in ``tool_protocols``.
"""

import hashlib
import json
import math
import re
from decimal import Decimal
from importlib.resources import files
from typing import Any

import orjson

NORMALIZATION_VERSION = "v1"
_RULES = json.loads(files("context_gateway").joinpath("client-rules.json").read_text())
PLUGIN_TAGS = tuple(_RULES["plugin_tags"])
NOISE_TAGS = tuple(_RULES["noise_tags"])
AUXILIARY_PATTERNS = tuple(_RULES["auxiliary_patterns"])
# Vendors whose models expect their reasoning back in later turns.
REASONING_VENDORS = {"deepseek", "ark", "byteplus"}


def clean_text(text: str) -> str:
    for tag in NOISE_TAGS:
        text = re.sub(rf"<{tag}\b[^>]*>[\s\S]*?</{tag}>", " ", text, flags=re.I)
    text = re.sub(r"^\s*\[Subagent Context\][^\n]*", "", text, flags=re.M)
    return text.replace("\0", "").strip()


def unwrap_client(text: str) -> str:
    # Open WebUI's RAG template is only present on the current request. Preserve
    # the user's actual query as the anchor, not transient retrieved documents.
    match = re.search(r"<user_query>\s*([\s\S]*?)\s*</user_query>", text)
    return match[1] if match and "<context>" in text else text


def parse_body(raw: bytes) -> dict | None:
    """Parse once, rejecting numbers or duplicate keys unsafe to reserialize."""

    def integer(value):
        number = int(value)
        if not -(2**63) <= number < 2**63:
            raise ValueError("integer precision")
        return number

    def floating(value):
        number = float(value)
        if not math.isfinite(number):
            raise ValueError("nonfinite")
        if Decimal(orjson.dumps(number).decode()) != Decimal(value):
            raise ValueError("decimal precision")
        return number

    def pairs(items):
        value = {}
        for key, item in items:
            if key in value:
                raise ValueError("duplicate key")
            value[key] = item
        return value

    try:
        body = json.loads(
            raw,
            parse_int=integer,
            parse_float=floating,
            object_pairs_hook=pairs,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite")),
        )
        return body if isinstance(body, dict) else None
    except (ValueError, TypeError, OverflowError, RecursionError):
        return None


def text_content(message: dict) -> str:
    content = message.get("content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            x.get("text", "")
            for x in content
            if isinstance(x, dict)
            and x.get("type") in {"text", "input_text", "output_text"}
            and isinstance(x.get("text", ""), str)
        )
    return ""


def normalize(value: Any) -> Any:
    if isinstance(value, list):
        return [
            normalize(v)
            for v in value
            if not (
                isinstance(v, dict)
                and (
                    v.get("type") in {"thinking", "redacted_thinking"}
                    or (
                        v.get("type") in {"text", "input_text", "output_text"}
                        and not v.get("text", "").strip()
                    )
                )
            )
        ]
    if isinstance(value, dict):
        out = {
            k: (v if k in {"input", "arguments"} else normalize(v))
            for k, v in value.items()
            if k != "cache_control"
        }
        if "content" in out:
            content = out["content"]
            if isinstance(content, list) and len(content) == 1 and isinstance(content[0], dict):
                block = content[0]
                if set(block) == {"type", "text"} and block["type"] in {
                    "text",
                    "input_text",
                    "output_text",
                }:
                    content = block["text"]
            if isinstance(content, str):
                content = unwrap_client(content).strip()
            out["content"] = content
        if out.get("type") in {"text", "input_text", "output_text"}:
            out["text"] = unwrap_client(out.get("text", "")).strip()
        # Responses message items may omit type/status/id when resent by a client.
        # IDs of opaque items (item_reference, reasoning, calls) remain significant.
        if out.get("role") and out.get("type") == "message":
            out.pop("type", None)
            out.pop("status", None)
            out.pop("id", None)
        return out
    return value


def prefix_chain(messages: list[dict]) -> list[str]:
    previous = hashlib.sha256(NORMALIZATION_VERSION.encode()).digest()
    result = []
    for message in messages:
        if (
            message.get("role") in {"system", "developer"}
            or message.get("type") == "additional_tools"
        ):
            result.append("")
            continue
        canonical = normalize(message)
        if (
            message.get("role") == "assistant"
            and canonical.get("content") == []
            and not message.get("tool_calls")
        ):
            result.append("")
            continue
        previous = hashlib.sha256(
            previous + orjson.dumps(canonical, option=orjson.OPT_SORT_KEYS)
        ).digest()
        result.append(previous.hex())
    return result


def plugin_present(body: dict, headers: dict) -> bool:
    if headers.get("x-openviking-plugin"):
        return True

    def marked(text):
        # An echoed or summarized gateway envelope is not a plugin installation.
        text = re.sub(
            r"<openviking-context\b[^>]*>",
            lambda match: (
                ""
                if any(
                    name.lower() == "source" and value.lower().startswith("gateway-")
                    for name, _, value in re.findall(r"([\w-]+)\s*=\s*(['\"])(.*?)\2", match[0])
                )
                else match[0]
            ),
            text,
            flags=re.I,
        )
        return any(re.search(rf"<{re.escape(tag)}(?:\s|>)", text, re.I) for tag in PLUGIN_TAGS)

    texts = [text_content({"content": body.get("system", "")}), body.get("instructions", "")]
    for field in ("messages", "input"):
        messages = body.get(field, [])
        if isinstance(messages, str):
            texts.append(messages)
        elif isinstance(messages, list):
            texts.extend(
                text_content(m)
                for m in messages
                if isinstance(m, dict) and m.get("role") in {"user", "system", "developer"}
            )
    if any(marked(text) for text in texts if isinstance(text, str)):
        return True
    # Only tool definitions identify plugin ownership. File names, tool results
    # and assistant prose must never permanently disable a session.
    for tool in [*body.get("tools", []), *body.get("additional_tools", [])]:
        if not isinstance(tool, dict):
            continue
        definition = tool.get("function", tool)
        name = definition.get("name", definition.get("namespace", ""))
        if isinstance(name, str) and re.search(r"(^|__)openviking(?:_|__|$)", name, re.I):
            return True
    return False


def is_user(message):
    return (
        message.get("role") == "user"
        and bool(clean_text(unwrap_client(text_content(message))))
        and not (
            isinstance(message.get("content"), list)
            and any(
                b.get("type") == "tool_result" for b in message["content"] if isinstance(b, dict)
            )
        )
    )


def classify(body: dict, headers: dict, messages: list[dict]) -> tuple[str, int]:
    anchor = next((i for i in range(len(messages) - 1, -1, -1) if is_user(messages[i])), -1)
    request_class = headers.get("x-claude-code-request-class", "")
    if request_class in {"subagent", "workflow"}:
        return "subagent", anchor
    if request_class in {"compaction", "auxiliary"}:
        return "auxiliary", anchor
    if any(
        headers.get(k)
        for k in ("x-claude-code-agent-id", "x-openai-subagent", "x-codex-parent-thread-id")
    ):
        return "subagent", anchor
    hints = " ".join(
        headers.get(k, "")
        for k in (
            "x-openviking-task",
            "x-claude-code-request-type",
            "x-claude-code-request-kind",
            "x-claude-code-agent-type",
            "x-codex-turn-metadata",
        )
    )
    if re.search(r"compact|summar|title|classif|heartbeat|suggest|permission", hints, re.I):
        return "auxiliary", anchor
    probe = str(body.get("system", "")) + str(body.get("instructions", ""))
    if anchor >= 0:
        probe += text_content(messages[anchor])
    if any(re.search(pattern, probe, re.I) for pattern in AUXILIARY_PATTERNS):
        return "auxiliary", anchor
    if anchor < 0:
        return "continuation", anchor
    tail = messages[anchor + 1 :]
    if any(
        m.get("role") not in {"system", "developer"}
        and not (m.get("role") == "user" and not is_user(m))
        for m in tail
    ):
        return "continuation", anchor
    return "user", anchor


def session_id(headers: dict) -> str | None:
    for key in (
        "x-openviking-session",
        "thread-id",
        "x-claude-code-session-id",
        "x-opencode-session-id",
        "x-session-id",
        "session-id",
    ):
        if headers.get(key):
            return hashlib.sha256(headers[key].encode()).hexdigest()
    # The kernel continues the session whose reply the history contains, or
    # allocates an isolated anonymous one. Opening user text alone is not an identity.
    return None


def strip_thinking(messages: list[dict]) -> list[dict]:
    """Copies without reasoning, in every form a protocol carries it in messages."""
    result = [dict(message) for message in messages]
    for message in result:
        message.pop("reasoning_content", None)
        message.pop("reasoning_details", None)
        if isinstance(message.get("content"), list):
            message["content"] = [
                b
                for b in message["content"]
                if b.get("type") not in {"thinking", "redacted_thinking"}
            ]
    return result


def replays_reasoning(upstream: dict) -> bool:
    """Whether the gateway restores reasoning clients drop; DeepSeek requires it with tools."""
    value = upstream.get("replay_reasoning")
    return upstream.get("vendor") in REASONING_VENDORS if value is None else value


def usage_of(value: dict) -> dict:
    """Token counts from any protocol's usage, including compatible endpoints that mix forms."""
    usage = value.get("usage") or {}
    details = usage.get("prompt_tokens_details") or usage.get("input_tokens_details") or {}
    cached = usage.get("cache_read_input_tokens", details.get("cached_tokens", 0)) or 0
    written = usage.get("cache_creation_input_tokens", 0) or 0
    input_tokens = usage.get("prompt_tokens", usage.get("input_tokens", 0)) or 0
    if "cache_read_input_tokens" in usage:
        input_tokens += cached + written
    return {
        "input_tokens": input_tokens,
        "output_tokens": usage.get("completion_tokens", usage.get("output_tokens", 0)) or 0,
        "cached_tokens": cached,
        "cache_write_tokens": written,
    }


class SSEDecoder:
    """Incremental byte framing, including CRLF and frames split inside UTF-8."""

    def __init__(self, max_buffer=8 * 1024 * 1024):
        self.buffer = b""
        self.max_buffer = max_buffer

    def feed(self, chunk: bytes) -> list[bytes]:
        self.buffer += chunk
        frames = []
        while match := re.search(rb"\r?\n\r?\n", self.buffer):
            frames.append(self.buffer[: match.end()])
            self.buffer = self.buffer[match.end() :]
        if len(self.buffer) > self.max_buffer:
            raise ValueError("SSE event exceeds capture limit")
        return frames

    @staticmethod
    def data(frame: bytes) -> dict | None:
        data = b"\n".join(
            line[5:].lstrip(b" ") for line in frame.splitlines() if line.startswith(b"data:")
        )
        try:
            value = orjson.loads(data)
            return value if isinstance(value, dict) else None
        except ValueError:
            return None

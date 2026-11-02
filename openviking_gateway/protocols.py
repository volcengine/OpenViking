# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Protocol-independent request operations; unknown protocol fields survive unchanged.

What differs between wire protocols lives in their adapters in ``tool_protocols``.
"""

import asyncio
import hashlib
import json
import math
import re
from collections.abc import Sequence
from decimal import Decimal
from importlib.resources import files
from typing import Any

import orjson

from .config import ClientRules

NORMALIZATION_VERSION = "v1"
PACKAGED_RULES = json.loads(files("openviking_gateway").joinpath("client-rules.json").read_text())
# re.I tries a pattern at every offset; plain ASCII patterns (no escapes, sets or flags),
# lowercased, get re's literal search instead, the same match unless the text has one of
# the only letters where lower() and re.I differ against ASCII (checked in the tests).
REI_CASE_EXCEPTIONS = "İıſ"


def lowered(pattern: str) -> re.Pattern | None:
    plain = pattern.isascii() and not re.search(r"[\\\[]|\(\?", pattern)
    return re.compile(pattern.lower()) if plain else None


def configure_rules(changes: ClientRules | None = None) -> list[str]:
    """Apply ``GatewayConfig.client_rules`` to the packaged rules, for the whole process.

    Returns the removals that name no packaged entry. Each call starts from the packaged
    rules, so calling it again with other changes replaces the earlier ones.
    """
    global PLUGIN_TAGS, NOISE_TAGS, NOISE_BLOCKS, AUXILIARY_PATTERNS, NOISE_PATTERNS, AUXILIARY
    changes = changes or ClientRules()
    rules, unknown = {}, []
    for name, packaged in PACKAGED_RULES.items():
        change = getattr(changes, name)
        removed = set(change.remove)
        unknown += [f"{name}: {entry}" for entry in sorted(removed.difference(packaged))]
        kept = [entry for entry in packaged if entry not in removed]
        rules[name] = tuple(dict.fromkeys([*kept, *change.add]))
    PLUGIN_TAGS = rules["plugin_tags"]
    NOISE_TAGS = rules["noise_tags"]
    NOISE_BLOCKS = tuple(
        re.compile(rf"<{re.escape(tag)}(?=[\s>])[^>]*>[\s\S]*?</{re.escape(tag)}>", re.I)
        for tag in NOISE_TAGS
    )
    AUXILIARY_PATTERNS = rules["auxiliary_patterns"]
    # Whole messages clients write on the user's behalf: pi-ai's label for a tool result's
    # images, which it sends as a user message after the tool result, and DSH's runtime and
    # time snapshots, which DSH can append mid-turn. Each is a message of its own, so a
    # snapshot pattern takes the rest of the text. Anchored, so other text fails at once.
    NOISE_PATTERNS = tuple(re.compile(p) for p in rules["noise_patterns"])
    # Each pattern keeps its lowercase form when it has one, so one configured regex does
    # not move the others off the fast path.
    AUXILIARY = tuple((lowered(p), re.compile(p, re.I)) for p in AUXILIARY_PATTERNS)
    return unknown


configure_rules()

# Vendors whose models expect their reasoning back in later turns.
REASONING_VENDORS = {"deepseek", "ark", "byteplus"}


def clean_text(text: str) -> str:
    for block in NOISE_BLOCKS:
        text = block.sub(" ", text)
    text = re.sub(r"^\s*\[Subagent Context\][^\n]*", "", text, flags=re.M)
    for pattern in NOISE_PATTERNS:
        text = pattern.sub("", text)
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


def string_type(value: Any) -> str:
    """A block's ``type`` when it is a string; malformed histories may carry an object."""
    kind = value.get("type") if isinstance(value, dict) else None
    return kind if isinstance(kind, str) else ""


def text_content(message: dict) -> str:
    content = message.get("content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            x.get("text", "")
            for x in content
            if isinstance(x, dict)
            and string_type(x) in {"text", "input_text", "output_text"}
            and isinstance(x.get("text", ""), str)
        )
    return ""


def canonical_arguments(value: Any) -> Any:
    """Tool-call arguments as compact JSON in their own key order, else unchanged.

    Clients such as pi-ai parse the arguments a reply streamed and send them back
    re-serialized, so an upstream that spaces its JSON never matches its own reply.
    Numbers keep their JSON spelling, so ``30.0`` and ``30`` still differ.
    """
    if not isinstance(value, str):
        return value
    try:
        return orjson.dumps(orjson.loads(value)).decode()
    except orjson.JSONDecodeError:
        return value


def normalize(value: Any) -> Any:
    if isinstance(value, list):
        return [
            normalize(v)
            for v in value
            if not (
                isinstance(v, dict)
                and (
                    string_type(v) in {"thinking", "redacted_thinking"}
                    or (
                        string_type(v) in {"text", "input_text", "output_text"}
                        and not (isinstance(v.get("text", ""), str) and v.get("text", "").strip())
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
        if "arguments" in out:
            out["arguments"] = canonical_arguments(out["arguments"])
        if "content" in out:
            content = out["content"]
            if isinstance(content, list) and len(content) == 1 and isinstance(content[0], dict):
                block = content[0]
                if set(block) == {"type", "text"} and string_type(block) in {
                    "text",
                    "input_text",
                    "output_text",
                }:
                    content = block["text"]
            if isinstance(content, str):
                content = unwrap_client(content).strip()
            out["content"] = content
        if string_type(out) in {"text", "input_text", "output_text"} and isinstance(
            out.get("text", ""), str
        ):
            out["text"] = unwrap_client(out.get("text", "")).strip()
        # Responses message items may omit type/status/id when resent by a client.
        # IDs of opaque items (item_reference, reasoning, calls) remain significant.
        if out.get("role") and out.get("type") == "message":
            out.pop("type", None)
            out.pop("status", None)
            out.pop("id", None)
        return out
    return value


def prefix_chain(messages: list[dict], after: Sequence[str] = ()) -> list[str]:
    """Anchors of ``messages``; ``after``, the chain of the history before them, continues it."""
    previous = next(
        (bytes.fromhex(a) for a in reversed(after) if a),
        hashlib.sha256(NORMALIZATION_VERSION.encode()).digest(),
    )
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


def client_texts(body: dict) -> list[str]:
    """The system prompt and the client-written messages, without replies or tool output."""
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
    return [text for text in texts if isinstance(text, str)]


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

    # Plugins mark the context they inject. OpenViking tools alone, as from an MCP
    # connection, only keep the gateway's tools out (see client_openviking_tools).
    return any(marked(text) for text in client_texts(body))


OPENVIKING_WORD = r"(?<![a-z0-9])openviking(?![a-z0-9])"


def client_openviking_tools(body: dict, names) -> bool:
    """Whether the client already exposes OpenViking tools, whatever its MCP server is named.

    ``names`` are the bare OpenViking tool names, such as ``read`` and ``add_resource``.
    """
    if not names:
        return False
    pending = []
    for key in ("tools", "additional_tools", "mcp_servers"):
        if isinstance(body.get(key), list):
            pending.extend(("", tool) for tool in body[key])
    if isinstance(body.get("input"), list):
        for item in body["input"]:
            if isinstance(item, dict) and item.get("type") == "additional_tools":
                if isinstance(item.get("tools"), list):
                    pending.extend(("", tool) for tool in item["tools"])
    # Prefixed like mcp__ov__read or ov_read; a bare Read is a client's own tool.
    alternatives = "|".join(map(re.escape, names))
    ending = re.compile(rf"[^a-z0-9](?:{alternatives})$", re.I)
    while pending:
        parent, tool = pending.pop()
        if not isinstance(tool, dict):
            continue
        definition = tool.get("function", tool)
        if not isinstance(definition, dict):
            continue
        labels = [
            parent + definition[key]
            for key in ("name", "namespace", "server_label", "mcp_server_name")
            if isinstance(definition.get(key), str)
        ]
        if isinstance(definition.get("tools"), list):
            # A namespace's tools are named without it, as in mcp__kb + glob; they
            # decide, not the namespace name.
            prefix = (labels[0] + "__") if labels else ""
            pending.extend((prefix, child) for child in definition["tools"])
            continue
        # Hosted MCP servers (Responses mcp, Anthropic mcp_servers and mcp_toolset) list
        # no tools, so their name is all there is.
        server = string_type(definition) in {"mcp", "mcp_toolset", "url"}
        named_tool = isinstance(definition.get("name"), str) and not server
        # A tool must be one of OpenViking's, so a connector's authenticate tool is not.
        if any(
            re.search(OPENVIKING_WORD, label, re.I) and (not named_tool or ending.search(label))
            for label in labels
        ):
            return True
        # A renamed server's tools keep their names and mention viking:// URIs, while
        # a container such as a skill list may quote an OpenViking description too.
        if (
            any(ending.search(label) for label in labels)
            and "viking://" in orjson.dumps(definition).decode().lower()
        ):
            return True
    # Deferred MCP tools, as in Claude Code's tool search, are listed only by name, one
    # per line; a name mentioned in prose is not a tool. Most texts have no "__" at all.
    deferred = rf"^[ \t]*mcp__[\w-]*?{OPENVIKING_WORD}[\w-]*__(?:{alternatives})[ \t]*$"
    return any(
        "__" in text and re.search(deferred, text, re.I | re.M) for text in client_texts(body)
    )


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


def auxiliary(text: str) -> bool:
    """Whether ``text`` matches an auxiliary pattern, ignoring case."""
    lower = None if any(c in text for c in REI_CASE_EXCEPTIONS) else text.lower()
    return any(
        fast.search(lower) if fast and lower is not None else pattern.search(text)
        for fast, pattern in AUXILIARY
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
        )
    )
    if re.search(r"compact|summar|title|classif|heartbeat|suggest|permission", hints, re.I):
        return "auxiliary", anchor
    # Codex sends the whole turn metadata (workspace paths, remotes, tool names) on every
    # request; only its request_kind says what the request is.
    try:
        turn = orjson.loads(headers.get("x-codex-turn-metadata") or "null")
    except orjson.JSONDecodeError:
        turn = None
    if isinstance(turn, dict) and turn.get("request_kind") in ("compaction", "memory"):
        return "auxiliary", anchor
    # Each part on its own and as plain text, so a pattern's anchors and line breaks hold.
    parts = [text_content({"content": body.get(key, "")}) for key in ("system", "instructions")]
    if anchor >= 0:
        parts.append(text_content(messages[anchor]))
    if any(auxiliary(part) for part in parts if part):
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
                if string_type(b) not in {"thinking", "redacted_thinking"}
            ]
    return result


def replays_reasoning(upstream: dict) -> bool:
    """Whether the gateway restores reasoning clients drop; DeepSeek requires it with tools."""
    value = upstream.get("replay_reasoning")
    return upstream.get("vendor") in REASONING_VENDORS if value is None else value


# Fields relays put cache counts in, at the top of usage or in its token details.
CACHE_READ = ("cache_read_input_tokens", "cached_tokens", "prompt_cache_hit_tokens")
CACHE_WRITE = ("cache_creation_input_tokens", "cache_write_tokens", "cached_creation_tokens")


def usage_of(value: dict, excludes_cache: bool = False) -> dict:
    """Token counts from any protocol's usage, including compatible endpoints that mix forms.

    ``input_tokens`` always includes cached and written tokens: Anthropic counts them apart
    (``excludes_cache``), Chat and Responses totals already hold them.
    """
    usage = value.get("usage") or {}
    scopes = [
        usage,
        usage.get("prompt_tokens_details") or {},
        usage.get("input_tokens_details") or {},
    ]
    # Relays copy one count into several fields and zero-fill the rest.
    cached, written = (
        max((s[k] for s in scopes for k in keys if s.get(k) is not None), default=0)
        for keys in (CACHE_READ, CACHE_WRITE)
    )
    # Each protocol's own total first; compatible endpoints often fill both, one with zero.
    own, other = (
        ("input_tokens", "prompt_tokens") if excludes_cache else ("prompt_tokens", "input_tokens")
    )
    input_tokens = usage.get(own) or usage.get(other) or 0
    # A total below its own cache counted the cache apart, as older relays do too.
    if excludes_cache or input_tokens < cached + written:
        input_tokens += cached + written
    return {
        "input_tokens": input_tokens,
        "output_tokens": usage.get("completion_tokens") or usage.get("output_tokens") or 0,
        "cached_tokens": cached,
        "cache_write_tokens": written,
    }


# Stay below the common 60-second load-balancer idle timeout.
HEARTBEAT_SECONDS = 15


async def heartbeat(chunks, seconds):
    """Yield None during idle periods while one task owns the source iterator."""
    queue, end = asyncio.Queue(1), object()

    async def pump():
        try:
            async for chunk in chunks:
                await queue.put(chunk)
        except asyncio.CancelledError as error:
            # A deadline can cancel queue.put while the source's timeout is suspended at yield.
            if throw := getattr(chunks, "athrow", None):
                await throw(error)
            raise
        finally:
            # Close suspended generators in the task that owns their timeout contexts.
            if close := getattr(chunks, "aclose", None):
                await close()

    def wake_consumer(task):
        # A full queue already wakes the consumer; completion must not block cleanup.
        if queue.empty():
            queue.put_nowait(end)

    task = asyncio.create_task(pump())
    task.add_done_callback(wake_consumer)
    try:
        while not task.done() or not queue.empty():
            try:
                chunk = await asyncio.wait_for(queue.get(), seconds)
            except asyncio.TimeoutError:
                yield None
                continue
            if chunk is end:
                break
            yield chunk
        await task
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


class SSEDecoder:
    """Incremental byte framing, including CRLF and frames split inside UTF-8."""

    # A frame ends at a blank line. Only where it ends matters, so the pattern starts
    # at the newline, which the scan finds much faster than an optional CR.
    frame_end = re.compile(rb"\n\r?\n")

    def __init__(self, max_buffer=8 * 1024 * 1024):
        self.buffer = b""
        self.max_buffer = max_buffer

    def feed(self, chunk: bytes) -> list[bytes]:
        # One pass and one copy of the rest, however many frames the chunk holds.
        buffer, frames, start = self.buffer + chunk, [], 0
        for match in self.frame_end.finditer(buffer):
            frames.append(buffer[start : match.end()])
            start = match.end()
        self.buffer = buffer[start:]
        if len(self.buffer) > self.max_buffer:
            raise ValueError("SSE event exceeds capture limit")
        return frames

    @staticmethod
    def data(frame: bytes) -> dict | None:
        data = b"\n".join(
            [line[5:].lstrip(b" ") for line in frame.splitlines() if line.startswith(b"data:")]
        )
        try:
            value = orjson.loads(data)
            return value if isinstance(value, dict) else None
        except ValueError:
            return None

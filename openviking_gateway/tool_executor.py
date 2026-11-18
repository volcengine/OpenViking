# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Bounded, user-authenticated MCP calls, native tools and signed file link handoff."""

import asyncio
import base64
import binascii
import html
import re
import time
import uuid
from pathlib import PurePosixPath
from urllib.parse import parse_qs, quote, urlsplit

import async_timeout
import orjson

from .blocks import token_estimate
from .capture import capture_messages, lineage
from .capture_store import Document
from .client import VikingError, peer_argument
from .compaction import active_cut
from .config import MOUNT_PATH
from .protocols import string_type
from .records import RecordKind as K
from .state_store import get_state
from .storage import digest
from .tool_catalog import PREFIX, TOOL_OVERRIDES
from .tool_protocols import tool_protocol
from .windows import NATIVE_TOOLS, NEW_CONTEXT

UPLOAD_URL = re.compile(
    r'https?://[^\s<>"\x27`]+/api/v1/resources/temp_upload\?token=[^\s<>"\x27`]+'
)
DOWNLOAD_URL = re.compile(r'https?://[^\s<>"\x27`]+/api/v1/content/download\?token=[^\s<>"\x27`]+')
AUTO_DETECTED_NOTE = re.compile(
    r"\n*Note for the user: this (?:upload|download) URL was auto-detected[^\n]*"
)
NOTHING_SAVED = (
    "Nothing from this conversation is saved in OpenViking yet, so there is nothing to look up; "
    "work from what is in your context."
)
UNKNOWN_OUTCOME = "Tool timed out or a previous attempt has an unknown outcome; inspect state before retrying writes"


def error_result(text):
    return {"content": [{"type": "text", "text": text}], "isError": True}


def has_shell(body):
    return any(
        re.search(
            r"(^|[_-])(bash|shell|exec_command|terminal|run_command)([_-]|$)",
            tool.get("function", tool).get("name", ""),
            re.I,
        )
        for tool in body.get("tools", [])
        if isinstance(tool, dict)
    )


def attachments(body, protocol):
    output = []
    for message in tool_protocol(protocol).messages(body):
        if message.get("role") not in {"user", "system"}:
            continue
        content = message.get("content", [])
        if isinstance(content, list):
            for part in content:
                if string_type(part) in {"file", "input_file"}:
                    output.append(part.get("file", part))
                elif isinstance(part, dict) and part.get("type") == "document":
                    source = part.get("source", {})
                    if source.get("type") == "base64":
                        output.append(
                            {
                                "filename": part.get("title", "attachment.pdf"),
                                "file_data": source.get("data", ""),
                            }
                        )
                    elif source.get("type") == "text":
                        output.append(
                            {
                                "filename": part.get("title", "attachment.txt"),
                                "text": source.get("data", ""),
                            }
                        )
        # Open WebUI can send extracted documents as source tags instead of bytes.
        # Only marked source text is importable, never arbitrary conversation text.
        texts = (
            [content]
            if isinstance(content, str)
            else [p.get("text", "") for p in content if isinstance(p, dict)]
        )
        for text in texts:
            for context in re.findall(r"<context>([\s\S]*?)</context>", text):
                for index, (attrs, source) in enumerate(
                    re.findall(r"<source\b([^>]*)>([\s\S]*?)</source>", context)
                ):
                    name = re.search(r'name=["\']([^"\']+)["\']', attrs)
                    filename = html.unescape(name[1]) if name else f"source-{index}.txt"
                    output.append(
                        {
                            "filename": filename + ".txt"
                            if not filename.endswith(".txt")
                            else filename,
                            "text": html.unescape(source),
                        }
                    )
    return output


def content_blocks(result):
    blocks = result.get("content", []) if isinstance(result, dict) else None
    if not isinstance(blocks, list) or not all(isinstance(block, dict) for block in blocks):
        raise ValueError("Invalid MCP result")
    return blocks


def signed_token(result, pattern):
    for block in content_blocks(result):
        if block.get("type") == "text":
            match = pattern.search(block.get("text", ""))
            if match:
                return parse_qs(urlsplit(match[0]).query).get("token", [""])[0]
    return ""


def attachment_bytes(part, limit):
    name = PurePosixPath(part.get("filename", "attachment.txt")).name
    if not name or name in {".", ".."}:
        name = "attachment.txt"
    if isinstance(part.get("text"), str):
        data = part["text"].encode()
    else:
        value = part.get("file_data", "")
        if not isinstance(value, str) or not value:
            raise ValueError(
                "Attachment bytes are unavailable; file IDs and remote URLs cannot be imported"
            )
        if value.startswith("data:"):
            header, value = value.split(",", 1)
            if not header.endswith(";base64"):
                raise ValueError("Expected a base64 attachment")
        if len(value) > (limit + 2) // 3 * 4:
            raise ValueError("Attachment exceeds upload limit")
        data = base64.b64decode(value, validate=True)
    if len(data) > limit:
        raise ValueError("Attachment exceeds upload limit")
    return name, data


# session_history results may fill this share of a context window, and at least two
# full-size results, so a small window still allows a search and a read.
HISTORY_SHARE = 0.2
HISTORY_FLOOR = 8000


def history_tokens(messages):
    """Tokens of the raw session_history results in hidden tool rounds, in any wire protocol.

    Calls are Chat tool_calls, Anthropic tool_use blocks or Responses function_call items;
    results are Chat tool messages, tool_result blocks or function_call_output items.
    """
    calls, results = set(), []
    for message in messages:
        content = message.get("content")
        blocks = content if isinstance(content, list) else []
        for item in [message, *(message.get("tool_calls") or []), *blocks]:
            if not isinstance(item, dict):
                continue
            function = item["function"] if isinstance(item.get("function"), dict) else item
            if function.get("name") == "openviking_session_history":
                calls.add(item.get("call_id", item.get("id")))
            elif "tool_call_id" in item or item.get("type") in {
                "tool_result",
                "function_call_output",
            }:
                identifier = item.get("tool_call_id", item.get("tool_use_id", item.get("call_id")))
                results.append((identifier, item.get("content", item.get("output"))))
    return sum(token_estimate(text) for identifier, text in results if identifier in calls and text)


def unsaved(request, capture):
    """How many messages are neither saved nor in the current context window.

    They follow the delivered anchor up to the active cut, the part a compaction summary or a
    new window replaced. Without a cut everything unsaved is still in context. Without a delivered anchor in this
    history, or when a new OpenViking session took over from earlier ones, the gateway cannot
    tell which messages the saved sessions hold, so it counts none rather than too many.
    """
    delivered, chain, cut = capture.get("delivered", ""), request.capture_chain, active_cut(request)
    if not delivered or delivered not in chain:
        return 0
    start = chain.index(delivered) + 1
    return len(capture_messages(request.messages[start : cut + 1], chain[start : cut + 1]))


def unsaved_note(count):
    """The line after a result that lacks the ``count`` unsaved messages before the window."""
    last = "last message" if count == 1 else f"last {count} messages"
    verb = "is" if count == 1 else "are"
    return (
        f"Gateway note: the {last} before your current context window {verb} not saved yet and "
        "may be missing from this result."
    )


def result_text(result, limit):
    """Text blocks only; structuredContent repeats them, and media becomes a note."""
    parts = []
    for block in content_blocks(result):
        if block.get("type") != "text":
            parts.append(f"[OpenViking returned {block.get('type', 'non-text')} content; omitted.]")
        elif isinstance(block.get("text"), str):
            parts.append(block["text"])
        else:
            raise ValueError("Invalid MCP text block")
    text = "\n".join(parts)
    if len(orjson.dumps(text)) > limit:
        suffix = "\n[Tool result truncated.]"
        text = text.encode()[: limit - len(suffix)].decode("utf-8", errors="ignore")
        while len(orjson.dumps(text + suffix)) > limit:
            text = text[: len(text) * 3 // 4]
        text += suffix
    return text


class ToolExecutor:
    def __init__(
        self, viking, store, prepared, credential, public_url, max_upload_bytes, file_links="proxy"
    ):
        self.viking, self.store, self.request = viking, store, prepared
        self.credential, self.key = credential, credential["openviking_key"]
        self.public_url, self.max_upload_bytes = public_url.rstrip("/"), max_upload_bytes
        self.file_links = file_links
        self.policy = prepared.root["policy"]
        self.schemas = {
            t["function"]["name"]: t["function"]["parameters"] for t in prepared.root["tools"]
        }
        self.allowed = set(self.schemas)
        # session_history result tokens of this request, by the cut of the window they went to.
        self.spent = {}

    async def execute(self, call):
        result = await self._execute(call)
        self.spend(call.get("function", {}).get("name", ""), result["content"])
        return result

    async def _execute(self, call):
        name = call.get("function", {}).get("name", "")
        call_id = call.get("id", "")
        result = {"role": "tool", "tool_call_id": call_id}
        if name not in self.allowed or not call_id:
            return {**result, "content": "Tool is not allowed", "failed": True}
        native = NATIVE_TOOLS.get(name)
        if native:
            # Native tools only change this request's tool loop, so retries need no receipt.
            try:
                args = self.arguments(call)
            except (ValueError, TypeError):
                return {**result, "content": "Invalid tool arguments", "failed": True}
            return {**result, **await native.handler(self.request, self.credential, args)}
        if name == "openviking_session_history" and (refusal := self.history_over_budget()):
            return {**result, "content": refusal, "failed": False}
        # Retries sharing a history and call ID share the claim. A process crash
        # never causes automatic repetition of a potentially committed write.
        anchor = digest(
            (self.request.chain[-1] if self.request.chain else "") + orjson.dumps(call).decode()
        )
        owner = uuid.uuid4().hex
        scope, session = self.request.scope, self.request.session
        receipt_key = "tool:" + session + ":" + anchor
        empty = Document()
        claim = {"owner": owner, "time": time.time()}
        won = await self.store.state.swap(scope, receipt_key, empty, claim)
        receipt = (
            Document(claim, 1) if won else await get_state(self.store.state, scope, receipt_key)
        )
        claim = receipt.value
        if not claim:
            return {**result, "content": UNKNOWN_OUTCOME, "failed": True}
        timeout = self.policy.get("tool_timeout_seconds", 30)
        try:
            async with async_timeout.timeout(timeout):
                if claim["owner"] != owner:
                    while True:
                        saved = await get_state(self.store.state, scope, receipt_key)
                        if not saved.value:
                            raise asyncio.TimeoutError
                        if "content" in saved.value:
                            return {
                                **result,
                                "content": saved.value["content"],
                                "failed": saved.value["failed"],
                            }
                        if time.time() - claim["time"] > timeout:
                            raise asyncio.TimeoutError
                        await asyncio.sleep(0.05)
                if name == "openviking_session_history":
                    content, failed = await self.history(self.arguments(call))
                else:
                    value = await self._call(name.removeprefix(PREFIX), self.arguments(call))
                    content = result_text(value, self.policy.get("tool_result_bytes", 65536))
                    failed = bool(value.get("isError"))
                await self.store.state.swap(
                    scope, receipt_key, receipt, {**claim, "content": content, "failed": failed}
                )
                return {**result, "content": content, "failed": failed}
        except asyncio.TimeoutError:
            content = UNKNOWN_OUTCOME
        except (ValueError, TypeError, binascii.Error, VikingError, KeyError):
            # Validation and server errors can contain arguments and secrets.
            content = "Invalid tool arguments or OpenViking operation failed"
        if claim["owner"] == owner:
            await self.store.state.swap(
                scope, receipt_key, receipt, {**claim, "content": content, "failed": True}
            )
        return {**result, "content": content, "failed": True}

    def spend(self, name, content):
        if name == "openviking_session_history":
            cut = active_cut(self.request)
            self.spent[cut] = self.spent.get(cut, 0) + token_estimate(content)

    async def history(self, args):
        """A session_history lookup; returns its text and whether it failed.

        Without a session from the model it looks up this conversation's saved sessions and
        says when messages cut from the current window are not saved yet, after the result
        so that truncation keeps it.
        """
        session = args.get("session")
        properties = self.schemas["openviking_session_history"].get("properties", {})
        capture = None
        if "session" in properties and (
            session is None or (isinstance(session, str) and not session.strip())
        ):
            try:
                capture = await self.store.capture.get(self.request.scope, self.request.session)
                capture = capture.value
            except Exception:
                # A read-only lookup never fails on the store; the request's own view serves.
                capture = self.request.capture.value
            sessions = lineage(capture)
            if not sessions:
                # Nothing is saved yet, so every message so far is waiting.
                waiting = len(capture_messages(self.request.messages, self.request.capture_chain))
                if not waiting:
                    return NOTHING_SAVED, False
                count = "message so far is" if waiting == 1 else f"{waiting} messages so far are"
                return NOTHING_SAVED + f" Its {count} waiting to be saved.", False
            args["session"] = ",".join(sessions)
        value = await self._call("session_history", args)
        content = result_text(value, self.policy.get("tool_result_bytes", 65536))
        if capture is not None and (count := unsaved(self.request, capture)):
            content += "\n" + unsaved_note(count)
        return content, bool(value.get("isError"))

    def history_over_budget(self):
        """The answer to a session_history call once its window budget is used, or "".

        The budget covers the results in the current window: those in the hidden rounds
        it replays, and this request's, as a cut inside the request starts a new window.
        """
        cut, records = active_cut(self.request), self.request.records
        used = self.spent.get(cut, 0) + sum(
            history_tokens(records[K.HIDDEN, anchor]["messages"])
            for anchor in self.request.capture_chain[cut + 1 :]
            if (K.HIDDEN, anchor) in records
        )
        if used < max(HISTORY_SHARE * self.request.context_window, HISTORY_FLOOR):
            return ""
        later = [
            text
            for text, offered in (
                ("the next compaction", self.policy.get("compaction", True)),
                (NEW_CONTEXT + " starts a new window", NEW_CONTEXT in self.allowed),
            )
            if offered
        ]
        more = " More lookups are possible after " + " or after ".join(later) + "." if later else ""
        return (
            "The session history budget for this context window is used up: earlier results "
            f"(about {used} tokens) are still in context above. Work from them and note the "
            "ids you need." + more
        )

    def arguments(self, call):
        """Decoded arguments with only frozen keys and every required one."""
        args = orjson.loads(call["function"]["arguments"])
        schema = self.schemas[call["function"]["name"]]
        if (
            not isinstance(args, dict)
            or args.keys() - schema.get("properties", {}).keys()
            or set(schema.get("required", [])) - args.keys()
        ):
            raise ValueError("Invalid tool arguments")
        return args

    async def _call(self, name, args):
        upload = TOOL_OVERRIDES.get(name, {}).get("attachment", False)
        index = args.pop("attachment_index", None) if upload else None
        file = None
        if index is not None:
            parts = attachments(self.request.original, self.request.protocol)
            if type(index) is not int or index < 0 or index >= len(parts):
                raise ValueError("Attachment is unavailable")
            file = attachment_bytes(parts[index], self.max_upload_bytes)
            # Only a synthetic local filename is sent. The gateway never opens it.
            args["path"] = "/client-upload/" + file[0]
            if name == "add_skill":
                args.pop("data", None)
        elif upload and args.get("path"):
            path = args["path"]
            if not isinstance(path, str):
                raise ValueError("Expected a path string")
            if not path.startswith(("https://", "http://")) and not has_shell(
                self.request.original
            ):
                raise ValueError("A local path requires a client shell or attachment")
        elif name == "download" and not has_shell(self.request.original):
            return error_result("download requires a client shell tool to fetch the file")
        actor = self.policy.get("recall_peer_scope", "all") == "actor"
        peer = self.request.peer if actor else None
        # The peer hides other peers from every tool but context search, which must be told.
        if peer and name == "search" and args.get("mode") == "context":
            args["peer_scope"] = "actor"
        value = await self.viking.mcp(
            "tools/call", self.key, {"name": name, "arguments": args}, **peer_argument(peer)
        )
        if upload:
            pattern, route = UPLOAD_URL, "/uploads"
        elif name == "download":
            pattern, route = DOWNLOAD_URL, "/downloads"
        else:
            pattern, route = None, ""
        token = signed_token(value, pattern) if pattern else ""
        if file:
            if not token or value.get("isError"):
                raise VikingError("upload_instruction_missing")
            uploaded = await self.viking.upload(token, file[0], file[1])
            return {
                "content": [{"type": "text", "text": orjson.dumps(uploaded).decode()}],
                "isError": uploaded.get("status") == "error" or bool(uploaded.get("error")),
            }
        if token and self.file_links == "proxy":
            if not self.public_url:
                return error_result("Set gateway.public_url for client uploads and downloads")
            url = self.public_url + MOUNT_PATH + route + "?token=" + quote(token, safe="")
            for block in value.get("content", []):
                if block.get("type") == "text":
                    text = AUTO_DETECTED_NOTE.sub("", block["text"])
                    block["text"] = pattern.sub(lambda _: url, text)
        return value

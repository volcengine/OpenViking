# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""One capture document per client session: its OpenViking session and how far it is saved.

The request that ends a turn writes the turn to OpenViking, and a gateway cut inside a
turn writes the history before it at once. A commit then archives the saved messages
and extracts memories from them: the gateway commits, or with ``openviking_auto_commit``
OpenViking does by itself. The document holds no conversation text, only where the
session stands and when its idle commit is due. Replay records are independent: a
failed save never invalidates an old replacement.
"""

import asyncio
import hashlib
import logging
import time
import uuid
from collections import Counter

import async_timeout
import orjson

from .capture_store import Document
from .client import without_deadline
from .models import Policy
from .notices import without_notices
from .protocols import (
    clean_text,
    normalize,
    string_type,
    text_content,
    unwrap_client,
)

logger = logging.getLogger(__name__)
MAX_PREVIOUS = 5
SOURCE = "gateway:"
# OpenViking accepts this many messages in one write.
BATCH = 100
# How long a request waits for the write at a cut inside its turn.
CUT_SECONDS = 10
# Each process looks this often for idle commits overdue by as long: ones whose process
# went away before the commit, and ones that failed.
SWEEP_SECONDS = 60
# ``created`` says who commits the OpenViking session as it was last set up. ``idle_at``
# is when the gateway's idle commit is due, for the key ``account`` and ``credential_id`` name.
FIELDS = (
    "ov_session",
    "delivered",
    "created",
    "reason",
    "previous",
    "peers",
    "idle_at",
    "account",
    "credential_id",
)


def new_capture(reason="", old=None):
    value = {
        "ov_session": "gateway-" + uuid.uuid4().hex,
        "delivered": "",
        "reason": reason,
        "previous": lineage(old or {})[:MAX_PREVIOUS],
    }
    # A new OpenViking session receives the history again, from the same peers.
    if (old or {}).get("peers"):
        value["peers"] = old["peers"]
    return value


def current(value):
    """The document's fields, or a new document; earlier versions also queued messages here."""
    return {key: value[key] for key in FIELDS if key in value} if value else new_capture()


def lineage(value):
    """The OpenViking sessions holding this history's saved messages, newest first."""
    previous = value.get("previous", [])
    return [value["ov_session"], *previous] if value.get("delivered") else list(previous)


async def reset_capture(store, scope, session, reason):
    """Start a new OpenViking session; the next finished turn writes the whole history to it."""
    while True:
        old = await store.get(scope, session)
        value = new_capture(reason, old.value)
        if await store.swap(scope, session, old, value):
            return value


def segment(value, messages, chain, start, stop):
    """messages[start:stop] as OpenViking messages, the users' naming their peers."""
    peers = peer_anchors(value.get("peers"), messages[:stop], chain[:stop])
    return with_peers(capture_messages(messages[start:stop], chain[start:stop]), peers)


class CapturePipeline:
    """Saves turns and, unless OpenViking commits by itself, commits what they saved.

    A turn's end commits at once when the uncommitted messages reach the profile's
    ``commit_tokens``, keeping the latest ``keep_recent_messages`` live. Everything
    left is committed once no turn has ended for ``idle_seconds``. That wait is a
    timer in this process; the capture document records when it is due, so any
    process finds the commit in ``sweep`` if this one is gone by then or failed.
    Whoever makes it reads the OpenViking key from the stored key record at that
    time, so a key revoked meanwhile commits nothing.
    """

    def __init__(self, store, viking=None, auto_commit=False, management=None):
        self.store, self.viking, self.auto_commit = store, viking, auto_commit
        self.management = management
        # Idle commits waiting in this process with their due times and the ones
        # running, by session, and every commit in flight.
        self.timers, self.running, self.tasks = {}, set(), set()

    async def open(self, request):
        """Name this history's OpenViking session and the peer of each new user message."""
        old = request.capture
        while True:
            value = record_peers(current(old.value), request)
            if value == old.value or await self.store.swap(
                request.scope, request.session, old, value
            ):
                request.capture = Document(value, old.version + (value != old.value))
                return
            old = await self.store.get(request.scope, request.session)

    async def save(self, request, credential, messages, chain, stop=None):
        """Write what OpenViking lacks of messages[:stop]; returns how many messages went.

        Writing starts after the cursor, the last message written. A history without
        the cursor is another branch or was edited: it goes on in the same OpenViking
        session from the turn's user message, so OpenViking may then hold a message of
        it twice, and keeps what the client dropped. A write whose response is lost is
        sent again by the next turn; that rare duplicate is accepted too.
        """
        ended, stop = stop is None, len(messages) if stop is None else stop
        policy = Policy.model_validate(request.root["policy"])
        doc = await self.store.get(request.scope, request.session)
        value = current(doc.value)
        positions = {anchor: index for index, anchor in enumerate(chain) if anchor}
        cursor = value["delivered"]
        if cursor in positions:
            start = positions[cursor] + 1
        else:
            start = request.anchor if cursor else 0
        captured = await asyncio.to_thread(segment, value, messages, chain, start, stop)
        if not captured:
            return 0
        key, session = credential["openviking_key"], value["ov_session"]
        committer = "openviking" if self.auto_commit else "gateway"
        # The reply is out, or a summary call took the time: the request's budget is over.
        with without_deadline():
            # Also after the setting changed: the session then gets the other policy.
            if value.get("created") != committer:
                await self.viking.create_session(key, session, policy, self.auto_commit)
            for offset in range(0, len(captured), BATCH):
                batch = captured[offset : offset + BATCH]
                written = await self.viking.write(key, session, batch)
                anchor = batch[-1]["source_message_ids"][0].removeprefix(SOURCE)
                mark = {"created": committer}
                if self.auto_commit:
                    mark["idle_at"] = None
                elif ended and offset + BATCH >= len(captured):
                    mark.update(
                        idle_at=time.time() + policy.idle_seconds,
                        account=credential["account"],
                        credential_id=credential["id"],
                    )
                doc = await self.moved(request, doc, value, anchor or value["delivered"], mark)
                if doc is None:
                    return offset + len(batch)
                request.capture, value = doc, doc.value
        if ended and not self.auto_commit:
            if (written or {}).get("pending_tokens", 0) >= policy.commit_tokens:
                self.spawn(self.commit(key, session, policy.keep_recent_messages))
            self.wait((request.scope, request.session), value["idle_at"])
        return len(captured)

    def wait(self, name, due):
        """Make the idle commit of ``name`` at ``due``; a later turn of it waits anew."""
        if name in self.timers:
            self.timers.pop(name)[0].cancel()
        timer = asyncio.get_running_loop().call_later(max(due - time.time(), 0), self.idle, name)
        self.timers[name] = (timer, due)

    def idle(self, name):
        self.spawn(self.idle_commit(name, self.timers.pop(name)[1]))

    async def idle_commit(self, name, due):
        """Commit everything with the stored key, then clear the due time.

        A failed commit leaves the due time for ``sweep``. A key that was revoked, or
        whose record expired, commits nothing: the conversation's next turn waits anew.
        """
        self.running.add(name)
        try:
            doc = await self.due(name, due)
            if not doc:
                return
            value = doc.value
            key = await self.management.get(value["account"], "keys", value["credential_id"])
            if key and not await self.commit(key["openviking_key"], value["ov_session"], 0):
                return
            while doc:
                done = {k: v for k, v in doc.value.items() if k != "idle_at"}
                if await self.store.swap(*name, doc, done):
                    return
                doc = await self.due(name, due)
        except Exception as error:
            logger.warning(
                "OpenViking Gateway could not make an idle commit: %s", type(error).__name__
            )
        finally:
            self.running.discard(name)

    async def due(self, name, due):
        """The document while it still waits for the idle commit due at ``due``.

        Not once a later turn waits anew, here or in another process, a reset left
        the session behind, or another process made the commit.
        """
        doc = await self.store.get(*name)
        return doc if doc.value.get("idle_at") == due else None

    async def sweep(self):
        """Make the idle commits that are overdue, one at a time."""
        if self.viking.unavailable_reason:
            return
        before = time.time() - SWEEP_SECONDS
        for name in await self.store.waiting(before):
            if name in self.running:
                continue
            try:
                due = (await self.store.get(*name)).value.get("idle_at")
                if due is None:
                    # An earlier version listed it, or its document expired.
                    await self.store.forget(*name)
                elif due <= before:
                    # Not one that a turn ending meanwhile set to a later time.
                    await self.idle_commit(name, due)
            except Exception as error:
                logger.warning(
                    "OpenViking Gateway could not look at an overdue commit: %s",
                    type(error).__name__,
                )

    async def commit(self, key, session, keep):
        try:
            with without_deadline():
                await self.viking.commit(key, session, keep)
        except Exception as error:
            logger.warning(
                "OpenViking Gateway could not commit %s: %s",
                session,
                getattr(error, "reason", type(error).__name__),
            )
            return False
        return True

    def spawn(self, work):
        task = asyncio.ensure_future(work)
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    async def close(self):
        """Stop waiting. What was due stays recorded, and ``sweep`` finds it."""
        for timer, _ in self.timers.values():
            timer.cancel()
        self.timers.clear()
        for task in list(self.tasks):
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)

    async def moved(self, request, doc, value, anchor, mark):
        """Store the cursor after a write, unless a reset or another save moved it meanwhile.

        Returns None then: a save that began earlier must not take the cursor back.
        """
        session, cursor = value["ov_session"], value["delivered"]
        while True:
            value = {**value, "delivered": anchor, **mark}
            if await self.store.swap(request.scope, request.session, doc, value):
                return Document(value, doc.version + 1)
            doc = await self.store.get(request.scope, request.session)
            value = current(doc.value)
            if value["ov_session"] != session or value["delivered"] != cursor:
                return None

    async def save_cut(self, request, credential, anchor):
        """Write the history up to a gateway cut inside a turn, without waiting for its end.

        The model may search the saved session right after the cut. A turn's end covers
        cuts before a user message, and whatever this write could not save.
        """
        chain = request.capture_chain
        if (
            request.disabled
            or request.anchor < 0
            or not anchor
            or anchor not in chain
            or not Policy.model_validate(request.root["policy"]).capture
        ):
            return
        # The request waits for this write, so one that cannot finish soon is left out.
        if self.viking.unavailable_reason:
            return
        try:
            async with async_timeout.timeout(CUT_SECONDS):
                await self.save(
                    request, credential, request.messages, chain, chain.index(anchor) + 1
                )
        except Exception as error:
            logger.warning(
                "OpenViking Gateway could not save the history before a cut: %s",
                getattr(error, "reason", type(error).__name__),
            )

    @staticmethod
    def metrics(request, policy):
        request.metrics.update(
            capture_status="active" if policy.capture else "disabled",
            capture_reason=request.capture.value.get("reason", ""),
        )


def peer_keys(messages, chain):
    """A key for each user message: its content and how often that content came before.

    Unlike the anchor, the key survives an edit or a dropped message earlier in the
    history, as a client's sliding window drops them. Other messages have no key.
    """
    seen = Counter()
    keys = []
    for message, anchor in zip(messages, chain, strict=True):
        if not anchor or message.get("role") != "user":
            keys.append(None)
            continue
        canonical = orjson.dumps(normalize(message), option=orjson.OPT_SORT_KEYS)
        content = hashlib.sha256(canonical).hexdigest()[:32]
        keys.append(f"{content}#{seen[content]}")
        seen[content] += 1
    return keys


def record_peers(value, request):
    """Name the peer of each user message in the history the first time it is seen.

    A session may hold several peers' messages, as a group chat does: a message keeps
    the peer of the request that first brought it, None when that request had no peer,
    and later requests leave it unchanged. The map covers the user messages of the
    current history, so a turn saved late still names the peers of its messages.
    User messages after the request's last one, such as text sent beside a tool
    result, are not mapped and carry no peer.
    """
    peers = value.get("peers") or {}
    stop = request.anchor + 1
    keys = peer_keys(request.messages[:stop], request.capture_chain[:stop])
    recorded = {key: peers.get(key, request.peer) for key in keys if key}
    return {**value, "peers": recorded}


def peer_anchors(peers, messages, chain):
    """The recorded peer of each user message, keyed by its anchor."""
    if not peers:
        return {}
    return {
        anchor: peers[key]
        for anchor, key in zip(chain, peer_keys(messages, chain), strict=True)
        if key and peers.get(key)
    }


def with_peers(captured, peers):
    """Captured messages with the user's naming their peer, by anchor; an assistant has none."""
    if not peers:
        return captured
    return [
        {**m, "peer_id": peer}
        if m["role"] == "user"
        and (peer := peers.get(m["source_message_ids"][0].removeprefix(SOURCE)))
        else m
        for m in captured
    ]


def capture_messages(messages, chain):
    """Keep complete tool pairs, using the server's ToolPart representation."""
    results = {}
    for message in messages:
        if message.get("role") == "tool":
            results[message.get("tool_call_id")] = message.get("content", "")
        if string_type(message) in {"function_call_output", "custom_tool_call_output"}:
            results[message.get("call_id")] = message.get("output", "")
        for block in message.get("content", []) if isinstance(message.get("content"), list) else []:
            if isinstance(block, dict) and block.get("type") == "tool_result":
                results[block.get("tool_use_id")] = block.get("content", "")
    output = []
    for message, anchor in zip(messages, chain, strict=True):
        if message.get("role") not in {"user", "assistant"} and string_type(message) not in {
            "function_call",
            "custom_tool_call",
        }:
            continue
        text = text_content(message)
        if message.get("role") == "assistant":
            # Tool and recall notices are the gateway's, not the model's. Replies are
            # recorded without the recall notice already; this also catches merged text.
            text = without_notices(text)
        text = clean_text(unwrap_client(text))
        parts = [{"type": "text", "text": text}] if text else []
        calls = list(message.get("tool_calls") or [])
        if string_type(message) in {"function_call", "custom_tool_call"}:
            calls.append(message)
        calls.extend(
            b
            for b in message.get("content", [])
            if isinstance(b, dict) and b.get("type") == "tool_use"
        ) if isinstance(message.get("content"), list) else None
        for call in calls:
            identifier = call.get("call_id", call.get("id"))
            if identifier not in results:
                continue
            function = call.get("function", call)
            arguments = function.get("arguments", call.get("input", {}))
            if isinstance(arguments, str):
                try:
                    arguments = orjson.loads(arguments)
                except ValueError:
                    arguments = {"raw": arguments}
            result = results[identifier]
            parts.append(
                {
                    "type": "tool",
                    "tool_id": identifier,
                    "tool_name": function.get("name", "tool"),
                    "tool_input": arguments,
                    "tool_status": "completed",
                    "tool_output": clean_text(
                        result if isinstance(result, str) else orjson.dumps(result).decode()
                    ),
                }
            )
        if parts:
            output.append(
                {
                    "role": message.get("role", "assistant"),
                    "parts": parts,
                    "source_message_ids": [SOURCE + anchor],
                }
            )
    return output

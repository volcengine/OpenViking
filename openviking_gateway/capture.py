# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""One capture document per client session; the mailbox is its only authority.

Requests replace the unconfirmed tail, and a gateway cut confirms the history
before it at once. The sole lease holder advances delivery and archives. A
changed confirmed prefix starts a new OpenViking session that remembers the
earlier ones. Replay records are independent: a failed capture never
invalidates an old replacement.
"""

import asyncio
import copy
import logging
import time
import uuid
from itertools import pairwise

import async_timeout
import orjson

from .archives import TERMINAL, observe_archive
from .capture_store import Document, LeaseLost
from .models import Policy
from .notices import without_notices
from .protocols import clean_text, is_user, text_content, unwrap_client
from .records import RecordKind as K
from .tool_protocols import hidden_chain

logger = logging.getLogger(__name__)
MAX_ATTEMPTS = 5
RECOVERY_SECONDS = 300
MAX_PREVIOUS = 5
SOURCE = "gateway:"


def new_capture(reason="", old=None):
    return {
        "ov_session": "gateway-" + uuid.uuid4().hex,
        "delivered": "",
        "pending": [],
        "retained": [],
        "tokens": 0,
        "archive": None,
        "error": {},
        "idle": False,
        "reason": reason,
        "previous": lineage(old or {})[:MAX_PREVIOUS],
    }


def lineage(value):
    """The OpenViking sessions holding this history's saved messages, newest first."""
    previous = value.get("previous", [])
    return [value["ov_session"], *previous] if value.get("delivered") else list(previous)


def ready_at(state):
    if state.get("error"):
        return state["error"]["retry_at"]
    archive = state.get("archive")
    times = [turn["ready"] for turn in state.get("pending", [])[:1]]
    if archive and archive["status"] not in TERMINAL:
        times.append(archive.get("next_check", 0))
    return min(times) if times else None


async def reset_capture(queue, scope, session, reason):
    while True:
        old = await queue.get(scope, session)
        value = new_capture(reason, old.value)
        if await queue.swap(scope, session, old, value, None):
            return value


class CapturePipeline:
    def __init__(self, queue):
        self.queue = queue

    @staticmethod
    def turns(messages, chain, start, stop, confirmed, idle_seconds):
        """Split messages[start:stop] at real user messages into delivery segments.

        A segment that does not start at a user message continues the turn
        before it. Its anchor is its last captured message, so the delivered
        position always has a prefix anchor and a source ID.
        """
        bounds = [start, *(i for i in range(start + 1, stop) if is_user(messages[i])), stop]
        turns = []
        for begin, end in pairwise(bounds):
            captured = capture_messages(messages[begin:end], chain[begin:end])
            if captured:
                turns.append(
                    {
                        "anchor": captured[-1]["source_message_ids"][0].removeprefix(SOURCE),
                        "messages": captured,
                        "continued": not is_user(messages[begin]),
                        "confirmed": confirmed,
                        "ready": 0 if confirmed else time.time() + idle_seconds,
                    }
                )
        return turns

    @classmethod
    def advance(cls, value, messages, chain, user, stop, confirmed, idle_seconds):
        """Queue messages[:stop] after the delivered and confirmed part of a capture.

        Everything before the user message at ``user`` is confirmed. Confirmed
        content missing from this history belongs to another branch, and a tail
        delivered while idle may lack its unanswered tool calls, so either one
        starts a new OpenViking session that receives the whole history again.
        Returns the new value and the segments it added.
        """
        positions = {a: i for i, a in enumerate(chain) if a}
        held = [t for t in value["pending"] if t["confirmed"]]
        ends = [a for a in (value["delivered"], *(t["anchor"] for t in held)) if a]
        reason = ""
        if any(a not in positions for a in ends):
            reason = "history_changed"
        elif value["delivered"] and value.get("idle") and positions[value["delivered"]] >= user:
            reason = "continued_after_idle"
        if reason:
            value, held, ends = {**value, **new_capture(reason, value)}, [], []
        start = 1 + max((positions[a] for a in ends), default=-1)
        split = min(max(start, user), stop)
        added = [
            *cls.turns(messages, chain, start, split, True, 0),
            *cls.turns(messages, chain, split, stop, confirmed, idle_seconds),
        ]
        return {**value, "pending": [*held, *added]}, added

    async def confirm(self, request, credential, policy):
        old = request.capture
        positions = set(request.capture_chain)
        user_anchor = request.capture_chain[request.anchor]
        while True:
            value = old.value or new_capture()
            # The worker may be writing a due tail, so a changed one starts over here;
            # advance() drops any other unconfirmed tail.
            now = time.time()
            if any(t["anchor"] not in positions for t in value["pending"] if t["ready"] <= now):
                value = new_capture("history_changed", value)
            value, _ = await asyncio.to_thread(
                self.advance,
                value,
                request.messages,
                request.capture_chain,
                request.anchor,
                request.anchor,
                True,
                0,
            )
            # Reusing a prepared prompt does not generate another queue write.
            value.update(
                request_anchor=user_anchor,
                credential_id=credential["id"],
                account=credential["account"],
                protocol=request.protocol,
                policy=policy.model_dump(),
            )
            if value == old.value or await self.queue.swap(
                request.scope, request.session, old, value, ready_at(value)
            ):
                request.capture = Document(value, old.version + (value != old.value))
                request.capture_target = value["ov_session"]
                self.metrics(request, policy)
                return
            old = await self.queue.get(request.scope, request.session)

    async def confirm_cut(self, request, anchor):
        """Queue the history up to a gateway cut as confirmed and ready at once.

        The model may search the saved session right after the cut, so delivery
        does not wait for the turn to end. confirm() covers cuts before a user
        message; this is for cuts inside a turn.
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
        old = request.capture
        while True:
            if (
                old.value.get("ov_session") != request.capture_target
                or old.value.get("request_anchor") != chain[request.anchor]
            ):
                return  # A newer request or explicit reset owns the capture document.
            value, added = await asyncio.to_thread(
                self.advance,
                old.value,
                request.messages,
                chain,
                request.anchor,
                chain.index(anchor) + 1,
                True,
                0,
            )
            if not added:
                return  # Everything up to the cut is already delivered or queued.
            if await self.queue.swap(request.scope, request.session, old, value, ready_at(value)):
                request.capture = Document(value, old.version + 1)
                request.capture_target = value["ov_session"]
                return
            old = await self.queue.get(request.scope, request.session)

    @staticmethod
    def metrics(request, policy):
        value = request.capture.value
        error = value.get("error", {})
        request.metrics.update(
            capture_status=("paused" if error["attempts"] >= MAX_ATTEMPTS else "retrying")
            if error
            else ("active" if policy.capture else "disabled"),
            capture_reason=error.get("reason", value.get("reason", "")),
            capture_retry_at=error.get("retry_at"),
        )

    async def stage(self, request, response, policy):
        messages = [*request.messages, *response.output]
        chain = await asyncio.to_thread(hidden_chain, messages, request.protocol)
        while True:
            old = await self.queue.get(request.scope, request.session)
            if (
                old.value.get("ov_session") != request.capture_target
                or old.value.get("request_anchor") != request.capture_chain[request.anchor]
            ):
                return  # A newer request or explicit reset superseded this response.
            # Tool continuations replace the unconfirmed tail in place, after
            # anything a cut inside this turn already confirmed.
            value, added = await asyncio.to_thread(
                self.advance,
                old.value,
                messages,
                chain,
                request.anchor,
                len(messages),
                False,
                policy.idle_seconds,
            )
            if not added:
                return
            if await self.queue.swap(request.scope, request.session, old, value, ready_at(value)):
                return


class BranchChanged(Exception):
    pass


class CaptureWorker:
    def __init__(self, store, management, viking):
        self.queue, self.replay = store.capture, store.replay
        self.management, self.viking = management, viking

    async def save(self, item, previous, value, written=None):
        """Merge worker-owned progress into a concurrently updated request tail.

        ``written`` is the delivered message when this save moves ``delivered``.
        """
        while True:
            if await self.queue.swap(
                item["scope"],
                item["session"],
                previous,
                value,
                ready_at(value),
                owner=item["owner"],
            ):
                return Document(value, previous.version + 1)
            fresh = await self.queue.get(item["scope"], item["session"])
            if fresh.value.get("ov_session") != value["ov_session"]:
                raise BranchChanged
            # The writer may only advance through the queued messages it read.
            pending = fresh.value["pending"]
            if value["delivered"] != previous.value["delivered"]:
                pending = remaining(pending, written)
                if pending is None:
                    # An idle reply was edited, or gained its tool results, while its
                    # HTTP write was in flight.
                    await reset_capture(
                        self.queue,
                        item["scope"],
                        item["session"],
                        "history_changed_during_delivery",
                    )
                    raise BranchChanged
            value = {
                **fresh.value,
                **{
                    key: value[key]
                    for key in ("delivered", "retained", "tokens", "archive", "error", "idle")
                    if key in value
                },
                "pending": pending,
            }
            previous = fresh

    async def once(self):
        item = await self.queue.claim()
        if item is None:
            return False
        try:
            async with async_timeout.timeout(100):
                await self.deliver(item)
        except (BranchChanged, LeaseLost):
            pass
        except Exception as error:
            old = await self.queue.get(item["scope"], item["session"])
            if old.value.get("ov_session") == item["document"].value.get("ov_session"):
                previous = old.value.get("error", {})
                attempts = previous.get("attempts", 0) + 1
                reason = getattr(error, "reason", "delivery_failed")
                failure = {
                    "attempts": attempts,
                    "reason": reason,
                    "retry_at": time.time()
                    + (RECOVERY_SECONDS if attempts >= MAX_ATTEMPTS else 10 * 2 ** (attempts - 1)),
                }
                await self.save(item, old, {**old.value, "error": failure})
                # Record status changes only: a paused capture retries forever.
                if attempts in (1, MAX_ATTEMPTS) or reason != previous.get("reason"):
                    status = "paused" if attempts >= MAX_ATTEMPTS else "retrying"
                    await self.log(item, old.value, status, reason)
                    logger.warning("Capture %s: %s (attempt %s)", item["session"], reason, attempts)
                else:
                    logger.debug("Capture %s: %s (attempt %s)", item["session"], reason, attempts)
        finally:
            await self.queue.release(item)
        return True

    async def log(self, item, state, status, reason):
        await self.management.log(
            state["account"],
            {
                "kind": "capture",
                "session": item["session"],
                "protocol": state["protocol"],
                "credential_id": state["credential_id"],
                "capture_status": status,
                "capture_reason": reason,
            },
        )

    async def deliver(self, item):
        doc = await self.queue.get(item["scope"], item["session"])
        state = copy.deepcopy(doc.value)
        if state.get("ov_session") != item["document"].value.get("ov_session"):
            return
        disabled = await self.replay.read(item["scope"], item["session"], [""])
        key = await self.management.get(state["account"], "keys", state["credential_id"])
        if (K.DISABLED, "") in disabled or not key:
            state["pending"] = []
            state["archive"] = None
            state["error"] = {}
            await self.save(item, doc, state)
            return
        token, session = key["openviking_key"], state["ov_session"]
        policy = Policy.model_validate(state["policy"])
        if state.get("error"):
            await self.viking.health(token)
        await self.viking.create_session(token, session)
        # A write or commit whose response is lost is sent again; the rare
        # duplicate in OpenViking is accepted.
        while state["pending"] and state["pending"][0]["ready"] <= time.time():
            turn = state["pending"][0]
            for offset in range(0, len(turn["messages"]), 100):
                await self.viking.write(token, session, turn["messages"][offset : offset + 100])
            count = len(turn["messages"])
            if turn.get("continued") and state["retained"]:
                # Commits keep whole turns, so a continuation joins its turn.
                count += state["retained"].pop()["count"]
            state["retained"].append({"anchor": turn["anchor"], "count": count})
            state["tokens"] += sum(
                (len(orjson.dumps(m["parts"])) + 2) // 3 for m in turn["messages"]
            )
            state["delivered"] = turn["anchor"]
            state["pending"] = state["pending"][1:]
            state["idle"] = not turn["confirmed"]
            doc = await self.save(item, doc, state, turn["messages"][-1])
            state = copy.deepcopy(doc.value)
        # Each save may merge a concurrent request, so continue from what it stored.
        if state.get("archive") and state["archive"]["status"] not in TERMINAL:
            state["archive"] = await observe_archive(self.viking, state["archive"], token)
            doc = await self.save(item, doc, state)
            state = copy.deepcopy(doc.value)
        if state.get("error"):
            state["error"] = {}
            doc = await self.save(item, doc, state)
            state = copy.deepcopy(doc.value)
            await self.log(item, state, "active", "delivery_recovered")
        if state.get("archive") and state["archive"]["status"] not in TERMINAL:
            return
        pending = await self.viking.pending_tokens(token, session)
        if pending < policy.commit_tokens and not state.get("idle"):
            return
        retained, keep = 0, 0
        if not state.get("idle"):
            for turn in reversed(state["retained"]):
                if keep >= policy.keep_recent_messages:
                    break
                retained += 1
                keep += turn["count"]
        archived = state["retained"][: len(state["retained"]) - retained]
        if not archived:
            return
        uri = (await self.viking.commit(token, session, keep)).get("archive_uri")
        archive = {"status": "pending", "archive_uri": uri, "created": time.time()}
        state["archive"] = archive if uri else None
        state["retained"] = state["retained"][len(archived) :]
        await self.save(item, doc, state)


def remaining(pending, written):
    """The queue after a delivered message, trimming a segment re-queued around it.

    None when the queue no longer holds the message exactly as it was written.
    """
    for index, turn in enumerate(pending):
        if written in turn["messages"]:
            rest = turn["messages"][turn["messages"].index(written) + 1 :]
            head = [{**turn, "messages": rest, "continued": True}] if rest else []
            return head + pending[index + 1 :]
    return None


def capture_messages(messages, chain):
    """Keep complete tool pairs, using the server's ToolPart representation."""
    results = {}
    for message in messages:
        if message.get("role") == "tool":
            results[message.get("tool_call_id")] = message.get("content", "")
        if message.get("type") in {"function_call_output", "custom_tool_call_output"}:
            results[message.get("call_id")] = message.get("output", "")
        for block in message.get("content", []) if isinstance(message.get("content"), list) else []:
            if isinstance(block, dict) and block.get("type") == "tool_result":
                results[block.get("tool_use_id")] = block.get("content", "")
    output = []
    for message, anchor in zip(messages, chain, strict=True):
        if message.get("role") not in {"user", "assistant"} and message.get("type") not in {
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
        if message.get("type") in {"function_call", "custom_tool_call"}:
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

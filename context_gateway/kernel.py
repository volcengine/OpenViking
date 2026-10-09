# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Framework-independent recall, immutable replay, compaction and capture orchestration."""

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field, replace

import orjson

from .blocks import block, gateway_note, history_hint, token_estimate
from .capture import CapturePipeline, lineage
from .capture_store import Document
from .client import VikingError
from .compaction import (
    BACKOFF_SECONDS,
    active_cut,
    apply_cut,
    cut_hint,
    cut_point,
    estimate,
    opening_block,
    replacement_text,
    summary_instruction,
    window_size,
)
from .models import Policy
from .notices import recall_notice
from .profile import build_profile
from .protocols import (
    classify,
    clean_text,
    is_user,
    plugin_present,
    prefix_chain,
    replays_reasoning,
    session_id,
    strip_thinking,
    text_content,
    unwrap_client,
)
from .records import RecordKind as K
from .state_store import get_state
from .storage import KernelStore, digest
from .tool_catalog import reply_block_reason, select_tools, tool_block_reason
from .tool_protocols import hidden_chain, replay_hidden, tool_protocol
from .tool_protocols.common import SummaryError
from .vendors import parameter_fingerprint
from .windows import remind, status_line

logger = logging.getLogger(__name__)
# State keys for unsigned thinking the gateway relayed, by digest of its text.
THINKING = "thinking:"


@dataclass
class Prepared:
    body: dict
    original: dict
    protocol: str
    scope: str
    session: str
    chain: list[str]
    messages: list[dict]
    kind: str
    anchor: int
    root: dict
    records: dict
    disabled: bool = False
    metrics: dict = field(default_factory=dict)
    body_chain: list[str] = field(default_factory=list)
    capture_chain: list[str] = field(default_factory=list)
    strip_replayed_thinking: bool = False
    anonymous: bool = False
    capture: Document = field(default_factory=Document)
    observation: Document = field(default_factory=Document)
    capture_target: str = ""
    tools_active: bool = False
    # Definitions and hidden history replay, but the tool loop refuses every gateway call.
    tools_closed: bool = False
    hidden_history_unavailable: bool = False
    upstream: dict = field(default_factory=dict)
    # The estimated current context and the window it is measured against.
    context_tokens: int = 0
    context_window: int = 0
    # Sessions and thinking digests this request read; it keeps them from expiring.
    ancestors: list[str] = field(default_factory=list)
    thinking: list[str] = field(default_factory=list)
    # Anchors of replies whose thinking the client resent as text.
    thinking_as_text: set[str] = field(default_factory=set)
    # The reply's anchor; ``relayed`` records it once it succeeds.
    relayed: bool = False
    reply_anchor: str = ""
    # What the reply starts with for the user: the recall notice of this turn's injection.
    reply_lead: str = ""


class MemoryKernel:
    def __init__(self, store: KernelStore, viking):
        self.store, self.viking = store, viking

    @staticmethod
    def degraded_body(body, protocol):
        adapter = tool_protocol(protocol)
        if adapter.signed_thinking:
            return {**body, adapter.field: strip_thinking(body.get(adapter.field, []))}
        return body

    async def enhance(self, body, protocol, *args):
        try:
            prepared = await self.prepare(body, protocol, *args)
            return prepared.body, prepared, prepared.metrics
        except Exception:
            logger.exception("Context Gateway preparation failed")
            return self.degraded_body(body, protocol), None, {"degradation": "memory_store_failure"}

    async def sent_history(self, scope, messages, protocol):
        """The history as the gateway relayed it: thinking a client resent as text is dropped.

        Some providers return thinking without a signature, and clients such as pi
        resend it as a text block. Anchors drop thinking, so that text would keep
        every reply from matching its anchors in the next request.
        """
        adapter = tool_protocol(protocol)
        texts = {
            id(b): THINKING + digest(b["text"])
            for m in messages
            for b in adapter.thinking_as_text(m)
        }
        known = await self.store.state.read(scope, list(set(texts.values()))) if texts else {}
        if not known:
            return messages, []
        dropped = {block for block, key in texts.items() if key in known}
        return [
            {**m, "content": [b for b in m["content"] if id(b) not in dropped]}
            if adapter.thinking_as_text(m)
            else m
            for m in messages
        ], sorted(known)

    async def identity(self, scope, messages, protocol, headers):
        """Resolve the session and the sessions whose replies the history contains.

        Replies are matched in the chain flavour ``completed`` records them in. A
        request without a session header continues the session that produced the
        latest reply it contains, or starts a new one when two sessions produced it.
        """
        sid = session_id(headers)
        adapter = tool_protocol(protocol)

        def chains():
            chain = prefix_chain(messages)
            if not adapter.canonicalizes_history:
                return chain, chain
            return chain, hidden_chain(messages, protocol)

        # Small prompts need no executor; large payloads must not block the loop.
        if len(messages) > 32 or any(len(m.get("content") or "") > 8192 for m in messages):
            chain, body_chain = await asyncio.to_thread(chains)
        else:
            chain, body_chain = chains()
        endpoints = [
            a for a, m in zip(body_chain, messages, strict=True) if a and adapter.is_reply(m)
        ]
        owners = await self.store.replay.replies(scope, endpoints) if endpoints else {}
        anonymous = sid is None
        if anonymous:
            latest = next((owners[a] for a in reversed(endpoints) if a in owners), set())
            sid = next(iter(latest)) if len(latest) == 1 else "anonymous-" + uuid.uuid4().hex
        # A reply two sessions produced, such as a greeting, proves no inheritance.
        ancestors = sorted({next(iter(o)) for o in owners.values() if len(o) == 1} - {sid})
        return sid, anonymous, chain, body_chain, ancestors

    async def prepare(
        self,
        body,
        protocol,
        headers,
        credential,
        upstream,
        policy,
        counting=False,
        upstreams=(),
        summarize=None,
    ):
        """Build the upstream body; ``summarize(prepared, body)`` sends a summary request."""
        adapter = tool_protocol(protocol)
        sent = adapter.messages(body)
        if not isinstance(sent, list) or not all(isinstance(m, dict) for m in sent):
            raise ValueError("invalid message list")
        # The lead is the gateway's, not the model's: the client resends it, but nothing
        # matches, captures or forwards it, as replies were recorded without it.
        sent = adapter.strip_lead(sent)
        scope = digest(credential["account"] + "\0" + credential["user_id"] + "\0" + protocol)
        # Anchors, capture and recall read the history as the gateway relayed it;
        # the upstream still gets exactly what the client sent.
        messages, thinking = await self.sent_history(scope, sent, protocol)
        sid, anonymous, chain, body_chain, ancestors = await self.identity(
            scope, messages, protocol, headers
        )
        as_text = (
            {a for a, m, s in zip(body_chain, messages, sent, strict=True) if m != s}
            if thinking
            else set()
        )
        records, observations, capture = await self.store.load(
            scope, sid, list(dict.fromkeys(["", *chain, *body_chain])), ancestors
        )
        plugin = plugin_present(body, headers)
        root = await self.session_root(
            records, scope, sid, body, protocol, upstream, policy, credential, plugin
        )
        upstream = next((u for u in upstreams if u["id"] == root["upstream_id"]), upstream)
        policy = Policy.model_validate(root["policy"])
        kind, anchor = classify(body, headers, messages)
        disabled = (K.DISABLED, "") in records
        if plugin and not disabled:
            await self.store.replay.put(scope, sid, K.DISABLED, "", {"reason": "plugin_present"})
            disabled = True
        # A token count gets gateway tools exactly when the request it measures would.
        owner = not disabled and kind in {"user", "continuation"}
        kind = "count" if counting else kind
        result = {**body, adapter.field: [dict(m) for m in sent]}
        prepared = Prepared(
            result,
            body,
            protocol,
            scope,
            sid,
            chain,
            messages,
            kind,
            anchor,
            root,
            records,
            disabled=disabled,
            anonymous=anonymous,
            body_chain=body_chain,
            capture_chain=body_chain,
            capture=capture,
            observation=observations.get(sid, Document()),
            capture_target=capture.value.get("ov_session", ""),
            upstream=upstream,
            ancestors=ancestors,
            thinking=thinking,
            thinking_as_text=as_text,
            metrics={
                "kind": kind,
                "replay_hits": 0,
                "recall_count": 0,
                "recall_ms": 0,
                "session": sid,
                "protocol": protocol,
                "credential_id": credential["id"],
            },
        )
        CapturePipeline.metrics(prepared, policy)
        # Replay first: it decides whether replies keep their reasoning, which tools depend on.
        self.replay(prepared)
        self.configure_tools(prepared, policy, owner)
        if not disabled and policy.capture and kind == "user" and anchor >= 0:
            await CapturePipeline(self.store.capture).confirm(prepared, credential, policy)
        if kind in {"user", "continuation"}:
            prepared.context_window = window_size(prepared, policy)
            prepared.context_tokens = await self.measure(prepared)
            prepared.metrics.update(
                context_tokens=prepared.context_tokens, context_window=prepared.context_window
            )
            if (
                summarize
                and policy.compaction
                and not disabled
                and prepared.context_tokens >= policy.compaction_threshold * prepared.context_window
            ):
                await self.compact(prepared, credential, policy, summarize)
        # Recall and window signals come after any new cut, so they describe the
        # context the model actually gets.
        if not disabled:
            if kind == "user" and anchor >= 0 and (K.INJECTION, chain[anchor]) not in records:
                await self.recall(prepared, credential, policy)
            await remind(self.store, prepared, policy)
        if (
            not disabled
            and kind == "user"
            and anchor >= 0
            and policy.show_recall
            and not reply_block_reason(body)
        ):
            # A retry of the turn shows the same notice: it is part of the immutable decision.
            decision = prepared.records.get((K.INJECTION, chain[anchor]), {})
            prepared.reply_lead = decision.get("notice", "")
        self.assemble(prepared)
        if isinstance(body.get("input"), str) and result.get("input") == sent:
            result["input"] = body["input"]
        return prepared

    async def session_root(
        self, records, scope, sid, body, protocol, upstream, policy, credential, plugin
    ):
        root = records.get((K.ROOT, ""))
        if root is None:
            # The tool list is frozen here, so a failed load leaves the session without tools.
            tools, reason = [], ""
            if (
                not plugin
                and policy.get("gateway_tools")
                and not tool_block_reason(body, protocol, upstream)
            ):
                try:
                    catalog = await self.viking.tools(credential["openviking_key"])
                    tools = select_tools(catalog, policy)
                except VikingError:
                    reason = "tools_unavailable"
            root = await self.store.replay.put(
                scope,
                sid,
                K.ROOT,
                "",
                {
                    "upstream_id": upstream["id"],
                    "tools": tools,
                    "tool_skip_reason": reason,
                    "policy": policy,
                    "credential_id": credential["id"],
                    "vendor": {
                        "prompt_cache_key": body.get("prompt_cache_key")
                        or "ovcg-" + digest(scope + sid)[:40],
                        "parameters": parameter_fingerprint(body),
                    },
                },
            )
        return root

    @staticmethod
    def configure_tools(request, policy, owner):
        tools = request.root["tools"]
        request.metrics["tools_tokens"] = 0
        adapter = tool_protocol(request.protocol)
        if tools:
            # Assemble restores no reasoning here, and thinking resent as text stays text.
            restores = not (
                request.disabled or request.strip_replayed_thinking or request.thinking_as_text
            )
            reason = tool_block_reason(
                request.original, request.protocol, request.upstream, restores
            )
            names = {t["function"]["name"] for t in tools}
            collision = any(
                t.get("function", t).get("name") in names for t in request.original.get("tools", [])
            )
            hidden = any((K.HIDDEN, anchor) in request.records for anchor in request.body_chain)
            if reason or collision:
                request.metrics["tool_skip_reason"] = reason or "tool_name_collision"
                request.hidden_history_unavailable = hidden
            elif owner or hidden:
                # Only the conversation's own turns get gateway tools. Others that resend
                # history using them still replay it byte for byte, for provider caches and
                # thinking signatures, but the tool loop refuses their calls.
                adapter.add_tools(request.body, tools)
                request.tools_active, request.tools_closed = True, not owner
                request.metrics["tools_tokens"] = token_estimate(
                    orjson.dumps(adapter.wire_tools(tools)).decode()
                )
        elif policy.gateway_tools:
            request.metrics["tool_skip_reason"] = (
                tool_block_reason(request.original, request.protocol, request.upstream)
                or request.root["tool_skip_reason"]
                or "tools_not_selected_at_session_start"
            )

    @staticmethod
    def replay(request):
        adapter = tool_protocol(request.protocol)
        messages = request.body[adapter.field]
        missing = False
        sent = set(request.observation.value.get("sent", []))
        # A cut removes the messages up to it, together with any lost injection.
        cut = active_cut(request)
        for index, anchor in enumerate(request.chain):
            decision = request.records.get((K.INJECTION, anchor))
            if decision is not None:
                if decision["text"]:
                    adapter.append_context(messages[index], decision["text"])
                request.metrics["replay_hits"] += 1
            elif anchor in sent and index > cut:
                missing = True
        reason = ""
        if request.upstream["id"] != request.root["upstream_id"]:
            reason = "upstream_changed"
        elif missing and adapter.signed_thinking:
            reason = "missing_injection_record"
        if reason:
            request.body[adapter.field] = strip_thinking(messages)
            request.strip_replayed_thinking = True
            request.metrics["degradation"] = reason
        if request.disabled:
            request.metrics["degradation"] = "plugin_present"

    async def recall(self, request, credential, policy):
        started = time.monotonic()
        existing = [
            (index, request.records[K.INJECTION, anchor])
            for index, anchor in enumerate(request.chain)
            if (K.INJECTION, anchor) in request.records
        ]
        # The budget is per context window: a cut frees the recall it replaced.
        cut = active_cut(request)
        current = [value for index, value in existing if index > cut]
        used = sum(v.get("tokens", 0) for v in current)
        exclude = list(dict.fromkeys(u for v in current for u in v.get("uris", [])))
        budget = min(policy.max_tokens, policy.session_max_tokens - used)
        query = clean_text(unwrap_client(text_content(request.messages[request.anchor])))[
            : policy.query_max_chars
        ]
        decision = {"text": "", "uris": [], "tokens": 0, "reason": "disabled"}
        reserved = False
        if policy.recall and budget >= 64 and len(query) >= 3:
            # The window starts at the cut, or at the history's first user message.
            if cut >= 0:
                window = request.capture_chain[cut]
            else:
                window = next(
                    a
                    for m, a in zip(request.messages, request.capture_chain, strict=True)
                    if is_user(m)
                )
            budget = await self.reserve_recall(request, policy, used, window)
            reserved = budget > 0
        tools = request.root["tools"]
        lead = "Relevant memory from OpenViking."
        if any(tool["function"]["name"] == "openviking_read" for tool in tools):
            lead += " Use the openviking_read tool to expand URIs."
        overhead = token_estimate(block("gateway-recall", lead + "\n"))
        # The search entries a recall returned, for the notice.
        entries = []

        async def retrieve():
            if not reserved:
                return decision
            if budget - overhead < 64:
                return {**decision, "reason": "budget"}
            try:
                response = await self.viking.recall(
                    credential["openviking_key"], query, policy, exclude, budget - overhead
                )
                rendered = response.get("rendered") or ""
                text = lead + "\n" + rendered if rendered else ""
                if text:
                    entries.extend(e for e in response.get("entries", []) if isinstance(e, dict))
                return {
                    "text": text,
                    "uris": [
                        entry["uri"] for entry in response.get("entries", []) if entry.get("uri")
                    ]
                    if text
                    else [],
                    "tokens": token_estimate(block("gateway-recall", text)),
                    "reason": "recalled" if text else "empty",
                }
            except (VikingError, asyncio.TimeoutError) as error:
                return {**decision, "reason": getattr(error, "reason", "recall_timeout")}

        async def opening():
            if existing:
                return "", []
            profile = await build_profile(self.viking, credential["openviking_key"], policy, tools)
            request.metrics["profile_reason"] = profile["reason"]
            # After client-side compaction, the capture document still names the earlier sessions.
            hint = history_hint(
                credential["user_id"], lineage(request.capture.value), tools, policy.capture
            )
            text = block(
                "gateway-session-start",
                "\n\n".join(
                    part for part in (gateway_note(policy, tools), hint, profile["text"]) if part
                ),
            )
            return text, [*profile["parts"], *(["history"] if hint else [])]

        decision, (start, parts) = await asyncio.gather(retrieve(), opening())
        status, reminder = status_line(request, policy)
        recalled = block("gateway-recall", "\n\n".join(p for p in (decision["text"], status) if p))
        # The opening block is immutable with recall but has its own budget, and
        # the window status line costs nothing.
        decision["text"] = "\n\n".join(part for part in (start, recalled) if part)
        if reminder:
            decision["reminder"] = reminder
        if policy.show_recall:
            # Replay reads only text, so the notice never changes what the model gets.
            decision["notice"] = recall_notice(parts, decision["reason"], entries)
        anchor = request.chain[request.anchor]
        decision = await self.store.replay.put(
            request.scope, request.session, K.INJECTION, anchor, decision
        )
        if reserved:
            await self.settle_recall(request, anchor, decision["tokens"])
        request.records[K.INJECTION, anchor] = decision
        if decision["text"]:
            adapter = tool_protocol(request.protocol)
            adapter.append_context(request.body[adapter.field][request.anchor], decision["text"])
        request.metrics.update(
            recall_count=len(decision["uris"]),
            recall_ms=round((time.monotonic() - started) * 1000, 2),
            recall_reason=decision["reason"],
        )

    async def reserve_recall(self, request, policy, used, window):
        """Reserve a bounded allowance before recall; only this document uses CAS.

        A crash can leave a conservative reservation, never overspend the cap.
        Competing requests for the same anchor share its reservation and the
        immutable decision; settling it twice cannot refund twice. A ledger for
        another context window starts over from the records in this one.
        """
        anchor, old = request.chain[request.anchor], request.observation
        while True:
            ledger = old.value.get("recall", {})
            if ledger.get("window") != window:
                ledger = {"window": window, "spent": used, "pending": {}}
            if anchor in ledger["pending"]:
                request.observation = old
                return ledger["pending"][anchor]
            budget = min(policy.max_tokens, policy.session_max_tokens - ledger["spent"])
            if budget < 64:
                return 0
            value = {
                **old.value,
                "recall": {
                    "window": window,
                    "spent": ledger["spent"] + budget,
                    "pending": {**ledger["pending"], anchor: budget},
                },
            }
            if await self.store.state.swap(request.scope, request.session, old, value):
                request.observation = Document(value, old.version + 1)
                return budget
            old = await get_state(self.store.state, request.scope, request.session)

    async def settle_recall(self, request, anchor, tokens):
        old = request.observation
        while anchor in old.value.get("recall", {}).get("pending", {}):
            ledger = old.value["recall"]
            pending = dict(ledger["pending"])
            reserved = pending.pop(anchor)
            value = {
                **old.value,
                "recall": {
                    **ledger,
                    "spent": ledger["spent"] - reserved + tokens,
                    "pending": pending,
                },
            }
            if await self.store.state.swap(request.scope, request.session, old, value):
                request.observation = Document(value, old.version + 1)
                return
            old = await get_state(self.store.state, request.scope, request.session)

    async def relayed(self, request, response):
        """Record what the client's next request is matched by, before the client can send it."""
        if request.relayed:
            return
        output = response.output
        for text in tool_protocol(request.protocol).unsigned_thinking(output):
            await self.store.state.swap(request.scope, THINKING + digest(text), Document(), {})
        if output and request.kind in {"user", "continuation"}:
            chain = await asyncio.to_thread(
                hidden_chain, [*request.messages, *output], request.protocol
            )
            request.reply_anchor = chain[-1]
            if response.finished and not request.disabled and replays_reasoning(request.upstream):
                await self.record_reasoning(request, output, chain)
        if response.finished and request.reply_anchor:
            # A reply that hands the turn to client tools is resent with their results.
            await self.store.replay.put(
                request.scope, request.session, K.REPLY, request.reply_anchor, {}
            )
        request.relayed = True

    async def record_reasoning(self, request, output, chain):
        """Keep the reply's reasoning under the anchors the client resends it by."""
        adapter = tool_protocol(request.protocol)
        visible, reasoning = adapter.split_reasoning(output)
        if not reasoning:
            return
        if visible is not output:
            chain = await asyncio.to_thread(
                hidden_chain, [*request.messages, *visible], request.protocol
            )
            # Without its reasoning items the reply ends at another anchor; forks find it there.
            if chain[-1]:
                await self.store.replay.put(request.scope, request.session, K.REPLY, chain[-1], {})
        tail = chain[len(request.messages) :]
        for index, value in reasoning.items():
            if tail[index]:
                await self.store.replay.put(
                    request.scope, request.session, K.REASONING, tail[index], value
                )

    async def completed(self, request, credential, response):
        await self.relayed(request, response)
        endpoint = request.reply_anchor
        chain = set(request.chain)
        sent = [
            a
            for (k, a), v in request.records.items()
            if k == K.INJECTION and v.get("text") and a in chain
        ]
        # The next request measures its context from this usage while the reply stays in it.
        # Without counts from the upstream, it estimates the whole request instead.
        usage = {
            **(response.context_usage or response.usage or {}),
            "anchor": endpoint,
            "time": time.time(),
        }

        def observe(value):
            if (
                usage.get("input_tokens")
                and endpoint
                and usage["time"] >= value.get("usage", {}).get("time", 0)
            ):
                value["usage"] = usage
            if sent:
                value["sent"] = list(dict.fromkeys([*value.get("sent", []), *sent]))
            if request.kind == "user" and usage["time"] >= value.get("user_at", 0):
                value["user_at"] = usage["time"]
            return value

        await self.update_observation(request, observe)
        if request.ancestors or request.thinking:
            await self.store.keep(request.scope, request.ancestors, request.thinking)
        if (
            request.disabled
            or request.kind not in {"user", "continuation"}
            or not response.complete
            or not response.message
            or request.anchor < 0
        ):
            return
        policy = Policy.model_validate(request.root["policy"])
        if policy.capture:
            await CapturePipeline(self.store.capture).stage(request, response, policy)

    @staticmethod
    def assemble(request):
        """Apply the latest cut, restore dropped reasoning, then expand or drop hidden history."""
        apply_cut(request)
        adapter = tool_protocol(request.protocol)
        if (
            not request.disabled
            and not request.strip_replayed_thinking
            and not (request.hidden_history_unavailable and adapter.omits_reasoning)
            and replays_reasoning(request.upstream)
        ):
            MemoryKernel.restore_reasoning(request)
        messages = request.body[adapter.field]
        if request.tools_active:
            messages = replay_hidden(
                messages, request.body_chain, request.records, request.protocol
            )
            if request.strip_replayed_thinking:
                messages = strip_thinking(messages)
        elif request.hidden_history_unavailable:
            cleaned = adapter.omit_hidden_history(messages)
            if cleaned != messages:
                messages = cleaned
                request.metrics.setdefault("degradation", "hidden_tool_history_unavailable")
        request.body[adapter.field] = messages

    @staticmethod
    def restore_reasoning(request):
        """Give each reply back the reasoning the client dropped, the same bytes every turn.

        Inserted items get no anchor, so the body chain stays aligned with the body.
        Spans a hidden transcript replaces are left alone: the transcript has the
        reasoning, and its visible count is the span as the client sent it.
        """
        adapter = tool_protocol(request.protocol)
        covered = set()
        if request.tools_active:
            for index, anchor in enumerate(request.body_chain):
                record = request.records.get((K.HIDDEN, anchor)) if anchor else None
                if record and 0 < record["visible_count"] <= index + 1:
                    covered.update(range(index + 1 - record["visible_count"], index + 1))
        messages, chain, restored = [], [], 0
        for index, (message, anchor) in enumerate(
            zip(request.body[adapter.field], request.body_chain, strict=True)
        ):
            value = request.records.get((K.REASONING, anchor)) if anchor else None
            items = [message]
            if value and anchor not in request.thinking_as_text and index not in covered:
                items = adapter.restore_reasoning(messages, message, value)
                restored += items != [message]
            messages.extend(items)
            chain.extend(["" for _ in items[1:]] + [anchor])
        request.body[adapter.field], request.body_chain = messages, chain
        if restored:
            request.metrics["reasoning_restored"] = restored

    @classmethod
    def assembled(cls, request):
        """The body the upstream would get now, without changing the request."""
        candidate = replace(request, body=dict(request.body), metrics={})
        cls.assemble(candidate)
        return candidate.body

    async def measure(self, request):
        """Estimate the context: the last reply's usage plus what the client added after it.

        Usage counts only when its reply is still in this history after the latest
        cut, so usage from subagents, other branches or before a cut is ignored.
        Otherwise the estimate covers the whole body the upstream would get.
        """
        usage = request.observation.value.get("usage", {})
        anchor, chain = usage.get("anchor"), request.capture_chain
        index = chain.index(anchor) if anchor and anchor in chain else -1
        if index > active_cut(request):
            tokens = usage.get("input_tokens", 0) + usage.get("output_tokens", 0)
            return tokens + await asyncio.to_thread(estimate, request.messages[index + 1 :])
        return await asyncio.to_thread(estimate, self.assembled(request))

    async def compact(self, request, credential, policy, summarize):
        """Replace the history before a new cut with a model-written summary.

        The summary covers only what the cut replaces, so every branch continuing these
        replies can reuse the record. Failures forward the full history and back off.
        """
        failed_at = request.observation.value.get("compaction", {}).get("failed_at", 0)
        cut = cut_point(request)
        if cut <= active_cut(request) or time.time() - failed_at < BACKOFF_SECONDS:
            return
        adapter = tool_protocol(request.protocol)
        body, kept = self.assembled(request), request.body[adapter.field][cut + 1 :]
        messages = body[adapter.field]
        span = messages[: len(messages) - len(kept)]
        # A continuation is cut in the middle of the turn, before the model answered.
        in_progress = request.kind == "continuation"
        started = time.monotonic()
        try:
            # Messages after the cut are new client input that hidden history never expands.
            if messages[len(span) :] != kept:
                raise SummaryError("cut_not_found")
            instruction = summary_instruction(policy.summary_max_tokens, in_progress)
            limit = policy.summary_max_tokens
            try:
                response = await summarize(
                    request, adapter.summary_request(body, span, instruction, limit)
                )
            except SummaryError as error:
                # Models with a small output limit reject the headroom; ask for the bare cap.
                if error.reason != "summary_http_400":
                    raise
                response = await summarize(
                    request, adapter.summary_request(body, span, instruction, limit, headroom=0)
                )
            summary = adapter.summary_text(response).strip()
            if not summary:
                raise SummaryError("summary_empty")
        except SummaryError as error:
            await self.compaction_failed(request, error.reason)
            return
        except Exception:
            logger.exception("Context Gateway summary failed")
            await self.compaction_failed(request, "summary_failed")
            return
        anchor = request.capture_chain[cut]
        if policy.capture and in_progress:
            # First, so the hint names the session that receives the part being cut.
            await CapturePipeline(self.store.capture).confirm_cut(request, anchor)
        hint = cut_hint(request, credential["user_id"], policy)
        opening = opening_block(request.records, request.chain)
        text = replacement_text(summary, hint, opening, in_progress)
        # A concurrent request may have written this cut first; its text wins.
        record = await self.store.replay.put(
            request.scope,
            request.session,
            K.REPLACEMENT,
            anchor,
            {"source": "compaction", "text": text, "tokens": token_estimate(text)},
        )
        request.records[K.REPLACEMENT, anchor] = record
        request.metrics.update(
            compaction_tokens=record["tokens"],
            compaction_ms=round((time.monotonic() - started) * 1000, 2),
        )
        # Later readers see the compacted context; the metric keeps the estimate that triggered it.
        request.context_tokens = await self.measure(request)
        if request.context_tokens >= policy.compaction_threshold * request.context_window:
            # What a cut cannot remove fills the window, so summarizing again soon cannot help.
            await self.compaction_failed(request, "still_over_threshold")

    async def compaction_failed(self, request, reason):
        request.metrics["compaction_failed"] = reason
        failure = {"failed_at": time.time(), "reason": reason}
        await self.update_observation(request, lambda value: {**value, "compaction": failure})

    async def update_observation(self, request, change):
        """Apply ``change`` to the session's observation document, retrying on conflicts."""
        old = request.observation
        while True:
            value = change(dict(old.value))
            if value == old.value:
                return
            if await self.store.state.swap(request.scope, request.session, old, value):
                request.observation = Document(value, old.version + 1)
                return
            old = await get_state(self.store.state, request.scope, request.session)

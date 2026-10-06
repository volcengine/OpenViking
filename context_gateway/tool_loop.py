# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""One hidden-tool lifecycle shared by native model protocol adapters."""

import asyncio
import time

import async_timeout
import orjson

from .capture import CapturePipeline
from .compaction import cut_messages
from .protocols import SSEDecoder, replays_reasoning, usage_of
from .records import RecordKind as K
from .tool_catalog import notice_head
from .tool_protocols import hidden_chain, tool_protocol
from .tool_protocols.common import PREFIX, ToolLoopError, ToolRound, add_usage, notice_tail
from .windows import ALONE, NEW_CONTEXT, window_number

REFUSED = "OpenViking tools are unavailable for the rest of this request; continue without them."


def added_tokens(value):
    # Client history, schemas and images never consume the continuation budget.
    return (len(orjson.dumps(value)) + 2) // 3


class HiddenToolLoop:
    def __init__(self, prepared, executor, store, capture):
        self.prepared, self.executor, self.store, self.capture = prepared, executor, store, capture
        self.protocol = prepared.protocol
        self.adapter = tool_protocol(self.protocol)(prepared.body)
        self.body = {
            **prepared.body,
            self.adapter.field: list(self.adapter.messages(prepared.body)),
        }
        self.policy, self.allowed = prepared.root["policy"], executor.allowed
        self.deadline = time.monotonic() + self.policy.get("tool_total_seconds", 120)
        self.transcript, self.usage = [], {}
        self.final, self.hidden, self.window = None, False, None
        self.rounds, self.token_cost, self.refused = 0, 0, False
        self.round = ToolRound([], [], {}, False)

    async def read(self, response):
        adapter = self.adapter
        adapter.begin()
        if response.status >= 300:
            raise ToolLoopError(
                "Model upstream rejected a tool continuation",
                response.status,
                await response.read(),
                dict(response.headers),
            )
        if response.headers.get("content-encoding"):
            raise ToolLoopError("Compressed tool responses are unsupported")
        if not self.body.get("stream"):
            adapter.load(orjson.loads(await response.read()))
        else:
            if "text/event-stream" not in response.headers.get("content-type", ""):
                raise ToolLoopError("Expected an event stream")
            decoder, received = SSEDecoder(), 0
            async for chunk in response.content.iter_any():
                received += len(chunk)
                if received > 64 * 1024 * 1024:
                    raise ToolLoopError("Model stream exceeds tool response budget")
                for frame in decoder.feed(chunk):
                    value = decoder.data(frame)
                    if value is None:
                        continue
                    if "error" in value or value.get("type") == "error":
                        raise ToolLoopError("Model stream failed")
                    for event in adapter.event(value):
                        yield adapter.encode(event)
            if decoder.buffer.strip():
                raise ToolLoopError("Incomplete model event stream")
        self.round = adapter.end()
        response.close()

    def observe(self):
        self.transcript.extend(self.round.output)
        add_usage(self.usage, self.round.usage)
        normalized = usage_of({"usage": self.round.usage})
        if normalized["input_tokens"]:
            # The latest round measures the window best; native tools read it here.
            self.capture.context_usage = normalized
            self.prepared.context_tokens = normalized["input_tokens"] + normalized["output_tokens"]
        if self.rounds:
            self.token_cost += normalized["output_tokens"] or added_tokens(self.round.output)
            self.prepared.metrics["hidden_upstream_calls"] = self.rounds
        prefix = "hidden_upstream_" if self.rounds else "first_upstream_"
        for key, value in normalized.items():
            self.prepared.metrics[prefix + key] = self.prepared.metrics.get(prefix + key, 0) + value

    def stream(self, events):
        # Adapters update visible history either way; only streams send events.
        return [self.adapter.encode(e) for e in events] if self.body.get("stream") else []

    async def execute(self, calls):
        """Run gateway-owned calls, yielding the visible notice for each one.

        In a closed request, or once rounds or tokens run out, the calls are refused so
        the model goes on with the client's tools; calling again right after a refused
        round ends the request.
        """
        budget = self.policy.get("tool_total_tokens", 100000)
        closed = (
            self.prepared.tools_closed
            or self.adapter.tool_choice(self.body) == "none"
            or self.rounds >= self.policy.get("tool_max_rounds", 5)
            or self.token_cost >= budget
        )
        if closed and self.refused:
            raise ToolLoopError("Model exceeded the hidden tool round limit")
        self.refused = closed
        if not self.rounds:
            self.token_cost += added_tokens(calls)
        show = self.policy.get("show_tool_calls", True)
        events = self.adapter.open_notice() if show else []
        results, window = [], None
        # A new window replaces the whole context, so it cannot share a round.
        crowded = len(self.round.calls) > 1 and any(
            call["function"]["name"] == NEW_CONTEXT for call in calls
        )
        for call in calls:
            skipped = closed or self.token_cost >= budget
            if show:
                # The head streams before a slow call runs; its outcome follows.
                events.extend(self.adapter.notice("\n\n" + notice_head(call)))
                for event in self.stream(events):
                    yield event
            if skipped:
                result = {"role": "tool", "tool_call_id": call["id"], "content": REFUSED}
            elif crowded:
                result = {
                    "role": "tool",
                    "tool_call_id": call["id"],
                    "content": ALONE,
                    "failed": True,
                }
            else:
                result = await self.executor.execute(call)
            window = result.pop("cut", window)
            if show:
                events = self.adapter.notice(notice_tail(result.get("failed", False), skipped))
            results.append(result)
            self.token_cost += added_tokens(result)
        if show:
            events.extend([*self.adapter.notice("\n\n"), *self.adapter.close_notice()])
            for event in self.stream(events):
                yield event
        if window:
            self.reset(*window)
        else:
            results = self.adapter.results(results)
            self.transcript.extend(results)
            self.body[self.adapter.field].extend([*self.round.output, *results])
        self.rounds += 1
        self.hidden = True
        self.prepared.metrics.update(hidden_rounds=self.rounds, hidden_added_tokens=self.token_cost)
        if self.token_cost >= budget:
            self.prepared.metrics["tool_stop_reason"] = "token_budget"

    def reset(self, anchor, value):
        """Continue in the window the model started, cut the way the next request replays it."""
        start, chain = self.adapter.messages(self.prepared.body), self.prepared.capture_chain
        # Only system and developer messages follow the anchor in this request's body.
        index = len(start) - len(chain) + chain.index(anchor)
        self.body[self.adapter.field] = cut_messages(start, index, value["text"])
        self.transcript, self.window = [], (anchor, value)
        # Later native calls see the cut the way the next request replays it.
        self.prepared.records[K.REPLACEMENT, anchor] = value
        self.prepared.metrics.update(window=window_number(self.prepared), window_reset=True)

    async def persist(self):
        visible = self.adapter.visible
        anchor = (
            hidden_chain([*self.prepared.messages, *visible], self.protocol)[-1] if visible else ""
        )
        if self.window:
            # new_context refuses an anchor whose cut this request applies, so only a
            # concurrent request can have stored another header here first.
            await self.store.replay.put(
                self.prepared.scope, self.prepared.session, K.REPLACEMENT, *self.window
            )
        if self.hidden and anchor:
            value = {
                "messages": self.transcript,
                "visible_count": len(visible),
                "upstream_id": self.prepared.root["upstream_id"],
            }
            await self.store.replay.put(
                self.prepared.scope, self.prepared.session, K.HIDDEN, anchor, value
            )
            # A client that drops reasoning items resends the reply at another anchor,
            # which relaying recorded as the same reply; the transcript replaces it there too.
            dropped, _ = self.adapter.split_reasoning(visible)
            if dropped is not visible and replays_reasoning(self.prepared.upstream):
                other = hidden_chain([*self.prepared.messages, *dropped], self.protocol)[-1]
                if dropped and other:
                    await self.store.replay.put(
                        self.prepared.scope,
                        self.prepared.session,
                        K.HIDDEN,
                        other,
                        {**value, "visible_count": len(dropped)},
                    )
        elif self.hidden:
            self.prepared.metrics["degradation"] = "hidden_reply_without_anchor"
        if self.window:
            await CapturePipeline(self.store.capture).confirm_cut(self.prepared, self.window[0])
        self.final = self.adapter.final(self.usage)
        # The combined reply reads like any relayed one: calls left for the client
        # hand the turn off, and the continuation that ends it is captured.
        self.capture.nonstream(self.final)
        self.prepared.metrics["hidden_rounds"] = self.rounds

    def error(self, message: str) -> bytes:
        return b"".join(self.adapter.encode(event) for event in self.adapter.error(message))

    async def run(self, response, send):
        """Publish the terminal event only after the exact transcript is durable."""
        try:
            async with async_timeout.timeout_at(self.deadline):
                while True:
                    async for event in self.read(response):
                        yield event
                    self.observe()
                    owned, client = [], []
                    for call in self.round.calls:
                        name = call["function"]["name"]
                        if name.startswith(PREFIX) and name not in self.allowed:
                            raise ToolLoopError("Model called an unavailable gateway tool")
                        (owned if name in self.allowed else client).append(call)
                    if owned:
                        if not self.round.tool_stop:
                            raise ToolLoopError("Gateway tool call has an invalid stop reason")
                        async for event in self.execute(owned):
                            yield event
                    for event in self.stream(self.adapter.publish_calls(client)):
                        yield event
                    if client or not owned:
                        await self.persist()
                        if self.body.get("stream"):
                            for event in self.adapter.terminal(self.final):
                                yield self.adapter.encode(event)
                        return
                    response = await send(self.body, max(0.01, self.deadline - time.monotonic()))
        except (ValueError, TypeError, KeyError, IndexError) as error:
            raise ToolLoopError("Invalid model tool response") from error
        except asyncio.TimeoutError as error:
            raise ToolLoopError("Hidden tool request timed out", 504) from error
        finally:
            response.close()

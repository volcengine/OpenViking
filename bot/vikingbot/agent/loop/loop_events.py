"""Provider streaming, memory events, and response analytics."""

from __future__ import annotations

import json
from contextlib import aclosing
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from vikingbot.bus.events import InboundMessage, OutboundEventType, OutboundMessage
from vikingbot.config.schema import SessionKey
from vikingbot.heartbeat.service import HEARTBEAT_METADATA_KEY
from vikingbot.integrations.langfuse import LangfuseClient
from vikingbot.observability.outcome import evaluate_response_outcome, should_update_outcome
from vikingbot.session.manager import Session
from vikingbot.utils.helpers import cal_str_tokens


@dataclass(frozen=True)
class _ResponseOutcomeUpdate:
    previous: dict[str, Any] | None
    payload: dict[str, Any]


class LoopEventMixin:
    """Provider streaming, memory events, and response analytics. State is owned by AgentLoop."""

    async def _publish_auto_memory_context(
        self,
        session_key: SessionKey,
        query: str,
        result: str,
        *,
        publish_events: bool = True,
    ) -> dict[str, Any]:
        """Expose automatic OpenViking memory lookup using the existing tool event stream."""
        args_str = json.dumps({"query": query}, ensure_ascii=False)
        if publish_events:
            await self.bus.publish_outbound(
                OutboundMessage(
                    session_key=session_key,
                    content=f"auto_memory_search({args_str})",
                    event_type=OutboundEventType.TOOL_CALL,
                )
            )
            await self.bus.publish_outbound(
                OutboundMessage(
                    session_key=session_key,
                    content=result,
                    event_type=OutboundEventType.TOOL_RESULT,
                )
            )
        return {
            "tool_name": "auto_memory_search",
            "args": args_str,
            "result": result,
            "duration": 0,
            "execute_success": True,
            "input_token": cal_str_tokens(query, text_type="mixed"),
            "output_token": cal_str_tokens(result, text_type="mixed"),
            "auto": True,
        }

    async def _chat_with_stream_events(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        session_key: SessionKey,
        publish_events: bool,
        *,
        agent_id: str = "main",
        iteration: int | None = None,
    ) -> tuple[Any, bool, bool]:
        """Forward provider events and return the response and content/reasoning-stream flags."""
        streamed_content = False
        streamed_reasoning = False
        response = None
        async with aclosing(
            self.provider.chat_stream(
                messages=messages,
                tools=tools,
                model=self.model,
                temperature=self.temperature,
                session_id=session_key.safe_name(),
            )
        ) as stream:
            async for event in stream:
                if event.type == "content_delta":
                    if event.content:
                        streamed_content = True
                        if publish_events:
                            await self.bus.publish_outbound(
                                OutboundMessage(
                                    session_key=session_key,
                                    content=event.content,
                                    event_type=OutboundEventType.CONTENT_DELTA,
                                )
                            )
                elif event.type == "reasoning_delta":
                    if event.content:
                        streamed_reasoning = True
                        if publish_events:
                            await self.bus.publish_outbound(
                                OutboundMessage(
                                    session_key=session_key,
                                    content=event.content,
                                    event_type=OutboundEventType.REASONING_DELTA,
                                )
                            )
                elif event.type == "response":
                    response = event.response

        if response is None:
            response = await self.provider.chat(
                messages=messages,
                tools=tools,
                model=self.model,
                temperature=self.temperature,
                session_id=session_key.safe_name(),
            )
        return response, streamed_content, streamed_reasoning

    @staticmethod
    def _build_response_completed_payload(
        msg: InboundMessage,
        response_id: str,
        final_content: str,
        final_reasoning_content: str | None,
        token_usage: dict[str, Any],
        time_cost_seconds: float,
        iteration: int,
        tools_used: list[dict[str, Any]] | None,
    ) -> dict[str, Any]:
        """Build a stable response fact shared by analytics sinks."""
        prompt_tokens = int(token_usage.get("prompt_tokens", 0) or 0)
        completion_tokens = int(token_usage.get("completion_tokens", 0) or 0)
        total_tokens = int(token_usage.get("total_tokens", prompt_tokens + completion_tokens) or 0)
        tools_used_names = [
            str(tool_name)
            for tool in (tools_used or [])
            if (tool_name := tool.get("tool_name")) is not None
        ]
        return {
            "response_id": response_id,
            "session_id": msg.session_key.safe_name(),
            "user_id": msg.sender_id,
            "channel": msg.session_key.channel_key(),
            "session_type": msg.session_key.type,
            "time_cost_ms": round(time_cost_seconds * 1000),
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
            "iteration_count": iteration,
            "tool_count": len(tools_used_names),
            "tools_used_names": tools_used_names,
            "response_length": len(final_content),
            "created_at": datetime.now().isoformat(),
            "has_reasoning": bool(final_reasoning_content),
        }

    async def _evaluate_previous_response_outcome(
        self, session: Session, msg: InboundMessage
    ) -> _ResponseOutcomeUpdate | None:
        """Evaluate the previous response and return only this turn's changed outcome."""
        if msg.metadata.get(HEARTBEAT_METADATA_KEY):
            return

        last_response = None
        for message in reversed(session.messages):
            if message.get("role") == "assistant" and message.get("response_id"):
                last_response = message
                break
            if message.get("role") == "user":
                break

        if last_response is None:
            return

        response_id = last_response["response_id"]
        evaluation = evaluate_response_outcome(
            session.messages
            + [{"role": "user", "content": msg.content, "timestamp": msg.timestamp.isoformat()}],
            response_id,
            feedback_events=session.metadata.get("feedback_events", []),
            now=msg.timestamp,
        )
        if evaluation is None:
            return

        outcomes = session.metadata.setdefault("response_outcomes", {})
        previous = outcomes.get(response_id)
        if not should_update_outcome(previous, evaluation):
            return

        outcome_payload = evaluation.to_dict()
        outcomes[response_id] = outcome_payload
        LangfuseClient.get_instance().update_response_outcome(
            response_id,
            outcome_payload["outcome_label"],
            outcome_payload,
        )
        await self.bus.publish_outbound(
            OutboundMessage(
                session_key=msg.session_key,
                content="",
                event_type=OutboundEventType.RESPONSE_OUTCOME_EVALUATED,
                response_id=response_id,
                metadata={"response_outcome_evaluated": outcome_payload},
            )
        )
        return _ResponseOutcomeUpdate(previous=previous, payload=outcome_payload)

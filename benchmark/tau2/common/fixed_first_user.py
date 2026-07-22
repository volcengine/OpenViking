#!/usr/bin/env python3
"""Tau2 user simulator that replays one fixed first user message."""

from __future__ import annotations

from typing import Any

from tau2.data_model.message import AssistantMessage, MultiToolMessage, ToolMessage, UserMessage
from tau2.user.user_simulator import UserSimulator


def _has_user_message(state: Any) -> bool:
    return any(
        str(getattr(getattr(message, "role", None), "value", getattr(message, "role", None)))
        == "user"
        for message in getattr(state, "messages", []) or []
    )


def _append_incoming_context(message: Any, state: Any) -> None:
    if isinstance(message, MultiToolMessage):
        state.messages.extend(message.tool_messages)
    elif isinstance(message, ToolMessage):
        state.messages.append(message)
    elif isinstance(message, AssistantMessage) and (
        message.has_content() or message.is_tool_call()
    ):
        state.messages.append(message)


class FixedFirstUserSimulator(UserSimulator):
    """Return a recorded first turn, then resume the normal LLM simulator."""

    def __init__(self, *, fixed_first_message: str, **kwargs: Any):
        message = str(fixed_first_message)
        if not message.strip():
            raise ValueError("fixed_first_message must not be empty")
        super().__init__(**kwargs)
        self.fixed_first_message = message

    def _generate_next_message(self, message: Any, state: Any) -> UserMessage:
        if _has_user_message(state):
            return super()._generate_next_message(message, state)
        _append_incoming_context(message, state)
        return UserMessage(role="user", content=self.fixed_first_message)

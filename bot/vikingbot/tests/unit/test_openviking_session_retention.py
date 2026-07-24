from types import SimpleNamespace

import pytest

from vikingbot.agent.loop import AgentLoop
from vikingbot.config.schema import SessionKey
from vikingbot.hooks.base import HookContext
from vikingbot.hooks.builtins.openviking_hooks import OpenVikingCompactHook
from vikingbot.session.manager import Session


@pytest.mark.asyncio
async def test_session_context_commit_uses_turn_budget_retention():
    session_key = SessionKey(type="test", channel_id="channel", chat_id="chat")
    session = Session(key=session_key)
    session.add_message("user", "question")
    session.add_message("assistant", "answer")
    commit_calls: list[dict] = []

    class FakeClient:
        @staticmethod
        def session_owner_user_id():
            return None

        @staticmethod
        async def append_messages(**kwargs):
            return {"added": len(kwargs["messages"])}

        @staticmethod
        async def get_session(session_id, user_id=None):
            return {"session_id": session_id, "pending_tokens": 0}

        @staticmethod
        async def commit_session(**kwargs):
            commit_calls.append(kwargs)
            return {"archived": True}

    result = await OpenVikingCompactHook()._execute_session_context_commit(
        HookContext(event_type="message.compact", session_key=session_key),
        session,
        FakeClient(),
        SimpleNamespace(commit_token_threshold=200_000),
        "admin",
        force_commit=True,
        keep_recent_turn_count=3,
        retained_message_token_budget=12_000,
        min_raw_tail_steps=1,
        commit_message_threshold=None,
    )

    assert result["success"] is True
    assert commit_calls == [
        {
            "session_id": session_key.safe_name(),
            "keep_recent_count": 0,
            "retention_mode": "turn_budget",
            "keep_recent_turn_count": 3,
            "retained_message_token_budget": 12_000,
            "min_raw_tail_steps": 1,
            "user_id": None,
        }
    ]


def test_prompt_history_consumes_overview_checkpoint_and_raw_tail_in_order():
    session_key = SessionKey(type="test", channel_id="channel", chat_id="chat")
    session = Session(key=session_key)
    context_payload = {
        "latest_archive_overview": "Earlier turns were compacted.",
        "messages": [
            {
                "role": "user",
                "message_kind": "user_query",
                "parts": [{"type": "text", "text": "Investigate the outage"}],
            },
            {
                "role": "assistant",
                "message_kind": "checkpoint",
                "parts": [
                    {
                        "type": "context",
                        "context_type": "memory",
                        "uri": "viking://user/default/sessions/s/history/archive_001",
                        "abstract": "Checked early signals and confirmed pool saturation.",
                    }
                ],
            },
            {
                "role": "assistant",
                "message_kind": "assistant_step",
                "parts": [
                    {"type": "text", "text": "I will verify the recovery setting."},
                    {
                        "type": "tool",
                        "tool_id": "call-2",
                        "tool_name": "read_config",
                        "tool_output": "recovery_timeout=30",
                    },
                ],
            },
        ],
    }

    loop = AgentLoop.__new__(AgentLoop)
    history = loop._build_ov_history_messages(session, context_payload)

    assert [message["role"] for message in history] == [
        "assistant",
        "user",
        "assistant",
        "assistant",
    ]
    assert history[0]["content"] == (
        "[Earlier conversation summary]\nEarlier turns were compacted."
    )
    assert history[1]["content"] == "Investigate the outage"
    assert history[2]["content"] == (
        "Checked early signals and confirmed pool saturation."
    )
    assert history[3]["content"] == (
        "I will verify the recovery setting.\nrecovery_timeout=30"
    )

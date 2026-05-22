from pathlib import Path
import sys
import types
from types import SimpleNamespace

import pytest

if "vikingbot.providers.registry" not in sys.modules:
    providers_registry = types.ModuleType("vikingbot.providers.registry")
    providers_registry.find_by_name = lambda provider_name: None
    sys.modules["vikingbot.providers.registry"] = providers_registry

if "vikingbot.sandbox.manager" not in sys.modules:
    sandbox_manager = types.ModuleType("vikingbot.sandbox.manager")

    class _SandboxManager:
        pass

    sandbox_manager.SandboxManager = _SandboxManager
    sys.modules["vikingbot.sandbox.manager"] = sandbox_manager

from vikingbot.bus.events import OutboundEventType
from vikingbot.bus.queue import MessageBus
from vikingbot.channels.feishu import FeishuChannel
from vikingbot.config.schema import Config, FeishuChannelConfig, SessionKey

try:
    from lark_oapi.api.im.v1.model.p2_im_message_reaction_created_v1 import (
        P2ImMessageReactionCreatedV1,
    )

    FEISHU_SDK_MODELS_AVAILABLE = True
except ImportError:
    P2ImMessageReactionCreatedV1 = None
    FEISHU_SDK_MODELS_AVAILABLE = False


class _FakeLangfuseClient:
    def __init__(self):
        self.outcome_calls = []

    def update_response_outcome(self, response_id, outcome_label, payload):
        self.outcome_calls.append((response_id, outcome_label, payload))
        return payload


def _build_channel(temp_dir: Path, monkeypatch) -> tuple[FeishuChannel, MessageBus]:
    config = Config(storage_workspace=str(temp_dir))
    monkeypatch.setattr(
        "vikingbot.channels.feishu.load_config",
        lambda: config,
    )
    bus = MessageBus()
    channel = FeishuChannel(
        config=FeishuChannelConfig(app_id="app-123"),
        bus=bus,
    )
    return channel, bus


@pytest.mark.asyncio
async def test_feishu_store_response_message_mapping_persists_lookup_fields(tmp_path: Path, monkeypatch):
    channel, _ = _build_channel(tmp_path, monkeypatch)
    session_key = SessionKey(type="feishu", channel_id="app-123", chat_id="oc_chat")

    session = channel._session_manager.get_or_create(session_key, skip_heartbeat=True)
    session.add_message("assistant", "hello", response_id="resp-123")
    session.metadata["response_facts"] = {"resp-123": {"response_id": "resp-123"}}
    await channel._session_manager.save(session)

    await channel._store_response_message_mapping(
        session_key=session_key,
        response_id="resp-123",
        platform_message_id="om_msg_1",
    )

    persisted = channel._session_manager.get_or_create(session_key, skip_heartbeat=True)
    assert persisted.metadata[channel.REACTION_MESSAGE_MAP_KEY]["om_msg_1"] == "resp-123"
    assert persisted.metadata["response_facts"]["resp-123"]["platform_message_id"] == "om_msg_1"
    assert persisted.messages[-1]["platform_message_id"] == "om_msg_1"


@pytest.mark.asyncio
async def test_feishu_submit_reaction_feedback_emits_feedback_and_outcome(tmp_path: Path, monkeypatch):
    channel, bus = _build_channel(tmp_path, monkeypatch)
    fake_langfuse = _FakeLangfuseClient()
    monkeypatch.setattr(
        "vikingbot.channels.feishu.LangfuseClient.get_instance",
        staticmethod(lambda: fake_langfuse),
    )

    session_key = SessionKey(type="feishu", channel_id="app-123", chat_id="oc_chat")
    session = channel._session_manager.get_or_create(session_key, skip_heartbeat=True)
    session.add_message(
        "assistant",
        "hello",
        response_id="resp-123",
        sender_id="user-a",
        timestamp="2026-04-30T00:00:00",
        platform_message_id="om_msg_1",
    )
    session.metadata[channel.REACTION_MESSAGE_MAP_KEY] = {"om_msg_1": "resp-123"}
    await channel._session_manager.save(session)

    await channel._submit_reaction_feedback(
        chat_id="oc_chat",
        chat_type="group",
        root_id=None,
        platform_message_id="om_msg_1",
        user_id="user-b",
        emoji_type="THUMBSDOWN",
        feedback_type="thumb_down",
    )

    assert bus.outbound_size == 2
    outcome_event = await bus.consume_outbound()
    feedback_event = await bus.consume_outbound()

    assert outcome_event.event_type == OutboundEventType.RESPONSE_OUTCOME_EVALUATED
    assert outcome_event.response_id == "resp-123"
    assert outcome_event.metadata["response_outcome_evaluated"]["outcome_label"] == "negative_feedback"

    assert feedback_event.event_type == OutboundEventType.FEEDBACK_SUBMITTED
    assert feedback_event.response_id == "resp-123"
    assert feedback_event.metadata["feedback_submitted"]["feedback_type"] == "thumb_down"
    assert feedback_event.metadata["feedback_submitted"]["feedback_reason"] == "feishu_reaction"

    persisted = channel._session_manager.get_or_create(session_key, skip_heartbeat=True)
    assert persisted.metadata["feedback_events"][0]["feedback_type"] == "thumb_down"
    assert persisted.metadata["response_outcomes"]["resp-123"]["outcome_label"] == "negative_feedback"
    assert fake_langfuse.outcome_calls == [
        ("resp-123", "negative_feedback", persisted.metadata["response_outcomes"]["resp-123"])
    ]


@pytest.mark.asyncio
async def test_feishu_submit_reaction_feedback_dedupes_same_reaction(tmp_path: Path, monkeypatch):
    channel, bus = _build_channel(tmp_path, monkeypatch)
    fake_langfuse = _FakeLangfuseClient()
    monkeypatch.setattr(
        "vikingbot.channels.feishu.LangfuseClient.get_instance",
        staticmethod(lambda: fake_langfuse),
    )

    session_key = SessionKey(type="feishu", channel_id="app-123", chat_id="oc_chat")
    session = channel._session_manager.get_or_create(session_key, skip_heartbeat=True)
    session.add_message(
        "assistant",
        "hello",
        response_id="resp-123",
        timestamp="2026-04-30T00:00:00",
        platform_message_id="om_msg_1",
    )
    session.metadata[channel.REACTION_MESSAGE_MAP_KEY] = {"om_msg_1": "resp-123"}
    session.metadata["feedback_events"] = [
        {
            "response_id": "resp-123",
            "session_id": session_key.safe_name(),
            "user_id": "user-b",
            "feedback_type": "thumb_down",
            "feedback_score": -1.0,
            "feedback_reason": "feishu_reaction",
            "feedback_text": "THUMBSDOWN",
            "feedback_delay_sec": 1.0,
            "channel": session_key.channel_key(),
            "created_at": "2026-04-30T00:01:00",
        }
    ]
    await channel._session_manager.save(session)

    await channel._submit_reaction_feedback(
        chat_id="oc_chat",
        chat_type="group",
        root_id=None,
        platform_message_id="om_msg_1",
        user_id="user-b",
        emoji_type="THUMBSDOWN",
        feedback_type="thumb_down",
    )

    assert bus.outbound_size == 0
    persisted = channel._session_manager.get_or_create(session_key, skip_heartbeat=True)
    assert len(persisted.metadata["feedback_events"]) == 1
    assert fake_langfuse.outcome_calls == []


@pytest.mark.asyncio
async def test_feishu_submit_reaction_feedback_overwrites_previous_reaction(tmp_path: Path, monkeypatch):
    channel, bus = _build_channel(tmp_path, monkeypatch)
    fake_langfuse = _FakeLangfuseClient()
    monkeypatch.setattr(
        "vikingbot.channels.feishu.LangfuseClient.get_instance",
        staticmethod(lambda: fake_langfuse),
    )

    session_key = SessionKey(type="feishu", channel_id="app-123", chat_id="oc_chat")
    session = channel._session_manager.get_or_create(session_key, skip_heartbeat=True)
    session.add_message(
        "assistant",
        "hello",
        response_id="resp-123",
        timestamp="2026-04-30T00:00:00",
        platform_message_id="om_msg_1",
    )
    session.metadata[channel.REACTION_MESSAGE_MAP_KEY] = {"om_msg_1": "resp-123"}
    session.metadata["feedback_events"] = [
        {
            "response_id": "resp-123",
            "session_id": session_key.safe_name(),
            "user_id": "user-b",
            "feedback_type": "thumb_up",
            "feedback_score": 1.0,
            "feedback_reason": "feishu_reaction",
            "feedback_text": "THUMBSUP",
            "feedback_delay_sec": 1.0,
            "channel": session_key.channel_key(),
            "created_at": "2026-04-30T00:01:00",
        }
    ]
    await channel._session_manager.save(session)

    await channel._submit_reaction_feedback(
        chat_id="oc_chat",
        chat_type="group",
        root_id=None,
        platform_message_id="om_msg_1",
        user_id="user-b",
        emoji_type="THUMBSDOWN",
        feedback_type="thumb_down",
    )

    assert bus.outbound_size == 2
    outcome_event = await bus.consume_outbound()
    feedback_event = await bus.consume_outbound()

    assert outcome_event.event_type == OutboundEventType.RESPONSE_OUTCOME_EVALUATED
    assert outcome_event.metadata["response_outcome_evaluated"]["outcome_label"] == "negative_feedback"
    assert feedback_event.event_type == OutboundEventType.FEEDBACK_SUBMITTED
    assert feedback_event.metadata["feedback_submitted"]["feedback_type"] == "thumb_down"

    persisted = channel._session_manager.get_or_create(session_key, skip_heartbeat=True)
    assert len(persisted.metadata["feedback_events"]) == 1
    assert persisted.metadata["feedback_events"][0]["feedback_type"] == "thumb_down"
    assert persisted.metadata["feedback_events"][0]["feedback_score"] == -1.0
    assert persisted.metadata["response_outcomes"]["resp-123"]["outcome_label"] == "negative_feedback"
    assert fake_langfuse.outcome_calls[0][1] == "negative_feedback"


def test_feishu_candidate_session_keys_prioritize_thread_session(tmp_path: Path, monkeypatch):
    channel, _ = _build_channel(tmp_path, monkeypatch)

    session_keys = channel._candidate_session_keys("oc_chat", "group", "om_root")

    assert [key.chat_id for key in session_keys] == ["oc_chat#om_root", "oc_chat"]
    assert all(key.type == "feishu" for key in session_keys)
    assert all(key.channel_id == "app-123" for key in session_keys)


@pytest.mark.asyncio
async def test_feishu_reaction_event_accepts_dict_payload_shape(tmp_path: Path, monkeypatch):
    channel, bus = _build_channel(tmp_path, monkeypatch)
    fake_langfuse = _FakeLangfuseClient()
    monkeypatch.setattr(
        "vikingbot.channels.feishu.LangfuseClient.get_instance",
        staticmethod(lambda: fake_langfuse),
    )

    session_key = SessionKey(type="feishu", channel_id="app-123", chat_id="oc_chat#om_root")
    session = channel._session_manager.get_or_create(session_key, skip_heartbeat=True)
    session.add_message(
        "assistant",
        "hello",
        response_id="resp-123",
        timestamp="2026-04-30T00:00:00",
        platform_message_id="om_msg_1",
    )
    session.metadata[channel.REACTION_MESSAGE_MAP_KEY] = {"om_msg_1": "resp-123"}
    await channel._session_manager.save(session)

    payload = {
        "event": {
            "message": {
                "message_id": "om_msg_1",
                "chat_id": "oc_chat",
                "chat_type": "group",
                "root_id": "om_root",
            },
            "reaction": {"reaction_type": {"emoji_type": "THUMBSUP"}},
            "operator": {"operator_type": "user", "operator_id": "user-b"},
        }
    }

    await channel._on_message_reaction_created(payload)

    assert bus.outbound_size == 2
    outcome_event = await bus.consume_outbound()
    feedback_event = await bus.consume_outbound()
    assert outcome_event.metadata["response_outcome_evaluated"]["outcome_label"] == "positive_feedback"
    assert feedback_event.metadata["feedback_submitted"]["feedback_type"] == "thumb_up"
    assert fake_langfuse.outcome_calls[0][1] == "positive_feedback"


@pytest.mark.asyncio
async def test_feishu_reaction_event_accepts_object_payload_shape(tmp_path: Path, monkeypatch):
    channel, bus = _build_channel(tmp_path, monkeypatch)
    fake_langfuse = _FakeLangfuseClient()
    monkeypatch.setattr(
        "vikingbot.channels.feishu.LangfuseClient.get_instance",
        staticmethod(lambda: fake_langfuse),
    )

    session_key = SessionKey(type="feishu", channel_id="app-123", chat_id="oc_chat")
    session = channel._session_manager.get_or_create(session_key, skip_heartbeat=True)
    session.add_message(
        "assistant",
        "hello",
        response_id="resp-456",
        timestamp="2026-04-30T00:00:00",
        platform_message_id="om_msg_2",
    )
    session.metadata[channel.REACTION_MESSAGE_MAP_KEY] = {"om_msg_2": "resp-456"}
    await channel._session_manager.save(session)

    payload = SimpleNamespace(
        event=SimpleNamespace(
            message=SimpleNamespace(
                id="om_msg_2",
                chat_id="oc_chat",
                chat_type="p2p",
            ),
            reaction_type=SimpleNamespace(emoji_type="THUMBSDOWN"),
            operator=SimpleNamespace(operator_type="user", operator_id="user-c"),
        )
    )

    await channel._on_message_reaction_created(payload)

    assert bus.outbound_size == 2
    outcome_event = await bus.consume_outbound()
    feedback_event = await bus.consume_outbound()
    assert outcome_event.response_id == "resp-456"
    assert outcome_event.metadata["response_outcome_evaluated"]["outcome_label"] == "negative_feedback"
    assert feedback_event.metadata["feedback_submitted"]["feedback_type"] == "thumb_down"
    assert fake_langfuse.outcome_calls[0][0] == "resp-456"


@pytest.mark.asyncio
async def test_feishu_reaction_event_ignores_non_user_or_unknown_reactions(tmp_path: Path, monkeypatch):
    channel, bus = _build_channel(tmp_path, monkeypatch)
    fake_langfuse = _FakeLangfuseClient()
    monkeypatch.setattr(
        "vikingbot.channels.feishu.LangfuseClient.get_instance",
        staticmethod(lambda: fake_langfuse),
    )

    ignored_payloads = [
        {
            "event": {
                "message_id": "om_msg_1",
                "chat_id": "oc_chat",
                "chat_type": "group",
                "emoji_type": "HEART",
                "user_id": "user-b",
            }
        },
        {
            "event": {
                "message_id": "om_msg_1",
                "chat_id": "oc_chat",
                "chat_type": "group",
                "emoji_type": "THUMBSUP",
                "user_id": "bot-operator",
                "operator_type": "app",
            }
        },
    ]

    for payload in ignored_payloads:
        await channel._on_message_reaction_created(payload)

    assert bus.outbound_size == 0
    assert fake_langfuse.outcome_calls == []


@pytest.mark.asyncio
@pytest.mark.skipif(not FEISHU_SDK_MODELS_AVAILABLE, reason="Feishu SDK models unavailable")
async def test_feishu_reaction_event_accepts_real_sdk_payload_without_chat_context(
    tmp_path: Path, monkeypatch
):
    channel, bus = _build_channel(tmp_path, monkeypatch)
    fake_langfuse = _FakeLangfuseClient()
    monkeypatch.setattr(
        "vikingbot.channels.feishu.LangfuseClient.get_instance",
        staticmethod(lambda: fake_langfuse),
    )

    session_key = SessionKey(type="feishu", channel_id="app-123", chat_id="oc_chat#om_root")
    session = channel._session_manager.get_or_create(session_key, skip_heartbeat=True)
    session.add_message(
        "assistant",
        "hello",
        response_id="resp-real-sdk",
        timestamp="2026-04-30T00:00:00",
        platform_message_id="om_msg_real",
    )
    session.metadata[channel.REACTION_MESSAGE_MAP_KEY] = {"om_msg_real": "resp-real-sdk"}
    await channel._session_manager.save(session)

    payload = P2ImMessageReactionCreatedV1(
        {
            "event": {
                "message_id": "om_msg_real",
                "reaction_type": {"emoji_type": "THUMBSUP"},
                "operator_type": "user",
                "user_id": {"open_id": "ou_user_real"},
                "app_id": "cli_a",
                "action_time": "1714435200",
            }
        }
    )

    await channel._on_message_reaction_created(payload)

    assert bus.outbound_size == 2
    outcome_event = await bus.consume_outbound()
    feedback_event = await bus.consume_outbound()
    assert outcome_event.response_id == "resp-real-sdk"
    assert outcome_event.metadata["response_outcome_evaluated"]["outcome_label"] == "positive_feedback"
    assert feedback_event.metadata["feedback_submitted"]["feedback_type"] == "thumb_up"
    assert feedback_event.metadata["feedback_submitted"]["user_id"] == "ou_user_real"

    persisted = channel._session_manager.get_or_create(session_key, skip_heartbeat=True)
    assert persisted.metadata["feedback_events"][0]["user_id"] == "ou_user_real"
    assert fake_langfuse.outcome_calls[0][0] == "resp-real-sdk"


@pytest.mark.asyncio
@pytest.mark.skipif(not FEISHU_SDK_MODELS_AVAILABLE, reason="Feishu SDK models unavailable")
async def test_feishu_reaction_event_real_sdk_payload_dedupes_by_open_id(tmp_path: Path, monkeypatch):
    channel, bus = _build_channel(tmp_path, monkeypatch)
    fake_langfuse = _FakeLangfuseClient()
    monkeypatch.setattr(
        "vikingbot.channels.feishu.LangfuseClient.get_instance",
        staticmethod(lambda: fake_langfuse),
    )

    session_key = SessionKey(type="feishu", channel_id="app-123", chat_id="oc_chat")
    session = channel._session_manager.get_or_create(session_key, skip_heartbeat=True)
    session.add_message(
        "assistant",
        "hello",
        response_id="resp-real-dedupe",
        timestamp="2026-04-30T00:00:00",
        platform_message_id="om_msg_real_dedupe",
    )
    session.metadata[channel.REACTION_MESSAGE_MAP_KEY] = {"om_msg_real_dedupe": "resp-real-dedupe"}
    session.metadata["feedback_events"] = [
        {
            "response_id": "resp-real-dedupe",
            "session_id": session_key.safe_name(),
            "user_id": "ou_user_real",
            "feedback_type": "thumb_up",
            "feedback_score": 1.0,
            "feedback_reason": "feishu_reaction",
            "feedback_text": "THUMBSUP",
            "feedback_delay_sec": 1.0,
            "channel": session_key.channel_key(),
            "created_at": "2026-04-30T00:01:00",
        }
    ]
    await channel._session_manager.save(session)

    payload = P2ImMessageReactionCreatedV1(
        {
            "event": {
                "message_id": "om_msg_real_dedupe",
                "reaction_type": {"emoji_type": "THUMBSUP"},
                "operator_type": "user",
                "user_id": {"open_id": "ou_user_real"},
            }
        }
    )

    await channel._on_message_reaction_created(payload)

    assert bus.outbound_size == 0
    persisted = channel._session_manager.get_or_create(session_key, skip_heartbeat=True)
    assert len(persisted.metadata["feedback_events"]) == 1
    assert fake_langfuse.outcome_calls == []

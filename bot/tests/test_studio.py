"""Studio channel persistence, isolation and real delivery boundaries."""

import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from vikingbot.bus.events import OutboundMessage
from vikingbot.bus.queue import MessageBus
from vikingbot.config.schema import FeishuChannelConfig, SessionKey
from vikingbot.studio.providers.feishu.channel import StudioFeishuChannel
from vikingbot.studio.providers.registry import get_provider
from vikingbot.studio.service import StudioService
from vikingbot.studio.store import StudioStore


def record():
    return {
        "id": "connection",
        "account": "a",
        "app_id": "cli_test",
        "app_secret": "secret",
        "bot_name": "Bot",
        "bot_open_id": "ou_bot",
        "enabled": True,
        "revision": 1,
        "step": 2,
        "identity": {"user_id": "group-user", "api_key": "private", "role": "user"},
    }


def test_store_survives_restart_deduplicates_and_paginates(tmp_path):
    path = tmp_path / "studio.db"
    store = StudioStore(path)
    store.save(record())
    for i in range(105):
        store.append(
            "connection",
            "group",
            str(i),
            {
                "content": str(i),
                "title": "Team",
                "chat_type": "group",
            },
        )
    store.append("connection", "group", "0", {"content": "duplicate"})
    reloaded = StudioStore(path)
    assert reloaded.connections("b") == []
    assert reloaded.connections("a")[0]["id"] == "connection"
    page = reloaded.history("connection", "group")
    assert len(page) == 101
    assert page[0]["content"] == "104"
    assert len(reloaded.history("connection", "group", page[99]["id"])) == 5
    assert reloaded.conversations("connection")[0]["title"] == "0"
    assert reloaded.conversations("connection")[0]["group_name"] == "Team"
    store.append(
        "connection",
        "group#topic",
        "topic",
        {
            "role": "user",
            "content": "First question",
            "chat_type": "group",
            "title": "Team / First question",
            "topic_title": "Release plan",
        },
    )
    topic = reloaded.conversations("connection")[0]
    assert topic["title"] == "Release plan"
    assert topic["group_name"] == "Team"
    assert path.stat().st_mode & 0o777 == 0o600


@pytest.fixture
def channel(tmp_path):
    result = StudioFeishuChannel(
        FeishuChannelConfig(app_id="cli_test", bot_name="Old name"),
        MessageBus(),
        record=record(),
        store=StudioStore(tmp_path / "s.db"),
    )
    result._running = True
    return result


def test_mentions_use_identity_even_after_rename(channel):
    assert channel._is_bot_mention(
        SimpleNamespace(id=SimpleNamespace(open_id="ou_bot"), name="New")
    )
    assert not channel._is_bot_mention(
        SimpleNamespace(id=SimpleNamespace(open_id="ou_other"), name="Old name")
    )


async def test_fixed_verification_bypasses_model_and_requires_actual_send(channel, monkeypatch):
    channel.verification = {
        "code": "ABC123",
        "expires_at": time.time() + 60,
        "received": False,
        "sent": False,
    }
    monkeypatch.setattr(channel, "send", AsyncMock(return_value=False))
    await channel._handle_message(
        "sender",
        "group",
        "VikingBot connection test ABC123",
        metadata={"chat_type": "group", "message_id": "m1"},
    )
    assert channel.verification["received"]
    assert not channel.verification["sent"]
    assert channel.store.history("connection") == []
    with pytest.raises(TimeoutError):
        await asyncio.wait_for(channel.bus.consume_inbound(), 0.01)


async def test_group_identity_and_peer_are_separate_from_installer(channel):
    await channel._handle_message(
        "sender",
        "group",
        "hello",
        sender_name="Alice",
        metadata={"chat_type": "group", "message_id": "m1"},
    )
    event = await channel.bus.consume_inbound()
    assert event.openviking_connection["user_id"] == "group-user"
    assert event.actor_peer_id.startswith("feishu-")
    assert event.actor_peer_id != "sender"
    assert channel.store.history("connection")[0]["sender"] == "Alice"


async def test_pause_drops_new_inbound_messages(channel):
    await channel.stop()
    await channel._handle_message("sender", "group", "hello")
    assert channel.store.history("connection") == []


@pytest.mark.parametrize("accepted", [False, True])
async def test_delivery_status_matches_send_result(channel, monkeypatch, accepted):
    from vikingbot.channels.feishu import FeishuChannel

    monkeypatch.setattr(FeishuChannel, "send", AsyncMock(return_value=accepted))
    result = await channel.send(
        OutboundMessage(
            SessionKey(type="feishu", channel_id="cli_test", chat_id="studio:connection:group"),
            "answer",
        )
    )
    assert result is accepted
    assert bool(channel.last_sent) is accepted
    message = channel.store.history("connection")[0]
    assert message["content"] == "answer"
    assert message["status"] == ("sent" if accepted else "send_failed")


@pytest.mark.parametrize(
    "metadata,emoji",
    [
        ({"action": "processing_tick", "tick_count": 0}, "StatusInFlight"),
        ({"action": "add_reaction", "emoji": "THINKING"}, "THINKING"),
    ],
)
async def test_control_events_do_not_create_delivery_history(channel, monkeypatch, metadata, emoji):
    reaction = AsyncMock()
    monkeypatch.setattr(channel, "send_processing_reaction", reaction)
    await channel.send(
        OutboundMessage(
            SessionKey(type="feishu", channel_id="cli_test", chat_id="studio:connection:group"),
            "",
            metadata={**metadata, "message_id": "m1"},
        )
    )
    reaction.assert_awaited_once_with("m1", emoji)
    assert channel.store.history("connection") == []
    assert channel.store.conversations("connection") == []
    assert channel.last_sent is None


def test_public_config_never_returns_credentials_and_filters_accounts(tmp_path):
    manager = SimpleNamespace(channels={})
    config = SimpleNamespace(bot_data_path=tmp_path)
    service = StudioService(config, manager)
    service.store.save(record())
    public = service.public(record())
    assert "secret" not in str(public)
    assert "private" not in str(public)
    with pytest.raises(HTTPException) as error:
        service.get("other-account", "connection")
    assert error.value.status_code == 404


async def test_private_gateway_rejects_loopback_without_secret(tmp_path, monkeypatch):
    import httpx
    from fastapi import FastAPI
    from vikingbot.studio.router import create_router

    monkeypatch.delenv("OPENVIKING_BOT_STUDIO_TOKEN", raising=False)
    channel = SimpleNamespace(
        _gateway_token=lambda: "internal", _is_loopback_request=lambda r: True
    )
    service = StudioService(SimpleNamespace(bot_data_path=tmp_path), SimpleNamespace(channels={}))
    app = FastAPI()
    app.include_router(create_router(channel, service))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        for token in ["", "wrong"]:
            response = await client.post(
                "/studio/dispatch",
                headers={"X-Gateway-Token": token},
                json={"account": "a", "action": "list"},
            )
            assert response.status_code == 403
        response = await client.post(
            "/studio/dispatch",
            headers={"X-Gateway-Token": "internal"},
            json={"account": "a", "action": "list"},
        )
        assert response.status_code == 200


async def test_duplicate_message_does_not_rerun_agent(channel):
    for _ in range(2):
        await channel._handle_message("s", "group", "hello", metadata={"message_id": "same"})
    await channel.bus.consume_inbound()
    with pytest.raises(TimeoutError):
        await asyncio.wait_for(channel.bus.consume_inbound(), 0.01)


async def test_failed_secret_rotation_preserves_old_connection(tmp_path, monkeypatch):
    service = StudioService(SimpleNamespace(bot_data_path=tmp_path), SimpleNamespace(channels={}))
    service.store.save(record())
    monkeypatch.setattr(
        get_provider({}), "validate_app", AsyncMock(side_effect=HTTPException(400, "Invalid"))
    )
    with pytest.raises(HTTPException):
        await service.update(
            "a", "connection", {"revision": 1, "action": "credentials", "app_secret": "wrong"}
        )
    assert service.get("a", "connection")["app_secret"] == "secret"
    assert service.get("a", "connection")["enabled"]


def test_managed_group_tools_deny_local_and_unregistered_capabilities():
    from vikingbot.studio.policy import disabled_group_tools

    assert disabled_group_tools(
        ["openviking_search", "exec", "read_file", "mcp_admin", "spawn"]
    ) == ["exec", "read_file", "mcp_admin", "spawn"]


async def test_delete_connection_stops_runtime_and_cleans_owned_data(tmp_path):
    service = StudioService(SimpleNamespace(bot_data_path=tmp_path), SimpleNamespace(channels={}))
    item = record()
    service.store.save(item)
    service.store.save({**item, "id": "other", "account": "b"})
    service.store.append(item["id"], "group", "event", {"content": "hello"})
    runtime = SimpleNamespace(stop=AsyncMock())
    key = get_provider(item).runtime_key(item)
    service.manager.channels[key] = runtime
    with pytest.raises(HTTPException) as error:
        await service.update("b", item["id"], {"action": "delete", "revision": 1})
    assert error.value.status_code == 404
    with pytest.raises(HTTPException) as error:
        await service.update("a", item["id"], {"action": "delete", "revision": 0})
    assert error.value.status_code == 409
    runtime.stop.assert_not_awaited()
    assert await service.update("a", item["id"], {"action": "delete", "revision": 1}) == {
        "deleted": True
    }
    runtime.stop.assert_awaited_once()
    assert key not in service.manager.channels
    assert service.store.connections("a") == []
    assert len(service.store.connections("b")) == 1
    assert service.store.history(item["id"]) == []


async def test_reply_settings_persist_apply_immediately_and_keep_revision_guard(
    tmp_path, channel, monkeypatch
):
    provider = get_provider({"type": "feishu"})
    service = StudioService(
        SimpleNamespace(
            bot_data_path=tmp_path, channels=[{"type": "feishu", "app_id": "cli_test"}]
        ),
        SimpleNamespace(channels={provider.runtime_key(record()): channel}),
    )
    service.store.save(record())
    assert service.public(record())["settings"] == {"thread_require_mention": True}
    monkeypatch.setattr(channel, "_get_chat_mode", AsyncMock(return_value="group"))
    assert not await channel._check_should_process("group", "g", SimpleNamespace(), False)
    updated = await service.update(
        "a",
        "connection",
        {
            "revision": 1,
            "action": "settings",
            "settings": {"thread_require_mention": False},
        },
    )
    assert updated["revision"] == 2
    assert await channel._check_should_process("group", "g", SimpleNamespace(), False)
    assert service.config.channels[0]["thread_require_mention"] is False
    persisted = StudioStore(tmp_path / "studio.sqlite3").connections("a")[0]
    assert persisted["settings"] == {"thread_require_mention": False}
    with pytest.raises(HTTPException) as error:
        await service.update(
            "a", "connection", {"revision": 1, "action": "settings", "settings": {}}
        )
    assert error.value.status_code == 409
    assert channel.config.thread_require_mention is False
    captured = []

    def restored(config, *args, **kwargs):
        captured.append(config.thread_require_mention)
        return SimpleNamespace()

    monkeypatch.setattr("vikingbot.studio.providers.feishu.channel.StudioFeishuChannel", restored)
    service.manager.bus = None
    service.manager.add_channel = lambda channel: None
    service.config.workspace_path = tmp_path
    provider.install(service, persisted)
    assert captured == [False]


async def test_recreated_connection_isolates_sessions_and_rejects_old_replies(
    tmp_path, monkeypatch
):
    from vikingbot.channels.feishu import FeishuChannel
    from vikingbot.channels.manager import ChannelManager
    from vikingbot.session.manager import SessionManager

    async def start(channel):
        channel._running = True

    provider = get_provider({"type": "feishu"})
    monkeypatch.setattr(provider, "validate_app", AsyncMock(return_value={"open_id": "ou_bot"}))
    monkeypatch.setattr(StudioFeishuChannel, "start", start)
    delivery = AsyncMock(return_value=True)
    monkeypatch.setattr(FeishuChannel, "send", delivery)
    bus = MessageBus()
    service = StudioService(
        SimpleNamespace(bot_data_path=tmp_path, workspace_path=tmp_path, channels=[]),
        ChannelManager(bus),
    )

    async def connect(account):
        result = await service.create(
            account,
            {"type": "feishu", "credentials": {"app_id": "cli_test", "app_secret": "secret"}},
            {"account_id": account, "user_id": "bot-user", "role": "user"},
        )
        await asyncio.gather(*service.tasks.values())
        channel = service.runtime(service.get(account, result["id"]))
        await channel._handle_message(
            "sender", "oc_group#topic", "hello", metadata={"message_id": account}
        )
        return result, channel, await bus.consume_inbound()

    old, _, old_event = await connect("a")
    sessions = SessionManager(tmp_path)
    old_session = sessions.get_or_create(old_event.session_key)
    old_session.add_message("assistant", "account A private result")
    await sessions.save(old_session)
    await service.update("a", old["id"], {"action": "delete", "revision": old["revision"]})
    new, channel, event = await connect("b")
    assert event.openviking_connection["account_id"] == "b"
    assert sessions.get_or_create(event.session_key).get_history() == []
    assert SessionManager(tmp_path).get_or_create(event.session_key).get_history() == []
    assert service.store.history(old["id"]) == []

    assert not await channel.send(OutboundMessage(old_event.session_key, "late old reply"))
    delivery.assert_not_awaited()
    assert await channel.send(OutboundMessage(event.session_key, "new reply"))
    assert delivery.await_args.args[0].session_key.chat_id == "oc_group#topic"
    assert event.session_key.channel_key() in service.manager.channels
    assert service.store.history(new["id"], "oc_group#topic")[0]["content"] == "new reply"

    # Restarting the same connection must retain its scoped session.
    current_session = sessions.get_or_create(event.session_key)
    current_session.add_message("assistant", "account B result")
    await sessions.save(current_session)
    service.install(service.get("b", new["id"]))
    restored = service.runtime(service.get("b", new["id"]))
    await restored.start()
    await restored._handle_message("sender", "oc_group#topic", "again")
    restored_event = await bus.consume_inbound()
    assert restored_event.session_key == event.session_key
    assert SessionManager(tmp_path).get_or_create(restored_event.session_key).get_history() == [
        {"role": "assistant", "content": "account B result"}
    ]

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
        store.append("connection", "group", str(i), {"content": str(i), "title": "Team"})
    store.append("connection", "group", "0", {"content": "duplicate"})
    reloaded = StudioStore(path)
    assert reloaded.connections("b") == []
    assert reloaded.connections("a")[0]["id"] == "connection"
    page = reloaded.history("connection", "group")
    assert len(page) == 101
    assert page[0]["content"] == "104"
    assert len(reloaded.history("connection", "group", page[99]["id"])) == 5
    assert reloaded.conversations("connection")[0]["title"] == "Team"
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


async def test_delivery_failure_is_not_reported_as_sent(channel, monkeypatch):
    from vikingbot.channels.feishu import FeishuChannel

    monkeypatch.setattr(FeishuChannel, "send", AsyncMock(return_value=False))
    result = await channel.send(
        OutboundMessage(SessionKey(type="feishu", channel_id="cli_test", chat_id="group"), "answer")
    )
    assert not result
    assert channel.last_sent is None
    assert channel.store.history("connection")[0]["status"] == "send_failed"


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


async def test_revision_conflict_does_not_mutate_config(tmp_path):
    service = StudioService(SimpleNamespace(bot_data_path=tmp_path), SimpleNamespace(channels={}))
    service.store.save(record())
    with pytest.raises(HTTPException) as error:
        await service.update("a", "connection", {"revision": 0, "action": "pause"})
    assert error.value.status_code == 409
    assert service.get("a", "connection")["enabled"]


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


async def test_cannot_mark_setup_complete_before_reply_is_accepted(tmp_path):
    service = StudioService(SimpleNamespace(bot_data_path=tmp_path), SimpleNamespace(channels={}))
    service.store.save(record())
    with pytest.raises(HTTPException) as exc:
        await service.update("a", "connection", {"revision": 1, "action": "step", "step": 5})
    assert exc.value.status_code == 409


def test_managed_group_tools_deny_local_and_unregistered_capabilities():
    from vikingbot.studio.policy import disabled_group_tools

    assert disabled_group_tools(
        ["openviking_search", "exec", "read_file", "mcp_admin", "spawn"]
    ) == ["exec", "read_file", "mcp_admin", "spawn"]


def test_provider_registry_preserves_legacy_and_rejects_unknown():
    assert get_provider({}).type == "feishu"
    assert get_provider({"type": "feishu"}).type == "feishu"
    with pytest.raises(HTTPException) as error:
        get_provider({"type": "slack"})
    assert error.value.status_code == 400

# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Regression tests for preserving channel delivery metadata."""

from types import SimpleNamespace

import pytest
from vikingbot.agent.loop import AgentLoop
from vikingbot.agent.subagent import SubagentManager
from vikingbot.agent.tools.cron import CronTool
from vikingbot.agent.tools.message import MessageTool
from vikingbot.agent.tools.spawn import SpawnTool
from vikingbot.bus.events import InboundMessage, OutboundMessage
from vikingbot.bus.queue import MessageBus
from vikingbot.channels.feishu import FeishuChannel
from vikingbot.config.schema import Config, FeishuChannelConfig, SessionKey
from vikingbot.cron.service import CronService
from vikingbot.cron.types import CronSchedule


@pytest.mark.asyncio
async def test_message_tool_preserves_channel_metadata():
    sent = []
    metadata = {"reply_to": "oc_chat", "chat_type": "group", "message_id": "om_old"}

    async def send_callback(msg: OutboundMessage) -> None:
        sent.append(msg)

    tool = MessageTool(send_callback=send_callback)
    context = SimpleNamespace(
        session_key=SessionKey(type="feishu", channel_id="cli_app", chat_id="oc_chat"),
        channel_metadata=metadata,
    )

    result = await tool.execute(context, content="hello")

    assert result.startswith("Message sent")
    assert sent[0].metadata == metadata


@pytest.mark.asyncio
async def test_spawn_tool_preserves_channel_metadata():
    metadata = {"reply_to": "oc_chat", "chat_type": "group", "message_id": "om_old"}
    connection = {"api_key": "request-key", "account_id": "acct", "user_id": "alice"}
    calls = []

    class FakeSubagentManager:
        async def spawn(self, **kwargs):
            calls.append(kwargs)
            return "started"

    tool = SpawnTool(manager=FakeSubagentManager())
    context = SimpleNamespace(
        session_key=SessionKey(type="feishu", channel_id="cli_app", chat_id="oc_chat"),
        channel_metadata=metadata,
        openviking_connection=connection,
    )

    result = await tool.execute(context, task="read files", label="read")

    assert result == "started"
    assert calls[0]["channel_metadata"] == metadata
    assert calls[0]["openviking_connection"] is connection


@pytest.mark.asyncio
async def test_subagent_announcement_preserves_channel_metadata(tmp_path):
    bus = MessageBus()
    metadata = {"reply_to": "oc_chat", "chat_type": "group", "message_id": "om_old"}
    connection = {"api_key": "request-key", "account_id": "acct", "user_id": "alice"}
    session_key = SessionKey(type="feishu", channel_id="cli_app", chat_id="oc_chat")
    manager = SubagentManager(
        provider=SimpleNamespace(get_default_model=lambda: "fake-model"),
        workspace=tmp_path,
        bus=bus,
        config=SimpleNamespace(),
    )

    await manager._announce_result(
        "task-id",
        "read",
        "read files",
        "done",
        session_key,
        "ok",
        metadata,
        connection,
    )

    inbound = await bus.consume_inbound()
    assert inbound.metadata == metadata
    assert inbound.openviking_connection is connection


@pytest.mark.asyncio
async def test_system_message_response_preserves_channel_metadata(tmp_path, monkeypatch):
    monkeypatch.setattr(AgentLoop, "_register_builtin_hooks", lambda self: None)
    monkeypatch.setattr(AgentLoop, "_register_default_tools", lambda self: None)
    monkeypatch.setattr("vikingbot.agent.loop.SubagentManager", lambda **kwargs: SimpleNamespace())

    metadata = {"reply_to": "oc_chat", "chat_type": "group", "message_id": "om_old"}
    connection = {"api_key": "request-key", "account_id": "acct", "user_id": "alice"}
    captured = {}
    session_key = SessionKey(type="feishu", channel_id="cli_app", chat_id="oc_chat")
    config = Config(storage_workspace=str(tmp_path / "data"))
    loop = AgentLoop(
        bus=MessageBus(),
        provider=SimpleNamespace(get_default_model=lambda: "fake-model"),
        workspace=tmp_path / "workspace",
        model=config.agents.model,
        temperature=config.agents.temperature,
        config=config,
    )

    async def fake_build_prompt_history(*args, **kwargs):
        return []

    async def fake_build_messages(**kwargs):
        return [{"role": "user", "content": kwargs["current_message"]}]

    async def fake_run_agent_loop(**kwargs):
        return "summary", None, [], {}, 1

    loop._build_prompt_history = fake_build_prompt_history

    class FakeContextBuilder:
        def __init__(self, *args, **kwargs):
            captured["connection"] = kwargs.get("openviking_connection")

        async def build_messages(self, **kwargs):
            return await fake_build_messages(**kwargs)

    monkeypatch.setattr("vikingbot.agent.context.ContextBuilder", FakeContextBuilder)
    loop._run_agent_loop = fake_run_agent_loop

    outbound = await loop._process_system_message(
        InboundMessage(
            sender_id="subagent",
            session_key=session_key,
            content="subagent done",
            metadata=metadata,
            openviking_connection=connection,
        )
    )

    assert outbound.content == "summary"
    assert outbound.metadata == metadata
    assert captured["connection"] is connection


@pytest.mark.asyncio
async def test_cron_tool_persists_only_delivery_metadata(tmp_path):
    service = CronService(tmp_path / "jobs.json")
    tool = CronTool(service)
    context = SimpleNamespace(
        session_key=SessionKey(type="feishu", channel_id="cli_app", chat_id="oc_chat"),
        channel_metadata={
            "reply_to": "oc_chat",
            "chat_type": "group",
            "chat_mode": "thread",
            "root_id": "om_root",
            "sender_id": "ou_sender",
            "message_id": "om_should_not_be_persisted",
        },
    )

    result = await tool.execute(
        context,
        action="add",
        name="standup",
        message="time for standup",
        every_seconds=3600,
    )

    assert result.startswith("Created job")
    loaded = CronService(tmp_path / "jobs.json").list_jobs()
    assert len(loaded) == 1
    assert loaded[0].payload.channel_metadata == {
        "reply_to": "oc_chat",
        "chat_type": "group",
        "chat_mode": "thread",
        "root_id": "om_root",
        "sender_id": "ou_sender",
    }


@pytest.mark.parametrize(
    "schedule",
    [
        CronSchedule(kind="every", every_ms=0),
        CronSchedule(kind="at", at_ms=1),
        CronSchedule(kind="cron", expr="not a cron expression"),
    ],
)
def test_cron_service_rejects_unschedulable_jobs(tmp_path, schedule):
    path = tmp_path / "jobs.json"
    key = SessionKey(type="cli", channel_id="default", chat_id="default")
    with pytest.raises(ValueError, match="future run time"):
        CronService(path).add_job("invalid", schedule, "hello", key)
    assert not path.exists()


def test_cron_service_rejects_reenabling_expired_one_shot(tmp_path, monkeypatch):
    path = tmp_path / "jobs.json"
    key = SessionKey(type="cli", channel_id="default", chat_id="default")
    monkeypatch.setattr("vikingbot.cron.service._now_ms", lambda: 1_000)
    service = CronService(path)
    job = service.add_job("one-shot", CronSchedule(kind="at", at_ms=2_000), "hello", key)
    service.enable_job(job.id, enabled=False)
    original = path.read_bytes()

    monkeypatch.setattr("vikingbot.cron.service._now_ms", lambda: 3_000)
    with pytest.raises(ValueError, match="future run time"):
        service.enable_job(job.id)

    assert path.read_bytes() == original
    assert not job.enabled
    assert job.state.next_run_at_ms is None


@pytest.mark.asyncio
async def test_cron_service_without_callback_preserves_one_shot(tmp_path):
    path = tmp_path / "jobs.json"
    service = CronService(path)
    job = service.add_job(
        "one-shot",
        CronSchedule(kind="at", at_ms=9999999999999),
        "hello",
        SessionKey(type="cli", channel_id="default", chat_id="default"),
        delete_after_run=True,
    )
    original = path.read_bytes()

    with pytest.raises(RuntimeError, match="no job execution callback"):
        await service.run_job(job.id)
    assert path.read_bytes() == original


@pytest.mark.asyncio
async def test_cron_service_without_callback_rearms_recurring_timer(tmp_path, monkeypatch):
    path = tmp_path / "jobs.json"
    now_ms = 1_000
    monkeypatch.setattr("vikingbot.cron.service._now_ms", lambda: now_ms)
    service = CronService(path)
    job = service.add_job(
        "recurring",
        CronSchedule(kind="every", every_ms=100),
        "hello",
        SessionKey(type="cli", channel_id="default", chat_id="default"),
    )
    service._running = True
    rearmed_at = []
    monkeypatch.setattr(
        service, "_arm_timer", lambda: rearmed_at.append(service._get_next_wake_ms())
    )
    now_ms = 1_100

    await service._on_timer()

    assert job.state.last_status == "error"
    assert job.state.last_error == "Cron service has no job execution callback"
    assert job.state.last_run_at_ms == now_ms
    assert job.state.next_run_at_ms == 1_200
    assert rearmed_at == [1_200]
    persisted = CronService(path).list_jobs()
    assert persisted[0].state.last_status == "error"
    assert persisted[0].state.next_run_at_ms == 1_200


def test_feishu_uses_thread_root_for_scheduled_delivery():
    assert (
        FeishuChannel._reply_to_message_id_from_metadata(
            {
                "reply_to": "oc_chat",
                "chat_type": "group",
                "chat_mode": "thread",
                "root_id": "om_root",
            }
        )
        == "om_root"
    )


@pytest.mark.parametrize("retry", [False, True], ids=["jpeg-upload", "normalize-and-retry"])
async def test_feishu_uploads_jpeg(monkeypatch, retry):
    import httpx

    channel = FeishuChannel(FeishuChannelConfig(app_id="cli_app"), MessageBus())
    original = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01original-with-metadata"
    normalized = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01normalized"
    uploads = []

    def handle(request):
        uploads.append(request.content)
        assert b'filename="image.jpg"' in request.content
        assert b"image/jpeg" in request.content
        if retry and len(uploads) == 1:
            return httpx.Response(400, json={"code": 234011, "msg": "can not recognize image"})
        return httpx.Response(200, json={"code": 0, "data": {"image_key": "img_key"}})

    async def token():
        return "tenant-token"

    real_client = httpx.AsyncClient
    transport = httpx.MockTransport(handle)
    monkeypatch.setattr(channel, "_get_tenant_access_token", token)
    monkeypatch.setattr(channel, "_normalize_image_for_feishu", lambda data: normalized)
    monkeypatch.setattr(
        "vikingbot.channels.feishu.httpx.AsyncClient",
        lambda **kwargs: real_client(transport=transport, **kwargs),
    )
    assert await channel._upload_image_to_feishu(original) == "img_key"
    assert original in uploads[0]
    assert len(uploads) == (2 if retry else 1)
    if retry:
        assert normalized in uploads[1]

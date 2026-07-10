# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Regression tests for preserving channel delivery metadata."""

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from vikingbot.agent.loop import AgentLoop  # noqa: E402
from vikingbot.agent.subagent import SubagentManager  # noqa: E402
from vikingbot.agent.tools.cron import CronTool  # noqa: E402
from vikingbot.agent.tools.message import MessageTool  # noqa: E402
from vikingbot.agent.tools.spawn import SpawnTool  # noqa: E402
from vikingbot.bus.events import InboundMessage, OutboundMessage  # noqa: E402
from vikingbot.bus.queue import MessageBus  # noqa: E402
from vikingbot.channels.feishu import FeishuChannel  # noqa: E402
from vikingbot.config.schema import Config, FeishuChannelConfig, SessionKey  # noqa: E402
from vikingbot.cron.service import CronService  # noqa: E402
from vikingbot.cron.types import CronSchedule  # noqa: E402


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
    assert sent[0].metadata is not metadata


@pytest.mark.asyncio
async def test_spawn_tool_preserves_channel_metadata():
    metadata = {"reply_to": "oc_chat", "chat_type": "group", "message_id": "om_old"}
    calls = []

    class FakeSubagentManager:
        async def spawn(self, **kwargs):
            calls.append(kwargs)
            return "started"

    tool = SpawnTool(manager=FakeSubagentManager())
    context = SimpleNamespace(
        session_key=SessionKey(type="feishu", channel_id="cli_app", chat_id="oc_chat"),
        channel_metadata=metadata,
    )

    result = await tool.execute(context, task="read files", label="read")

    assert result == "started"
    assert calls[0]["channel_metadata"] == metadata


@pytest.mark.asyncio
async def test_subagent_announcement_preserves_channel_metadata(tmp_path):
    bus = MessageBus()
    metadata = {"reply_to": "oc_chat", "chat_type": "group", "message_id": "om_old"}
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
    )

    inbound = await bus.consume_inbound()
    assert inbound.metadata == metadata
    assert inbound.metadata is not metadata


@pytest.mark.asyncio
async def test_system_message_response_preserves_channel_metadata(tmp_path, monkeypatch):
    monkeypatch.setattr(AgentLoop, "_register_builtin_hooks", lambda self: None)
    monkeypatch.setattr(AgentLoop, "_register_default_tools", lambda self: None)
    monkeypatch.setattr("vikingbot.agent.loop.SubagentManager", lambda **kwargs: SimpleNamespace())

    metadata = {"reply_to": "oc_chat", "chat_type": "group", "message_id": "om_old"}
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
    loop.context = SimpleNamespace(build_messages=fake_build_messages)
    loop._run_agent_loop = fake_run_agent_loop

    outbound = await loop._process_system_message(
        InboundMessage(
            sender_id="subagent",
            session_key=session_key,
            content="subagent done",
            metadata=metadata,
        )
    )

    assert outbound.content == "summary"
    assert outbound.metadata == metadata
    assert outbound.metadata is not metadata


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


def test_cron_service_accepts_missing_channel_metadata(tmp_path):
    service = CronService(tmp_path / "jobs.json")
    job = service.add_job(
        name="cli-job",
        schedule=CronSchedule(kind="every", every_ms=3600),
        message="hello",
        session_key=SessionKey(type="cli", channel_id="default", chat_id="default"),
        deliver=True,
    )

    assert job.payload.channel_metadata == {}


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


@pytest.mark.asyncio
async def test_feishu_send_skips_normal_message_without_reply_to():
    channel = FeishuChannel(FeishuChannelConfig(app_id="cli_app"), MessageBus())
    channel._client = object()

    await channel.send(
        OutboundMessage(
            session_key=SessionKey(type="feishu", channel_id="cli_app", chat_id="oc_chat"),
            content="hello",
        )
    )


@pytest.mark.asyncio
async def test_feishu_upload_image_uses_detected_jpeg_format(monkeypatch):
    channel = FeishuChannel(FeishuChannelConfig(app_id="cli_app"), MessageBus())
    jpeg_bytes = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01fake-jpeg"
    captured = {}

    async def fake_token():
        return "tenant-token"

    class FakeResponse:
        is_error = False
        status_code = 200
        text = ""

        def raise_for_status(self):
            return None

        def json(self):
            return {"code": 0, "data": {"image_key": "img_key"}}

    class FakeClient:
        def __init__(self, timeout):
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def post(self, url, headers, data, files):
            captured.update(url=url, headers=headers, data=data, files=files)
            return FakeResponse()

    monkeypatch.setattr(channel, "_get_tenant_access_token", fake_token)
    monkeypatch.setattr("vikingbot.channels.feishu.httpx.AsyncClient", FakeClient)

    image_key = await channel._upload_image_to_feishu(jpeg_bytes)

    filename, file_obj, mime_type = captured["files"]["image"]
    assert image_key == "img_key"
    assert filename == "image.jpg"
    assert mime_type == "image/jpeg"
    assert file_obj.read() == jpeg_bytes


@pytest.mark.asyncio
async def test_feishu_upload_retries_with_normalized_image_after_bad_request(monkeypatch):
    channel = FeishuChannel(FeishuChannelConfig(app_id="cli_app"), MessageBus())
    original_bytes = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01original-with-metadata"
    normalized_bytes = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01normalized"
    uploads = []

    async def fake_token():
        return "tenant-token"

    class FakeResponse:
        def __init__(self, status_code, body):
            self.status_code = status_code
            self.text = body
            self.is_error = status_code >= 400

        def json(self):
            return {"code": 0, "data": {"image_key": "img_key"}}

    class FakeClient:
        def __init__(self, timeout):
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def post(self, url, headers, data, files):
            filename, file_obj, mime_type = files["image"]
            uploads.append((filename, mime_type, file_obj.read()))
            if len(uploads) == 1:
                return FakeResponse(400, '{"code":234011,"msg":"can not recognize image"}')
            return FakeResponse(200, '{"code":0}')

    monkeypatch.setattr(channel, "_get_tenant_access_token", fake_token)
    monkeypatch.setattr(channel, "_normalize_image_for_feishu", lambda data: normalized_bytes)
    monkeypatch.setattr("vikingbot.channels.feishu.httpx.AsyncClient", FakeClient)

    image_key = await channel._upload_image_to_feishu(original_bytes)

    assert image_key == "img_key"
    assert uploads == [
        ("image.jpg", "image/jpeg", original_bytes),
        ("image.jpg", "image/jpeg", normalized_bytes),
    ]

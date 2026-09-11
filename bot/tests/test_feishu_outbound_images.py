# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Regression coverage for knowledge-base images in Feishu cards."""

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from vikingbot.bus.events import OutboundMessage
from vikingbot.bus.queue import MessageBus
from vikingbot.channels.feishu import FeishuChannel
from vikingbot.channels.manager import ChannelManager
from vikingbot.config.schema import Config, FeishuChannelConfig, SessionKey
from vikingbot.openviking_mount.ov_server import VikingClient
from vikingbot.utils.session_paths import workspace_name

from openviking.utils.media_limits import MAX_INLINE_TOOL_RESULT_MEDIA_BYTES

PNG = b"\x89PNG\r\n\x1a\nfake-png"
URI = "viking://resources/knowledge/image.png"


@pytest.fixture
def delivery(monkeypatch):
    config = Config()
    channel = FeishuChannel(FeishuChannelConfig(app_id="cli_app"), MessageBus(), bot_config=config)
    client = SimpleNamespace(
        stat=AsyncMock(return_value={"size": len(PNG)}),
        download_bytes=AsyncMock(return_value=PNG),
        close=AsyncMock(),
    )
    create = AsyncMock(return_value=client)
    monkeypatch.setattr(VikingClient, "create", create)
    channel._upload_image_to_feishu = AsyncMock(return_value="img_uploaded")
    msg = OutboundMessage(
        session_key=SessionKey(type="feishu", channel_id="cli_app", chat_id="oc_chat"),
        content=f"正文\n![示意图]({URI})\n结尾",
        metadata={"reply_to": "oc_chat", "sender_id": "ou_sender"},
    )
    return channel, client, create, msg, config


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "reference, expected_uri",
    [
        (f"![示意图]({URI})", URI),
        (f'![示意图](<{URI}> "标题")', URI),
        ("![图片](viking://~/resources/image.PNG)", "viking://~/resources/image.PNG"),
        (
            "![图片](viking://resources/image.png?version=1#preview)",
            "viking://resources/image.png?version=1#preview",
        ),
        ("![图片](viking://resources/image(1).png)", "viking://resources/image(1).png"),
        (
            "![图片](<viking://resources/image with spaces.png>)",
            "viking://resources/image with spaces.png",
        ),
    ],
)
async def test_viking_image_upload_uses_sender_and_config(delivery, reference, expected_uri):
    channel, client, create, msg, config = delivery
    cleaned, images = await channel._extract_and_upload_images(f"正文\n{reference}\n结尾", msg)

    assert cleaned == "正文\n\n结尾"
    assert images == [{"image_key": "img_uploaded"}]
    create.assert_awaited_once_with(
        workspace_name(msg.session_key, config.sandbox.mode, portable=False),
        actor_peer_id="ou_sender",
        config=config,
    )
    client.stat.assert_awaited_once_with(expected_uri)
    client.download_bytes.assert_awaited_once_with(expected_uri)
    channel._upload_image_to_feishu.assert_awaited_once_with(PNG)
    client.close.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("scheme", ["send", "viking"])
async def test_repeated_images_upload_once_and_viking_citation_stays(delivery, scheme):
    channel, client, create, msg, config = delivery
    uri = f"{scheme}://image.png"
    channel._parse_data_uri = AsyncMock(return_value=(False, PNG))
    cleaned, images = await channel._extract_and_upload_images(
        f"![图片]({uri})\n{uri}\n![重复]({uri})", msg
    )

    assert cleaned == (uri if scheme == "viking" else "")
    assert images == [{"image_key": "img_uploaded"}]
    channel._upload_image_to_feishu.assert_awaited_once_with(PNG)
    if scheme == "send":
        create.assert_not_awaited()
        channel._parse_data_uri.assert_awaited_once_with(uri)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "content",
    [
        URI,
        f"- `{URI}`",
        f"[查看图片]({URI})",
        f'[查看图片]({URI} "标题")',
        f"`![示例]({URI})`",
        f"``![示例]({URI})``",
        f"```markdown\n![示例]({URI})\n```",
        f"~~~markdown\n![示例]({URI})\n~~~",
        f"```markdown\n![示例]({URI})",  # Unclosed code fence
        f"\\![示例]({URI})",
        "`send://image.png`",
        "```markdown\n![示例](send://image.png)\n```",
    ],
)
async def test_citations_and_code_examples_do_not_send_images(delivery, content):
    channel, client, create, msg, config = delivery

    assert await channel._extract_and_upload_images(content, msg) == (content, [])
    create.assert_not_awaited()
    channel._upload_image_to_feishu.assert_not_awaited()


@pytest.mark.asyncio
async def test_explicit_image_after_code_block_still_sends(delivery):
    channel, client, create, msg, config = delivery
    example = f"```markdown\n![示例]({URI})\n```"
    content = f"{example}\n图片如下：\n![照片]({URI})\n来源：`{URI}`"

    cleaned, images = await channel._extract_and_upload_images(content, msg)

    assert cleaned == f"{example}\n图片如下：\n\n来源：`{URI}`"
    assert images == [{"image_key": "img_uploaded"}]
    channel._upload_image_to_feishu.assert_awaited_once_with(PNG)


@pytest.mark.asyncio
async def test_image_listing_card_preserves_uri_without_attaching_image(delivery):
    pytest.importorskip("lark_oapi")
    channel, client, create, msg, config = delivery
    msg.content = (
        f"目前 OpenViking 中有 **1 张图片**：\n\n- `{URI}`\n\n"
        "如果你愿意，我还可以继续帮你查看这张图片。"
    )
    send = Mock(return_value=SimpleNamespace(success=lambda: True))
    channel._client = SimpleNamespace(
        im=SimpleNamespace(v1=SimpleNamespace(message=SimpleNamespace(create=send)))
    )

    await channel.send(msg)

    send.assert_called_once()
    card = json.loads(send.call_args.args[0].request_body.content)
    assert card["elements"] == [{"tag": "markdown", "content": msg.content}]
    create.assert_not_awaited()
    channel._upload_image_to_feishu.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("channel_type", ["feishu", "telegram"])
async def test_image_delivery_instructions_are_scoped_to_feishu(tmp_path, channel_type):
    from vikingbot.agent.context import ContextBuilder

    context = ContextBuilder(tmp_path)
    context._templates_ensured = True
    context._skills = SimpleNamespace(get_always_skills=lambda: [], build_skills_summary=lambda: "")
    session_key = SessionKey(type=channel_type, channel_id="app", chat_id="chat")

    prompt = await context.build_system_prompt(session_key, ov_tools_enable=False)

    if channel_type == "feishu":
        assert "![description](viking://...) outside code" in prompt
        assert "To cite an image URI without displaying it, use inline code" in prompt
        assert "Reading an image does not send it to the user" in prompt
    else:
        assert "## Feishu images" not in prompt


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["stat", "download_bytes", "upload"])
async def test_image_failure_keeps_body_and_removes_invalid_markdown(delivery, stage):
    channel, client, create, msg, config = delivery
    operation = channel._upload_image_to_feishu if stage == "upload" else getattr(client, stage)
    operation.side_effect = RuntimeError("image unavailable")

    cleaned, images = await channel._extract_and_upload_images(msg.content, msg)

    assert cleaned == "正文\n[图片发送失败]\n结尾"
    assert images == []
    client.close.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("size", [None, True, -1, 0, MAX_INLINE_TOOL_RESULT_MEDIA_BYTES + 1])
async def test_invalid_image_size_is_not_downloaded(delivery, size):
    channel, client, create, msg, config = delivery
    client.stat.return_value = {"size": size}

    cleaned, images = await channel._extract_and_upload_images(msg.content, msg)

    assert "[图片发送失败]" in cleaned
    assert images == []
    client.download_bytes.assert_not_awaited()
    channel._upload_image_to_feishu.assert_not_awaited()
    client.close.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("data", [b"not an image", PNG + b"changed"])
async def test_invalid_or_changed_bytes_are_not_uploaded(delivery, data):
    channel, client, create, msg, config = delivery
    client.download_bytes.return_value = data

    cleaned, images = await channel._extract_and_upload_images(msg.content, msg)

    assert "[图片发送失败]" in cleaned
    assert images == []
    channel._upload_image_to_feishu.assert_not_awaited()
    client.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_missing_sender_does_not_fall_back_to_unscoped_access(delivery):
    channel, client, create, msg, config = delivery
    msg.metadata.pop("sender_id")

    cleaned, images = await channel._extract_and_upload_images(msg.content, msg)

    assert "viking://" not in cleaned
    assert images == []
    create.assert_not_awaited()


@pytest.mark.asyncio
async def test_failed_image_does_not_block_other_images(delivery):
    channel, client, create, msg, config = delivery
    channel._upload_image_to_feishu.side_effect = [RuntimeError("rejected"), "img_second"]

    cleaned, images = await channel._extract_and_upload_images(
        f"正文 ![坏图]({URI}) ![好图](viking://resources/other.png) 结尾", msg
    )

    assert cleaned == "正文 [图片发送失败]  结尾"
    assert images == [{"image_key": "img_second"}]


@pytest.mark.asyncio
async def test_normal_text_and_web_images_are_preserved(delivery):
    channel, client, create, msg, config = delivery
    content = "正文 [链接](https://example.com) ![图片](https://example.com/image.png)"

    assert await channel._extract_and_upload_images(content, msg) == (content, [])
    create.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("reply", [False, True])
@pytest.mark.parametrize("fail", [False, True])
async def test_send_builds_valid_card_after_image_conversion(delivery, reply, fail):
    pytest.importorskip("lark_oapi")
    channel, client, create, msg, config = delivery
    if reply:
        msg.metadata["message_id"] = "om_original"
    if fail:
        client.download_bytes.side_effect = RuntimeError("not found")
    send = Mock(return_value=SimpleNamespace(success=lambda: True))
    channel._client = SimpleNamespace(
        im=SimpleNamespace(v1=SimpleNamespace(message=SimpleNamespace(create=send, reply=send)))
    )

    await channel.send(msg)

    send.assert_called_once()
    request = send.call_args.args[0]
    assert request.request_body.msg_type == "interactive"
    card = json.loads(request.request_body.content)
    assert "viking://" not in request.request_body.content
    assert "正文" in request.request_body.content
    image_elements = [element for element in card["elements"] if element["tag"] == "img"]
    if fail:
        assert image_elements == []
        assert "图片发送失败" in request.request_body.content
    else:
        assert [element["img_key"] for element in image_elements] == ["img_uploaded"]


def test_manager_passes_active_config_to_feishu():
    config = Config(channels=[FeishuChannelConfig(app_id="cli_app", enabled=True)])
    manager = ChannelManager(MessageBus())

    manager.load_channels_from_config(config)

    channel = next(iter(manager.channels.values()))
    assert channel._bot_config is config

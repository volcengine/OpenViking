from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from telegram import Chat, DirectMessagesTopic, Message, Update, User
from vikingbot.bus.events import OutboundMessage
from vikingbot.bus.queue import MessageBus
from vikingbot.channels.telegram import TelegramChannel
from vikingbot.config.schema import SessionKey, TelegramChannelConfig

CHAT_ID = 722341991
TOPIC_ID = 31189


class RecordingBot:
    def __init__(self, fail_html_once=False):
        self.calls = []
        self.fail_html_once = fail_html_once

    async def send_photo(self, **kwargs):
        self.calls.append(("send_photo", kwargs))

    async def send_message(self, **kwargs):
        self.calls.append(("send_message", kwargs))
        if self.fail_html_once and kwargs.get("parse_mode") == "HTML":
            self.fail_html_once = False
            raise RuntimeError("invalid HTML")


class NoTypingTelegramChannel(TelegramChannel):
    def _start_typing(self, chat_id: str) -> None:
        return None


def make_update(text="hello", with_topic=True):
    user = User(id=1007, first_name="Ada", is_bot=False, username="ada")
    message = Message(
        message_id=9001,
        date=datetime(2026, 10, 13, tzinfo=timezone.utc),
        chat=Chat(id=CHAT_ID, type=Chat.PRIVATE),
        from_user=user,
        text=text,
        direct_messages_topic=(
            DirectMessagesTopic(topic_id=TOPIC_ID, user=user) if with_topic else None
        ),
    )
    return Update(update_id=1, message=message)


def make_channel(bot=None):
    channel = NoTypingTelegramChannel(TelegramChannelConfig(token="123:token"), MessageBus())
    channel._app = SimpleNamespace(bot=bot) if bot else None
    return channel


def outbound(metadata, content="reply"):
    return OutboundMessage(
        session_key=SessionKey(type="telegram", channel_id="123", chat_id=str(CHAT_ID)),
        content=content,
        metadata=metadata,
    )


@pytest.mark.parametrize(
    ("handler_name", "text"),
    [("_on_message", "hello"), ("_forward_command", "/new")],
)
@pytest.mark.asyncio
async def test_native_dm_topic_round_trip(handler_name, text):
    bot = RecordingBot()
    channel = make_channel(bot)

    await getattr(channel, handler_name)(make_update(text), None)
    inbound = channel.bus.inbound.get_nowait()
    await channel.send(outbound(inbound.metadata))

    assert inbound.metadata["direct_messages_topic_id"] == TOPIC_ID
    assert [(name, call["direct_messages_topic_id"]) for name, call in bot.calls] == [
        ("send_message", TOPIC_ID)
    ]


@pytest.mark.asyncio
async def test_native_dm_topic_routes_images_and_plain_text_fallback(tmp_path):
    image_path = tmp_path / "image.png"
    image_path.write_bytes(b"image")
    bot = RecordingBot(fail_html_once=True)
    channel = make_channel(bot)

    await channel.send(
        outbound(
            {"direct_messages_topic_id": TOPIC_ID},
            content=f"`{image_path}` reply",
        )
    )

    assert [name for name, _call in bot.calls] == [
        "send_photo",
        "send_message",
        "send_message",
    ]
    assert all(call["direct_messages_topic_id"] == TOPIC_ID for _name, call in bot.calls)


@pytest.mark.asyncio
async def test_plain_chat_omits_native_dm_topic_argument():
    bot = RecordingBot()
    channel = make_channel(bot)

    await channel.send(outbound({}))

    assert len(bot.calls) == 1
    assert "direct_messages_topic_id" not in bot.calls[0][1]

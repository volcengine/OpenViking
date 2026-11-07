# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Regression coverage for automatic email response loops."""

from datetime import date
from email import policy
from email.message import EmailMessage

import pytest
from vikingbot.bus.events import OutboundMessage
from vikingbot.bus.queue import MessageBus
from vikingbot.channels.email import EmailChannel
from vikingbot.config.schema import EmailChannelConfig, SessionKey


class FakeIMAP:
    def __init__(self, raw_message: bytes):
        self.raw_message = raw_message
        self.store_calls = []

    def login(self, *_args):
        return "OK", [b"authenticated"]

    def select(self, *_args):
        return "OK", [b"1"]

    def search(self, *_args):
        return "OK", [b"1"]

    def fetch(self, *_args):
        descriptor = f"1 (UID 42 BODY[] {{{len(self.raw_message)}}})".encode()
        return "OK", [(descriptor, self.raw_message), b")"]

    def store(self, *args):
        self.store_calls.append(args)
        return "OK", [b""]

    def logout(self):
        return "BYE", [b""]


def email_config() -> EmailChannelConfig:
    return EmailChannelConfig(
        consent_granted=True,
        imap_host="imap.invalid",
        imap_username="vikingbot@example.com",
        imap_password="unused",
        smtp_host="smtp.invalid",
        smtp_username="vikingbot@example.com",
        smtp_password="unused",
        from_address="vikingbot@example.com",
    )


def raw_message(auto_submitted: str | None) -> bytes:
    message = EmailMessage()
    message["From"] = "sender@example.com"
    message["To"] = "vikingbot@example.com"
    message["Subject"] = "Hello"
    message["Message-ID"] = "<message@example.com>"
    if auto_submitted is not None:
        message["Auto-Submitted"] = auto_submitted
    message.set_content("Hello from the sender")
    return message.as_bytes(policy=policy.default)


def channel_with_message(monkeypatch, auto_submitted: str | None):
    imap = FakeIMAP(raw_message(auto_submitted))
    monkeypatch.setattr("vikingbot.channels.email.imaplib.IMAP4_SSL", lambda *_args: imap)
    return EmailChannel(email_config(), MessageBus()), imap


@pytest.mark.parametrize(
    ("auto_submitted", "expected_count"),
    [
        (None, 1),
        ("no", 1),
        ("NO (manual test)", 1),
        ("auto-replied", 0),
        ("auto-generated; x-reason=probe", 0),
    ],
)
def test_live_poll_does_not_admit_automatic_email(monkeypatch, auto_submitted, expected_count):
    channel, imap = channel_with_message(monkeypatch, auto_submitted)

    assert len(channel._fetch_new_messages()) == expected_count
    assert len(imap.store_calls) == 1


def test_historical_fetch_preserves_automatic_email(monkeypatch):
    channel, imap = channel_with_message(monkeypatch, "auto-replied")

    messages = channel.fetch_messages_between_dates(date(2026, 1, 1), date(2026, 1, 2))

    assert len(messages) == 1
    assert imap.store_calls == []


@pytest.mark.asyncio
async def test_outbound_email_is_marked_as_automatic(monkeypatch):
    channel = EmailChannel(email_config(), MessageBus())
    sent = []
    monkeypatch.setattr(channel, "_smtp_send", sent.append)

    await channel.send(
        OutboundMessage(
            session_key=SessionKey(
                type="email",
                channel_id="vikingbot@example.com",
                chat_id="sender@example.com",
            ),
            content="Automatic reply",
        )
    )

    assert len(sent) == 1
    assert sent[0]["Auto-Submitted"] == "auto-replied"

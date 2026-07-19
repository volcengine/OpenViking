# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Unit tests for EmailAccessor and the accessor sync standard types."""

from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from email.utils import format_datetime
from pathlib import Path
from typing import Dict, List, Optional

import pytest

from openviking.parse.accessors.base import (
    AccessError,
    AccessResult,
    ConnectionStatus,
    DataAccessor,
    LocalResource,
)
from openviking.parse.accessors.email_accessor import (
    SYNC_CHECKPOINT_KEY,
    EmailAccessor,
)
from openviking_cli.exceptions import OpenVikingError

UTC = timezone.utc


def _raw_email(
    subject: str,
    message_id: Optional[str],
    date: Optional[datetime],
    body: str = "hello",
    to: str = "rcpt@example.com",
) -> bytes:
    msg = EmailMessage()
    msg["From"] = "sender@example.com"
    msg["To"] = to
    msg["Subject"] = subject
    if message_id:
        msg["Message-ID"] = message_id
    if date:
        msg["Date"] = format_datetime(date)
    msg.set_content(body)
    return bytes(msg)


class FakeIMAP:
    """Minimal IMAP4-like double: mailbox -> {uid: raw rfc822 bytes}."""

    def __init__(self, mailboxes: Dict[str, Dict[bytes, bytes]]):
        self.mailboxes = mailboxes
        self.login_calls: List[tuple] = []
        self.search_criteria: List[str] = []
        self.logged_out = False
        self._selected: Optional[str] = None
        self.fail_fetch_uids: set = set()

    def login(self, username: str, password: str):
        self.login_calls.append((username, password))
        return "OK", [b"Logged in"]

    def select(self, mailbox: str, readonly: bool = False):
        name = mailbox.strip('"')
        if name not in self.mailboxes:
            return "NO", [b"nonexistent mailbox"]
        self._selected = name
        return "OK", [str(len(self.mailboxes[name])).encode()]

    def uid(self, command: str, *args):
        assert self._selected is not None
        box = self.mailboxes[self._selected]
        if command == "SEARCH":
            self.search_criteria.append(args[-1])
            uids = b" ".join(box.keys())
            return "OK", [uids]
        if command == "FETCH":
            uid = args[0].encode() if isinstance(args[0], str) else args[0]
            if uid in self.fail_fetch_uids:
                return "NO", [None]
            raw = box.get(uid)
            if raw is None:
                return "NO", [None]
            return "OK", [(b"1 (RFC822 {%d}" % len(raw), raw), b")"]
        raise AssertionError(f"unexpected IMAP command: {command}")

    def logout(self):
        self.logged_out = True
        return "BYE", [b"bye"]


def _accessor(fake: FakeIMAP) -> EmailAccessor:
    return EmailAccessor(imap_factory=lambda host, port, use_ssl, timeout: fake)


AUTH = {"password": "app-secret"}
SOURCE = "imaps://user%40example.com@mail.example.com/INBOX"


class TestStandardTypes:
    def test_access_result_defaults(self, tmp_path: Path) -> None:
        resource = LocalResource(path=tmp_path, source_type="email", original_source="x")
        result = AccessResult(resource=resource)
        assert result.cursor is None
        assert result.doc_ids is None
        assert result.errors == []

    def test_access_error_defaults(self) -> None:
        err = AccessError(doc_id=None, message="boom")
        assert err.kind == "permanent"

    def test_data_accessor_defaults(self) -> None:
        class Minimal(DataAccessor):
            def can_handle(self, source, **kwargs):
                return False

            async def access(self, source, **kwargs):
                raise NotImplementedError

            @property
            def priority(self):
                return 10

        accessor = Minimal()
        assert accessor.auth_spec() is None
        status = accessor.check({})
        assert isinstance(status, ConnectionStatus)
        assert status.success is True


class TestEmailAccessorBasics:
    def test_can_handle_imap_schemes(self) -> None:
        accessor = EmailAccessor()
        assert accessor.can_handle("imap://u@h/INBOX")
        assert accessor.can_handle("imaps://u@h:993/INBOX")
        assert not accessor.can_handle("https://example.com")
        assert not accessor.can_handle("/local/path")

    def test_auth_spec_required_fields(self) -> None:
        spec = EmailAccessor().auth_spec()
        assert spec is not None
        assert set(spec["required"]) == {"host", "username", "password"}
        assert set(spec["properties"]) == {"host", "port", "use_ssl", "username", "password"}

    def test_check_success_and_failure(self) -> None:
        fake = FakeIMAP({"INBOX": {}})
        auth = {"host": "mail.example.com", "username": "u", "password": "p"}
        assert _accessor(fake).check(auth).success is True

        def broken_factory(host, port, use_ssl, timeout):
            raise ConnectionRefusedError("no route")

        status = EmailAccessor(imap_factory=broken_factory).check(auth)
        assert status.success is False
        assert "no route" in status.message

    async def test_missing_credentials_rejected(self) -> None:
        fake = FakeIMAP({"INBOX": {}})
        with pytest.raises(OpenVikingError):
            await _accessor(fake).access("imaps://mail.example.com/INBOX")


class TestEmailAccessorSync:
    async def test_full_sync_writes_markdown_and_doc_ids(self) -> None:
        now = datetime.now(UTC)
        fake = FakeIMAP(
            {
                "INBOX": {
                    b"1": _raw_email("First", "<id-1@example.com>", now - timedelta(days=2)),
                    b"2": _raw_email("Second", None, now - timedelta(days=1)),
                }
            }
        )
        result = await _accessor(fake).access(SOURCE, auth=AUTH)

        assert isinstance(result, AccessResult)
        assert result.resource.source_type == "email"
        assert result.cursor is not None and SYNC_CHECKPOINT_KEY in result.cursor
        # Full view: doc_ids returned; Message-ID used, uid fallback for the second
        assert result.doc_ids is not None and len(result.doc_ids) == 2
        assert "<id-1@example.com>" in result.doc_ids
        assert any(doc_id.endswith("_2") for doc_id in result.doc_ids)
        assert result.errors == []

        inbox_dir = result.resource.path / "INBOX"
        files = sorted(p.name for p in inbox_dir.glob("*.md"))
        assert len(files) == 2
        content = (inbox_dir / files[0]).read_text(encoding="utf-8")
        assert content.startswith("# ")
        assert "**Mailbox**: INBOX" in content
        # URL userinfo decoded and used for login
        assert fake.login_calls == [("user@example.com", "app-secret")]
        assert fake.logged_out is True
        result.resource.cleanup()

    async def test_incremental_strict_filter_and_no_doc_ids(self) -> None:
        checkpoint = datetime(2026, 7, 10, 12, 0, 0, tzinfo=UTC)
        fake = FakeIMAP(
            {
                "INBOX": {
                    b"1": _raw_email("Old", "<old@example.com>", checkpoint - timedelta(hours=1)),
                    b"2": _raw_email("Boundary", "<boundary@example.com>", checkpoint),
                    b"3": _raw_email("New", "<new@example.com>", checkpoint + timedelta(hours=1)),
                }
            }
        )
        cursor = {SYNC_CHECKPOINT_KEY: "2026-07-10T12:00:00Z"}
        result = await _accessor(fake).access(SOURCE, auth=AUTH, cursor=cursor)

        # Strict `<` filter: old skipped, boundary (== checkpoint) re-pulled
        files = list((result.resource.path / "INBOX").glob("*.md"))
        names = {p.name for p in files}
        assert len(files) == 2
        assert not any("old" in n for n in names)
        # Incremental view never returns doc_ids
        assert result.doc_ids is None
        # Server-side coarse filter used the checkpoint date
        assert fake.search_criteria == ["SINCE 10-Jul-2026"]
        result.resource.cleanup()

    async def test_since_days_disables_doc_ids(self) -> None:
        fake = FakeIMAP({"INBOX": {b"1": _raw_email("Hi", "<a@b>", datetime.now(UTC))}})
        result = await _accessor(fake).access(SOURCE, auth=AUTH, since_days=7)
        assert result.doc_ids is None
        assert result.cursor is not None
        result.resource.cleanup()

    async def test_single_message_failure_is_isolated(self) -> None:
        now = datetime.now(UTC)
        fake = FakeIMAP(
            {
                "INBOX": {
                    b"1": _raw_email("Good", "<good@example.com>", now),
                    b"2": _raw_email("Bad", "<bad@example.com>", now),
                }
            }
        )
        fake.fail_fetch_uids = {b"2"}
        result = await _accessor(fake).access(SOURCE, auth=AUTH)

        # Good message synced, bad one isolated as a permanent error
        assert result.doc_ids == {"<good@example.com>"}
        assert len(result.errors) == 1
        assert result.errors[0].kind == "permanent"
        assert result.errors[0].doc_id is not None and result.errors[0].doc_id.endswith("_2")
        # Cursor still advances: the run itself completed
        assert result.cursor is not None
        result.resource.cleanup()

    async def test_mailbox_failure_aborts_without_cursor(self) -> None:
        now = datetime.now(UTC)
        fake = FakeIMAP({"INBOX": {b"1": _raw_email("Hi", "<x@y>", now)}})
        with pytest.raises(OpenVikingError) as exc_info:
            await _accessor(fake).access(
                SOURCE, auth=AUTH, mailboxes=["INBOX", "Archive"]
            )
        assert "Archive" in str(exc_info.value)

    async def test_progress_callback_invoked(self) -> None:
        now = datetime.now(UTC)
        fake = FakeIMAP(
            {
                "INBOX": {
                    b"1": _raw_email("A", "<a@example.com>", now),
                    b"2": _raw_email("B", "<b@example.com>", now),
                }
            }
        )
        calls: List[Dict] = []
        result = await _accessor(fake).access(
            SOURCE, auth=AUTH, progress=lambda **kw: calls.append(kw)
        )
        assert [c["done"] for c in calls] == [1, 2]
        result.resource.cleanup()

    async def test_multiple_mailboxes_one_dir_each(self) -> None:
        now = datetime.now(UTC)
        fake = FakeIMAP(
            {
                "INBOX": {b"1": _raw_email("A", "<a@example.com>", now)},
                "Sent": {b"1": _raw_email("B", "<b@example.com>", now)},
            }
        )
        result = await _accessor(fake).access(
            SOURCE, auth=AUTH, mailboxes=["INBOX", "Sent"]
        )
        assert (result.resource.path / "INBOX").is_dir()
        assert (result.resource.path / "Sent").is_dir()
        # Same uid in different mailboxes must not collide (mailbox-hash fallback)
        assert result.doc_ids == {"<a@example.com>", "<b@example.com>"}
        result.resource.cleanup()

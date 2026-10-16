# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Download tokens bind one file and identity without retaining credentials."""

from dataclasses import asdict

import pytest

from openviking.server.download_token_store import DownloadTokenError, DownloadTokenStore
from openviking.server.identity import RequestContext, Role
from openviking_cli.session.user_id import UserIdentifier


def _context():
    return RequestContext(
        user=UserIdentifier("account-a", "alice"),
        role=Role(Role.ADMIN),
        actor_peer_id="peer-a",
        from_oauth=True,
        api_key="must-not-be-retained",
        bypass_acl=True,
        group_ids=("old-group",),
    )


def test_download_token_binds_one_file_and_identity_without_credentials():
    store = DownloadTokenStore()
    token, _ = store.issue("viking://resources/report.pdf", _context())

    record = store.consume(token)

    assert record.uri == "viking://resources/report.pdf"
    assert record.account_id == "account-a"
    assert record.user_id == "alice"
    assert record.role == Role.ADMIN
    assert record.actor_peer_id == "peer-a"
    assert record.from_oauth is True
    assert {"api_key", "bypass_acl", "group_ids"}.isdisjoint(asdict(record))
    assert "must-not-be-retained" not in repr(record)
    assert len(token) >= 43


def test_download_token_cannot_be_replayed():
    store = DownloadTokenStore()
    token, _ = store.issue("viking://resources/report.pdf", _context())
    store.consume(token)

    with pytest.raises(DownloadTokenError, match="unknown or already-consumed"):
        store.consume(token)


@pytest.mark.parametrize("token", ["", "unknown-token"])
def test_invalid_download_token_is_rejected(token):
    with pytest.raises(DownloadTokenError):
        DownloadTokenStore().consume(token)


def test_download_token_expires_at_the_deadline(monkeypatch):
    import openviking.server.download_token_store as module

    now = [1000.0]
    monkeypatch.setattr(module.time, "time", lambda: now[0])
    store = DownloadTokenStore()
    token, expires_at = store.issue("viking://resources/report.pdf", _context(), 60)
    assert expires_at == 1060.0
    now[0] = 1060.0

    with pytest.raises(DownloadTokenError, match="expired"):
        store.consume(token)
    with pytest.raises(DownloadTokenError, match="unknown or already-consumed"):
        store.consume(token)


def test_issuing_download_token_purges_expired_records(monkeypatch):
    import openviking.server.download_token_store as module

    now = [1000.0]
    monkeypatch.setattr(module.time, "time", lambda: now[0])
    store = DownloadTokenStore()
    expired, _ = store.issue("viking://resources/expired.pdf", _context(), 1)
    valid, _ = store.issue("viking://resources/valid.pdf", _context(), 60)
    now[0] = 1001.0
    store.issue("viking://resources/new.pdf", _context())

    with pytest.raises(DownloadTokenError, match="unknown or already-consumed"):
        store.consume(expired)
    assert store.consume(valid).uri == "viking://resources/valid.pdf"


def test_token_collision_cannot_replace_an_existing_download(monkeypatch):
    import openviking.server.download_token_store as module

    tokens = iter(["first-token", "first-token", "second-token"])
    monkeypatch.setattr(module.secrets, "token_urlsafe", lambda size: next(tokens))
    store = DownloadTokenStore()
    first, _ = store.issue("viking://resources/first.pdf", _context())
    second, _ = store.issue("viking://resources/second.pdf", _context())

    assert store.consume(first).uri == "viking://resources/first.pdf"
    assert store.consume(second).uri == "viking://resources/second.pdf"

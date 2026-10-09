# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Shared owner classification, initial deadlines and expiry visibility."""

from __future__ import annotations

import types
from datetime import datetime, timezone

import pytest

import openviking.core.ttl as ttl
from openviking_cli.utils.config import TTLConfig


def _install_config(monkeypatch, config: TTLConfig | None) -> None:
    """Point the ttl module's config seam at a specific TTLConfig (or raise)."""

    def _fake_get_config():
        if config is None:
            raise RuntimeError("config not initialized")
        return types.SimpleNamespace(ttl=config)

    monkeypatch.setattr(ttl, "get_openviking_config", _fake_get_config)


@pytest.mark.parametrize(
    "uri, expected",
    [
        ("viking://user/u1/memories/events/2026/e.md", "user_events"),
        ("viking://user/u1/memories/events/2026/09/28", "user_events"),
        ("viking://user/u1/peers/p1/memories/events/e.md", "peer_events"),
        ("viking://user/u1/sessions/s1", "sessions"),
        ("viking://user/u1/sessions/s1/messages.jsonl", "sessions"),
        # Out of scope: never TTL these.
        ("viking://user/u1/memories/notes/n.md", None),
        ("viking://user/u1/preferences/p", None),
        ("viking://user/u1/resources/r.md", None),
        ("viking://user/u1/peers/p1/memories/notes/n.md", None),
        ("viking://user/u1", None),
        ("not-a-viking-uri", None),
    ],
)
def test_ttl_scope_for_uri(uri, expected):
    assert ttl.ttl_scope_for_uri(uri) == expected


@pytest.mark.parametrize("owner", ["user/u1", "user/u1/peers/p1"])
@pytest.mark.parametrize(
    "suffix", ["", "/event.md", "/.note", "/nested/event.custom", "/.abstract.md", "/.overview.md"]
)
def test_event_directory_and_descendants_share_one_owner(owner, suffix):
    root = f"viking://{owner}/memories/events/2026/09/28"
    assert ttl.ttl_object_for_uri(root + suffix) == (ttl.OBJECT_TYPE_EVENT, root)


@pytest.mark.parametrize(
    "path",
    [
        "",
        "/.abstract.md",
        "/.overview.md",
        "/.relations.json",
        "/.path.ovlock",
        "/.exact.ovlock.event",
        "/.redirect.json",
        "/.sync_log.json",
        "/2026",
        "/2026/09",
        "/2026/02/30",
        "/2026/9/28",
        "/notes.md",
        "/events.txt",
    ],
)
def test_event_containers_and_derived_files_are_not_ttl_objects(path):
    uri = "viking://user/u1/memories/events" + path
    assert ttl.ttl_object_for_uri(uri) is None


def test_initial_ttl_fields_snapshot(monkeypatch):
    config = TTLConfig(user_events={"mode": "days", "ttl_days": 10})
    _install_config(monkeypatch, config)

    received = datetime(2026, 1, 1, tzinfo=timezone.utc)
    snap = ttl.initial_ttl_fields(
        "viking://user/u1/memories/events/2026/09/28", received_at=received
    )
    assert snap is not None
    assert snap == {
        "received_at": "2026-01-01T00:00:00.000Z",
        "expires_at": "2026-01-11T00:00:00.000Z",
    }


def test_initial_ttl_fields_none_when_out_of_scope(monkeypatch):
    config = TTLConfig(**{"global": {"mode": "days", "ttl_days": 5}})
    _install_config(monkeypatch, config)
    # Only lifecycle directories inherit TTL; files and resources do not.
    assert ttl.initial_ttl_fields("viking://user/u1/skills/r.md") is None
    assert ttl.initial_ttl_fields("viking://user/u1/resources/r.md") is None
    assert ttl.initial_ttl_fields("viking://user/u1/memories/events/2026/09/28/a.md") is None
    assert ttl.initial_ttl_fields("viking://user/u1/sessions/s1") is not None


def test_initial_ttl_fields_naive_received_at_treated_as_utc(monkeypatch):
    config = TTLConfig(sessions={"mode": "days", "ttl_days": 1})
    _install_config(monkeypatch, config)
    naive = datetime(2026, 5, 1, 12, 0, 0)  # no tzinfo
    snap = ttl.initial_ttl_fields("viking://user/u1/sessions/s1", received_at=naive)
    assert "received_at" not in snap
    assert snap["expires_at"] == "2026-05-02T12:00:00.000Z"


def test_missing_or_invalid_deadline_is_visible():
    for expiry in (None, "", "not-a-timestamp"):
        assert ttl.hidden_by_ttl(expiry) is False


def test_visibility_at_deadline():
    now = datetime(2026, 6, 1, tzinfo=timezone.utc)
    past = "2026-05-31T23:59:59.000Z"
    future = "2026-06-01T00:00:01.000Z"
    equal = "2026-06-01T00:00:00.000Z"
    assert ttl.hidden_by_ttl(past, now=now) is True
    assert ttl.hidden_by_ttl(equal, now=now) is True
    assert ttl.hidden_by_ttl(future, now=now) is False


def test_naive_deadline_is_treated_as_utc():
    now = datetime(2026, 6, 1, tzinfo=timezone.utc)
    assert ttl.hidden_by_ttl("2026-05-01T00:00:00", now=now) is True


@pytest.mark.parametrize(
    "config", [None, TTLConfig(), TTLConfig(sessions={"mode": "days", "ttl_days": 1})]
)
def test_visibility_uses_saved_deadline_independently_of_config(monkeypatch, config):
    _install_config(monkeypatch, config)
    enabled = config is not None and config.enabled
    assert ttl.ttl_enabled() is enabled
    assert (ttl.initial_ttl_fields("viking://user/u1/sessions/new") is not None) is enabled
    now = datetime(2026, 6, 1, tzinfo=timezone.utc)
    assert ttl.hidden_by_ttl("2026-05-01T00:00:00.000Z", now=now) is True
    assert ttl.hidden_by_ttl("2999-01-01T00:00:00.000Z", now=now) is False
    # Absent expiry stays visible even with TTL on.
    assert ttl.hidden_by_ttl("", now=now) is False

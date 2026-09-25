# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Unit tests for the central TTL resolution seam (``openviking.core.ttl``).

These cover the pure resolution/barrier logic that every writer and reader
shares: URI -> scope classification, frozen-snapshot computation, the
absent/future/expired rules of ``is_expired``/``hidden_by_ttl``, and the
``range_out`` read-barrier predicate. TTL is default OFF for object creation,
while already-frozen snapshots remain authoritative after a policy change.
"""

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


# ── Scope classification ────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "uri, expected",
    [
        ("viking://user/u1/memories/events/2026/e.md", "user_events"),
        ("viking://user/u1/memories/events/e.md", "user_events"),
        ("viking://user/u1/peers/p1/memories/events/e.md", "peer_events"),
        ("viking://user/u1/sessions/s1", "sessions"),
        ("viking://user/u1/sessions/s1/messages.jsonl", "sessions"),
        # Out of scope: never TTL these.
        ("viking://user/u1/memories/notes/n.md", None),
        ("viking://user/u1/preferences/p", None),
        ("viking://user/u1/resources/r.md", "resources"),
        ("viking://user/u1/peers/p1/memories/notes/n.md", None),
        ("viking://user/u1", None),
        ("not-a-viking-uri", None),
    ],
)
def test_ttl_scope_for_uri(uri, expected):
    assert ttl.ttl_scope_for_uri(uri) == expected


@pytest.mark.parametrize("owner", ["user/u1", "user/u1/peers/p1"])
@pytest.mark.parametrize("basename", ["event", ".note"])
@pytest.mark.parametrize(
    "extension",
    [
        ".md",
        ".MD",
        ".txt",
        ".TXT",
        ".json",
        ".yaml",
        ".yml",
        ".toml",
        ".py",
        ".js",
        ".ts",
        ".custom",
        "",
    ],
)
def test_event_objects_cover_all_public_content_write_extensions(owner, basename, extension):
    uri = f"viking://{owner}/memories/events/2026/{basename}{extension}"
    assert ttl.ttl_object_for_uri(uri) == (ttl.OBJECT_TYPE_EVENT, uri)


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
    ],
)
def test_event_containers_and_derived_files_are_not_ttl_objects(path):
    uri = "viking://user/u1/memories/events" + path
    assert ttl.ttl_object_for_uri(uri) is None


def test_event_directory_is_not_an_object_even_with_a_file_extension():
    for name in ("2026", "notes.md", "events.txt"):
        assert (
            ttl.ttl_object_for_uri(f"viking://user/u1/memories/events/{name}", is_dir=True) is None
        )


# ── resolve_ttl_days / freeze_ttl_fields ────────────────────────────────────


def test_resolve_ttl_days_uses_scope_then_global(monkeypatch):
    config = TTLConfig(
        **{"global": {"mode": "days", "ttl_days": 7}},
        user_events={"mode": "days", "ttl_days": 30},
        # sessions inherits -> global (7); peer_events disabled -> off
        peer_events={"mode": "disabled"},
    )
    _install_config(monkeypatch, config)

    assert ttl.resolve_ttl_days("viking://user/u1/memories/events/e.md") == 30
    assert ttl.resolve_ttl_days("viking://user/u1/sessions/s1") == 7
    assert ttl.resolve_ttl_days("viking://user/u1/peers/p1/memories/events/e.md") is None
    assert ttl.resolve_ttl_days("viking://user/u1/resources/r.md") is None
    # Out-of-scope URIs are never TTL'd even when global is on.
    assert ttl.resolve_ttl_days("viking://user/u1/skills/r.md") is None


def test_resolve_ttl_days_none_when_config_unavailable(monkeypatch):
    _install_config(monkeypatch, None)
    assert ttl.resolve_ttl_days("viking://user/u1/sessions/s1") is None


def test_resolve_ttl_days_uses_nearest_concrete_directory(monkeypatch):
    config = TTLConfig(
        user_events={"mode": "days", "ttl_days": 30},
        directories={
            "viking://user/u1/memories/events/project": {"mode": "days", "ttl_days": 5},
            "viking://user/u1/memories/events/project/keep": {"mode": "disabled"},
        },
    )
    _install_config(monkeypatch, config)
    assert ttl.resolve_ttl_days("viking://user/u1/memories/events/project/e.md") == 5
    assert ttl.resolve_ttl_days("viking://user/u1/memories/events/project/keep/e.md") is None
    # An out-of-scope URI never becomes TTL-managed merely because configured.
    assert ttl.resolve_ttl_days("viking://user/u1/resources/project/r.md") is None


def test_freeze_ttl_fields_snapshot(monkeypatch):
    config = TTLConfig(user_events={"mode": "days", "ttl_days": 10})
    _install_config(monkeypatch, config)

    received = datetime(2026, 1, 1, tzinfo=timezone.utc)
    snap = ttl.freeze_ttl_fields("viking://user/u1/memories/events/e.md", received_at=received)
    assert snap is not None
    generation = snap.pop("ttl_generation")
    assert generation
    assert snap == {
        "ttl_days": 10,
        "received_at": "2026-01-01T00:00:00.000Z",
        "expires_at": "2026-01-11T00:00:00.000Z",
    }


def test_freeze_ttl_fields_none_when_out_of_scope(monkeypatch):
    config = TTLConfig(**{"global": {"mode": "days", "ttl_days": 5}})
    _install_config(monkeypatch, config)
    # Only events and sessions inherit global; resources require a separate policy.
    assert ttl.freeze_ttl_fields("viking://user/u1/skills/r.md") is None
    assert ttl.freeze_ttl_fields("viking://user/u1/resources/r.md") is None
    assert ttl.freeze_ttl_fields("viking://user/u1/sessions/s1") is not None


def test_freeze_ttl_fields_naive_received_at_treated_as_utc(monkeypatch):
    config = TTLConfig(sessions={"mode": "days", "ttl_days": 1})
    _install_config(monkeypatch, config)
    naive = datetime(2026, 5, 1, 12, 0, 0)  # no tzinfo
    snap = ttl.freeze_ttl_fields("viking://user/u1/sessions/s1", received_at=naive)
    assert snap["received_at"] == "2026-05-01T12:00:00.000Z"
    assert snap["expires_at"] == "2026-05-02T12:00:00.000Z"


def test_apply_ttl_fields_owns_creation_and_renews_relative_ttl_on_update(monkeypatch):
    config = TTLConfig(user_events={"mode": "days", "ttl_days": 3})
    _install_config(monkeypatch, config)
    received = datetime(2026, 1, 1, tzinfo=timezone.utc)
    created = ttl.apply_ttl_fields(
        "viking://user/u1/memories/events/e.md",
        {"title": "x", "ttl_days": 999, "expires_at": "2999-01-01T00:00:00Z"},
        received_at=received,
    )
    assert created["ttl_days"] == 3
    assert created["expires_at"] == "2026-01-04T00:00:00.000Z"
    updated = ttl.apply_ttl_fields(
        "viking://user/u1/memories/events/e.md",
        {"title": "y", "ttl_days": 1},
        existing_fields=created,
        received_at=datetime(2026, 1, 10, tzinfo=timezone.utc),
    )
    assert updated["title"] == "y"
    assert updated["ttl_days"] == 3
    assert updated["received_at"] == "2026-01-10T00:00:00.000Z"
    assert updated["expires_at"] == "2026-01-13T00:00:00.000Z"
    assert updated["ttl_generation"] == created["ttl_generation"]


def test_apply_ttl_fields_preserves_explicit_absolute_deadline_on_update():
    existing = {
        "received_at": "2026-01-01T00:00:00.000Z",
        "expires_at": "2026-02-01T00:00:00.000Z",
        "ttl_generation": "absolute-generation",
    }
    updated = ttl.apply_ttl_fields(
        "viking://user/u1/memories/events/e.md",
        {"title": "changed"},
        existing_fields=existing,
        received_at=datetime(2026, 1, 10, tzinfo=timezone.utc),
    )
    assert updated == {
        "title": "changed",
        **existing,
        "received_at": "2026-01-10T00:00:00.000Z",
        "ttl_days": None,
    }


def test_compute_expires_at_day_granularity():
    received = datetime(2026, 3, 10, 6, 30, tzinfo=timezone.utc)
    assert ttl.compute_expires_at(received, 3) == datetime(2026, 3, 13, 6, 30, tzinfo=timezone.utc)


# ── is_expired: absent / future / past rules ────────────────────────────────


def test_is_expired_absent_is_never_expired():
    assert ttl.is_expired(None) is False
    assert ttl.is_expired("") is False
    assert ttl.is_expired("not-a-timestamp") is False


def test_is_expired_boundary_and_future():
    now = datetime(2026, 6, 1, tzinfo=timezone.utc)
    past = "2026-05-31T23:59:59.000Z"
    future = "2026-06-01T00:00:01.000Z"
    equal = "2026-06-01T00:00:00.000Z"
    assert ttl.is_expired(past, now=now) is True
    assert ttl.is_expired(equal, now=now) is True  # at-or-past
    assert ttl.is_expired(future, now=now) is False


def test_is_expired_naive_expiry_treated_as_utc():
    now = datetime(2026, 6, 1, tzinfo=timezone.utc)
    assert ttl.is_expired("2026-05-01T00:00:00", now=now) is True


# ── ttl_enabled / hidden_by_ttl snapshot semantics ─────


def test_disabled_config_stops_creation_but_does_not_revive_snapshots(monkeypatch):
    _install_config(monkeypatch, TTLConfig())  # default OFF
    assert ttl.ttl_enabled() is False
    assert ttl.freeze_ttl_fields("viking://user/u1/sessions/new") is None
    # A snapshot frozen while an earlier policy was active stays authoritative.
    assert ttl.hidden_by_ttl("2000-01-01T00:00:00.000Z") is True


def test_unavailable_config_stops_creation_without_reviving_snapshots(monkeypatch):
    _install_config(monkeypatch, None)
    assert ttl.ttl_enabled() is False
    assert ttl.hidden_by_ttl("2000-01-01T00:00:00.000Z") is True


def test_enabled_config_hides_expired_only(monkeypatch):
    _install_config(monkeypatch, TTLConfig(sessions={"mode": "days", "ttl_days": 1}))
    assert ttl.ttl_enabled() is True
    now = datetime(2026, 6, 1, tzinfo=timezone.utc)
    assert ttl.hidden_by_ttl("2026-05-01T00:00:00.000Z", now=now) is True
    assert ttl.hidden_by_ttl("2999-01-01T00:00:00.000Z", now=now) is False
    # Absent expiry stays visible even with TTL on.
    assert ttl.hidden_by_ttl("", now=now) is False

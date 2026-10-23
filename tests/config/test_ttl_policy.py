# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Unit tests for effective TTL policy resolution.

TTL is default-off and public-cloud only. These tests pin the two-stage
resolution contract from the technical design: per-node account-over-cluster
merge, then specific-root > type-default > ``global`` > default-off, with
``disabled`` stopping the fallback.
"""

from __future__ import annotations

import pytest

from openviking.config.ttl import (
    EffectiveTTL,
    TTLIndexType,
    classify_ttl_uri,
    resolve_effective_ttl,
)
from openviking_cli.utils.config.ttl_config import TTLConfig

USER_EVENTS_DIR = "viking://user/u1/memories/events/2026/10/01"
USER_EVENTS_ROOT = "viking://user/u1/memories/events"
PEER_EVENTS_DIR = "viking://user/u1/peers/p1/memories/events/2026/10/01"
SESSION_DIR = "viking://user/u1/sessions/s1"


def _cfg(**kwargs) -> TTLConfig:
    return TTLConfig(**kwargs)


# -- classification ---------------------------------------------------------


@pytest.mark.parametrize(
    "uri,expected",
    [
        (USER_EVENTS_DIR, TTLIndexType.USER_EVENTS),
        (USER_EVENTS_ROOT, TTLIndexType.USER_EVENTS),
        (PEER_EVENTS_DIR, TTLIndexType.PEER_EVENTS),
        (SESSION_DIR, TTLIndexType.SESSIONS),
        ("viking://user/u1/sessions", TTLIndexType.SESSIONS),
        ("viking://user/u1/memories/entities/x", None),
        ("viking://resources/project/doc", None),
        ("viking://agent/skills/s", None),
        ("viking://user/u1", None),
    ],
)
def test_classify_ttl_uri(uri, expected):
    assert classify_ttl_uri(uri) == expected


def test_ungoverned_uri_is_always_off():
    cfg = _cfg(global_=30)
    result = resolve_effective_ttl(cfg, cfg, "viking://resources/x")
    assert result == EffectiveTTL.off(None)


# -- default off ------------------------------------------------------------


def test_default_off_when_no_config():
    result = resolve_effective_ttl(None, None, SESSION_DIR)
    assert result == EffectiveTTL.off(TTLIndexType.SESSIONS)
    assert result.enabled is False
    assert result.days is None


def test_empty_config_is_off_but_classified():
    result = resolve_effective_ttl(_cfg(), _cfg(), USER_EVENTS_DIR)
    assert result.index_type is TTLIndexType.USER_EVENTS
    assert result.enabled is False


# -- target selection precedence -------------------------------------------


def test_global_applies_when_no_type_default():
    cluster = _cfg(global_=45)
    result = resolve_effective_ttl(cluster, None, SESSION_DIR)
    assert result.enabled is True
    assert result.days == 45


def test_type_default_beats_global():
    cluster = _cfg(global_=45, sessions=30)
    result = resolve_effective_ttl(cluster, None, SESSION_DIR)
    assert result.days == 30


def test_specific_root_beats_type_default():
    cluster = _cfg(sessions=30, roots={"viking://user/u1/sessions": 7})
    result = resolve_effective_ttl(cluster, None, SESSION_DIR)
    assert result.days == 7


def test_specific_root_prefix_matches_descendant():
    cluster = _cfg(sessions=30, roots={"viking://user/u1/sessions": 7})
    result = resolve_effective_ttl(cluster, None, SESSION_DIR)
    assert result.days == 7


def test_reject_session_level_override():
    with pytest.raises(ValueError):
        _cfg(roots={SESSION_DIR: 3})


def test_peer_events_type_default():
    cluster = _cfg(peer_events=14, user_events=60)
    result = resolve_effective_ttl(cluster, None, PEER_EVENTS_DIR)
    assert result.days == 14


def test_user_events_type_default_does_not_apply_to_peer():
    cluster = _cfg(user_events=60)
    result = resolve_effective_ttl(cluster, None, PEER_EVENTS_DIR)
    # peer_events unset -> falls through to global -> default off
    assert result.enabled is False


# -- disabled stops the chain ----------------------------------------------


def test_disabled_type_stops_fallback_to_global():
    cluster = _cfg(global_=45, sessions="disabled")
    result = resolve_effective_ttl(cluster, None, SESSION_DIR)
    assert result.enabled is False
    assert result.index_type is TTLIndexType.SESSIONS


def test_disabled_root_stops_fallback():
    cluster = _cfg(global_=45, sessions=30, roots={"viking://user/u1/sessions": "disabled"})
    result = resolve_effective_ttl(cluster, None, SESSION_DIR)
    assert result.enabled is False


# -- per-node account-over-cluster merge -----------------------------------


def test_doc_example_account_global_does_not_override_cluster_sessions():
    # 库 sessions=30, Account.global=45 -> Session still 30.
    cluster = _cfg(sessions=30)
    account = _cfg(global_=45)
    result = resolve_effective_ttl(cluster, account, SESSION_DIR)
    assert result.days == 30


def test_doc_example_account_sessions_overrides_at_same_node():
    # Account sessions=45 overrides the 库 sessions=30 at the same node.
    cluster = _cfg(sessions=30)
    account = _cfg(sessions=45)
    result = resolve_effective_ttl(cluster, account, SESSION_DIR)
    assert result.days == 45


def test_account_global_overrides_cluster_global_only():
    cluster = _cfg(global_=10)
    account = _cfg(global_=90)
    result = resolve_effective_ttl(cluster, account, SESSION_DIR)
    assert result.days == 90


def test_account_root_overrides_cluster_root_same_uri():
    cluster = _cfg(roots={"viking://user/u1/sessions": 7})
    account = _cfg(roots={"viking://user/u1/sessions": 2})
    result = resolve_effective_ttl(cluster, account, SESSION_DIR)
    assert result.days == 2


def test_account_disable_overrides_cluster_enabled_type():
    cluster = _cfg(sessions=30)
    account = _cfg(sessions="disabled")
    result = resolve_effective_ttl(cluster, account, SESSION_DIR)
    assert result.enabled is False


def test_specific_root_still_wins_after_account_merge():
    # Account raises the type default, but a specific cluster root stays most
    # specific and keeps priority.
    cluster = _cfg(sessions=30, roots={"viking://user/u1/sessions": 7})
    account = _cfg(sessions=90)
    result = resolve_effective_ttl(cluster, account, SESSION_DIR)
    assert result.days == 7

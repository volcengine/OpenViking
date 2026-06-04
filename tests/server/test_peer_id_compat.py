# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Peer ID compatibility tests."""

import pytest

from openviking.core.peer_id import normalize_peer_id
from openviking.core.retrieval_targets import default_target_directories, resolve_retrieval_targets
from openviking.server.identity import RequestContext, Role
from openviking_cli.exceptions import InvalidArgumentError
from openviking_cli.retrieve import ContextType
from openviking_cli.session.user_id import UserIdentifier


def test_normalize_peer_id_accepts_peer_id():
    assert normalize_peer_id("web:visitor:alice") == "web:visitor:alice"


def _target_dirs(target_uri="", peer_id=None):
    ctx = RequestContext(user=UserIdentifier("acct", "support_bot"), role=Role.USER)
    return resolve_retrieval_targets(target_uri, ctx, peer_id).target_directories


def test_default_search_targets_user_memory_without_all_peer_memories():
    targets = _target_dirs()

    assert targets == [
        "viking://user/support_bot/memories",
        "viking://resources",
        "viking://user/support_bot/skills",
    ]


def test_default_peer_search_targets_self_and_requested_peer_memories():
    targets = _target_dirs(peer_id="web:visitor:alice")

    assert targets == [
        "viking://user/support_bot/memories",
        "viking://user/support_bot/peers/web:visitor:alice/memories",
        "viking://resources",
        "viking://user/support_bot/skills",
    ]


def test_peer_search_keeps_explicit_target_uri():
    targets = _target_dirs("viking://resources/docs", peer_id="web:visitor:alice")

    assert targets == ["viking://resources/docs"]


def test_peer_search_expands_default_user_memory_target():
    targets = _target_dirs("viking://user/memories", peer_id="web:visitor:alice")

    assert targets == [
        "viking://user/support_bot/memories",
        "viking://user/support_bot/peers/web:visitor:alice/memories",
    ]


def test_peer_search_explicit_peer_root_targets_that_peer_memory():
    targets = _target_dirs("viking://user/support_bot/peers/web:visitor:alice")

    assert targets == ["viking://user/support_bot/peers/web:visitor:alice/memories"]


def test_peer_search_explicit_peer_memory_matches_peer_id():
    targets = _target_dirs(
        "viking://user/support_bot/peers/web:visitor:alice/memories",
        peer_id="web:visitor:alice",
    )

    assert targets == ["viking://user/support_bot/peers/web:visitor:alice/memories"]


def test_peer_search_explicit_peer_target_rejects_mismatched_peer_id():
    with pytest.raises(InvalidArgumentError, match="target_uri peer does not match peer_id"):
        _target_dirs(
            "viking://user/support_bot/peers/web:visitor:alice/memories",
            peer_id="web:visitor:bob",
        )


def test_peer_search_rejects_all_peers_target():
    with pytest.raises(InvalidArgumentError, match="all peer memories"):
        _target_dirs("viking://user/support_bot/peers")


def test_peer_search_user_root_targets_user_memory_and_skills_only():
    targets = _target_dirs("viking://user", peer_id="web:visitor:alice")

    assert targets == [
        "viking://user/support_bot/memories",
        "viking://user/support_bot/peers/web:visitor:alice/memories",
        "viking://user/support_bot/skills",
    ]


def test_peer_search_expands_canonical_user_memory_target():
    targets = _target_dirs(
        "viking://user/support_bot/memories",
        peer_id="web:visitor:alice",
    )

    assert targets == [
        "viking://user/support_bot/memories",
        "viking://user/support_bot/peers/web:visitor:alice/memories",
    ]


def test_peer_search_list_expands_only_default_memory_targets():
    targets = _target_dirs(
        ["viking://user/memories", "viking://resources/docs"],
        peer_id="web:visitor:alice",
    )

    assert targets == [
        "viking://user/support_bot/memories",
        "viking://user/support_bot/peers/web:visitor:alice/memories",
        "viking://resources/docs",
    ]


def test_default_memory_roots_exclude_all_peer_memories():
    ctx = RequestContext(user=UserIdentifier("acct", "support_bot"), role=Role.USER)

    assert default_target_directories(ctx, context_type=ContextType.MEMORY) == [
        "viking://user/support_bot/memories",
    ]

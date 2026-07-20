# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Actor peer retrieval target resolution tests."""

import pytest

from openviking.core.peer_id import normalize_peer_id
from openviking.core.retrieval_targets import default_target_directories, resolve_retrieval_targets
from openviking.server.identity import RequestContext, Role
from openviking_cli.exceptions import PermissionDeniedError
from openviking_cli.retrieve import ContextType
from openviking_cli.session.user_id import UserIdentifier


def test_normalize_peer_id_accepts_peer_id():
    assert normalize_peer_id("web-visitor-alice") == "web-visitor-alice"


def _ctx(
    actor_peer_id: str | None = None,
) -> RequestContext:
    return RequestContext(
        user=UserIdentifier("acct", "support_bot"),
        role=Role.USER,
        actor_peer_id=actor_peer_id,
    )


def _target_dirs(
    target_uri="",
    actor_peer_id: str | None = None,
):
    return resolve_retrieval_targets(
        target_uri,
        _ctx(actor_peer_id),
    ).target_directories


def test_owner_default_search_targets_owner_memory_resources_and_skills():
    targets = _target_dirs()

    assert targets == [
        "viking://user/support_bot",
        "viking://resources",
    ]


def test_actor_default_search_keeps_user_view_and_filters_peer_collection():
    targets = _target_dirs(actor_peer_id="web-visitor-alice")

    assert targets == [
        "viking://resources",
        "viking://user/support_bot/memories",
        "viking://user/support_bot/resources",
        "viking://user/support_bot/skills",
        "viking://user/support_bot/peers/web-visitor-alice/memories",
        "viking://user/support_bot/peers/web-visitor-alice/resources",
    ]


def test_actor_search_keeps_explicit_resource_target():
    targets = _target_dirs("viking://resources/docs", actor_peer_id="web-visitor-alice")

    assert targets == ["viking://resources/docs"]


def test_actor_search_user_root_keeps_user_content_and_filters_peer_collection():
    targets = _target_dirs("viking://user", actor_peer_id="web-visitor-alice")

    assert targets == [
        "viking://user/support_bot/memories",
        "viking://user/support_bot/resources",
        "viking://user/support_bot/skills",
        "viking://user/support_bot/peers/web-visitor-alice/memories",
        "viking://user/support_bot/peers/web-visitor-alice/resources",
    ]


def test_actor_default_memory_targets_actor_peer_memory():
    assert default_target_directories(
        _ctx("web-visitor-alice"), context_type=ContextType.MEMORY
    ) == [
        "viking://user/support_bot/memories",
        "viking://user/support_bot/peers/web-visitor-alice/memories",
    ]


def test_actor_skill_defaults_include_user_and_shared_agent_skills():
    assert default_target_directories(
        _ctx("web-visitor-alice"), context_type=ContextType.SKILL
    ) == [
        "viking://user/support_bot/skills",
        "viking://agent/skills",
    ]


def test_actor_default_resource_targets_global_and_actor_peer_resources():
    assert default_target_directories(
        _ctx("web-visitor-alice"), context_type=ContextType.RESOURCE
    ) == [
        "viking://resources",
        "viking://user/support_bot/resources",
        "viking://user/support_bot/peers/web-visitor-alice/resources",
    ]


def test_explicit_user_memory_target_stays_self_memory_only():
    targets = _target_dirs("viking://user/memories")

    assert targets == ["viking://user/support_bot/memories"]


def test_owner_explicit_peer_root_targets_that_peer_content():
    targets = _target_dirs("viking://user/support_bot/peers/web-visitor-alice")

    assert targets == [
        "viking://user/support_bot/peers/web-visitor-alice/memories",
        "viking://user/support_bot/peers/web-visitor-alice/resources",
    ]


def test_owner_explicit_peer_memory_target_allows_any_peer():
    targets = _target_dirs("viking://user/support_bot/peers/web-visitor-bob/memories")

    assert targets == ["viking://user/support_bot/peers/web-visitor-bob/memories"]


def test_actor_explicit_own_peer_memory_target_is_allowed():
    targets = _target_dirs(
        "viking://user/support_bot/peers/web-visitor-alice/memories",
        actor_peer_id="web-visitor-alice",
    )

    assert targets == ["viking://user/support_bot/peers/web-visitor-alice/memories"]


def test_actor_explicit_other_peer_memory_target_is_denied():
    with pytest.raises(PermissionDeniedError, match="another peer"):
        _target_dirs(
            "viking://user/support_bot/peers/web-visitor-bob/memories",
            actor_peer_id="web-visitor-alice",
        )


def test_owner_peer_collection_targets_all_peer_contexts():
    assert _target_dirs("viking://user/support_bot/peers") == [
        "viking://user/support_bot/peers",
    ]


def test_actor_peer_collection_targets_actor_peer_only():
    assert _target_dirs(
        "viking://user/support_bot/peers",
        actor_peer_id="web-visitor-alice",
    ) == [
        "viking://user/support_bot/peers/web-visitor-alice/memories",
        "viking://user/support_bot/peers/web-visitor-alice/resources",
    ]

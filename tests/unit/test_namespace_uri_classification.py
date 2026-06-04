# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Tests for shared Viking URI namespace/content classification."""

from openviking.core.namespace import (
    classify_uri,
    context_type_for_uri,
    owner_space_for_uri,
)
from openviking.server.identity import RequestContext, Role
from openviking_cli.session.user_id import UserIdentifier


def test_context_type_for_uri_uses_path_segments():
    assert context_type_for_uri("viking://user/alice/memories/entities/m1.md") == "memory"
    assert context_type_for_uri("viking://user/memories/entities/m1.md") == "memory"
    assert context_type_for_uri("viking://user/alice/skills/demo") == "skill"
    assert context_type_for_uri("viking://user/skills/demo") == "skill"
    assert (
        context_type_for_uri(
            "viking://user/support_bot/peers/web:visitor:alice/memories/profile.md"
        )
        == "memory"
    )
    assert context_type_for_uri("viking://resources/memories-report.md") == "resource"
    assert context_type_for_uri("viking://user/alice/resources/skills-report.md") == "resource"


def test_exact_memory_and_skill_root_detection():
    assert classify_uri("viking://user/alice/memories/preferences/prefs.md").is_memory
    assert classify_uri("viking://user/alice/memories").is_memory_root
    assert classify_uri("viking://user/memories").is_memory_root
    assert not classify_uri("viking://user/alice/memories/preferences").is_memory_root

    assert classify_uri("viking://user/alice/skills/demo/SKILL.md").is_skill
    assert classify_uri("viking://user/alice/skills/demo").is_skill_root
    assert classify_uri("viking://user/skills/demo").is_skill_root
    assert not classify_uri("viking://user/alice/skills").is_skill_root
    assert not classify_uri("viking://user/alice/skills/demo/assets").is_skill_root


def test_owner_space_for_uri_uses_user_only():
    ctx = RequestContext(
        user=UserIdentifier(account_id="acct", user_id="alice"),
        role=Role.ROOT,
    )

    assert owner_space_for_uri("viking://user/alice/memories", ctx) == "alice"
    assert owner_space_for_uri("viking://user/alice/skills/demo", ctx) == "alice"
    assert owner_space_for_uri("viking://resources/readme.md", ctx) == ""

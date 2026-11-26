# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openviking.server.identity import Role
from openviking.storage.queuefs.semantic_msg import SemanticMsg
from openviking.storage.queuefs.semantic_processor import SemanticProcessor


@pytest.mark.asyncio
async def test_unchanged_l0_does_not_mark_or_enqueue_parent(monkeypatch):
    submit = AsyncMock()
    monkeypatch.setattr(
        "openviking.storage.queuefs.semantic_processor.enqueue_directory_refresh", submit
    )
    msg = SemanticMsg(
        uri="viking://resources/root/child",
        context_type="resource",
        role=str(Role.USER),
        generation_trigger="resource_ingest",
    )
    await SemanticProcessor()._enqueue_parent_refresh(msg, msg.uri, l0_body_changed=False)

    submit.assert_not_awaited()


@pytest.mark.asyncio
async def test_non_recursive_reindex_does_not_bubble_to_parent(monkeypatch):
    plan = AsyncMock()
    monkeypatch.setattr(
        "openviking.storage.queuefs.semantic_processor.enqueue_directory_refresh", plan
    )

    msg = SemanticMsg(
        uri="viking://resources/root/child",
        context_type="resource",
        recursive=False,
        generation_trigger="reindex",
        propagate_to_parent=False,
    )
    await SemanticProcessor()._enqueue_parent_refresh(msg, msg.uri, l0_body_changed=True)

    plan.assert_not_awaited()


@pytest.mark.parametrize(
    ("uri", "context_type"),
    [
        ("viking://resources", "resource"),
        ("viking://user/alice", "resource"),
        ("viking://agent/skills", "skill"),
        ("viking://agent/skills/demo", "skill"),
        ("viking://agent/skills/demo/reference", "skill"),
        ("viking://user/alice/skills/demo/reference", "skill"),
    ],
)
@pytest.mark.asyncio
async def test_parent_refresh_stops_at_nonsemantic_namespace_root(monkeypatch, uri, context_type):
    plan = AsyncMock(
        side_effect=AssertionError("non-semantic namespace root must not be refreshed")
    )
    monkeypatch.setattr(
        "openviking.storage.queuefs.semantic_processor.enqueue_directory_refresh",
        plan,
    )
    monkeypatch.setattr(
        "openviking.storage.queuefs.semantic_processor.get_openviking_config",
        lambda: SimpleNamespace(
            semantic=SimpleNamespace(
                overview_sample_limit=32,
                freshness_refresh_ratio=0.10,
            )
        ),
    )
    monkeypatch.setattr(
        "openviking.storage.queuefs.semantic_processor.get_viking_fs",
        lambda: SimpleNamespace(),
    )

    msg = SemanticMsg(uri=uri, context_type=context_type)
    await SemanticProcessor()._enqueue_parent_refresh(
        msg,
        msg.uri,
        l0_body_changed=True,
    )

    plan.assert_not_awaited()


@pytest.mark.parametrize(
    ("uri", "context_type", "expected_parent"),
    [
        ("viking://resources/project", "resource", "viking://resources"),
        ("viking://user/alice/resources", "resource", "viking://user/alice"),
        (
            "viking://agent/skills/demo/reference/nested",
            "skill",
            "viking://agent/skills/demo/reference",
        ),
    ],
)
@pytest.mark.asyncio
async def test_parent_refresh_preserves_semantic_roots(
    monkeypatch, uri, context_type, expected_parent
):
    submit = AsyncMock()
    monkeypatch.setattr(
        "openviking.storage.queuefs.semantic_processor.enqueue_directory_refresh", submit
    )

    msg = SemanticMsg(uri=uri, context_type=context_type)
    await SemanticProcessor()._enqueue_parent_refresh(
        msg,
        msg.uri,
        l0_body_changed=True,
    )

    submit.assert_awaited_once()
    assert submit.await_args.args == (expected_parent, 1)
    assert submit.await_args.kwargs["ctx"].bypass_acl is True

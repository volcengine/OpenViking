# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openviking.storage.queuefs.semantic_msg import SemanticMsg
from openviking.storage.queuefs.semantic_ops.freshness_policy import (
    FreshnessAction,
    FreshnessDecision,
)
from openviking.storage.queuefs.semantic_processor import SemanticProcessor


@pytest.mark.asyncio
async def test_unchanged_l0_does_not_mark_or_enqueue_parent(monkeypatch):
    plan = AsyncMock(
        return_value=FreshnessDecision(FreshnessAction.NOOP, pending_after=3, total_entries=161)
    )
    monkeypatch.setattr(
        "openviking.storage.queuefs.semantic_processor.plan_abstract_overview_refresh", plan
    )
    monkeypatch.setattr(
        "openviking.storage.queuefs.semantic_processor.get_openviking_config",
        lambda: SimpleNamespace(
            semantic=SimpleNamespace(overview_sample_limit=32, freshness_refresh_ratio=0.10)
        ),
    )
    monkeypatch.setattr(
        "openviking.storage.queuefs.semantic_processor.get_viking_fs",
        lambda: SimpleNamespace(),
    )
    get_queue_manager = AsyncMock(side_effect=AssertionError("parent must not be enqueued"))
    monkeypatch.setattr(
        "openviking.storage.queuefs.get_queue_manager", get_queue_manager
    )

    msg = SemanticMsg(uri="viking://resources/root/child", context_type="resource")
    await SemanticProcessor()._enqueue_parent_refresh(
        msg, msg.uri, l0_body_changed=False
    )

    assert plan.await_args.kwargs["l0_body_changed"] is False
    assert plan.await_args.kwargs["force_refresh"] is False
    get_queue_manager.assert_not_called()


@pytest.mark.asyncio
async def test_non_recursive_reindex_does_not_bubble_to_parent(monkeypatch):
    plan = AsyncMock(side_effect=AssertionError("non-recursive reindex must stop at target"))
    monkeypatch.setattr(
        "openviking.storage.queuefs.semantic_processor.plan_abstract_overview_refresh", plan
    )

    msg = SemanticMsg(
        uri="viking://resources/root/child",
        context_type="resource",
        recursive=False,
        generation_trigger="reindex",
        propagate_to_parent=False,
    )
    await SemanticProcessor()._enqueue_parent_refresh(
        msg, msg.uri, l0_body_changed=True
    )

    plan.assert_not_awaited()

@pytest.mark.parametrize(
    ("uri", "context_type"),
    [
        ("viking://user/alice", "resource"),
        ("viking://agent/skills", "skill"),
    ],
)
@pytest.mark.asyncio
async def test_parent_refresh_stops_at_nonsemantic_namespace_root(
    monkeypatch, uri, context_type
):
    plan = AsyncMock(
        side_effect=AssertionError("non-semantic namespace root must not be refreshed")
    )
    monkeypatch.setattr(
        "openviking.storage.queuefs.semantic_processor.plan_abstract_overview_refresh",
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
        ("viking://agent/skills/demo", "skill", "viking://agent/skills"),
    ],
)
@pytest.mark.asyncio
async def test_parent_refresh_preserves_semantic_roots(
    monkeypatch, uri, context_type, expected_parent
):
    plan = AsyncMock(
        return_value=FreshnessDecision(
            FreshnessAction.NOOP,
            pending_after=0,
            total_entries=1,
        )
    )
    monkeypatch.setattr(
        "openviking.storage.queuefs.semantic_processor.plan_abstract_overview_refresh",
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

    plan.assert_awaited_once()
    assert plan.await_args.kwargs["dir_uri"] == expected_parent

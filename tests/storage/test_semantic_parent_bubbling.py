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
    monkeypatch.setattr("openviking.storage.queuefs.get_queue_manager", get_queue_manager)

    msg = SemanticMsg(uri="viking://resources/root/child", context_type="resource")
    await SemanticProcessor()._enqueue_parent_refresh(msg, msg.uri, l0_body_changed=False)

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
    await SemanticProcessor()._enqueue_parent_refresh(msg, msg.uri, l0_body_changed=True)

    plan.assert_not_awaited()


def _patch_parent_plan(monkeypatch, plan):
    monkeypatch.setattr(
        "openviking.storage.queuefs.semantic_processor.plan_abstract_overview_refresh",
        plan,
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


@pytest.mark.asyncio
async def test_user_namespace_container_is_not_parent_refresh_target(monkeypatch):
    plan = AsyncMock(side_effect=AssertionError("viking://user has no directory sidecars"))
    _patch_parent_plan(monkeypatch, plan)

    msg = SemanticMsg(
        uri="viking://user/kb_1116",
        context_type="resource",
        role="user",
        user_id="kb_1116",
        generation_trigger="parent_refresh",
    )
    await SemanticProcessor()._enqueue_parent_refresh(msg, msg.uri, l0_body_changed=True)

    plan.assert_not_awaited()


@pytest.mark.asyncio
async def test_agent_scope_root_is_not_parent_refresh_target(monkeypatch):
    plan = AsyncMock(side_effect=AssertionError("viking://agent has no directory sidecars"))
    _patch_parent_plan(monkeypatch, plan)

    msg = SemanticMsg(
        uri="viking://agent/skills",
        context_type="skill",
        generation_trigger="parent_refresh",
    )
    await SemanticProcessor()._enqueue_parent_refresh(msg, msg.uri, l0_body_changed=True)

    plan.assert_not_awaited()


@pytest.mark.asyncio
async def test_user_home_still_receives_parent_refresh(monkeypatch):
    plan = AsyncMock(
        return_value=FreshnessDecision(FreshnessAction.NOOP, pending_after=1, total_entries=4)
    )
    _patch_parent_plan(monkeypatch, plan)

    msg = SemanticMsg(
        uri="viking://user/kb_1116/resources",
        context_type="resource",
        role="user",
        user_id="kb_1116",
    )
    await SemanticProcessor()._enqueue_parent_refresh(msg, msg.uri, l0_body_changed=True)

    plan.assert_awaited_once()
    assert plan.await_args.kwargs["dir_uri"] == "viking://user/kb_1116"

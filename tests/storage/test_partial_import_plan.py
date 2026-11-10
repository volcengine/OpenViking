# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openviking.storage.context_update_plan import build_context_update_plan_from_snapshot
from openviking.storage.resource_diff import ContentState, resolve_resource_diff
from openviking.storage.resource_rnfv import (
    FormalEntry,
    FormalTreeSnapshot,
    NewArtifactSnapshot,
    NewEntry,
    RequestIntent,
    RNFVSnapshot,
    VectorIndexSnapshot,
    VectorRecordSnapshot,
)


def snapshot(*, complete=False, allow_partial=True, mode="semantic_and_vectors", vectorize=True):
    root = "viking://resources/docs"
    records = {
        "old": VectorRecordSnapshot(
            "old",
            root + "/unknown/old.md",
            "unknown/old.md",
            2,
            {"md5": "old", "abstract": "old child"},
        ),
        "dir": VectorRecordSnapshot(
            "dir", root + "/unknown", "unknown", 0, {"abstract": "retained subtree"}
        ),
        "orphan": VectorRecordSnapshot("orphan", root + "/orphan.md", "orphan.md", 2),
    }
    return RNFVSnapshot(
        RequestIntent(root, mode, vectorize=vectorize),
        NewArtifactSnapshot({"new.md": NewEntry(md5="new")}, complete, allow_partial),
        FormalTreeSnapshot({"unknown": FormalEntry(is_dir=True), "unknown/old.md": FormalEntry()}),
        VectorIndexSnapshot(records, frozenset({"id", "uri", "level", "md5", "abstract"})),
    )


@pytest.mark.asyncio
async def test_partial_diff_keeps_unknown_subtree_and_complete_diff_still_deletes():
    for complete in (False, True):
        diff = await resolve_resource_diff(
            snapshot(complete=complete),
            store=AsyncMock(),
            artifact_ref=None,
            target=AsyncMock(),
            concurrency=1,
        )
        expected = ContentState.DELETED if complete else ContentState.UNCHANGED
        assert diff.entries["unknown/old.md"].content_state is expected
        assert diff.entries["unknown"].content_state is expected


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "vectorize,mode",
    [(True, "semantic_and_vectors"), (True, "vectors_only"), (False, "vectors_only")],
)
async def test_partial_plan_only_upserts_observed_content(monkeypatch, vectorize, mode):
    monkeypatch.setattr(
        "openviking_cli.utils.config.get_openviking_config",
        lambda: SimpleNamespace(
            queue_workers=SimpleNamespace(
                add_resource=SimpleNamespace(file_operation_concurrency=1)
            )
        ),
    )
    db = SimpleNamespace(hydrate_incremental_records=AsyncMock(return_value={}))
    diff, plan = await build_context_update_plan_from_snapshot(
        snapshot=snapshot(vectorize=vectorize, mode=mode),
        store=AsyncMock(),
        artifact_ref=None,
        target=AsyncMock(),
        vikingdb=db,
        context_type="resource",
        is_code_repo=False,
        account_id="test",
        ctx=object(),
        root_preexisting=True,
        artifact_paths={"new.md": "new.md"},
    )
    assert [(a.operation.value, a.relative_path) for a in plan.content_tree_actions] == [
        ("upsert", "new.md")
    ]
    assert not any(
        a.uri.endswith(("old.md", "orphan.md", "unknown")) for a in plan.direct_index_actions
    )
    if mode == "semantic_and_vectors":
        entries = {e.relative_path: e for e in plan.semantic_plan.tree.entries}
        assert entries["unknown"].semantic_action.value == "reuse"
        assert "unknown/old.md" not in entries
    else:
        assert plan.semantic_plan is None


@pytest.mark.asyncio
async def test_incomplete_inventory_is_not_allowed_implicitly():
    with pytest.raises(ValueError, match="incomplete"):
        await resolve_resource_diff(
            snapshot(allow_partial=False), store=AsyncMock(), artifact_ref=None, target=AsyncMock()
        )


@pytest.mark.asyncio
async def test_partial_plan_refuses_summary_that_omits_unreadable_sibling(monkeypatch):
    from dataclasses import replace

    monkeypatch.setattr(
        "openviking_cli.utils.config.get_openviking_config",
        lambda: SimpleNamespace(
            queue_workers=SimpleNamespace(
                add_resource=SimpleNamespace(file_operation_concurrency=1)
            )
        ),
    )
    s = snapshot()
    records = dict(s.vectors.records_by_id)
    records["dir"] = replace(records["dir"], fields={})
    s = replace(s, vectors=replace(s.vectors, records_by_id=records))
    with pytest.raises(ValueError, match="reusable abstracts"):
        await build_context_update_plan_from_snapshot(
            snapshot=s,
            store=AsyncMock(),
            artifact_ref=None,
            target=AsyncMock(),
            vikingdb=SimpleNamespace(hydrate_incremental_records=AsyncMock(return_value={})),
            context_type="resource",
            is_code_repo=False,
            account_id="test",
            ctx=object(),
            root_preexisting=True,
            artifact_paths={"new.md": "new.md"},
        )


@pytest.mark.asyncio
async def test_partial_plan_refuses_kind_replacement_that_erases_unknown_children(monkeypatch):
    from dataclasses import replace

    monkeypatch.setattr(
        "openviking_cli.utils.config.get_openviking_config",
        lambda: SimpleNamespace(
            queue_workers=SimpleNamespace(
                add_resource=SimpleNamespace(file_operation_concurrency=1)
            )
        ),
    )
    s = snapshot()
    s = replace(s, new=replace(s.new, entries={"unknown": NewEntry(md5="new")}))
    with pytest.raises(ValueError, match="retained children"):
        await build_context_update_plan_from_snapshot(
            snapshot=s,
            store=AsyncMock(),
            artifact_ref=None,
            target=AsyncMock(),
            vikingdb=AsyncMock(),
            context_type="resource",
            is_code_repo=False,
            account_id="test",
            ctx=object(),
            root_preexisting=True,
        )

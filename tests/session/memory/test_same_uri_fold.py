# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
Tests for the same-URI batch fold guard (issue #5279).

When several upsert operations in one batch resolve to the same single-file
memory (e.g. profile.md) and carry plain-string content, applying them in
order wholesale-replaces the file on every op, so only the last fact
survives -- with skipped_operations empty. The guard must report the folded
operations and apply only the first instead of silently losing them.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from openviking.server.identity import RequestContext, Role
from openviking.session.memory.dataclass import (
    MemoryTypeSchema,
    ResolvedOperation,
    ResolvedOperations,
)
from openviking.session.memory.memory_updater import MemoryUpdater, MemoryUpdateResult
from openviking.session.memory.merge_op import (
    FieldType,
    MergeOp,
    SearchReplaceBlock,
    StrPatch,
)
from openviking_cli.session.user_id import UserIdentifier

PROFILE_URI = "viking://user/alice/memories/profile.md"
OTHER_URI = "viking://user/alice/memories/profile_extra.md"


def make_op(content, uri=PROFILE_URI, memory_type="profile", page_id=None):
    uris = [uri] if isinstance(uri, str) else list(uri)
    return ResolvedOperation(
        memory_fields={"content": content},
        memory_type=memory_type,
        uris=uris,
        page_id=page_id,
    )


def run_guard(ops):
    registry = MagicMock()
    updater = MemoryUpdater(registry=registry)
    result = MemoryUpdateResult()
    kept = updater._report_same_uri_folds(list(ops), result)
    return kept, result


class TestReportSameURIFolds:
    def test_plain_string_ops_on_same_uri_fold_to_first(self):
        """The #5279 shape: only the first op survives, the rest are skipped."""
        ops = [make_op(f"# user\n\n- fact {i}") for i in range(6)]

        kept, result = run_guard(ops)

        assert len(kept) == 1
        assert kept[0] is ops[0]
        assert len(result.skipped_operations) == 5
        for skipped in result.skipped_operations:
            assert skipped.uri == PROFILE_URI
            assert skipped.reason_code.value == "same_uri_batch_fold"
            assert skipped.memory_type == "profile"

    def test_ops_on_distinct_uris_all_kept(self):
        """Distinct target files never fold, even with plain-string content."""
        ops = [
            make_op("content A", uri="viking://user/alice/memories/notes/a.md"),
            make_op("content B", uri="viking://user/alice/memories/notes/b.md"),
        ]

        kept, result = run_guard(ops)

        assert kept == ops
        assert result.skipped_operations == []

    def test_str_patch_blocks_are_not_folded(self):
        """True SEARCH/REPLACE patches compose safely and must pass through."""
        patch1 = StrPatch(
            blocks=[SearchReplaceBlock(search="alpha", replace="ALPHA")]
        )
        patch2 = StrPatch(
            blocks=[SearchReplaceBlock(search="beta", replace="BETA")]
        )
        ops = [make_op(patch1), make_op(patch2)]

        kept, result = run_guard(ops)

        assert kept == ops
        assert result.skipped_operations == []

    def test_dict_blocks_are_not_folded(self):
        """Dict-form StrPatch (JSON parse output) also has patch semantics."""
        ops = [
            make_op({"blocks": [{"search": "a", "replace": "A"}]}),
            make_op({"blocks": [{"search": "b", "replace": "B"}]}),
        ]

        kept, result = run_guard(ops)

        assert kept == ops
        assert result.skipped_operations == []

    @pytest.mark.parametrize("empty_value", ["", None])
    def test_empty_content_does_not_fold(self, empty_value):
        """Empty/None content keeps the original file, so nothing is lost."""
        ops = [make_op("first fact"), make_op(empty_value)]

        kept, result = run_guard(ops)

        assert kept == ops
        assert result.skipped_operations == []

    def test_multi_uri_op_is_kept(self):
        """One op resolving to several URIs is not a single-file fold."""
        op = make_op("shared", uri=[PROFILE_URI, OTHER_URI])

        kept, result = run_guard([op])

        assert kept == [op]
        assert result.skipped_operations == []

    def test_folded_groups_are_independent_per_uri(self):
        """Two folding groups each keep their own first op."""
        ops = [
            make_op("a1", uri="viking://user/alice/memories/notes/a.md"),
            make_op("b1", uri="viking://user/alice/memories/notes/b.md"),
            make_op("a2", uri="viking://user/alice/memories/notes/a.md"),
            make_op("b2", uri="viking://user/alice/memories/notes/b.md"),
            make_op("a3", uri="viking://user/alice/memories/notes/a.md"),
        ]

        kept, result = run_guard(ops)

        assert len(kept) == 2
        assert [op.memory_fields["content"] for op in kept] == ["a1", "b1"]
        assert len(result.skipped_operations) == 3

    def test_guard_only_runs_on_plain_string_not_strpatch_subclass_like(self):
        """Sanity: a non-str truthy value that is not str is untouched."""
        op = ResolvedOperation(
            memory_fields={"content": 12345},
            memory_type="profile",
            uris=[PROFILE_URI],
        )

        kept, result = run_guard([op])

        assert kept == [op]
        assert result.skipped_operations == []


class TestGuardEndToEnd:
    """The guard must be wired into apply_operations."""

    @pytest.mark.asyncio
    async def test_apply_operations_reports_fold_and_writes_one_uri(self):
        schema = MemoryTypeSchema(
            memory_type="profile",
            description="profile",
            fields=[],
        )
        registry = MagicMock()
        registry.get.return_value = schema

        updater = MemoryUpdater(registry=registry)
        updater._get_viking_fs = MagicMock(return_value=MagicMock())
        updater._apply_upsert = AsyncMock()
        updater._vectorize_memories = AsyncMock()
        updater.generate_overview = AsyncMock()

        ops = [
            make_op(f"# user\n\n- fact {i}", page_id=100 + i) for i in range(6)
        ]
        operations = ResolvedOperations(
            upsert_operations=ops,
            delete_file_contents=[],
            errors=[],
        )
        ctx = RequestContext(
            user=UserIdentifier("acme", "alice"), role=Role.USER
        )

        result = await updater.apply_operations(
            operations=operations, ctx=ctx
        )

        # Only the first op is applied; it is reported as written.
        assert result.written_uris == [PROFILE_URI]
        assert updater._apply_upsert.await_count == 1
        assert len(result.skipped_operations) == 5
        assert all(
            item.reason_code.value == "same_uri_batch_fold"
            for item in result.skipped_operations
        )

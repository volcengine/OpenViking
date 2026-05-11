# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Regression tests for bounded memory patch fallbacks."""

import pytest

import openviking.session.memory.merge_op.patch_handler as patch_handler
from openviking.session.memory.merge_op import PatchParseError, SearchReplaceBlock, StrPatch


def test_large_missing_patch_skips_unbounded_fuzzy_and_sanitizes_error(monkeypatch):
    """Large patch misses should fail without fuzzy scanning or dumping payloads."""

    def fail_if_called(*_args, **_kwargs):
        raise AssertionError("unbounded fuzzy search should be skipped")

    monkeypatch.setattr(patch_handler, "fuzzy_search", fail_if_called)
    original = "\n".join(f"memory line {i} {'x' * 80}" for i in range(1200))
    patch = StrPatch(
        blocks=[
            SearchReplaceBlock(
                search="missing stale memory fragment",
                replace="updated memory fragment",
            )
        ]
    )

    with pytest.raises(PatchParseError) as exc_info:
        patch_handler.apply_str_patch(original, patch)

    error = str(exc_info.value)
    assert "original_chars=" in error
    assert "patch_blocks=1" in error
    assert "memory line 1199" not in error
    assert "missing stale memory fragment" not in error

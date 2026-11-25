# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Ordered append operations must survive ingestion and patch serialization."""

import pytest

from openviking.storage.index_action import FieldPatch
from openviking.utils.ingest_options import IngestOptions
from openviking.utils.tags import normalize_search_tags


@pytest.mark.parametrize("route", ["direct", "ingest", "roundtrip"])
@pytest.mark.parametrize(
    ("operations", "expected"),
    [
        (["count+=1"], ["count=1"]),
        (["count+=1", "count+=1"], ["count=2"]),
        (["count+=1", "count=0", "count+=1"], ["count=1"]),
        (["count=10", "count+=1", "count=20"], ["count=20"]),
        (["count+=2", "count-=1", "count+=3"], ["count=4"]),
        (["count+=1", "score+=2"], ["count=1", "score=2"]),
    ],
)
def test_ordered_append_operations_survive_patch_construction(route, operations, expected):
    incoming = operations
    if route != "direct":
        options = IngestOptions.from_search_tags(operations, mode="append")
        if route == "roundtrip":
            options = IngestOptions.from_value(options.to_dict())
        incoming = options.search_tags

    patch = FieldPatch(values={"search_tags": incoming}, modes={"search_tags": "append"})
    if route == "roundtrip":
        patch = FieldPatch.from_dict(patch.to_dict())

    result = patch.apply({"context_type": "resource", "search_tags": ["count=0"]})
    assert result["search_tags"] == expected


def test_normalization_keeps_default_last_value_per_key():
    assert normalize_search_tags(["env=dev", "env=prod"]) == ["env=prod"]


def test_replace_mode_retains_default_last_value_per_key():
    options = IngestOptions.from_search_tags(["env=dev", "env=prod"], mode="replace")
    patch = FieldPatch(
        values={"search_tags": options.search_tags}, modes={"search_tags": "replace"}
    )
    assert patch.resolve({"search_tags": ["env=old"]})["search_tags"] == ["env=prod"]


def test_clear_mode_remains_empty():
    options = IngestOptions.from_search_tags(["env=prod"], mode="clear")
    assert options.search_tags == []
    assert options.search_tag_mode == "clear"


def test_append_normalizes_each_operation_without_discarding_duplicates():
    options = IngestOptions.from_search_tags([" Count+=1 ", "COUNT+=1"], mode="append")
    assert options.search_tags == ["count+=1", "count+=1"]


def test_append_retains_extracted_memory_type_and_unrelated_fields():
    patch = FieldPatch(
        values={"search_tags": ["count+=1", "count+=1"]},
        modes={"search_tags": "append"},
    )
    result = patch.apply(
        {
            "context_type": "memory",
            "content": "keep",
            "search_tags": ["memory_type=profile", "count=0"],
        }
    )
    assert result["search_tags"] == ["memory_type=profile", "count=2"]
    assert result["content"] == "keep"

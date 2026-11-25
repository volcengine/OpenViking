# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Pending tag operations remain ordered across producer paths."""

from types import SimpleNamespace

import pytest

from openviking.storage.content_write import ContentWriteCoordinator
from openviking.storage.index_action import FieldPatch, IndexAction
from openviking.utils.embedding_utils import _apply_ingest_options, _apply_planned_field_patch
from openviking.utils.ingest_options import IngestOptions


class _Message:
    def __init__(self, action, context=None):
        self.action = action
        self.context_data = dict(context or {})
        self.payload = SimpleNamespace(field_patch=None)

    @property
    def field_patch(self):
        return self.payload.field_patch


def _options():
    return IngestOptions.from_search_tags(["n+=1", "n+=2"], mode="append")


@pytest.mark.parametrize(("stored", "expected"), [(["n=5"], ["n=8"]), ([], ["n=3"])])
def test_legacy_merge_defers_tag_arithmetic_until_existing_record_read(stored, expected):
    message = _Message(IndexAction.MERGE)
    _apply_ingest_options(message, _options())
    assert message.context_data["search_tags"] == ["n+=1", "n+=2"]
    assert message.context_data["_upsert_options"]["search_tag_mode"] == "append"
    patch = FieldPatch(
        {"search_tags": message.context_data["search_tags"]}, {"search_tags": "append"}
    )
    assert patch.resolve({"search_tags": stored})["search_tags"] == expected


@pytest.mark.parametrize("upsert_options", [{}, {"partial_update": False}])
def test_full_upsert_resolves_against_its_initial_tags(upsert_options):
    message = _Message(
        IndexAction.UPSERT, {"search_tags": ["n=5"], "_upsert_options": upsert_options}
    )
    _apply_ingest_options(message, _options())
    assert message.context_data["search_tags"] == ["n=8"]


def test_full_upsert_resolves_operations_for_a_new_record():
    message = _Message(IndexAction.UPSERT)
    _apply_ingest_options(message, _options())
    assert message.context_data["search_tags"] == ["n=3"]


def test_full_upsert_does_not_reapply_operations_to_planner_resolved_tags():
    message = _Message(IndexAction.UPSERT, {"search_tags": ["n=8"]})
    _apply_ingest_options(message, _options(), preserve_search_tags=True)
    assert message.context_data["search_tags"] == ["n=8"]


@pytest.mark.parametrize("planned_first", [True, False])
def test_planned_tag_patch_is_not_overwritten_by_ingest_options(planned_first):
    message = _Message(IndexAction.MERGE, {"search_tags": ["n=5"]})
    patch = FieldPatch({"search_tags": ["n+=1", "n+=2"]}, {"search_tags": "append"})
    if planned_first:
        _apply_planned_field_patch(message, patch)
        _apply_ingest_options(message, _options())
    else:
        _apply_ingest_options(message, _options())
        _apply_planned_field_patch(message, patch)
    assert "search_tags" not in message.context_data
    assert message.field_patch.resolve({"search_tags": ["n=5"]})["search_tags"] == ["n=8"]


def test_conflict_tags_follow_pending_user_operations():
    previous = 'wrong\n<!-- CONFLICTS_CONTENT {"state":"unfixed","match_text":"wrong"} -->'
    _, options, conflict_tags = ContentWriteCoordinator._resolve_conflict_edit(
        previous, b"correct", _options(), user_id="alice"
    )
    assert conflict_tags == ["has_conflict=false", "conflict_num=0"]
    assert options.search_tag_mode == "append"
    assert options.search_tags == ["n+=1", "n+=2", "has_conflict=false", "conflict_num=0"]
    patch = FieldPatch({"search_tags": options.search_tags}, {"search_tags": "append"})
    assert patch.resolve({"search_tags": ["n=5"]})["search_tags"] == [
        "n=8",
        "has_conflict=false",
        "conflict_num=0",
    ]


@pytest.mark.parametrize("mode", ["replace", "clear"])
def test_conflict_tags_keep_replace_and_clear_semantics(mode):
    options = IngestOptions.from_search_tags(["env=old", "env=new"], mode=mode)
    _, resolved, _ = ContentWriteCoordinator._resolve_conflict_edit(
        "old", b"", options, user_id="alice"
    )
    expected = ["has_conflict=false", "conflict_num=0"]
    if mode == "replace":
        expected.insert(0, "env=new")
    assert resolved.search_tag_mode == "replace"
    assert resolved.search_tags == expected


def test_no_conflict_metadata_keeps_append_options():
    original = _options()
    result, options, conflict_tags = ContentWriteCoordinator._resolve_conflict_edit(
        "old", b"new", original, user_id="alice"
    )
    assert result == b"new"
    assert options == original
    assert conflict_tags is None

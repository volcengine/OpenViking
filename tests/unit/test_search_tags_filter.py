# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import logging

import pytest

from openviking.server.routers.search import _resolve_search_filter
from openviking.utils import tags as tags_module
from openviking.utils.tags import (
    build_search_tags_filter,
    merge_search_tags,
    normalize_search_tag,
    normalize_search_tags,
    preserve_memory_type_tag,
)
from openviking_cli.exceptions import InvalidArgumentError


def test_search_tags_filter_keeps_single_tag_as_single_must():
    assert build_search_tags_filter(["Team=Search"]) == {
        "op": "must",
        "field": "search_tags",
        "conds": ["team=search"],
    }


def test_search_tags_filter_dedupes_before_building_and_filter():
    assert build_search_tags_filter(["Env=Prod", " env=prod ", "team=Search"]) == {
        "op": "and",
        "conds": [
            {"op": "must", "field": "search_tags", "conds": ["env=prod"]},
            {"op": "must", "field": "search_tags", "conds": ["team=search"]},
        ],
    }


def test_search_tags_duplicate_keys_keep_last_value():
    assert normalize_search_tags(["channel=web", "channel=app"]) == ["channel=app"]


def test_search_tag_allows_dot_dash_underscore():
    assert normalize_search_tag("doc_type=api-v1.2") == "doc_type=api-v1.2"


@pytest.mark.parametrize(
    "tag",
    [
        "team=search platform",  # internal space
        "team=with/slash",
        "team=值",  # non-ascii
        "-team=search",
        "team=-search",
        "team=search,platform",
        "team,owner=search",
        "viking://user/alice/memories/experiences/workflow.md=1",
        "viking://user/%41lice/memories/experiences/%45xchange%3d%46low.md=1",
    ],
)
def test_search_tag_accepts_special_characters(tag):
    assert normalize_search_tag(tag) == tag
    assert normalize_search_tags([tag], discard_invalid=True) == [tag]


@pytest.mark.parametrize("tag", ["", "team", "=search", "team=", "te=am=search"])
def test_search_tag_still_requires_non_empty_kv_format(tag):
    with pytest.raises(InvalidArgumentError):
        normalize_search_tag(tag)


@pytest.mark.parametrize("key_length,value_length", [(64, 128), (65, 1), (4, 129), (256, 512)])
def test_search_tag_accepts_long_keys_and_values(key_length, value_length):
    key = "k" * key_length
    value = "v" * value_length
    assert normalize_search_tag(f"{key}={value}") == f"{key}={value}"


def test_discard_invalid_search_tags_logs_one_warning_for_batch(caplog):
    tags_module.logger.addHandler(caplog.handler)
    try:
        with caplog.at_level(logging.WARNING, logger="openviking.utils.tags"):
            result = normalize_search_tags(
                ["bad-one", "also-bad", "team=search"],
                discard_invalid=True,
            )
    finally:
        tags_module.logger.removeHandler(caplog.handler)

    assert result == ["team=search"]
    warnings = [record for record in caplog.records if record.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert warnings[0].invalid_tags == ["bad-one", "also-bad"]
    assert "Discarded invalid search tags" in warnings[0].message
    assert "bad-one" in warnings[0].message
    assert "also-bad" in warnings[0].message


def test_merge_search_tags_discards_invalid_existing_tags(caplog):
    tags_module.logger.addHandler(caplog.handler)
    try:
        with caplog.at_level(logging.WARNING, logger="openviking.utils.tags"):
            result = merge_search_tags(["bad-existing", "team=old"], ["owner=alice"])
    finally:
        tags_module.logger.removeHandler(caplog.handler)

    assert result == ["team=old", "owner=alice"]
    warnings = [record for record in caplog.records if record.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert warnings[0].invalid_tags == ["bad-existing"]


def test_merge_search_tags_executes_in_order_and_preserves_other_keys():
    assert merge_search_tags(
        ["team=search", "count=10", "other=4"],
        ["count-=2", "new_count+=3", "other+=0", "count+=0.5", "count=4", "count-=1"],
    ) == ["team=search", "count=3", "other=4", "new_count=3"]


@pytest.mark.parametrize(
    "existing,incoming,expected",
    [
        (["score=0.1"], ["score=0.2"], ["score=0.3"]),
        (["score=1.5"], ["score=-0.25"], ["score=1.25"]),
        ([], ["score=1.5"], ["score=1.5"]),
        ([], ["count=1e3"], ["count=1000"]),
    ],
)
def test_merge_search_tags_supports_decimal_values(existing, incoming, expected):
    incoming = [tag.replace("=", "+=", 1) for tag in incoming]
    assert merge_search_tags(existing, incoming) == expected


@pytest.mark.parametrize(
    "existing,incoming",
    [
        (["count=unknown"], ["count+=1"]),
        ([], ["count+=NaN"]),
        ([], ["count+=Infinity"]),
        (["count=-Infinity"], ["count+=1"]),
        (["count=sNaN"], ["count+=1"]),
    ],
)
def test_merge_search_tags_rejects_invalid_arithmetic(existing, incoming):
    with pytest.raises(InvalidArgumentError):
        merge_search_tags(existing, incoming)


def test_merge_search_tags_normalizes_and_preserves_duplicate_operations():
    assert merge_search_tags([], [" Count+=1 ", "COUNT-=0.5"]) == ["count=0.5"]


def test_preserve_memory_type_tag_does_not_reapply_tag_operations():
    assert preserve_memory_type_tag(
        ["memory_type=profile", "c++=cpp", "used_count=3"],
        ["memory_type=other", "c++=cpp", "used_count=4"],
    ) == ["memory_type=profile", "c++=cpp", "used_count=4"]


def test_find_tags_filter_requires_all_tags():
    result = _resolve_search_filter(
        request_filter=None,
        context_type=None,
        since=None,
        until=None,
        time_field=None,
        tags=["Env=Prod", "team=Search"],
    )

    assert result == {
        "op": "and",
        "conds": [
            {"op": "must", "field": "search_tags", "conds": ["env=prod"]},
            {"op": "must", "field": "search_tags", "conds": ["team=search"]},
        ],
    }


def test_find_tags_filter_ands_all_tags_with_existing_filter():
    existing_filter = {"op": "must", "field": "kind", "conds": ["email"]}

    result = _resolve_search_filter(
        request_filter=existing_filter,
        context_type=None,
        since=None,
        until=None,
        time_field=None,
        tags=["env=prod", "team=search"],
    )

    assert result == {
        "op": "and",
        "conds": [
            existing_filter,
            {"op": "must", "field": "search_tags", "conds": ["env=prod"]},
            {"op": "must", "field": "search_tags", "conds": ["team=search"]},
        ],
    }

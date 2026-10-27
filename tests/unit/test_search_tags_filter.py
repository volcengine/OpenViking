# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import pytest

from openviking.server.routers.search import _resolve_search_filter
from openviking.utils.tags import (
    build_search_tags_filter,
    merge_search_tags,
    normalize_search_tag,
    normalize_search_tags,
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


def test_discard_invalid_search_tags_keeps_valid_tags():
    result = normalize_search_tags(
        ["default", "also-bad", "team=search"],
        discard_invalid=True,
    )
    assert result == ["team=search"]


def test_merge_search_tags_discards_invalid_existing_tags():
    result = merge_search_tags(["bad-existing", "team=old"], ["owner=alice"])
    assert result == ["team=old", "owner=alice"]


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

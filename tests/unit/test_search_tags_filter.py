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
        "工作 流程=发布检查",
        "team=search，platform",  # full-width comma is not the ASCII delimiter
        "team=search%2cplatform",  # percent escapes are not decoded
        "viking://user/default/memories/experiences/cfg_streaming.md=1",
        "viking://user/%41lice/memories/experiences/%41%3d%42.md=1",
    ],
)
def test_search_tag_allows_free_form_characters(tag):
    assert normalize_search_tag(tag) == tag


@pytest.mark.parametrize(
    "tag",
    [
        "",
        "team",
        "=search",
        "team=",
        "te=am=search",
        "=",
        "k" * 65 + "=v",
        "team=" + "v" * 129,
        "k" * 256 + "=" + "v" * 512,
        "viking://user/default/memories/experiences/vikingdb_fe_repo_workflows.md=1",
    ],
)
@pytest.mark.parametrize("discard_invalid", [False, True])
def test_search_tag_accepts_strings_without_commas(tag, discard_invalid):
    assert normalize_search_tag(tag) == tag
    assert normalize_search_tags([tag], discard_invalid=discard_invalid) == [tag]
    assert merge_search_tags([tag], [tag]) == [tag]
    assert build_search_tags_filter([tag]) == {
        "op": "must",
        "field": "search_tags",
        "conds": [tag],
    }


@pytest.mark.parametrize(
    "tag",
    [",", ",key=value", "key,part=value", "key=value,part", "plain,tag", "key=value,", "a=b=c,d"],
)
def test_search_tag_rejects_only_commas(tag):
    with pytest.raises(InvalidArgumentError, match="must not contain ','"):
        normalize_search_tag(tag)
    with pytest.raises(InvalidArgumentError, match="must not contain ','"):
        normalize_search_tags(["team=search", tag])
    with pytest.raises(InvalidArgumentError, match="must not contain ','"):
        build_search_tags_filter([tag])
    assert normalize_search_tags([tag, "team=search"], discard_invalid=True) == ["team=search"]


def test_merge_search_tags_preserves_plain_tags_and_replaces_keyed_values():
    assert merge_search_tags(
        [" Team ", "team=old", "", "=old", "owner=alice", "old,tag"],
        ["team", "team=new=value", "=new", "   ", "other", "team=bad,value"],
    ) == ["team", "team=new=value", "", "=new", "owner=alice", "other"]


def test_normalize_search_tags_handles_empty_iterables():
    assert normalize_search_tags(None) == []
    assert build_search_tags_filter([]) is None
    assert merge_search_tags(None, None) == []
    assert merge_search_tags(iter(["tag", "team=old"]), iter(["team=new"])) == [
        "tag",
        "team=new",
    ]


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

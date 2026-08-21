# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import logging

from openviking.server.routers.search import _resolve_search_filter
from openviking.utils import tags as tags_module
from openviking.utils.tags import build_search_tags_filter, merge_search_tags, normalize_search_tags


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

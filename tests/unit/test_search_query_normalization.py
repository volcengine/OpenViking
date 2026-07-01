# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Tests for semantic search query normalization."""

from openviking.storage.viking_fs import _normalize_search_query


def test_normalize_cjk_fullwidth_to_halfwidth():
    # NFKC folds full-width Latin letters/digits to ASCII
    assert _normalize_search_query("ＯｐｅｎＶｉｋｉｎｇ") == "openviking"


def test_normalize_casefold_mixed_case():
    # casefold is stronger than lower() — handles ß, etc.
    assert _normalize_search_query("OpenViking") == "openviking"
    assert _normalize_search_query("OpenVAKING") == "openvaking"


def test_normalize_collapses_whitespace():
    assert _normalize_search_query("Harms  agent") == "harms agent"
    assert _normalize_search_query("  hello   world  ") == "hello world"


def test_normalize_strips_leading_trailing_whitespace():
    assert _normalize_search_query("\tquery\n") == "query"


def test_normalize_empty_query_returns_empty():
    assert _normalize_search_query("") == ""


def test_normalize_none_query_passthrough():
    # None is falsy; helper returns it unchanged (guard: `if not query`)
    assert _normalize_search_query(None) is None


def test_normalize_idempotent():
    once = _normalize_search_query("ＯｐｅｎＶｉｋｉｎｇ  Ａｇｅｎｔ")
    twice = _normalize_search_query(once)
    assert once == twice == "openviking agent"

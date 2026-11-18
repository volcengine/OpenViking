# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import pytest

from openviking.core.search_args import validate_search_args


def test_keywords_args_are_normalized():
    args = validate_search_args(
        {
            "bm25_k1": 1,
            "bm25_b": 0,
            "post_process_input_limit": 10,
        },
        search_type="keywords",
        limit=10,
    )

    assert args == {
        "bm25_k1": 1.0,
        "bm25_b": 0.0,
        "post_process_input_limit": 10,
    }


@pytest.mark.parametrize(
    "args",
    [
        {"bm25_k1": 0},
        {"bm25_k1": -0.1},
        {"bm25_b": -0.1},
        {"bm25_b": 1.1},
        {"post_process_input_limit": 9},
        {"post_process_input_limit": 2_000_001},
        {"post_process_input_limit": 10.5},
        {"unknown": 1},
        {"bm25_k1": "1.2"},
        {"bm25_k1": True},
    ],
)
def test_keywords_args_reject_invalid_values(args):
    with pytest.raises(ValueError):
        validate_search_args(args, search_type="keywords", limit=10)


def test_args_are_rejected_outside_keyword_search():
    with pytest.raises(ValueError, match="search_type='keywords'"):
        validate_search_args({"bm25_k1": 1.2}, search_type="semantic", limit=10)

# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from __future__ import annotations

import pytest

from openviking.session.auto_commit_policy import (
    DEFAULT_IDLE_TIMEOUT_SECONDS,
    DEFAULT_KEEP_RECENT_COUNT,
    DEFAULT_MESSAGE_COUNT_THRESHOLD,
    DEFAULT_MIN_COMMIT_INTERVAL_SECONDS,
    DEFAULT_PENDING_TOKEN_THRESHOLD,
    MAX_IDLE_TIMEOUT_SECONDS,
    MAX_MESSAGE_COUNT_THRESHOLD,
    MAX_PENDING_TOKEN_THRESHOLD,
    AutoCommitPolicy,
)
from openviking_cli.exceptions import InvalidArgumentError


def test_default_matches_prd_recommended_values():
    policy = AutoCommitPolicy.default()

    assert policy.pending_token_threshold == DEFAULT_PENDING_TOKEN_THRESHOLD == 1000
    assert policy.message_count_threshold == DEFAULT_MESSAGE_COUNT_THRESHOLD == 50
    assert policy.idle_timeout_seconds == DEFAULT_IDLE_TIMEOUT_SECONDS == 86400
    assert policy.keep_recent_count == DEFAULT_KEEP_RECENT_COUNT == 2
    assert policy.min_commit_interval_seconds == DEFAULT_MIN_COMMIT_INTERVAL_SECONDS == 60


def test_from_dict_none_returns_defaults():
    assert AutoCommitPolicy.from_dict(None).to_dict() == AutoCommitPolicy.default().to_dict()


def test_from_dict_fills_missing_fields_with_defaults():
    policy = AutoCommitPolicy.from_dict({"pending_token_threshold": 8000})

    assert policy.pending_token_threshold == 8000
    assert policy.message_count_threshold == DEFAULT_MESSAGE_COUNT_THRESHOLD
    assert policy.idle_timeout_seconds == DEFAULT_IDLE_TIMEOUT_SECONDS
    assert policy.keep_recent_count == DEFAULT_KEEP_RECENT_COUNT
    assert policy.min_commit_interval_seconds == DEFAULT_MIN_COMMIT_INTERVAL_SECONDS


def test_from_dict_clamps_to_upper_bounds():
    policy = AutoCommitPolicy.from_dict(
        {
            "pending_token_threshold": 10_000_000,
            "message_count_threshold": 10_000,
            "idle_timeout_seconds": 999_999_999,
            "keep_recent_count": 10_000,
            "min_commit_interval_seconds": 999_999_999,
        }
    )

    assert policy.pending_token_threshold == MAX_PENDING_TOKEN_THRESHOLD
    assert policy.message_count_threshold == MAX_MESSAGE_COUNT_THRESHOLD
    assert policy.idle_timeout_seconds == MAX_IDLE_TIMEOUT_SECONDS
    assert policy.keep_recent_count == MAX_MESSAGE_COUNT_THRESHOLD
    assert policy.min_commit_interval_seconds == MAX_IDLE_TIMEOUT_SECONDS


def test_from_dict_clamps_negatives_to_zero():
    policy = AutoCommitPolicy.from_dict(
        {
            "pending_token_threshold": -5,
            "message_count_threshold": -1,
            "idle_timeout_seconds": -100,
            "keep_recent_count": -3,
            "min_commit_interval_seconds": -9,
        }
    )

    assert policy.to_dict() == {
        "pending_token_threshold": 0,
        "message_count_threshold": 0,
        "idle_timeout_seconds": 0,
        "keep_recent_count": 0,
        "min_commit_interval_seconds": 0,
    }


def test_from_dict_rejects_unknown_keys():
    with pytest.raises(InvalidArgumentError):
        AutoCommitPolicy.from_dict({"enabled": True})

    with pytest.raises(InvalidArgumentError):
        AutoCommitPolicy.from_dict({"token_threshold": 500})


def test_from_dict_rejects_non_object():
    with pytest.raises(InvalidArgumentError):
        AutoCommitPolicy.from_dict(42)


def test_from_dict_rejects_non_integer_values():
    with pytest.raises(InvalidArgumentError):
        AutoCommitPolicy.from_dict({"pending_token_threshold": "abc"})


def test_from_dict_accepts_existing_policy_instance():
    original = AutoCommitPolicy.from_dict({"keep_recent_count": 7})

    assert AutoCommitPolicy.from_dict(original) is original


def test_merge_overwrites_only_present_keys():
    base = AutoCommitPolicy.from_dict(
        {
            "pending_token_threshold": 8000,
            "message_count_threshold": 40,
            "keep_recent_count": 10,
        }
    )

    merged = base.merge({"message_count_threshold": 25})

    assert merged.message_count_threshold == 25
    # Untouched fields preserved.
    assert merged.pending_token_threshold == 8000
    assert merged.keep_recent_count == 10
    assert merged.idle_timeout_seconds == base.idle_timeout_seconds
    assert merged.min_commit_interval_seconds == base.min_commit_interval_seconds


def test_merge_none_returns_same_policy():
    base = AutoCommitPolicy.from_dict({"keep_recent_count": 5})

    assert base.merge(None) is base


def test_merge_clamps_and_rejects_unknown_keys():
    base = AutoCommitPolicy.default()

    assert base.merge({"pending_token_threshold": 10_000_000}).pending_token_threshold == (
        MAX_PENDING_TOKEN_THRESHOLD
    )

    with pytest.raises(InvalidArgumentError):
        base.merge({"enabled": False})


def test_to_dict_round_trips():
    payload = {
        "pending_token_threshold": 8000,
        "message_count_threshold": 40,
        "idle_timeout_seconds": 600,
        "keep_recent_count": 10,
        "min_commit_interval_seconds": 30,
    }

    assert AutoCommitPolicy.from_dict(payload).to_dict() == payload

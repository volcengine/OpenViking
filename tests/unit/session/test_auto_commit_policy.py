# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from __future__ import annotations

import pytest

from openviking.session.auto_commit_policy import (
    DEFAULT_FAILED_ARCHIVE_ROLL_FORWARD_TOKEN_BUDGET,
    DEFAULT_IDLE_TIMEOUT_SECONDS,
    DEFAULT_KEEP_RECENT_COUNT,
    DEFAULT_MESSAGE_COUNT_THRESHOLD,
    DEFAULT_MIN_COMMIT_INTERVAL_SECONDS,
    DEFAULT_PENDING_TOKEN_THRESHOLD,
    MAX_FAILED_ARCHIVE_ROLL_FORWARD_TOKEN_BUDGET,
    MAX_IDLE_TIMEOUT_SECONDS,
    MAX_MESSAGE_COUNT_THRESHOLD,
    MAX_PENDING_TOKEN_THRESHOLD,
    AutoCommitPolicy,
)
from openviking_cli.exceptions import InvalidArgumentError


def test_default_matches_prd_recommended_values():
    policy = AutoCommitPolicy.default()

    assert policy.pending_token_threshold == DEFAULT_PENDING_TOKEN_THRESHOLD == 150_000
    assert policy.message_count_threshold == DEFAULT_MESSAGE_COUNT_THRESHOLD == 100
    assert policy.idle_timeout_seconds == DEFAULT_IDLE_TIMEOUT_SECONDS == 86400
    assert policy.keep_recent_count == DEFAULT_KEEP_RECENT_COUNT == 0
    assert policy.min_commit_interval_seconds == DEFAULT_MIN_COMMIT_INTERVAL_SECONDS == 0
    assert (
        policy.failed_archive_roll_forward_token_budget
        == DEFAULT_FAILED_ARCHIVE_ROLL_FORWARD_TOKEN_BUDGET
        == 150_000
    )


def test_from_dict_none_returns_defaults():
    assert AutoCommitPolicy.from_dict(None).to_dict() == AutoCommitPolicy.default().to_dict()


def test_from_dict_fills_missing_fields_with_defaults():
    policy = AutoCommitPolicy.from_dict({"pending_token_threshold": 8000})

    assert policy.pending_token_threshold == 8000
    assert policy.message_count_threshold == DEFAULT_MESSAGE_COUNT_THRESHOLD
    assert policy.idle_timeout_seconds == DEFAULT_IDLE_TIMEOUT_SECONDS
    assert policy.keep_recent_count == DEFAULT_KEEP_RECENT_COUNT
    assert policy.min_commit_interval_seconds == DEFAULT_MIN_COMMIT_INTERVAL_SECONDS
    assert (
        policy.failed_archive_roll_forward_token_budget
        == DEFAULT_FAILED_ARCHIVE_ROLL_FORWARD_TOKEN_BUDGET
    )


def test_from_dict_clamps_to_upper_bounds():
    policy = AutoCommitPolicy.from_dict(
        {
            "pending_token_threshold": 10_000_000,
            "message_count_threshold": 10_000,
            "idle_timeout_seconds": 999_999_999,
            "keep_recent_count": 10_000,
            "min_commit_interval_seconds": 999_999_999,
            "failed_archive_roll_forward_token_budget": 10_000_000,
        }
    )

    assert policy.pending_token_threshold == MAX_PENDING_TOKEN_THRESHOLD
    assert policy.message_count_threshold == MAX_MESSAGE_COUNT_THRESHOLD
    assert policy.idle_timeout_seconds == MAX_IDLE_TIMEOUT_SECONDS
    assert policy.keep_recent_count == MAX_MESSAGE_COUNT_THRESHOLD
    assert policy.min_commit_interval_seconds == MAX_IDLE_TIMEOUT_SECONDS
    assert (
        policy.failed_archive_roll_forward_token_budget
        == MAX_FAILED_ARCHIVE_ROLL_FORWARD_TOKEN_BUDGET
    )
    assert MAX_PENDING_TOKEN_THRESHOLD == 1_000_000
    assert MAX_MESSAGE_COUNT_THRESHOLD == 1_000


def test_from_dict_clamps_negatives_to_zero():
    policy = AutoCommitPolicy.from_dict(
        {
            "pending_token_threshold": -5,
            "message_count_threshold": -1,
            "idle_timeout_seconds": -100,
            "keep_recent_count": -3,
            "min_commit_interval_seconds": -9,
            "failed_archive_roll_forward_token_budget": -1,
        }
    )

    assert policy.to_dict() == {
        "pending_token_threshold": 0,
        "message_count_threshold": 0,
        "idle_timeout_seconds": 0,
        "keep_recent_count": 0,
        "min_commit_interval_seconds": 0,
        "failed_archive_roll_forward_token_budget": 0,
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


def test_to_dict_round_trips():
    payload = {
        "pending_token_threshold": 8000,
        "message_count_threshold": 40,
        "idle_timeout_seconds": 600,
        "keep_recent_count": 10,
        "min_commit_interval_seconds": 30,
        "failed_archive_roll_forward_token_budget": 120_000,
    }

    assert AutoCommitPolicy.from_dict(payload).to_dict() == payload


def test_roll_forward_budget_is_independent_of_pending_token_threshold():
    policy = AutoCommitPolicy.from_dict(
        {"pending_token_threshold": 8000, "failed_archive_roll_forward_token_budget": 0}
    )

    assert policy.pending_token_threshold == 8000
    assert policy.failed_archive_roll_forward_token_budget == 0

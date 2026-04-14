# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Tests for account-dimension whitelist and overflow policy behavior."""

from openviking.metrics.account_dimension import (
    OVERFLOW_ACCOUNT_ID,
    UNKNOWN_ACCOUNT_ID,
    MetricAccountDimensionPolicy,
)


def test_account_dimension_disabled_still_emits_unknown():
    policy = MetricAccountDimensionPolicy(
        enabled=False,
        metric_allowlist={"openviking_http_requests_total"},
        max_active_accounts=2,
    )

    assert (
        policy.resolve(metric_name="openviking_http_requests_total", account_id="acct-1")
        == UNKNOWN_ACCOUNT_ID
    )


def test_account_dimension_non_allowlisted_metric_emits_unknown():
    policy = MetricAccountDimensionPolicy(
        enabled=True,
        metric_allowlist={"openviking_http_requests_total"},
        max_active_accounts=2,
    )

    assert (
        policy.resolve(metric_name="openviking_task_pending", account_id="acct-1")
        == UNKNOWN_ACCOUNT_ID
    )


def test_account_dimension_overflow_after_limit():
    policy = MetricAccountDimensionPolicy(
        enabled=True,
        metric_allowlist={"openviking_http_requests_total"},
        max_active_accounts=1,
    )

    assert (
        policy.resolve(metric_name="openviking_http_requests_total", account_id="acct-1")
        == "acct-1"
    )
    assert (
        policy.resolve(metric_name="openviking_http_requests_total", account_id="acct-2")
        == OVERFLOW_ACCOUNT_ID
    )


def test_account_dimension_allowlist_supports_prefix_wildcard():
    policy = MetricAccountDimensionPolicy(
        enabled=True,
        metric_allowlist={"openviking_rerank_*"},
        max_active_accounts=10,
    )

    assert (
        policy.resolve(metric_name="openviking_rerank_calls_total", account_id="acct-1") == "acct-1"
    )
    assert (
        policy.resolve(metric_name="openviking_rerank_tokens_total", account_id="acct-1")
        == "acct-1"
    )
    assert (
        policy.resolve(metric_name="openviking_vlm_calls_total", account_id="acct-1")
        == UNKNOWN_ACCOUNT_ID
    )

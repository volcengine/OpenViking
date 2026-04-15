# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Tests for metrics request-scoped account context propagation."""

from openviking.metrics.account_context import (
    bind_metric_account_context,
    get_metric_account_context,
    reset_metric_account_context,
)


def test_http_account_context_round_trip():
    token = bind_metric_account_context(account_id="acct-1")
    try:
        assert get_metric_account_context().http_account_id == "acct-1"
    finally:
        reset_metric_account_context(token)

    assert get_metric_account_context().http_account_id is None

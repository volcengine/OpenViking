# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Token usage timestamps must be UTC-aware so reports do not shift them."""

from datetime import datetime, timedelta, timezone

from openviking.models.vlm.token_usage import TokenUsage, TokenUsageTracker
from openviking.utils.time_utils import parse_iso_datetime


def test_token_usage_timestamps_are_utc_aware():
    usage = TokenUsage()
    assert usage.last_updated.utcoffset() == timedelta(0)

    usage.update(10, 5)

    assert usage.last_updated.utcoffset() == timedelta(0)
    reported = parse_iso_datetime(usage.to_dict()["last_updated"])
    assert abs((datetime.now(timezone.utc) - reported).total_seconds()) < 60


def test_merge_keeps_utc_timestamps():
    tracker = TokenUsageTracker()
    tracker.update("model-a", "provider-a", 7, 3)

    merged = TokenUsageTracker.merge(tracker)
    model_usage = merged.get_model_usage("model-a")
    assert model_usage is not None
    provider_usage = model_usage.get_provider_usage("provider-a")
    assert provider_usage is not None

    assert provider_usage.last_updated.utcoffset() == timedelta(0)
    assert model_usage.total_usage.last_updated.utcoffset() == timedelta(0)
    reported = parse_iso_datetime(merged.to_dict()["total_usage"]["last_updated"])
    assert abs((datetime.now(timezone.utc) - reported).total_seconds()) < 60

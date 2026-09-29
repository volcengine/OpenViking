# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Request-model tests for the wall-clock schedule fields on PATCH /watches (#3932)."""

import pytest
from pydantic import ValidationError

from openviking.server.routers.watches import UpdateWatchRequest


def test_request_accepts_schedule_fields():
    body = UpdateWatchRequest(schedule_time="01:00", schedule_timezone="Asia/Shanghai")
    assert body.schedule_time == "01:00"
    assert body.schedule_timezone == "Asia/Shanghai"


def test_request_accepts_clear_strings():
    body = UpdateWatchRequest(schedule_time="", schedule_timezone="")
    assert body.schedule_time == ""
    assert body.schedule_timezone == ""


def test_request_rejects_unknown_fields():
    with pytest.raises(ValidationError):
        UpdateWatchRequest(schedule_hour=1)


def test_request_preserves_existing_untouched_fields():
    body = UpdateWatchRequest(reason="refreshed")
    assert body.schedule_time is None
    assert body.schedule_timezone is None

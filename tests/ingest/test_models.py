# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Unit tests for ingest timestamp helpers."""

from openviking.ingest.models import iso_from_epoch_ms


def test_iso_from_epoch_ms_accepts_integer_milliseconds():
    assert iso_from_epoch_ms(1_700_000_000_000) == "2023-11-14T22:13:20+00:00"


def test_iso_from_epoch_ms_accepts_integer_seconds():
    assert iso_from_epoch_ms(1_700_000_000) == "2023-11-14T22:13:20+00:00"


def test_iso_from_epoch_ms_rejects_bool():
    assert iso_from_epoch_ms(True) is None
    assert iso_from_epoch_ms(False) is None


def test_iso_from_epoch_ms_rejects_non_numeric():
    assert iso_from_epoch_ms("not-a-time") is None
    assert iso_from_epoch_ms(None) is None


def test_iso_from_epoch_ms_rejects_overflow():
    assert iso_from_epoch_ms(10**20) is None
    assert iso_from_epoch_ms(float("inf")) is None

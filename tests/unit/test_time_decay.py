# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from datetime import datetime, timezone

import pytest

from openviking.utils.time_decay import (
    TimeDecayFusionSpec,
    build_time_decay_fusion_spec,
    build_time_decay_post_process_ops,
    fuse_time_decay_scores,
    parse_duration_ms,
    time_decay_candidate_limit,
)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("0", 0),
        ("0m", 0),
        ("15m", 900_000),
        ("2h", 7_200_000),
        ("3d", 259_200_000),
        ("1095000d", 94_608_000_000_000),
    ],
)
def test_parse_duration_ms(value, expected):
    assert parse_duration_ms(value) == expected


@pytest.mark.parametrize(
    "value",
    ["", "-1d", "1.5h", "1s", "1D", " 1d", "1095001d", "99999999999999999999d", 1, None],
)
def test_parse_duration_ms_rejects_invalid_values(value):
    with pytest.raises(ValueError):
        parse_duration_ms(value)


def test_enabled_decay_uses_the_server_owned_curve():
    origin = datetime(2026, 1, 8, tzinfo=timezone.utc)
    spec = build_time_decay_fusion_spec(protection="1d", origin=origin)

    fused, addition = spec.fuse(0.8, "2025-12-31T00:00:00.000Z")
    assert addition == pytest.approx(0.5)
    assert fused == pytest.approx(0.8 * 0.5)

    protected, protected_time = spec.fuse(0.8, "2026-01-07T12:00:00.000Z")
    assert protected == pytest.approx(0.8)
    assert protected_time == pytest.approx(1.0)


@pytest.mark.parametrize("source_time", ["2026-01-01T00:00:00Z", "2026-01-15T00:00:00Z"])
def test_time_distance_matches_cloud_decay(source_time):
    spec = build_time_decay_fusion_spec(
        protection="0",
        origin=datetime(2026, 1, 8, tzinfo=timezone.utc),
    )

    fused, time_score = spec.fuse(0.8, source_time)

    assert fused == pytest.approx(0.4)
    assert time_score == pytest.approx(0.5)


@pytest.mark.parametrize("protection", ["0", "0m", "0h", "0d"])
def test_builder_accepts_zero_protection_duration(protection):
    spec = build_time_decay_fusion_spec(
        protection=protection,
        origin=datetime(2026, 1, 8, tzinfo=timezone.utc),
    )

    assert spec.offset_ms == 0


@pytest.mark.parametrize("protection", ["0", "0m", "0h", "0d"])
def test_post_process_omits_zero_offset(protection):
    origin = datetime(2026, 1, 8, tzinfo=timezone.utc)

    ops = build_time_decay_post_process_ops(protection=protection, origin=origin)

    addition = ops[0]["addition_score"][0]
    assert ops[0]["fusion_by"] == "multiply"
    assert "addition_score_weight" not in ops[0]
    assert "offset" not in addition
    assert addition == {
        "factor": 1,
        "base_value_from": "decay_func",
        "field": "updated_at",
        "func": "exp",
        "origin": "2026-01-08T00:00:00.000Z",
        "scale": "7d",
        "decay": 0.5,
    }


def test_direct_score_fusion_multiplies():
    assert fuse_time_decay_scores(origin_score=0.8, addition_score=0.5) == pytest.approx(0.4)


@pytest.mark.parametrize("source_time", [None, "", "not-a-time", float("nan")])
def test_missing_or_invalid_time_keeps_origin_score(source_time):
    spec = TimeDecayFusionSpec(
        field="updated_at",
        origin_ms=1_000.0,
        offset_ms=0,
        scale_ms=1_000,
        decay=0.5,
    )

    final_score, time_score = spec.fuse_optional(0.42, source_time)

    assert final_score == pytest.approx(0.42)
    assert time_score is None


def test_time_decay_candidate_budget_is_bounded():
    assert time_decay_candidate_limit(10) == 30
    assert time_decay_candidate_limit(10, offset=5) == 45
    assert time_decay_candidate_limit(100_000) == 100_000
    with pytest.raises(ValueError, match="must not exceed 100000"):
        time_decay_candidate_limit(100_001)

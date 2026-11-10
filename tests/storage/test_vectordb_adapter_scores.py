# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from datetime import datetime, timezone

from openviking.storage.vectordb_adapters.base import _normalize_result_score


def test_normalize_result_score_keeps_finite_numeric_scores():
    assert _normalize_result_score(0.75) == 0.75
    assert _normalize_result_score(float("nan")) == 0.0
    assert _normalize_result_score(float("inf")) == 0.0


def test_normalize_result_score_ignores_scalar_sort_values():
    assert _normalize_result_score("viking://user/alice/memories/trajectories/a.md") == 0.0
    assert _normalize_result_score(datetime.now(timezone.utc)) == 0.0

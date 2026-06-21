# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from __future__ import annotations

from openviking.session.train.batch_runner import (
    BatchTrainEvalConfig,
    _baseline_cache_key,
    _case_loader,
)


def test_case_loader_uses_sample_indices_filter_and_overrides_single_index():
    config = BatchTrainEvalConfig(
        dataset="tau2",
        domain="airline",
        train_index=99,
        train_indices=[1, 5, 5, 6],
        eval_index=88,
        eval_indices=[10, 14, 18],
        benchmark_service_url="http://127.0.0.1:1944",
    )

    train_loader = _case_loader(
        config,
        split="train",
        sample_index=config.train_index,
        sample_indices=config.train_indices,
    )
    eval_loader = _case_loader(
        config,
        split="train",
        sample_index=config.eval_index,
        sample_indices=config.eval_indices,
    )

    assert config.train_index is None
    assert config.eval_index is None
    assert config.train_indices == [1, 5, 6]
    assert config.eval_indices == [10, 14, 18]
    assert train_loader.filters == {"task_indices": [1, 5, 6]}
    assert eval_loader.filters == {"task_indices": [10, 14, 18]}


def test_baseline_cache_key_depends_on_eval_indices():
    base = BatchTrainEvalConfig(
        dataset="tau2",
        domain="airline",
        eval_indices=[1, 5, 6],
        trials=8,
        benchmark_service_url="http://127.0.0.1:1944",
    )

    assert _baseline_cache_key(base) == _baseline_cache_key(
        BatchTrainEvalConfig(
            dataset="tau2",
            domain="airline",
            eval_indices=[1, 5, 6],
            trials=8,
            benchmark_service_url="http://127.0.0.1:1944",
        )
    )
    assert _baseline_cache_key(base) != _baseline_cache_key(
        BatchTrainEvalConfig(
            dataset="tau2",
            domain="airline",
            eval_indices=[1, 5],
            trials=8,
            benchmark_service_url="http://127.0.0.1:1944",
        )
    )

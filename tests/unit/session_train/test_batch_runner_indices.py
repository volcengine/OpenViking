# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from __future__ import annotations

from openviking.session.train.batch_runner import (
    BatchTrainEvalConfig,
    _baseline_cache_key,
    _case_loader,
)


def test_case_loader_uses_multi_value_train_and_eval_index_filters():
    config = BatchTrainEvalConfig(
        dataset="tau2",
        domain="airline",
        train_index=[1, 5, 5, 6],
        eval_index="10,14,18",
        benchmark_service_url="http://127.0.0.1:1944",
    )

    train_loader = _case_loader(
        config,
        split="train",
        sample_index=config.train_index,
    )
    eval_loader = _case_loader(
        config,
        split="train",
        sample_index=config.eval_index,
    )

    assert config.train_index == [1, 5, 6]
    assert config.eval_index == [10, 14, 18]
    assert train_loader.filters == {"task_indices": [1, 5, 6]}
    assert eval_loader.filters == {"task_indices": [10, 14, 18]}


def test_baseline_cache_key_depends_on_multi_eval_index():
    base = BatchTrainEvalConfig(
        dataset="tau2",
        domain="airline",
        eval_index=[1, 5, 6],
        trials=8,
        benchmark_service_url="http://127.0.0.1:1944",
    )

    assert _baseline_cache_key(base) == _baseline_cache_key(
        BatchTrainEvalConfig(
            dataset="tau2",
            domain="airline",
            eval_index="1,5,6",
            trials=8,
            benchmark_service_url="http://127.0.0.1:1944",
        )
    )
    assert _baseline_cache_key(base) != _baseline_cache_key(
        BatchTrainEvalConfig(
            dataset="tau2",
            domain="airline",
            eval_index=[1, 5],
            trials=8,
            benchmark_service_url="http://127.0.0.1:1944",
        )
    )


def test_empty_index_filter_is_invalid():
    import pytest

    with pytest.raises(ValueError, match="train_index must not be empty"):
        BatchTrainEvalConfig(
            dataset="tau2",
            domain="airline",
            train_index="",
            benchmark_service_url="http://127.0.0.1:1944",
        )


def test_train_trials_defaults_to_one_and_validates_positive():
    import pytest

    config = BatchTrainEvalConfig(
        dataset="tau2",
        domain="airline",
        benchmark_service_url="http://127.0.0.1:1944",
    )

    assert config.train_trials == 1
    with pytest.raises(ValueError, match="train_trials must be > 0"):
        BatchTrainEvalConfig(
            dataset="tau2",
            domain="airline",
            train_trials=0,
            benchmark_service_url="http://127.0.0.1:1944",
        )

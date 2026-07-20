# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from __future__ import annotations

from openviking.session.train.batch_runner import (
    BatchTrainEvalConfig,
    _baseline_cache_key,
    _case_loader,
    _effective_eval_index,
    _train_rollout_cache_key_prefix,
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


def test_train_split_can_target_test_cases_and_separates_cache_keys():
    train_config = BatchTrainEvalConfig(
        dataset="vaka_dev_v1",
        domain="benchmark",
        train_split="train",
        benchmark_service_url="http://127.0.0.1:8765",
    )
    test_config = BatchTrainEvalConfig(
        dataset="vaka_dev_v1",
        domain="benchmark",
        train_split="test",
        benchmark_service_url="http://127.0.0.1:8765",
    )

    loader = _case_loader(test_config, split=test_config.train_split, sample_index=None)

    assert loader.split == "test"
    assert _train_rollout_cache_key_prefix(train_config) != _train_rollout_cache_key_prefix(
        test_config
    )


def test_train_split_rejects_unknown_split():
    import pytest

    with pytest.raises(ValueError, match="train_split must be train, dev, or test"):
        BatchTrainEvalConfig(
            dataset="vaka_dev_v1",
            domain="benchmark",
            train_split="unknown",
            benchmark_service_url="http://127.0.0.1:8765",
        )


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


def test_eval_split_train_defaults_eval_index_to_train_index():
    config = BatchTrainEvalConfig(
        dataset="tau2",
        domain="airline",
        train_index=[5, 6],
        eval_split="train",
        benchmark_service_url="http://127.0.0.1:1944",
    )

    eval_loader = _case_loader(
        config,
        split=config.eval_split,
        sample_index=_effective_eval_index(config),
    )

    assert config.eval_index is None
    assert _effective_eval_index(config) == [5, 6]
    assert eval_loader.filters == {"task_indices": [5, 6]}


def test_explicit_eval_index_overrides_train_index_for_train_split_eval():
    config = BatchTrainEvalConfig(
        dataset="tau2",
        domain="airline",
        train_index=[5, 6],
        eval_index=29,
        eval_split="train",
        benchmark_service_url="http://127.0.0.1:1944",
    )

    assert _effective_eval_index(config) == [29]


def test_test_split_eval_does_not_implicitly_use_train_index():
    config = BatchTrainEvalConfig(
        dataset="tau2",
        domain="airline",
        train_index=[5, 6],
        eval_split="test",
        benchmark_service_url="http://127.0.0.1:1944",
    )

    assert _effective_eval_index(config) is None

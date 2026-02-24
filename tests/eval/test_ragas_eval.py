# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0

import json
import pytest
from pathlib import Path

from openviking.eval.ragas import (
    RagasEvaluator,
    RagasConfig,
    EvalSample,
    EvalDataset,
    _create_ragas_llm_from_config,
)


EVAL_RESULTS_FILE = Path(__file__).parent.parent.parent / "eval_results.json"


def load_eval_results() -> dict:
    """Load eval_results.json for testing."""
    if not EVAL_RESULTS_FILE.exists():
        pytest.skip(f"Eval results file not found: {EVAL_RESULTS_FILE}")
    with open(EVAL_RESULTS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def test_load_eval_results():
    """Test that eval_results.json can be loaded correctly."""
    results = load_eval_results()
    assert "total_questions" in results
    assert "results" in results
    assert len(results["results"]) > 0


def test_eval_sample_from_results():
    """Test creating EvalSample from eval results."""
    results = load_eval_results()
    first_result = results["results"][0]

    sample = EvalSample(
        query=first_result["question"],
        context=[c["content"] for c in first_result["contexts"]],
        response="",
        ground_truth=first_result.get("ground_truth", ""),
    )

    assert sample.query == first_result["question"]
    assert len(sample.context) == len(first_result["contexts"])
    assert sample.ground_truth == first_result.get("ground_truth", "")


def test_eval_dataset_from_results():
    """Test creating EvalDataset from eval results."""
    results = load_eval_results()

    samples = []
    for result in results["results"]:
        sample = EvalSample(
            query=result["question"],
            context=[c["content"] for c in result["contexts"]],
            response="",
            ground_truth=result.get("ground_truth", ""),
        )
        samples.append(sample)

    dataset = EvalDataset(name="test_dataset", samples=samples)
    assert len(dataset) == len(results["results"])


def test_ragas_config_defaults():
    """Test RagasConfig default values."""
    config = RagasConfig()
    assert config.max_workers == 16
    assert config.batch_size == 10
    assert config.timeout == 180
    assert config.max_retries == 3
    assert config.show_progress is True
    assert config.raise_exceptions is False


def test_ragas_config_from_env(monkeypatch):
    """Test RagasConfig from environment variables."""
    monkeypatch.setenv("RAGAS_MAX_WORKERS", "8")
    monkeypatch.setenv("RAGAS_BATCH_SIZE", "5")
    monkeypatch.setenv("RAGAS_TIMEOUT", "120")
    monkeypatch.setenv("RAGAS_MAX_RETRIES", "2")

    config = RagasConfig.from_env()
    assert config.max_workers == 8
    assert config.batch_size == 5
    assert config.timeout == 120
    assert config.max_retries == 2


def test_ragas_evaluator_initialization():
    """Test RagasEvaluator can be initialized."""
    evaluator = RagasEvaluator()
    assert evaluator.metrics is not None
    assert len(evaluator.metrics) >= 2


def test_ragas_evaluator_with_config():
    """Test RagasEvaluator with custom config."""
    config = RagasConfig(
        max_workers=4,
        batch_size=2,
        timeout=60,
        max_retries=1,
    )
    evaluator = RagasEvaluator(config=config)
    assert evaluator.max_workers == 4
    assert evaluator.batch_size == 2
    assert evaluator.timeout == 60
    assert evaluator.max_retries == 1


def test_ragas_evaluator_with_params():
    """Test RagasEvaluator with individual parameters."""
    evaluator = RagasEvaluator(
        max_workers=8,
        batch_size=3,
        timeout=90,
        max_retries=2,
        show_progress=False,
    )
    assert evaluator.max_workers == 8
    assert evaluator.batch_size == 3
    assert evaluator.timeout == 90
    assert evaluator.max_retries == 2
    assert evaluator.show_progress is False


@pytest.mark.asyncio
async def test_run_ragas_evaluation_with_file():
    """
    Test run_ragas_evaluation using eval_results.json.

    This test requires LLM configuration via:
    - Environment variables: RAGAS_LLM_API_KEY, RAGAS_LLM_API_BASE, RAGAS_LLM_MODEL
    - Or OpenViking VLM config in ~/.openviking/ov.conf
    """
    results = load_eval_results()

    evaluator = RagasEvaluator(
        max_workers=4,
        batch_size=5,
        timeout=120,
    )
    if evaluator.llm is None:
        pytest.skip(
            "RAGAS LLM not configured. Set RAGAS_LLM_API_KEY, RAGAS_LLM_API_BASE, "
            "RAGAS_LLM_MODEL environment variables or configure VLM in ~/.openviking/ov.conf"
        )

    samples = []
    for result in results["results"]:
        sample = EvalSample(
            query=result["question"],
            context=[c["content"] for c in result["contexts"]],
            response="",
            ground_truth=result.get("ground_truth", ""),
        )
        samples.append(sample)

    dataset = EvalDataset(name="test_rag_eval", samples=samples)

    ragas_result = await evaluator.evaluate_dataset(dataset)

    assert ragas_result is not None
    assert ragas_result.sample_count == len(samples)
    assert len(ragas_result.results) == len(samples)
    assert ragas_result.mean_scores is not None


@pytest.mark.asyncio
async def test_run_ragas_evaluation_single_sample():
    """
    Test run_ragas_evaluation with a single sample.

    This test requires LLM configuration.
    """
    results = load_eval_results()
    first_result = results["results"][0]

    evaluator = RagasEvaluator()
    if evaluator.llm is None:
        pytest.skip(
            "RAGAS LLM not configured. Set RAGAS_LLM_API_KEY, RAGAS_LLM_API_BASE, "
            "RAGAS_LLM_MODEL environment variables or configure VLM in ~/.openviking/ov.conf"
        )

    sample = EvalSample(
        query=first_result["question"],
        context=[c["content"] for c in first_result["contexts"]],
        response="",
        ground_truth=first_result.get("ground_truth", ""),
    )

    dataset = EvalDataset(name="single_sample_test", samples=[sample])

    ragas_result = await evaluator.evaluate_dataset(dataset)

    assert ragas_result is not None
    assert ragas_result.sample_count == 1
    assert len(ragas_result.results) == 1


def test_llm_config_from_env(monkeypatch):
    """Test LLM configuration from environment variables."""
    monkeypatch.setenv("RAGAS_LLM_API_KEY", "test-api-key")
    monkeypatch.setenv("RAGAS_LLM_API_BASE", "https://api.example.com/v1")
    monkeypatch.setenv("RAGAS_LLM_MODEL", "test-model")

    llm = _create_ragas_llm_from_config()
    assert llm is not None


def test_ragas_evaluator_no_llm_error(monkeypatch):
    """Test that RagasEvaluator raises error when no LLM is configured."""
    monkeypatch.delenv("RAGAS_LLM_API_KEY", raising=False)

    sample = EvalSample(
        query="test question",
        context=["test context"],
        response="",
        ground_truth="test ground truth",
    )
    dataset = EvalDataset(name="error_test", samples=[sample])

    evaluator = RagasEvaluator()
    if evaluator.llm is not None:
        pytest.skip("LLM is configured, skipping no-LLM error test")

    import asyncio
    with pytest.raises(ValueError, match="RAGAS evaluation requires an LLM"):
        asyncio.run(evaluator.evaluate_dataset(dataset))

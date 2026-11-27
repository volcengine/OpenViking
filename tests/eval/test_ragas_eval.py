# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from types import SimpleNamespace

import pandas as pd
import pytest
import ragas
from ragas.metrics import ExactMatch

from openviking.eval import EvalDataset, EvalSample, RagasConfig, RagasEvaluator
from openviking.eval.ragas.rag_eval import run_ragas_evaluation


async def test_run_ragas_evaluation_scores_retrieval_results(monkeypatch):
    monkeypatch.setenv("RAGAS_LLM_API_KEY", "test-api-key")
    monkeypatch.setenv("RAGAS_LLM_API_BASE", "http://model.test/v1")
    monkeypatch.setenv("RAGAS_LLM_MODEL", "test-model")
    monkeypatch.setenv("RAGAS_MAX_WORKERS", "8")
    monkeypatch.setenv("RAGAS_BATCH_SIZE", "5")
    monkeypatch.setenv("RAGAS_TIMEOUT", "120")
    monkeypatch.setenv("RAGAS_MAX_RETRIES", "2")

    def score(dataset, *, metrics, llm, run_config, batch_size, **kwargs):
        assert dataset.to_dict() == {
            "question": ["Where?", "When?"],
            "contexts": [["In Paris"], ["In May", "On a weekend"]],
            "answer": ["", ""],
            "ground_truth": ["Paris", ""],
        }
        assert {metric.name for metric in metrics} == {
            "faithfulness",
            "answer_relevancy",
            "context_precision",
            "context_recall",
        }
        assert llm.langchain_llm.model_name == "test-model"
        assert str(llm.langchain_llm.openai_api_base) == "http://model.test/v1"
        assert (run_config.max_workers, run_config.timeout, run_config.max_retries) == (8, 120, 2)
        assert batch_size == 5
        scores = pd.DataFrame({"context_precision": [1.0, 0.0], "context_recall": [0.5, None]})
        return SimpleNamespace(to_pandas=lambda: scores)

    monkeypatch.setattr(ragas, "evaluate", score)
    result = await run_ragas_evaluation(
        {
            "results": [
                {
                    "question": "Where?",
                    "contexts": [{"content": "In Paris"}],
                    "ground_truth": "Paris",
                },
                {
                    "question": "When?",
                    "contexts": [{"content": "In May"}, {"content": "On a weekend"}],
                },
            ]
        }
    )

    assert result.dataset_name == "rag_eval"
    assert result.sample_count == 2
    first, second = result.results
    assert (first.sample.query, first.sample.ground_truth) == ("Where?", "Paris")
    assert first.scores == {"context_precision": 1.0, "context_recall": 0.5}
    assert second.sample.query == "When?"
    assert second.scores["context_precision"] == 0.0
    assert result.mean_scores == {"context_precision": 0.5, "context_recall": 0.5}


async def test_ragas_evaluator_scores_one_sample_with_explicit_config(monkeypatch):
    monkeypatch.setenv("RAGAS_DO_NOT_TRACK", "true")
    monkeypatch.setenv("RAGAS_BATCH_SIZE", "9")
    evaluator = RagasEvaluator(
        metrics=[ExactMatch()],
        llm=object(),  # ExactMatch uses neither generation nor embeddings.
        config=RagasConfig(batch_size=1, max_workers=4),
        max_workers=2,
        show_progress=False,
    )
    sample = EvalSample(
        query="Which city?", context=["In Paris"], response="Paris", ground_truth="Paris"
    )

    result = await evaluator.evaluate_sample(sample)

    assert result.sample == sample
    assert result.scores == {"exact_match": 1.0}
    assert (evaluator.batch_size, evaluator.max_workers) == (1, 2)


async def test_ragas_evaluator_no_llm_error(monkeypatch):
    def missing_config():
        raise FileNotFoundError("no OpenViking config")

    monkeypatch.delenv("RAGAS_LLM_API_KEY", raising=False)
    monkeypatch.setattr("openviking_cli.utils.config.get_openviking_config", missing_config)
    dataset = EvalDataset(
        samples=[
            EvalSample(query="Which city?", context=["In Paris"], response="", ground_truth="")
        ]
    )

    with pytest.raises(ValueError, match="RAGAS evaluation requires an LLM"):
        await RagasEvaluator().evaluate_dataset(dataset)

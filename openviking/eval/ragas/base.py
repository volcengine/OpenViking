# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""
Base evaluator class for OpenViking.
"""

from abc import ABC, abstractmethod
from typing import Dict, List

from .types import EvalDataset, EvalResult, EvalSample, SummaryResult


class BaseEvaluator(ABC):
    """Abstract base class for RAG evaluators."""

    @abstractmethod
    async def evaluate_sample(self, sample: EvalSample) -> EvalResult:
        """
        Evaluate a single sample.

        Args:
            sample: The evaluation sample (query, context, response, ground_truth)

        Returns:
            EvalResult with scores
        """
        pass

    async def evaluate_dataset(self, dataset: EvalDataset) -> SummaryResult:
        """
        Evaluate a dataset of samples.

        Args:
            dataset: The collection of evaluation samples

        Returns:
            SummaryResult with aggregated scores
        """
        results = []
        for sample in dataset.samples:
            res = await self.evaluate_sample(sample)
            results.append(res)

        return self._summarize(dataset.name, results)

    def _summarize(self, name: str, results: List[EvalResult]) -> SummaryResult:
        """Aggregate results into a summary."""
        if not results:
            return SummaryResult(
                dataset_name=name,
                sample_count=0,
                mean_scores={},
                results=[]
            )

        metric_sums: Dict[str, float] = {}
        for res in results:
            for metric, score in res.scores.items():
                metric_sums[metric] = metric_sums.get(metric, 0.0) + score

        count = len(results)
        mean_scores = {m: s / count for m, s in metric_sums.items()}

        return SummaryResult(
            dataset_name=name,
            sample_count=count,
            mean_scores=mean_scores,
            results=results
        )

# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""
RAGAS evaluator integration for OpenViking.
"""

import asyncio
import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from openviking_cli.utils.logger import get_logger

from .base import BaseEvaluator
from .generator import DatasetGenerator
from .pipeline import RAGQueryPipeline
from .playback import IOPlayback, PlaybackResult, PlaybackStats
from .record_analysis import (
    RecordAnalysisStats,
    analyze_records,
    print_analysis_stats,
)
from .types import EvalDataset, EvalResult, EvalSample, SummaryResult

logger = get_logger(__name__)

RAGAS_LLM_API_KEY_ENV = "RAGAS_LLM_API_KEY"
RAGAS_LLM_API_BASE_ENV = "RAGAS_LLM_API_BASE"
RAGAS_LLM_MODEL_ENV = "RAGAS_LLM_MODEL"

RAGAS_MAX_WORKERS_ENV = "RAGAS_MAX_WORKERS"
RAGAS_BATCH_SIZE_ENV = "RAGAS_BATCH_SIZE"
RAGAS_TIMEOUT_ENV = "RAGAS_TIMEOUT"
RAGAS_MAX_RETRIES_ENV = "RAGAS_MAX_RETRIES"


@dataclass
class RagasConfig:
    """
    Configuration for RAGAS evaluation.

    Attributes:
        max_workers: Maximum number of concurrent workers for evaluation.
        batch_size: Number of samples to process in each batch.
        timeout: Timeout in seconds for each evaluation.
        max_retries: Maximum number of retries for failed evaluations.
        show_progress: Whether to show progress bar during evaluation.
        raise_exceptions: Whether to raise exceptions during evaluation.
    """
    max_workers: int = 16
    batch_size: int = 10
    timeout: int = 180
    max_retries: int = 3
    show_progress: bool = True
    raise_exceptions: bool = False

    @classmethod
    def from_env(cls) -> "RagasConfig":
        """
        Create configuration from environment variables.

        Environment variables:
            - RAGAS_MAX_WORKERS: Maximum concurrent workers (default: 16)
            - RAGAS_BATCH_SIZE: Batch size for processing (default: 10)
            - RAGAS_TIMEOUT: Timeout in seconds (default: 180)
            - RAGAS_MAX_RETRIES: Maximum retries (default: 3)
        """
        return cls(
            max_workers=int(os.environ.get(RAGAS_MAX_WORKERS_ENV, 16)),
            batch_size=int(os.environ.get(RAGAS_BATCH_SIZE_ENV, 10)),
            timeout=int(os.environ.get(RAGAS_TIMEOUT_ENV, 180)),
            max_retries=int(os.environ.get(RAGAS_MAX_RETRIES_ENV, 3)),
        )


def _get_llm_config_from_env() -> Optional[Dict[str, str]]:
    """
    Get LLM configuration from environment variables.

    Environment variables:
        - RAGAS_LLM_API_KEY: API key for the LLM
        - RAGAS_LLM_API_BASE: API base URL (e.g., https://ark.cn-beijing.volces.com/api/v3)
        - RAGAS_LLM_MODEL: Model name (e.g., ep-xxxx-xxxx)

    Returns:
        Dict with api_key, api_base, model or None if not configured.
    """
    api_key = os.environ.get(RAGAS_LLM_API_KEY_ENV)
    api_base = os.environ.get(RAGAS_LLM_API_BASE_ENV)
    model = os.environ.get(RAGAS_LLM_MODEL_ENV)

    if api_key:
        return {
            "api_key": api_key,
            "api_base": api_base,
            "model": model,
        }
    return None


def _create_ragas_llm_from_config() -> Optional[Any]:
    """
    Create a RAGAS-compatible LLM from OpenViking VLM configuration or environment variables.

    Priority:
        1. Environment variables (RAGAS_LLM_API_KEY, RAGAS_LLM_API_BASE, RAGAS_LLM_MODEL)
        2. OpenViking VLM configuration (~/.openviking/ov.conf)

    Returns:
        RAGAS LLM instance or None if VLM is not configured.
    """
    try:
        from openai import OpenAI
        from ragas.llms import llm_factory
    except ImportError:
        return None

    env_config = _get_llm_config_from_env()
    if env_config:
        api_key = env_config["api_key"]
        api_base = env_config["api_base"]
        model_name = env_config["model"] or "gpt-4o-mini"

        logger.info(f"Using RAGAS LLM from environment: model={model_name}, base_url={api_base}")

        client = OpenAI(
            api_key=api_key,
            base_url=api_base,
        )
        return llm_factory(model_name, client=client)

    try:
        from openviking_cli.utils.config import get_openviking_config
    except ImportError:
        return None

    try:
        config = get_openviking_config()
    except FileNotFoundError:
        logger.debug("OpenViking config file not found, skipping VLM config")
        return None

    vlm_config = config.vlm

    if not vlm_config.is_available():
        logger.warning(
            "VLM is not configured for RAGAS evaluation. "
            "Please configure VLM in ~/.openviking/ov.conf or set environment variables "
            "(RAGAS_LLM_API_KEY, RAGAS_LLM_API_BASE, RAGAS_LLM_MODEL)."
        )
        return None

    client = OpenAI(
        api_key=vlm_config.api_key,
        base_url=vlm_config.api_base,
    )

    model_name = vlm_config.model or "gpt-4o-mini"
    return llm_factory(model_name, client=client)


class RagasEvaluator(BaseEvaluator):
    """
    Evaluator using the RAGAS framework.

    Requires 'ragas' and 'datasets' packages.

    Performance Configuration:
        - max_workers: Concurrent workers for parallel evaluation (default: 16)
        - batch_size: Samples per batch (default: 10)
        - timeout: Timeout per evaluation in seconds (default: 180)
        - max_retries: Retry attempts for failed evaluations (default: 3)

    Environment Variables:
        - RAGAS_MAX_WORKERS: Override max_workers
        - RAGAS_BATCH_SIZE: Override batch_size
        - RAGAS_TIMEOUT: Override timeout
        - RAGAS_MAX_RETRIES: Override max_retries
    """

    def __init__(
        self,
        metrics: Optional[List[Any]] = None,
        llm: Optional[Any] = None,
        embeddings: Optional[Any] = None,
        config: Optional[RagasConfig] = None,
        max_workers: Optional[int] = None,
        batch_size: Optional[int] = None,
        timeout: Optional[int] = None,
        max_retries: Optional[int] = None,
        show_progress: bool = True,
        raise_exceptions: bool = False,
    ):
        """
        Initialize Ragas evaluator.

        Args:
            metrics: List of Ragas metrics (e.g., faithfulness, answer_relevancy).
                    If None, uses a default set.
            llm: LLM to use for evaluation (RAGAS LLM instance).
                 If None, uses OpenViking VLM configuration.
            embeddings: Embeddings to use for evaluation.
            config: RagasConfig instance with all settings.
            max_workers: Override max_workers for concurrent evaluation.
            batch_size: Override batch size for processing.
            timeout: Override timeout in seconds.
            max_retries: Override max retries for failed evaluations.
            show_progress: Whether to show progress bar.
            raise_exceptions: Whether to raise exceptions during evaluation.
        """
        try:
            from ragas.metrics._answer_relevance import AnswerRelevancy
            from ragas.metrics._context_precision import ContextPrecision
            from ragas.metrics._context_recall import ContextRecall
            from ragas.metrics._faithfulness import Faithfulness
        except ImportError:
            raise ImportError(
                "RAGAS evaluation requires 'ragas' package. "
                "Install it with: pip install ragas datasets"
            )

        self.metrics = metrics or [
            Faithfulness(),
            AnswerRelevancy(),
            ContextPrecision(),
            ContextRecall(),
        ]
        self.llm = llm or _create_ragas_llm_from_config()
        self.embeddings = embeddings

        if config is None:
            config = RagasConfig.from_env()

        self.max_workers = max_workers if max_workers is not None else config.max_workers
        self.batch_size = batch_size if batch_size is not None else config.batch_size
        self.timeout = timeout if timeout is not None else config.timeout
        self.max_retries = max_retries if max_retries is not None else config.max_retries
        self.show_progress = show_progress
        self.raise_exceptions = raise_exceptions

        logger.info(
            f"RagasEvaluator initialized: max_workers={self.max_workers}, "
            f"batch_size={self.batch_size}, timeout={self.timeout}s, "
            f"max_retries={self.max_retries}"
        )

    async def evaluate_sample(self, sample: EvalSample) -> EvalResult:
        """Evaluate a single sample using Ragas."""
        dataset = EvalDataset(samples=[sample])
        summary = await self.evaluate_dataset(dataset)
        return summary.results[0]

    async def evaluate_dataset(self, dataset: EvalDataset) -> SummaryResult:
        """Evaluate a dataset using Ragas."""
        try:
            from datasets import Dataset
            from ragas import evaluate
            from ragas.run_config import RunConfig
        except ImportError:
            raise ImportError(
                "RAGAS evaluation requires 'datasets' package. "
                "Install it with: pip install datasets"
            )

        if self.llm is None:
            raise ValueError(
                "RAGAS evaluation requires an LLM. "
                "Please configure via one of:\n"
                "  1. Environment variables: RAGAS_LLM_API_KEY, RAGAS_LLM_API_BASE, RAGAS_LLM_MODEL\n"
                "  2. OpenViking VLM config in ~/.openviking/ov.conf\n"
                "  3. Pass an llm parameter to RagasEvaluator"
            )

        data = {
            "question": [s.query for s in dataset.samples],
            "contexts": [s.context for s in dataset.samples],
            "answer": [s.response or "" for s in dataset.samples],
            "ground_truth": [s.ground_truth or "" for s in dataset.samples],
        }

        ragas_dataset = Dataset.from_dict(data)

        run_config = RunConfig(
            timeout=self.timeout,
            max_retries=self.max_retries,
            max_workers=self.max_workers,
        )

        logger.info(
            f"Starting RAGAS evaluation: {len(dataset.samples)} samples, "
            f"{len(self.metrics)} metrics, batch_size={self.batch_size}"
        )

        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None,
            lambda: evaluate(
                ragas_dataset,
                metrics=self.metrics,
                llm=self.llm,
                embeddings=self.embeddings,
                run_config=run_config,
                batch_size=self.batch_size,
                show_progress=self.show_progress,
                raise_exceptions=self.raise_exceptions,
            ),
        )

        eval_results = []
        df = result.to_pandas()

        for i, sample in enumerate(dataset.samples):
            scores = {}
            for metric in self.metrics:
                metric_name = metric.name
                if metric_name in df.columns:
                    scores[metric_name] = float(df.iloc[i][metric_name])

            eval_results.append(
                EvalResult(
                    sample=sample,
                    scores=scores
                )
            )

        mean_scores = {}
        for metric in self.metrics:
            metric_name = metric.name
            if metric_name in df.columns:
                valid_scores = df[metric_name].dropna()
                if len(valid_scores) > 0:
                    mean_scores[metric_name] = float(valid_scores.mean())

        logger.info(f"RAGAS evaluation completed: mean_scores={mean_scores}")

        return SummaryResult(
            dataset_name=dataset.name,
            sample_count=len(dataset.samples),
            mean_scores=mean_scores,
            results=eval_results,
        )


__all__ = [
    "BaseEvaluator",
    "RagasEvaluator",
    "RagasConfig",
    "EvalSample",
    "EvalResult",
    "EvalDataset",
    "SummaryResult",
    "DatasetGenerator",
    "RAGQueryPipeline",
    "IOPlayback",
    "PlaybackResult",
    "PlaybackStats",
    "RecordAnalysisStats",
    "analyze_records",
    "print_analysis_stats",
]

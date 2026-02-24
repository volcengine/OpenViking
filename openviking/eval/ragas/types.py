# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""
Data types for OpenViking evaluation module.
"""

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class EvalSample(BaseModel):
    """A single evaluation sample."""

    query: str = Field(..., description="The input query/question")
    context: List[str] = Field(default_factory=list, description="Retrieved context chunks")
    response: Optional[str] = Field(None, description="The generated answer")
    ground_truth: Optional[str] = Field(None, description="The reference/correct answer")
    meta: Dict[str, Any] = Field(default_factory=dict, description="Additional metadata")


class EvalResult(BaseModel):
    """Result of an evaluation for a single sample."""

    sample: EvalSample
    scores: Dict[str, float] = Field(..., description="Metric names and their scores")
    feedback: Optional[str] = Field(None, description="Qualitative feedback or error message")


class EvalDataset(BaseModel):
    """A collection of evaluation samples."""

    samples: List[EvalSample] = Field(default_factory=list)
    name: str = "default_dataset"
    description: Optional[str] = None

    def __len__(self) -> int:
        return len(self.samples)


class SummaryResult(BaseModel):
    """Summary of evaluation results across a dataset."""

    dataset_name: str
    sample_count: int
    mean_scores: Dict[str, float]
    results: List[EvalResult]

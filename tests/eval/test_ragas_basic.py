# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0

import pytest
import tempfile
import json
from pathlib import Path

from openviking.eval.ragas.types import EvalSample, EvalDataset
from openviking.eval.ragas.generator import DatasetGenerator
from openviking.eval.ragas.pipeline import RAGQueryPipeline


def test_eval_types():
    sample = EvalSample(
        query="test query",
        context=["context1", "context2"],
        response="test response",
        ground_truth="test ground truth"
    )
    assert sample.query == "test query"
    assert len(sample.context) == 2
    
    dataset = EvalDataset(samples=[sample])
    assert len(dataset) == 1


def test_generator_initialization():
    gen = DatasetGenerator()
    assert gen.llm is None


def test_pipeline_initialization():
    pipeline = RAGQueryPipeline(config_path="./test.conf", data_path="./test_data")
    assert pipeline.config_path == "./test.conf"
    assert pipeline.data_path == "./test_data"
    assert pipeline._client is None


def test_question_loader():
    with tempfile.NamedTemporaryFile(mode='w', suffix='.jsonl', delete=False) as f:
        f.write('{"question": "What is OpenViking?"}\n')
        f.write('{"question": "How does memory work?", "ground_truth": "Hierarchical"}\n')
        f.write('\n')
        f.write('{"invalid": "no question field"}\n')
        temp_path = f.name
    
    try:
        questions = []
        with open(temp_path, 'r') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    item = json.loads(line)
                    if "question" in item:
                        questions.append(item)
                except json.JSONDecodeError:
                    pass
        
        assert len(questions) == 2
        assert questions[0]["question"] == "What is OpenViking?"
        assert questions[1]["ground_truth"] == "Hierarchical"
    finally:
        Path(temp_path).unlink()


def test_eval_dataset_operations():
    samples = [
        EvalSample(query="q1", context=["c1"], response="r1"),
        EvalSample(query="q2", context=["c2"], response="r2"),
    ]
    
    dataset = EvalDataset(name="test_dataset", samples=samples)
    assert len(dataset) == 2
    assert dataset.name == "test_dataset"
    
    dataset.samples.append(EvalSample(query="q3", context=["c3"]))
    assert len(dataset) == 3

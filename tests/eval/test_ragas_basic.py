# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import json
import queue
import tempfile
import threading
from pathlib import Path

from openviking.eval.recorder.async_writer import AsyncRecordWriter
from openviking.eval.ragas.generator import DatasetGenerator
from openviking.eval.ragas.pipeline import RAGQueryPipeline
from openviking.eval.ragas.types import EvalDataset, EvalSample
from openviking_cli.retrieve.types import ContextType, FindResult, MatchedContext


def test_eval_types():
    sample = EvalSample(
        query="test query",
        context=["context1", "context2"],
        response="test response",
        ground_truth="test ground truth",
    )
    assert sample.query == "test query"
    assert len(sample.context) == 2

    dataset = EvalDataset(samples=[sample])
    assert len(dataset) == 1


def test_generator_initialization():
    gen = DatasetGenerator()
    assert gen.llm is None


def test_pipeline_initialization():
    pipeline = RAGQueryPipeline(config_path="./test.conf", data_path="./test_data/test_ragas")
    assert pipeline.config_path == "./test.conf"
    assert pipeline.data_path == "./test_data/test_ragas"
    assert pipeline._client is None


def test_async_record_writer_drains_records_before_stop_sentinel():
    writer = AsyncRecordWriter.__new__(AsyncRecordWriter)
    writer._queue = queue.Queue()
    writer._stop_event = threading.Event()
    writer._stop_event.set()
    writer.batch_size = 100
    writer.flush_interval = 60
    writer._queue.put({"id": 1})
    writer._queue.put({"id": 2})
    writer._queue.put(None)
    flushed = []
    writer._flush_batch = lambda batch: flushed.extend(batch)

    writer._writer_loop()

    assert flushed == [{"id": 1}, {"id": 2}]


def test_pipeline_query_consumes_find_result_and_generates_answer():
    class Client:
        def search(self, **kwargs):
            return FindResult(
                memories=[
                    MatchedContext(
                        uri="viking://user/memories/profile.md",
                        context_type=ContextType.MEMORY,
                        overview="Profile overview",
                    )
                ],
                resources=[
                    MatchedContext(
                        uri="viking://resources/guide.md",
                        context_type=ContextType.RESOURCE,
                        abstract="Guide abstract",
                    )
                ],
                skills=[],
            )

    class LLM:
        def get_completion(self, prompt):
            return "Generated answer"

    pipeline = RAGQueryPipeline()
    pipeline._client = Client()
    pipeline._llm = LLM()

    result = pipeline.query("How does it work?")

    assert result["contexts"] == ["Profile overview", "Guide abstract"]
    assert result["retrieved_uris"] == [
        "viking://user/memories/profile.md",
        "viking://resources/guide.md",
    ]
    assert result["answer"] == "Generated answer"


def test_question_loader():
    with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
        f.write('{"question": "What is OpenViking?"}\n')
        f.write('{"question": "How does memory work?", "ground_truth": "Hierarchical"}\n')
        f.write("\n")
        f.write('{"invalid": "no question field"}\n')
        temp_path = f.name

    try:
        questions = []
        with open(temp_path, "r") as f:
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

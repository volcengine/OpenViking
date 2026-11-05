# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import json
import queue
import tempfile
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from openviking.eval.ragas.generator import DatasetGenerator
from openviking.eval.ragas.pipeline import RAGQueryPipeline
from openviking.eval.ragas.types import EvalDataset, EvalSample
from openviking.eval.recorder.async_writer import AsyncRecordWriter


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
    pipeline = RAGQueryPipeline(config_path="./test.conf", server_url="http://openviking.test")
    assert pipeline.config_path == "./test.conf"
    assert pipeline.server_url == "http://openviking.test"
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


def test_pipeline_query_consumes_http_result_and_generates_answer():
    class Client:
        def search(self, query, **kwargs):
            return {
                "memories": [
                    {
                        "uri": "viking://user/memories/profile.md",
                        "level": 1,
                        "overview": "Profile overview",
                    }
                ],
                "resources": [
                    {
                        "uri": "viking://resources/guide.md",
                        "abstract": "Guide abstract",
                    }
                ],
                "skills": [],
            }

        def read(self, uri):
            assert uri == "viking://resources/guide.md"
            return "Guide full text"

    class LLM:
        def get_completion(self, prompt):
            assert "Profile overview\n\n---\n\nGuide full text" in prompt
            assert "Guide abstract" not in prompt
            return "Generated answer"

    pipeline = RAGQueryPipeline()
    pipeline._client = Client()
    pipeline._llm = LLM()

    result = pipeline.query("How does it work?")

    assert result["contexts"] == ["Profile overview", "Guide full text"]
    assert result["retrieved_uris"] == [
        "viking://user/memories/profile.md",
        "viking://resources/guide.md",
    ]
    assert result["answer"] == "Generated answer"


@pytest.mark.asyncio
async def test_rag_cli_scores_exact_generation_evidence(monkeypatch, tmp_path):
    from openviking.eval import ragas
    from openviking.eval.ragas.rag_eval import RAGEvaluator, run_ragas_evaluation, save_report

    class Client:
        def search(self, query, **kwargs):
            assert query == "How long does approval take?"
            return {
                "resources": [
                    {"uri": f"viking://resources/demo/page{i}.md", "abstract": "Short summary"}
                    for i in range(5)
                ]
            }

        def read(self, uri):
            return f"Full body of {uri}: approval takes twelve business days."

    prompts = []

    class LLM:
        def get_completion(self, prompt):
            prompts.append(prompt)
            return "Approval takes twelve business days."

    captured = []

    class Judge:
        async def evaluate_dataset(self, dataset):
            captured.extend(dataset.samples)
            return SimpleNamespace(mean_scores={})

    evaluator = RAGEvaluator([], [])
    evaluator._client = Client()
    evaluator._pipeline._llm = LLM()
    results = await evaluator.evaluate(
        [{"question": "How long does approval take?", "answer": "REFERENCE_ONLY_SENTINEL"}],
        mode="end-to-end",
    )
    # Exercise the saved-report scoring path, with no second generation call.
    output = tmp_path / "results.json"
    save_report(results, str(output))
    monkeypatch.setattr(ragas, "RagasEvaluator", Judge)
    await run_ragas_evaluation(json.loads(output.read_text()))

    row = results["results"][0]
    assert len(row["retrieved_contexts"]) == 5
    assert len(row["contexts"]) == 3
    assert captured[0].response == row["answer"]
    assert captured[0].context == [c["content"] for c in row["contexts"]]
    assert len(prompts) == 1
    assert "\n\n---\n\n".join(captured[0].context) in prompts[0]
    assert "page3.md" not in prompts[0] and "page4.md" not in prompts[0]
    assert "REFERENCE_ONLY_SENTINEL" not in prompts[0]
    assert captured[0].ground_truth == "REFERENCE_ONLY_SENTINEL"


@pytest.mark.asyncio
async def test_retrieval_only_does_not_generate_or_score_answers():
    from openviking.eval.ragas.rag_eval import RAGEvaluator, run_ragas_evaluation

    evaluator = RAGEvaluator([], [])
    evaluator._client = SimpleNamespace(search=lambda *args, **kwargs: {"resources": []})
    results = await evaluator.evaluate([{"question": "Synthetic question"}])
    assert results["mode"] == "retrieval-only"
    assert results["results"][0]["answer"] is None
    with pytest.raises(ValueError, match="require --mode end-to-end"):
        await run_ragas_evaluation(results)


@pytest.mark.asyncio
async def test_cli_validates_mode_and_saves_failed_rows(monkeypatch, tmp_path):
    from openviking.eval.ragas.rag_eval import RAGEvaluator, main_async

    with pytest.raises(ValueError, match="--ragas requires --mode end-to-end"):
        await main_async(SimpleNamespace(mode="retrieval-only", ragas=True))

    def unavailable_llm(self):
        raise FileNotFoundError("Synthetic missing model config")

    client = SimpleNamespace(search=lambda *args, **kwargs: {"resources": []})
    monkeypatch.setattr(RAGEvaluator, "_get_client", lambda self: client)
    monkeypatch.setattr(RAGQueryPipeline, "_get_llm", unavailable_llm)
    questions = tmp_path / "questions.jsonl"
    questions.write_text(json.dumps({"question": "Synthetic question"}))
    output = tmp_path / "report.json"
    with pytest.raises(ValueError, match="rows \\[1\\]"):
        await main_async(
            SimpleNamespace(
                mode="end-to-end",
                ragas=True,
                question_file=str(questions),
                docs_dir=[],
                code_dir=[],
                config="unused.conf",
                url="http://openviking.test",
                top_k=5,
                output=str(output),
            )
        )
    row = json.loads(output.read_text())["results"][0]
    assert row["answer"] is None and row["contexts"] == []
    assert [e["stage"] for e in row["errors"]] == ["empty_context", "generation"]


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [TimeoutError("Synthetic model timeout"), " "])
async def test_failed_answers_and_evidence_remain_visible(failure):
    from openviking.eval.ragas.rag_eval import RAGEvaluator, run_ragas_evaluation

    class Client:
        def search(self, *args, **kwargs):
            return {
                "resources": [
                    {"uri": "viking://resources/demo/missing.md", "abstract": "Not a fallback"},
                    {"uri": "viking://resources/demo/chart.png", "abstract": "VLM description"},
                    {"uri": "viking://resources/demo/empty.md"},
                    {"uri": "viking://resources/demo/valid.md"},
                ]
            }

        def read(self, uri):
            if uri.endswith("missing.md"):
                raise OSError("Synthetic read failure")
            assert not uri.endswith(".png")
            return "" if uri.endswith("empty.md") else "Synthetic body"

    class LLM:
        def get_completion(self, prompt):
            assert "Not a fallback" not in prompt and "VLM description" not in prompt
            if isinstance(failure, Exception):
                raise failure
            return failure

    evaluator = RAGEvaluator([], [])
    evaluator._client = Client()
    evaluator._pipeline._llm = LLM()
    results = await evaluator.evaluate([{"question": "Synthetic question"}], mode="end-to-end")
    row = results["results"][0]
    assert row["answer"] is None
    assert len(row["retrieved_uris"]) == 4
    assert [e["stage"] for e in row["errors"]] == [
        "read",
        "unsupported_media",
        "empty_context",
        "generation",
    ]
    with pytest.raises(ValueError, match="rows \\[1\\]"):
        await run_ragas_evaluation(results)


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

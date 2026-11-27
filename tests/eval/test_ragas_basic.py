# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import asyncio
import json
import threading

import httpx

from openviking.eval.ragas.pipeline import RAGQueryPipeline
from openviking.eval.ragas.rag_eval import load_questions
from openviking.eval.recorder import async_writer


async def test_async_record_writer_drains_records_before_stop_sentinel(tmp_path, monkeypatch):
    path = tmp_path / "records.jsonl"
    first_write = threading.Event()
    release_write = threading.Event()

    def gated_open(*args, **kwargs):
        first_write.set()
        release_write.wait(timeout=5)
        return open(*args, **kwargs)

    monkeypatch.setattr(async_writer, "open", gated_open, raising=False)
    writer = async_writer.AsyncRecordWriter(str(path), batch_size=1)
    records: list[dict] = [{"id": 1}, {"id": 2}, {"id": 3, "uri": "viking://resources/文档"}]
    try:
        writer.write_record(records[0])
        assert await asyncio.to_thread(first_write.wait, 5)
        for record in records[1:]:
            writer.write_record(record)
        stop = asyncio.create_task(asyncio.to_thread(writer.stop))
        try:
            assert await asyncio.to_thread(writer._stop_event.wait, 5)
        finally:
            release_write.set()
            await stop
    finally:
        release_write.set()
        writer.stop()

    assert [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] == records


def test_pipeline_query_consumes_http_result_and_generates_answer(tmp_path, monkeypatch):
    http_client = httpx.AsyncClient
    monkeypatch.setenv("OPENVIKING_CLI_CONFIG_FILE", str(tmp_path / "missing.conf"))

    def search(request):
        assert request.url.path == "/api/v1/search/search"
        assert json.loads(request.content)["query"] == "How does it work?"
        return httpx.Response(
            200,
            json={
                "status": "ok",
                "result": {
                    "memories": [
                        {"uri": "viking://user/memories/profile.md", "overview": "Profile overview"}
                    ],
                    "resources": [
                        {"uri": "viking://resources/guide.md", "abstract": "Guide abstract"},
                        {"uri": "viking://resources/empty.md"},
                    ],
                    "skills": [],
                },
            },
        )

    class LLM:
        def get_completion(self, prompt):
            assert "Profile overview\n\n---\n\nGuide abstract" in prompt
            assert "Question: How does it work?" in prompt
            return "Generated answer"

    monkeypatch.setattr(
        "openviking_sdk.client.httpx.AsyncClient",
        lambda **kwargs: http_client(**kwargs, transport=httpx.MockTransport(search)),
    )
    pipeline = RAGQueryPipeline(server_url="http://openviking.test")
    monkeypatch.setattr(pipeline, "_llm", LLM())
    try:
        result = pipeline.query("How does it work?")
    finally:
        pipeline.close()

    assert result == {
        "question": "How does it work?",
        "contexts": ["Profile overview", "Guide abstract"],
        "retrieved_uris": ["viking://user/memories/profile.md", "viking://resources/guide.md"],
        "answer": "Generated answer",
    }


def test_question_loader(tmp_path):
    path = tmp_path / "questions.jsonl"
    path.write_text(
        '{"question":"What is OpenViking?"}\n'
        '{"question":"How does memory work?","ground_truth":"Hierarchical"}\n'
        '\n{"invalid":"no question field"}\n{"question":\n',
        encoding="utf-8",
    )

    assert load_questions(str(path)) == [
        {"question": "What is OpenViking?"},
        {"question": "How does memory work?", "ground_truth": "Hierarchical"},
    ]

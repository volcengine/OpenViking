import json
from email.utils import formatdate

import httpx
import pytest

from benchmark.aml.rerank import (
    RerankError,
    RerankRetriesExhausted,
    VikingDBReranker,
    align_scores,
    retry_after_seconds,
)


def test_provider_relevance_order_is_mapped_back_to_input_documents():
    assert align_scores(
        {"result": {"data": [{"id": 1, "score": 0.9}, {"id": 0, "score": 0.2}]}}, 2
    ) == [0.2, 0.9]


@pytest.mark.parametrize(
    "items",
    [
        [{"id": 0, "score": 0.8}, {"id": 0, "score": 0.1}],
        [{"id": 0, "score": 0.8}, {"id": 2, "score": 0.1}],
        [{"id": 0, "score": 0.8}],
        [{"id": 0, "score": 0.8}, {"id": 1, "score": float("nan")}],
        [{"id": True, "score": 0.8}, {"id": 0, "score": 0.1}],
    ],
)
def test_invalid_provider_mapping_fails_instead_of_silently_reordering(items):
    with pytest.raises(RerankError):
        align_scores({"result": {"data": items}}, 2)


def make_ranker(tmp_path):
    config = tmp_path / "rerank.json"
    config.write_text(json.dumps({"ak": "test", "sk": "test"}))
    return VikingDBReranker(str(config))


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, None),
        ("", None),
        (" 120 ", 120),
        ("0", 0),
        ("-1", None),
        ("1.5", None),
        ("NaN", None),
        ("inf", None),
        ("9" * 400, None),
        ("not a date", None),
        ("Fri, 30 Oct 2026 10:02:00 GMT", 120),
        ("Fri, 30 Oct 2026 09:59:59 GMT", 0),
    ],
)
def test_retry_after_seconds_and_dates(value, expected, monkeypatch):
    monkeypatch.setattr("benchmark.aml.rerank.time.time", lambda: 1793354400)
    assert retry_after_seconds(value) == expected


@pytest.mark.parametrize("status_code", [429, 503])
@pytest.mark.parametrize("header_kind", ["seconds", "date", "invalid", "absent"])
async def test_retry_after_is_respected_without_changing_payload_or_retry_count(
    tmp_path, monkeypatch, status_code, header_kind
):
    ranker = make_ranker(tmp_path)
    clock = [1000.0]
    epoch = 1793354400
    attempts = []
    real_client = httpx.AsyncClient
    headers = {
        "seconds": {"Retry-After": "120"},
        "date": {"Retry-After": formatdate(epoch + 120, usegmt=True)},
        "invalid": {"Retry-After": "NaN"},
        "absent": {},
    }[header_kind]

    def handle(request):
        attempts.append((clock[0], json.loads(request.content)))
        if len(attempts) == 1:
            return httpx.Response(status_code, headers=headers)
        return httpx.Response(200, json={"result": {"data": [{"id": 0, "score": 0.7}]}})

    async def sleep(seconds):
        clock[0] += seconds

    monkeypatch.setattr(ranker, "_signed_headers", lambda payload: ({}, json.dumps(payload)))
    monkeypatch.setattr("benchmark.aml.rerank.time.monotonic", lambda: clock[0])
    monkeypatch.setattr("benchmark.aml.rerank.time.time", lambda: epoch + clock[0] - 1000)
    monkeypatch.setattr("benchmark.aml.rerank.asyncio.sleep", sleep)
    monkeypatch.setattr(
        "benchmark.aml.rerank.httpx.AsyncClient",
        lambda **kwargs: real_client(**kwargs, transport=httpx.MockTransport(handle)),
    )
    assert await ranker._batch("question", ["A"]) == [0.7]
    minimum = 120 if header_kind in {"seconds", "date"} else (65 if status_code == 429 else 1)
    assert attempts[1][0] - attempts[0][0] == minimum
    assert len(attempts) == 2
    assert attempts[0][1] == attempts[1][1]


async def test_all_batches_are_scored_before_global_selection(tmp_path, monkeypatch):
    ranker = make_ranker(tmp_path)
    batches = []

    async def batch(query, documents):
        assert query == "question"
        batches.append(list(documents))
        return [int(text) / 1000 for text in documents]

    monkeypatch.setattr(ranker, "_batch", batch)
    hits = [{"uri": str(i), "abstract": str(i)} for i in range(205)]
    ranked = await ranker.rank("question", hits)
    assert list(map(len, batches)) == [100, 100, 5]
    assert [h["uri"] for h in ranked[:20]] == [str(i) for i in range(204, 184, -1)]


async def test_throttle_retries_preserve_id_mapping_and_resign(tmp_path, monkeypatch):
    ranker = make_ranker(tmp_path)
    clock = [0.0]
    attempts = []
    signed = []
    real_client = httpx.AsyncClient

    def handle(request):
        attempts.append(json.loads(request.content))
        if len(attempts) == 1:
            return httpx.Response(429, headers={"Retry-After": "1"})
        return httpx.Response(
            200, json={"result": {"data": [{"id": 1, "score": 0.9}, {"id": 0, "score": 0.2}]}}
        )

    def sign(payload):
        signed.append(clock[0])
        return {}, json.dumps(payload)

    async def sleep(seconds):
        clock[0] += seconds

    monkeypatch.setattr(ranker, "_signed_headers", sign)
    monkeypatch.setattr("benchmark.aml.rerank.time.monotonic", lambda: clock[0])
    monkeypatch.setattr("benchmark.aml.rerank.asyncio.sleep", sleep)
    monkeypatch.setattr(
        "benchmark.aml.rerank.httpx.AsyncClient",
        lambda **kwargs: real_client(**kwargs, transport=httpx.MockTransport(handle)),
    )
    assert await ranker._batch("question", ["A", "B"]) == [0.2, 0.9]
    assert attempts[0] == attempts[1]
    assert len(signed) == 2 and signed[1] >= 65


async def test_503_cooldown_applies_to_next_request_after_retry_exhaustion(tmp_path, monkeypatch):
    ranker = make_ranker(tmp_path)
    ranker.attempts = 1
    clock = [0.0]
    sent_at = []
    real_client = httpx.AsyncClient

    def handle(request):
        sent_at.append(clock[0])
        if len(sent_at) == 1:
            return httpx.Response(503, headers={"Retry-After": "120"})
        return httpx.Response(200, json={"result": {"data": [{"id": 0, "score": 0.7}]}})

    async def sleep(seconds):
        clock[0] += seconds

    monkeypatch.setattr(ranker, "_signed_headers", lambda payload: ({}, json.dumps(payload)))
    monkeypatch.setattr("benchmark.aml.rerank.time.monotonic", lambda: clock[0])
    monkeypatch.setattr("benchmark.aml.rerank.asyncio.sleep", sleep)
    monkeypatch.setattr(
        "benchmark.aml.rerank.httpx.AsyncClient",
        lambda **kwargs: real_client(**kwargs, transport=httpx.MockTransport(handle)),
    )
    with pytest.raises(RerankError, match="retries exhausted"):
        await ranker._batch("question", ["A"])
    assert await ranker._batch("question", ["A"]) == [0.7]
    assert sent_at == [0, 120]


@pytest.mark.parametrize("status_code", [429, 500, 502, 503, 504, None])
async def test_retryable_failures_exhaust_the_budget_with_a_distinct_error(
    tmp_path, monkeypatch, status_code
):
    ranker = make_ranker(tmp_path)
    ranker.attempts = 3
    clock = [0.0]
    requests = []
    real_client = httpx.AsyncClient

    def handle(request):
        requests.append(json.loads(request.content))
        if status_code is None:
            raise httpx.ReadTimeout("provider timeout", request=request)
        return httpx.Response(status_code)

    async def sleep(seconds):
        clock[0] += seconds

    monkeypatch.setattr(ranker, "_signed_headers", lambda payload: ({}, json.dumps(payload)))
    monkeypatch.setattr("benchmark.aml.rerank.time.monotonic", lambda: clock[0])
    monkeypatch.setattr("benchmark.aml.rerank.asyncio.sleep", sleep)
    monkeypatch.setattr(
        "benchmark.aml.rerank.httpx.AsyncClient",
        lambda **kwargs: real_client(**kwargs, transport=httpx.MockTransport(handle)),
    )
    with pytest.raises(RerankRetriesExhausted) as failure:
        await ranker._batch("question", ["A"])
    assert failure.value.attempts == 3
    assert failure.value.http_status == status_code
    assert len(requests) == 3 and requests[0] == requests[1] == requests[2]


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(403),
        httpx.Response(200, text="not JSON"),
        httpx.Response(200, json={"result": {"data": []}}),
    ],
)
async def test_rejection_and_invalid_responses_are_not_retry_exhaustion(
    tmp_path, monkeypatch, response
):
    ranker = make_ranker(tmp_path)
    requests = []
    real_client = httpx.AsyncClient

    def handle(request):
        requests.append(request)
        return response

    monkeypatch.setattr(ranker, "_signed_headers", lambda payload: ({}, json.dumps(payload)))
    monkeypatch.setattr(
        "benchmark.aml.rerank.httpx.AsyncClient",
        lambda **kwargs: real_client(**kwargs, transport=httpx.MockTransport(handle)),
    )
    with pytest.raises(RerankError) as failure:
        await ranker._batch("question", ["A"])
    assert not isinstance(failure.value, RerankRetriesExhausted)
    assert len(requests) == 1

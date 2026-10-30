"""Adapter behavior beyond the upstream Add/Search contract; no provider calls."""

import json
from unittest.mock import AsyncMock

import httpx
import pytest

from benchmark.aml import audit, server
from benchmark.aml.rerank import RerankError, RerankRetriesExhausted, VikingDBReranker
from benchmark.aml.server import AMLAdapter, AMLAddRequest, AMLSearchRequest, AMLSettings


@pytest.mark.parametrize(
    ("error", "kind"),
    [
        ("RuntimeError: failure_kind=empty_response", "extraction_empty_response"),
        ("MemoryUpdateError: Conflicting add/update/delete results", "memory_operation_conflict"),
        ("MemoryUpdateError: Written 6, Edited 1, Errors 1", "memory_update_error"),
        ("failure_kind=budget_exhausted", "extraction_output_budget"),
        ("failure_kind=parse_error", "extraction_parse_error"),
        ("unknown task failure secret-message-content", "commit_failure_unknown"),
        (None, "commit_failure_unknown"),
    ],
)
async def test_failed_commit_audit_preserves_reason_and_ids_without_error_text(error, kind, caplog):
    client = AsyncMock()
    client.batch_add_messages.return_value = {"added": 1}
    client.commit_session.return_value = {"status": "accepted", "task_id": "task-a"}
    client.get_task.return_value = {"status": "failed", "error": error}
    backend = server.OpenVikingBackend(AMLSettings(), client_factory=lambda user: client)
    with audit.request_audit(secrets=(), trace_id="trace-a", request_id="request-a"):
        with pytest.raises(server.OpenVikingBackendError):
            await backend.add_and_commit(
                user_id="alice", session_id="session-a", messages=[{"content": "original"}]
            )
    records = [
        json.loads(row.message) for row in caplog.records if row.name == "benchmark.aml.audit"
    ]
    failure = next(row for row in records if row["event"] == "commit_failed")
    assert failure["failure_kind"] == kind
    assert failure["failure_stage"] == "commit_task"
    assert failure["native_task_id"] == "task-a"
    assert failure["trace_id"] == "trace-a"
    assert "error" not in failure
    assert "secret-message-content" not in json.dumps(failure)
    client.batch_add_messages.assert_awaited_once()
    client.commit_session.assert_awaited_once()


class Backend:
    def __init__(self, hits=()):
        self.hits = list(hits)
        self.find_calls = []
        self.read_calls = []
        self.ready_calls = 0

    async def find(self, **kwargs):
        self.find_calls.append(kwargs)
        return self.hits

    async def read_memories(self, **kwargs):
        self.read_calls.append(kwargs)
        return ["full body " + uri.rsplit("/", 1)[-1] for uri in kwargs["uris"]]

    async def ready(self):
        self.ready_calls += 1
        return True


class Ranker:
    def __init__(self):
        self.calls = []

    async def rank(self, query, candidates):
        self.calls.append((query, list(candidates)))
        return list(reversed(candidates))


def hit(name, user="alice"):
    return {"uri": f"viking://user/{user}/memories/{name}", "abstract": f"abstract {name}"}


async def test_rerank_deduplicates_and_reads_selected_full_bodies_in_rank_order():
    backend = Backend([hit("a"), hit("a"), hit("b"), hit("c")])
    ranker = Ranker()
    adapter = AMLAdapter(
        AMLSettings(search_candidate_limit=40, search_result_limit=2), backend, ranker
    )
    result = await adapter.search(AMLSearchRequest(user_id="alice", query="original", top_k=10))
    assert backend.find_calls == [{"user_id": "alice", "query": "original", "limit": 40}]
    assert ranker.calls == [("original", [hit("a"), hit("b"), hit("c")])]
    assert backend.read_calls == [{"user_id": "alice", "uris": [hit("c")["uri"], hit("b")["uri"]]}]
    assert result == {
        "data": [{"id": hit(n)["uri"], "content": f"full body {n}"} for n in ["c", "b"]]
    }


@pytest.mark.parametrize("status_code", [503, 504, None])
async def test_search_returns_native_order_when_a_later_rerank_batch_exhausts_retries(
    tmp_path, monkeypatch, caplog, status_code
):
    config = tmp_path / "rerank.json"
    config.write_text(
        json.dumps(
            {"ak": "test", "sk": "test", "retry_attempts": 2, "batch_size": 2, "threshold": 0.5}
        )
    )
    ranker = VikingDBReranker(str(config))
    backend = Backend([hit("a"), hit("a"), hit("b"), hit("c"), hit("d")])
    attempts = []
    real_client = httpx.AsyncClient

    def handle(request):
        attempts.append(json.loads(request.content))
        if len(attempts) == 1:
            return httpx.Response(
                200, json={"result": {"data": [{"id": 1, "score": 0.9}, {"id": 0, "score": 0.1}]}}
            )
        if status_code is None:
            raise httpx.ReadTimeout("provider timeout", request=request)
        return httpx.Response(status_code)

    async def sleep(seconds):
        pass

    monkeypatch.setattr(ranker, "_signed_headers", lambda payload: ({}, json.dumps(payload)))
    monkeypatch.setattr("benchmark.aml.rerank.asyncio.sleep", sleep)
    monkeypatch.setattr(
        "benchmark.aml.rerank.httpx.AsyncClient",
        lambda **kwargs: real_client(**kwargs, transport=httpx.MockTransport(handle)),
    )
    app = server.create_app(
        settings=AMLSettings(search_candidate_limit=40, search_result_limit=2),
        backend=backend,
        reranker=ranker,
    )
    async with real_client(
        transport=httpx.ASGITransport(app=app), base_url="http://adapter.test"
    ) as client:
        response = await client.post(
            "/search", json={"user_id": "alice", "query": "question", "top_k": 10}
        )
    assert response.status_code == 200
    assert response.json() == {
        "data": [{"id": hit(n)["uri"], "content": f"full body {n}"} for n in ["a", "b"]]
    }
    assert len(backend.find_calls) == len(backend.read_calls) == 1
    assert backend.read_calls[0]["uris"] == [hit("a")["uri"], hit("b")["uri"]]
    assert len(attempts) == 3 and attempts[1] == attempts[2]
    records = [
        json.loads(row.message) for row in caplog.records if row.name == "benchmark.aml.audit"
    ]
    fallback = next(row for row in records if row["event"] == "search_rerank_fallback")
    assert fallback["http_status"] == status_code
    assert fallback["attempts"] == 2
    assert fallback["candidate_count"] == 4 and fallback["selected_count"] == 2
    assert not any(row["event"] in {"search_reranked", "search_failed"} for row in records)


async def test_invalid_rerank_response_still_fails_search():
    backend = Backend([hit("a")])
    ranker = AsyncMock()
    ranker.rank.side_effect = RerankError("Invalid rerank response")
    adapter = AMLAdapter(AMLSettings(), backend, ranker)
    with pytest.raises(server.HTTPException) as failure:
        await adapter.search(AMLSearchRequest(user_id="alice", query="q", top_k=1))
    assert failure.value.status_code == 503
    assert backend.read_calls == []


async def test_native_read_failure_after_rerank_fallback_still_fails_search():
    backend = Backend([hit("a")])
    backend.read_memories = AsyncMock(side_effect=server.OpenVikingBackendError("Read failed"))
    ranker = AsyncMock()
    ranker.rank.side_effect = RerankRetriesExhausted(attempts=2, http_status=504)
    adapter = AMLAdapter(AMLSettings(), backend, ranker)
    with pytest.raises(server.HTTPException) as failure:
        await adapter.search(AMLSearchRequest(user_id="alice", query="q", top_k=1))
    assert failure.value.status_code == 503
    backend.read_memories.assert_awaited_once()


async def test_explicit_smaller_top_k_is_respected():
    adapter = AMLAdapter(
        AMLSettings(search_candidate_limit=40, search_result_limit=40),
        Backend([hit("a"), hit("b")]),
        Ranker(),
    )
    result = await adapter.search(AMLSearchRequest(user_id="alice", query="q", top_k=1))
    assert len(result["data"]) == 1


async def test_cross_user_candidate_is_rejected_before_rerank_or_read():
    backend = Backend([hit("private", user="bob")])
    ranker = Ranker()
    adapter = AMLAdapter(AMLSettings(), backend, ranker)
    with pytest.raises(server.HTTPException) as error:
        await adapter.search(AMLSearchRequest(user_id="alice", query="q", top_k=1))
    assert error.value.status_code == 503
    assert ranker.calls == backend.read_calls == []


async def test_image_only_query_skips_text_rerank_and_keeps_native_order():
    backend = Backend([hit("a"), hit("b")])
    ranker = Ranker()
    adapter = AMLAdapter(AMLSettings(), backend, ranker)
    result = await adapter.search(
        AMLSearchRequest(
            user_id="alice",
            query=[{"type": "image_url", "image_url": {"url": "data:image/png;base64,YQ=="}}],
            top_k=1,
        )
    )
    assert ranker.calls == []
    assert backend.find_calls[0]["image"] == "data:image/png;base64,YQ=="
    assert result["data"][0]["id"] == hit("a")["uri"]


async def test_search_preserves_body_without_attribution_prefix():
    body = "# Alice\nOriginal preference"
    backend = Backend([{**hit("entities/person/alice.md"), "content": body}])
    request = AMLSearchRequest(user_id="alice", query="q", top_k=1)
    plain = await AMLAdapter(AMLSettings(), backend).search(request)
    assert plain["data"][0]["content"] == body


async def test_long_add_preserves_all_messages_and_commits_once_after_all_batches():
    calls = []

    class Client:
        async def initialize(self):
            pass

        async def close(self):
            pass

        async def batch_add_messages(self, session, messages):
            calls.append(("batch", messages))
            return {"added": len(messages)}

        async def commit_session(self, session, **kwargs):
            calls.append(("commit", kwargs))
            return {"status": "accepted", "task_id": "task"}

        async def get_task(self, task):
            return {"status": "completed"}

    messages = [{"role": "user", "content": f"original {i}"} for i in range(250)]
    request = AMLAddRequest(request_id="r", user_id="alice", session_id="s", messages=messages)
    backend = server.OpenVikingBackend(AMLSettings(), client_factory=lambda user: Client())
    await backend.add_and_commit(user_id="alice", session_id="s", messages=messages)
    assert len(request.messages) == 250
    assert [len(batch) for kind, batch in calls if kind == "batch"] == [100, 100, 50]
    assert [m for kind, batch in calls if kind == "batch" for m in batch] == messages
    assert calls[-1] == ("commit", {"keep_recent_count": 0})
    assert sum(kind == "commit" for kind, _ in calls) == 1


async def test_ambiguous_second_batch_stops_without_replay_or_commit():
    calls = []

    class Client:
        async def initialize(self):
            pass

        async def close(self):
            pass

        async def batch_add_messages(self, session, messages):
            calls.append(messages)
            if len(calls) == 2:
                raise httpx.ReadTimeout("unknown write result")
            return {"added": len(messages)}

        async def commit_session(self, *args, **kwargs):
            pytest.fail("Must not commit after an ambiguous Add")

    backend = server.OpenVikingBackend(
        AMLSettings(retry_attempts=7), client_factory=lambda user: Client()
    )
    with pytest.raises(server.OpenVikingBackendError):
        await backend.add_and_commit(
            user_id="alice", session_id="s", messages=[{"content": str(i)} for i in range(250)]
        )
    assert len(calls) == 2


@pytest.mark.parametrize("path", ["/health", "/docs", "/openapi.json", "/unmatched", "/search"])
async def test_authentication_precedes_every_route(path):
    backend = Backend()
    app = server.create_app(settings=AMLSettings(aml_api_key="synthetic-key"), backend=backend)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://adapter.test"
    ) as client:
        missing = await client.get(path)
        wrong = await client.get(path, headers={"X-API-Key": "wrong"})
        assert missing.status_code == wrong.status_code == 401
        assert missing.headers["X-AML-Trace-Id"] != wrong.headers["X-AML-Trace-Id"]
        assert backend.ready_calls == 0
        health = await client.get("/health", headers={"X-API-Key": "synthetic-key"})
    assert health.status_code == 200
    assert backend.ready_calls == 1


def test_audit_redacts_keys_and_bounds_untrusted_metadata(monkeypatch):
    lines = []
    monkeypatch.setattr(audit.logger, "info", lines.append)
    with audit.request_audit(
        secrets=("synthetic-secret",), user_id="synthetic-secret\n" + "x" * 1000
    ):
        audit.audit_event("request_received", query_chars=123)
    assert len(lines) == 1 and "synthetic-secret" not in lines[0]
    row = json.loads(lines[0])
    assert row["query_chars"] == 123
    assert len(row["user_id"]) == 512 and "\n" not in row["user_id"]

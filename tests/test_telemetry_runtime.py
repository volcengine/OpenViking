# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from openviking.models.embedder.base import EmbedResult
from openviking.server.identity import RequestContext, Role
from openviking.service.resource_service import ResourceService
from openviking.storage.collection_schemas import TextEmbeddingHandler
from openviking.storage.queuefs.semantic_dag import DagStats
from openviking.storage.queuefs.semantic_msg import SemanticMsg
from openviking.storage.queuefs.semantic_processor import SemanticProcessor
from openviking.telemetry import (
    get_current_telemetry,
    get_telemetry_runtime,
    register_telemetry,
    unregister_telemetry,
)
from openviking.telemetry.backends.memory import MemoryOperationTelemetry
from openviking.telemetry.context import bind_telemetry
from openviking.telemetry.snapshot import TelemetrySnapshot
from openviking_cli.session.user_id import UserIdentifier


def test_telemetry_module_exports_snapshot_and_runtime():
    snapshot = TelemetrySnapshot(
        telemetry_id="tm_demo",
        summary={"duration_ms": 1.2},
    )
    usage = snapshot.to_usage_dict()

    assert usage == {"duration_ms": 1.2, "token_total": 0}
    assert get_telemetry_runtime().meter() is not None


def test_telemetry_snapshot_to_dict_supports_summary_only():
    snapshot = TelemetrySnapshot(
        telemetry_id="tm_demo",
        summary={"duration_ms": 1.2, "tokens": {"total": 3}},
    )

    payload = snapshot.to_dict(include_summary=True)

    assert payload == {
        "id": "tm_demo",
        "summary": {"duration_ms": 1.2, "tokens": {"total": 3}},
    }


def test_telemetry_summary_breaks_down_llm_and_embedding_token_usage():
    telemetry = MemoryOperationTelemetry(operation="resources.add_resource", enabled=True)
    telemetry.record_token_usage("llm", 11, 7)
    telemetry.record_token_usage("embedding", 13, 0)

    summary = telemetry.finish().summary
    assert telemetry.telemetry_id
    assert telemetry.telemetry_id.startswith("tm_")
    assert summary["tokens"]["total"] == 31
    assert summary["duration_ms"] >= 0
    assert summary["tokens"]["llm"] == {
        "input": 11,
        "output": 7,
        "total": 18,
    }
    assert summary["tokens"]["embedding"] == {"total": 13}
    assert "queue" not in summary
    assert "vector" not in summary
    assert "semantic_nodes" not in summary
    assert "memory" not in summary
    assert "errors" not in summary


def test_disabled_telemetry_still_has_request_id():
    telemetry = MemoryOperationTelemetry(operation="resources.add_resource", enabled=False)

    assert telemetry.telemetry_id
    assert telemetry.telemetry_id.startswith("tm_")


def test_telemetry_summary_uses_simplified_internal_metric_keys():
    summary = MemoryOperationTelemetry(
        operation="search.find",
        enabled=True,
    )
    summary.count("vector.searches", 2)
    summary.count("vector.scored", 5)
    summary.count("vector.passed", 3)
    summary.set("vector.returned", 2)
    summary.count("vector.scanned", 5)
    summary.set("vector.scan_reason", "")
    summary.set("semantic_nodes.total", 4)
    summary.set("semantic_nodes.done", 3)
    summary.set("semantic_nodes.pending", 1)
    summary.set("semantic_nodes.running", 0)
    summary.set("memory.extracted", 6)

    result = summary.finish().summary

    assert result["vector"] == {
        "searches": 2,
        "scored": 5,
        "passed": 3,
        "returned": 2,
        "scanned": 5,
        "scan_reason": "",
    }
    assert result["semantic_nodes"] == {
        "total": 4,
        "done": 3,
        "pending": 1,
    }
    assert result["memory"] == {"extracted": 6}


def test_telemetry_summary_detects_groups_by_prefix_without_static_key_lists():
    telemetry = MemoryOperationTelemetry(operation="search.find", enabled=True)
    telemetry.set("vector.debug_probe", 1)
    telemetry.set("queue.semantic.processed", 2)
    telemetry.set("memory.extracted", 1)

    result = telemetry.finish().summary

    assert "vector" in result
    assert "queue" in result
    assert "memory" in result


@pytest.mark.asyncio
async def test_semantic_processor_binds_registered_operation_telemetry(monkeypatch):
    telemetry = MemoryOperationTelemetry(operation="resources.add_resource", enabled=True)
    register_telemetry(telemetry)

    processor = SemanticProcessor()

    class FakeVikingFS:
        async def ls(self, uri, ctx=None):
            return []

    class _FakeDagExecutor:
        def __init__(self, **kwargs):
            pass

        async def run(self, root_uri):
            assert get_current_telemetry() is telemetry
            get_current_telemetry().record_token_usage("llm", 11, 7)

        def get_stats(self):
            return DagStats()

    monkeypatch.setattr(
        "openviking.storage.queuefs.semantic_processor.get_viking_fs",
        lambda: FakeVikingFS(),
    )
    monkeypatch.setattr(
        "openviking.storage.queuefs.semantic_processor.SemanticDagExecutor",
        lambda **kwargs: _FakeDagExecutor(**kwargs),
    )

    try:
        await processor.on_dequeue(
            SemanticMsg(
                uri="viking://resources/demo",
                context_type="resource",
                recursive=False,
                telemetry_id=telemetry.telemetry_id,
            ).to_dict()
        )
    finally:
        unregister_telemetry(telemetry.telemetry_id)

    result = telemetry.finish()
    summary = result.summary
    assert summary["tokens"]["total"] == 18
    assert summary["tokens"]["llm"]["total"] == 18
    assert "embedding" not in summary["tokens"]


@pytest.mark.asyncio
async def test_embedding_handler_binds_registered_operation_telemetry(monkeypatch):
    telemetry = MemoryOperationTelemetry(operation="resources.add_resource", enabled=True)
    register_telemetry(telemetry)

    class _TelemetryAwareEmbedder:
        def embed(self, text: str) -> EmbedResult:
            assert text == "hello"
            get_current_telemetry().record_token_usage("embedding", 9, 0)
            return EmbedResult(dense_vector=[0.1, 0.2])

    class _DummyConfig:
        def __init__(self):
            self.storage = SimpleNamespace(vectordb=SimpleNamespace(name="context"))
            self.embedding = SimpleNamespace(
                dimension=2,
                get_embedder=lambda: _TelemetryAwareEmbedder(),
            )

    class _DummyVikingDB:
        is_closing = False

        async def upsert(self, _data, *, ctx=None):
            return "rec-1"

    monkeypatch.setattr(
        "openviking_cli.utils.config.get_openviking_config",
        lambda: _DummyConfig(),
    )

    handler = TextEmbeddingHandler(_DummyVikingDB())
    payload = {
        "data": json.dumps(
            {
                "id": "msg-1",
                "message": "hello",
                "telemetry_id": telemetry.telemetry_id,
                "context_data": {
                    "id": "id-1",
                    "uri": "viking://resources/sample",
                    "account_id": "default",
                    "abstract": "sample",
                },
            }
        )
    }

    try:
        await handler.on_dequeue(payload)
    finally:
        unregister_telemetry(telemetry.telemetry_id)

    result = telemetry.finish()
    summary = result.summary
    assert summary["tokens"]["embedding"] == {"total": 9}


@pytest.mark.asyncio
async def test_resource_service_add_resource_reports_queue_summary(monkeypatch):
    telemetry = MemoryOperationTelemetry(operation="resources.add_resource", enabled=True)

    class _DummyProcessor:
        async def process_resource(self, **kwargs):
            return {
                "status": "success",
                "root_uri": "viking://resources/demo",
            }

    class _DummyQueueManager:
        async def wait_complete(self, timeout=None):
            return {
                "Semantic": SimpleNamespace(processed=2, error_count=1, errors=[]),
                "Embedding": SimpleNamespace(processed=5, error_count=0, errors=[]),
            }

    monkeypatch.setattr(
        "openviking.service.resource_service.get_queue_manager",
        lambda: _DummyQueueManager(),
    )

    class _DagStats:
        total_nodes = 3
        done_nodes = 2
        pending_nodes = 1
        in_progress_nodes = 0

    monkeypatch.setattr(
        "openviking.storage.queuefs.semantic_processor.SemanticProcessor.consume_dag_stats",
        classmethod(lambda cls, telemetry_id="", uri=None: _DagStats()),
    )

    service = ResourceService(
        vikingdb=object(),
        viking_fs=object(),
        resource_processor=_DummyProcessor(),
        skill_processor=object(),
    )
    ctx = RequestContext(user=UserIdentifier.the_default_user(), role=Role.ROOT)

    with bind_telemetry(telemetry):
        result = await service.add_resource(path="/tmp/demo.md", ctx=ctx, wait=True)

    assert result["root_uri"] == "viking://resources/demo"
    telemetry_result = telemetry.finish()
    summary = telemetry_result.summary
    assert summary["queue"] == {
        "semantic": {"processed": 2, "error_count": 1},
        "embedding": {"processed": 5},
    }
    assert summary["semantic_nodes"] == {
        "total": 3,
        "done": 2,
        "pending": 1,
    }
    assert "memory" not in summary
    assert "errors" not in summary


def test_telemetry_summary_includes_only_memory_group_when_memory_metrics_exist():
    telemetry = MemoryOperationTelemetry(operation="session.commit", enabled=True)
    telemetry.record_token_usage("llm", 5, 3)
    telemetry.set("memory.extracted", 4)

    summary = telemetry.finish().summary

    assert summary["memory"] == {"extracted": 4}
    assert "queue" not in summary
    assert "vector" not in summary
    assert "semantic_nodes" not in summary
    assert "errors" not in summary

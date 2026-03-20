from types import SimpleNamespace

import pytest

from openviking_cli.exceptions import InvalidArgumentError
from openviking_cli.retrieve.types import FindResult


def test_operation_telemetry_summary_includes_memory_extract_breakdown():
    from openviking.telemetry.operation import OperationTelemetry

    telemetry = OperationTelemetry(operation="session.commit", enabled=True)
    telemetry.set("memory.extracted", 5)
    telemetry.set("memory.extract.total.duration_ms", 842.3)
    telemetry.set("memory.extract.candidates.total", 7)
    telemetry.set("memory.extract.candidates.standard", 5)
    telemetry.set("memory.extract.candidates.tool_skill", 2)
    telemetry.set("memory.extract.created", 3)
    telemetry.set("memory.extract.merged", 1)
    telemetry.set("memory.extract.deleted", 0)
    telemetry.set("memory.extract.skipped", 3)
    telemetry.set("memory.extract.stage.prepare_inputs.duration_ms", 8.4)
    telemetry.set("memory.extract.stage.llm_extract.duration_ms", 410.2)
    telemetry.set("memory.extract.stage.normalize_candidates.duration_ms", 6.7)
    telemetry.set("memory.extract.stage.tool_skill_stats.duration_ms", 1.9)
    telemetry.set("memory.extract.stage.profile_create.duration_ms", 12.5)
    telemetry.set("memory.extract.stage.tool_skill_merge.duration_ms", 43.0)
    telemetry.set("memory.extract.stage.dedup.duration_ms", 215.6)
    telemetry.set("memory.extract.stage.create_memory.duration_ms", 56.1)
    telemetry.set("memory.extract.stage.merge_existing.duration_ms", 22.7)
    telemetry.set("memory.extract.stage.delete_existing.duration_ms", 0.0)
    telemetry.set("memory.extract.stage.create_relations.duration_ms", 18.2)
    telemetry.set("memory.extract.stage.flush_semantic.duration_ms", 9.0)

    summary = telemetry.finish().summary

    assert summary["memory"]["extracted"] == 5
    assert summary["memory"]["extract"] == {
        "duration_ms": 842.3,
        "candidates": {
            "total": 7,
            "standard": 5,
            "tool_skill": 2,
        },
        "actions": {
            "created": 3,
            "merged": 1,
            "skipped": 3,
        },
        "stages": {
            "prepare_inputs_ms": 8.4,
            "llm_extract_ms": 410.2,
            "normalize_candidates_ms": 6.7,
            "tool_skill_stats_ms": 1.9,
            "profile_create_ms": 12.5,
            "tool_skill_merge_ms": 43.0,
            "dedup_ms": 215.6,
            "create_memory_ms": 56.1,
            "merge_existing_ms": 22.7,
            "create_relations_ms": 18.2,
            "flush_semantic_ms": 9.0,
        },
    }


def test_operation_telemetry_measure_accumulates_duration(monkeypatch):
    from openviking.telemetry.operation import OperationTelemetry

    perf_values = iter([10.0, 10.1, 10.3, 10.5, 10.8, 11.0])
    monkeypatch.setattr(
        "openviking.telemetry.operation.time.perf_counter", lambda: next(perf_values)
    )

    telemetry = OperationTelemetry(operation="session.commit", enabled=True)
    with telemetry.measure("memory.extract.stage.dedup"):
        pass
    with telemetry.measure("memory.extract.stage.dedup"):
        pass

    summary = telemetry.finish().summary
    assert summary["duration_ms"] == 1000.0
    assert summary["memory"]["extract"]["stages"]["dedup_ms"] == 500.0


def test_operation_telemetry_summary_includes_resource_breakdown():
    from openviking.telemetry.operation import OperationTelemetry

    telemetry = OperationTelemetry(operation="resources.add_resource", enabled=True)
    telemetry.set("resource.request.duration_ms", 152.3)
    telemetry.set("resource.process.duration_ms", 101.7)
    telemetry.set("resource.parse.duration_ms", 38.1)
    telemetry.set("resource.parse.warnings_count", 1)
    telemetry.set("resource.finalize.duration_ms", 22.4)
    telemetry.set("resource.summarize.duration_ms", 31.8)
    telemetry.set("resource.wait.duration_ms", 46.9)
    telemetry.set("resource.watch.duration_ms", 0.8)
    telemetry.set("resource.flags.wait", True)
    telemetry.set("resource.flags.build_index", True)
    telemetry.set("resource.flags.summarize", False)
    telemetry.set("resource.flags.watch_enabled", False)

    summary = telemetry.finish().summary

    assert summary["resource"] == {
        "request": {"duration_ms": 152.3},
        "process": {
            "duration_ms": 101.7,
            "parse": {"duration_ms": 38.1, "warnings_count": 1},
            "finalize": {"duration_ms": 22.4},
            "summarize": {"duration_ms": 31.8},
        },
        "wait": {"duration_ms": 46.9},
        "watch": {"duration_ms": 0.8},
        "flags": {
            "wait": True,
            "build_index": True,
            "summarize": False,
            "watch_enabled": False,
        },
    }


def test_operation_telemetry_summary_omits_zero_valued_fields():
    from openviking.telemetry.operation import OperationTelemetry

    telemetry = OperationTelemetry(operation="resources.add_resource", enabled=True)
    telemetry.set("queue.semantic.processed", 0)
    telemetry.set("queue.semantic.error_count", 0)
    telemetry.set("queue.embedding.processed", 4)
    telemetry.set("queue.embedding.error_count", 0)
    telemetry.set("semantic_nodes.total", 9)
    telemetry.set("semantic_nodes.done", 8)
    telemetry.set("semantic_nodes.pending", 1)
    telemetry.set("semantic_nodes.running", 0)
    telemetry.set("resource.process.duration_ms", 12.3)
    telemetry.set("resource.parse.duration_ms", 0.0)
    telemetry.set("resource.parse.warnings_count", 0)
    telemetry.set("resource.flags.wait", False)
    telemetry.set("resource.flags.build_index", True)

    summary = telemetry.finish().summary

    assert "tokens" not in summary
    assert "semantic" not in summary["queue"]
    assert summary["queue"]["embedding"] == {"processed": 4}
    assert "running" not in summary["semantic_nodes"]
    assert summary["resource"] == {
        "process": {"duration_ms": 12.3},
        "flags": {"wait": False, "build_index": True, "summarize": False, "watch_enabled": False},
    }


@pytest.mark.asyncio
async def test_run_with_telemetry_returns_usage_and_payload():
    from openviking.telemetry.execution import run_with_telemetry

    async def _run():
        return {"status": "ok"}

    execution = await run_with_telemetry(
        operation="search.find",
        telemetry=True,
        fn=_run,
    )

    assert execution.result == {"status": "ok"}
    assert execution.telemetry is not None
    assert execution.telemetry["summary"]["operation"] == "search.find"


@pytest.mark.asyncio
async def test_run_with_telemetry_raises_invalid_argument_for_bad_request():
    from openviking.telemetry.execution import run_with_telemetry

    async def _run():
        return {"status": "ok"}

    with pytest.raises(InvalidArgumentError, match="Unsupported telemetry options: invalid"):
        await run_with_telemetry(
            operation="search.find",
            telemetry={"invalid": True},
            fn=_run,
        )


@pytest.mark.asyncio
async def test_run_with_telemetry_rejects_events_selection():
    from openviking.telemetry.execution import run_with_telemetry

    async def _run():
        return {"status": "ok"}

    with pytest.raises(InvalidArgumentError, match="Unsupported telemetry options: events"):
        await run_with_telemetry(
            operation="search.find",
            telemetry={"summary": True, "events": False},
            fn=_run,
        )


def test_attach_telemetry_payload_adds_telemetry_to_dict_result():
    from openviking.telemetry.execution import attach_telemetry_payload

    result = attach_telemetry_payload(
        {"root_uri": "viking://resources/demo"},
        {"id": "tm_123", "summary": {"operation": "resources.add_resource"}},
    )

    assert result["telemetry"]["summary"]["operation"] == "resources.add_resource"


def test_attach_telemetry_payload_does_not_mutate_object_result():
    from openviking.telemetry.execution import attach_telemetry_payload

    result = SimpleNamespace(total=1)

    attached = attach_telemetry_payload(
        result,
        {"id": "tm_123", "summary": {"operation": "search.find"}},
    )

    assert attached is result
    assert not hasattr(result, "telemetry")


def test_find_result_ignores_usage_and_telemetry_payload_fields():
    result = FindResult.from_dict(
        {
            "memories": [],
            "resources": [],
            "skills": [],
            "telemetry": {"id": "tm_123", "summary": {"operation": "search.find"}},
        }
    )

    assert not hasattr(result, "telemetry")
    assert result.to_dict() == {
        "memories": [],
        "resources": [],
        "skills": [],
        "total": 0,
    }

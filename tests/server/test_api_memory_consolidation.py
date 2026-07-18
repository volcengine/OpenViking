import asyncio

from openviking.server.routers import memories as memories_router


def test_exact_duplicate_plan_endpoint_forwards_read_only_scope(monkeypatch):
    calls = []

    class _Service:
        async def plan_exact_memory_duplicates(self, **kwargs):
            calls.append(kwargs)
            return {
                "schema_version": "memory_consolidation_dry_run_plan_v1",
                "groups": [],
            }

    monkeypatch.setattr(memories_router, "get_service", lambda: _Service())
    request = memories_router.ExactDuplicatePlanRequest(
        scope_uri="viking://user/alice/memories/events",
        memory_type="events",
        node_limit=25,
    )

    request_context = object()
    response = asyncio.run(
        memories_router.plan_exact_duplicate_memories(request, _ctx=request_context)
    )

    assert response.status == "ok"
    assert response.result["schema_version"] == "memory_consolidation_dry_run_plan_v1"
    assert len(calls) == 1
    assert calls[0]["scope_uri"] == request.scope_uri
    assert calls[0]["memory_type"] == "events"
    assert calls[0]["ctx"] is request_context
    assert calls[0]["node_limit"] == 25

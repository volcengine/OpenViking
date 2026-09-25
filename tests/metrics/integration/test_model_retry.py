# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
import pytest

from openviking.metrics.datasources.model_retry import ModelRetryEventDataSource
from openviking.metrics.global_api import _build_event_router
from openviking.utils.model_call import model_workload, run_model_async


@pytest.mark.asyncio
async def test_owner_events_are_exported_once_with_bounded_labels(
    registry, render_prometheus, monkeypatch
):
    router = _build_event_router(registry)
    monkeypatch.setattr(ModelRetryEventDataSource, "_emit", router.dispatch)
    monkeypatch.setattr("openviking.utils.model_call.random.uniform", lambda *_: 0)

    async def request():
        raise TimeoutError("private-task-identifier")

    with model_workload("session_commit", stage="archive_summary"):
        with pytest.raises(TimeoutError):
            await run_model_async(request, model_type="vlm")

    text = render_prometheus(registry)
    assert (
        'openviking_model_attempts_total{error_class="transient",model_type="vlm",operation="session_commit",result="error",stage="archive_summary"} 4'
        in text
    )
    assert (
        'openviking_model_logical_calls_total{model_type="vlm",operation="session_commit",result="error",stage="archive_summary"} 1'
        in text
    )
    assert (
        'openviking_model_retry_exhausted_total{model_type="vlm",operation="session_commit",reason="max_attempts",stage="archive_summary"} 1'
        in text
    )
    assert 'decision="retry"' in text
    assert 'decision="stop"' in text
    assert "private-task-identifier" not in text

    with model_workload("user-supplied-operation-123", stage="private-task-identifier"):
        assert await run_model_async(_ok, model_type="vlm") == "ok"
    text = render_prometheus(registry)
    assert 'operation="other"' in text
    assert "user-supplied-operation-123" not in text
    assert "private-task-identifier" not in text


async def _ok():
    return "ok"

"""Provider diagnostics retain typed facts without response bodies in Task APIs."""

import json

import httpx
import openai
import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse

from openviking.models.model_error import ModelError, ModelErrorKind
from openviking.models.vlm.backends.openai_vlm import OpenAIVLM
from openviking.server.auth import get_request_context
from openviking.server.identity import RequestContext, Role
from openviking.server.models import ERROR_CODE_TO_HTTP_STATUS
from openviking.server.routers.tasks import router
from openviking.service.task_domain import TaskRecord, TaskStatus
from openviking.service.task_store import PersistentTaskStore
from openviking.service.task_tracker import TaskTracker, run_tracked_operation, set_task_tracker
from openviking.utils.agfs_utils import RagfsBindingConfig, create_agfs_client
from openviking_cli.exceptions import OpenVikingError
from openviking_cli.session.user_id import UserIdentifier
from openviking_cli.utils.config.agfs_config import AGFSConfig

MARKER = "SYNTHETIC_ONLY_PROVIDER_REJECTION_FRAGMENT"
CTX = RequestContext(user=UserIdentifier("synthetic-tenant", "alice"), role=Role.USER)
OWNER = {"account_id": CTX.account_id, "user_id": CTX.user.user_id}


@pytest.mark.asyncio
@pytest.mark.parametrize("structured", [False, True])
async def test_provider_failure_through_durable_task_and_http_projection(
    tmp_path, monkeypatch, structured
):
    requests = []

    def response(request):
        requests.append(request)
        if structured:
            return httpx.Response(
                400,
                json={
                    "error": {
                        "code": "content_filter",
                        "message": "synthetic rejection",
                        "details": MARKER,
                    }
                },
                headers={"x-request-id": "synthetic-provider-request"},
            )
        return httpx.Response(400, text="synthetic rejection: " + MARKER)

    http = httpx.AsyncClient(transport=httpx.MockTransport(response))
    sdk = openai.AsyncOpenAI(
        base_url="https://example.invalid/v1", api_key="synthetic", max_retries=0, http_client=http
    )
    provider = OpenAIVLM({"provider": "openai", "model": "synthetic-model", "temperature": 0})
    monkeypatch.setattr(provider, "get_async_client", lambda: sdk)
    native = create_agfs_client(
        RagfsBindingConfig(agfs=AGFSConfig(path=str(tmp_path), backend="local"))
    )
    tracker = TaskTracker(PersistentTaskStore(native))
    set_task_tracker(tracker)
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_request_context] = lambda: CTX

    @app.exception_handler(OpenVikingError)
    async def errors(_request, error):
        return JSONResponse(
            status_code=ERROR_CODE_TO_HTTP_STATUS[error.code], content={"error": error.code}
        )

    try:
        task = await tracker.create("compile", task_id="synthetic-provider-task", **OWNER)
        with pytest.raises(ModelError) as raised:
            await run_tracked_operation(
                "compile",
                **OWNER,
                operation=lambda: provider.get_completion_async("synthetic input"),
                wait=True,
                existing_task=task,
            )
        assert raised.value.status_code == 400
        assert raised.value.kind is (
            ModelErrorKind.CONTENT_SAFETY if structured else ModelErrorKind.INVALID_REQUEST
        )
        assert MARKER in str(raised.value.__cause__)
        assert len(requests) == 1  # A refusal must not cause a new model attempt.
        # Reconstruct the durable owner, rather than reading the old in-memory Task.
        await tracker.close()
        tracker = TaskTracker(PersistentTaskStore(native))
        set_task_tracker(tracker)
        persisted = await tracker.get_with_events(task.task_id, **OWNER)
        assert persisted.status is TaskStatus.FAILED
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://synthetic"
        ) as client:
            detail = await client.get(
                f"/api/v1/tasks/{task.task_id}", params={"include_events": True}
            )
            listing = await client.get("/api/v1/tasks")
            page = await client.get("/api/v1/tasks", params={"pagination": "cursor"})
            for result in [detail, listing, page]:
                assert result.status_code == 200, result.text
                assert MARKER not in result.text
            assert "400" in detail.json()["result"]["error"]

    finally:
        set_task_tracker(None)
        await tracker.close()
        await sdk.close()
        native.close()


@pytest.mark.parametrize(
    "value",
    [
        'Error code: 400 - {"error": {"details": "' + MARKER + '"}}',
        "Error code: 400 - " + MARKER,
        'Worker failed: Error code: 400 - {\n "response": "' + MARKER + '"\n}',
    ],
)
def test_legacy_task_diagnostics_are_projected_without_mutating_business_content(value):
    ordinary = 'Error code: 400 - {"code": "a synthetic documentation example"}'
    task = TaskRecord(
        task_id="legacy",
        task_type="compile",
        status=TaskStatus.FAILED,
        error=value,
        result={"message": value, "errors": [{"message": value}], "content": ordinary},
        execution_events={"events": [{"error": value, "kind": "error_recorded"}]},
    )
    before = json.dumps(task.result)
    view = task.to_dict(include_events=True)
    assert MARKER not in json.dumps(view)
    assert view["result"]["content"] == ordinary
    assert json.dumps(task.result) == before
    assert "400" in view["error"]


def test_successful_business_message_and_non_provider_errors_keep_their_meaning():
    value = 'Error code: 400 - {"example": "ordinary documentation"}'
    task = TaskRecord(
        task_id="success",
        task_type="compile",
        status=TaskStatus.COMPLETED,
        result={"message": value, "content": value},
        meta={"request": {"instruction": value}},
    )
    view = task.to_dict()
    assert view["result"] == {"message": value, "content": value}
    assert view["meta"]["request"]["instruction"] == value
    task.error = "File not found: synthetic.md"
    assert task.to_dict()["error"] == task.error


def test_model_public_message_keeps_classification_and_status_not_response_text():
    from openviking.models.model_error import model_error_from_status

    failure = model_error_from_status(
        400, MARKER, provider="synthetic-provider", code="content_filter"
    )
    assert failure.kind is ModelErrorKind.CONTENT_SAFETY
    assert failure.status_code == 400 and failure.code == "content_filter"
    assert MARKER not in str(failure)
    assert "content_safety" in str(failure) and "400" in str(failure)

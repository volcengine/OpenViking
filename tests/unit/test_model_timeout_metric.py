"""Exercise the existing failure metric through Ark's actual HTTPX transport."""

import httpx
import pytest
from volcenginesdkarkruntime import AsyncArk

from openviking.models.model_error import ModelError, ModelErrorKind
from openviking.models.vlm.backends.volcengine_vlm import VolcEngineVLM
from openviking.observability.events import register_event_subscriber, unregister_event_subscriber


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "timeout_type", [httpx.ConnectTimeout, httpx.ReadTimeout, httpx.PoolTimeout]
)
async def test_sdk_transport_timeout_uses_existing_metric_bucket(timeout_type, monkeypatch):
    requests = []
    observations = []

    def fail(request):
        requests.append(request.url.path)
        raise timeout_type("synthetic transport timeout", request=request)

    def record(event):
        if event.event_name == "vlm.call":
            observations.append(event.payload["error_code"])

    backend = VolcEngineVLM(
        {"provider": "volcengine", "model": "synthetic", "api_key": "synthetic"}
    )
    register_event_subscriber("test-timeout-metric", record)
    try:
        async with AsyncArk(
            api_key="synthetic",
            base_url="https://model.example.test/v1",
            max_retries=0,
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(fail)),
        ) as sdk:
            monkeypatch.setattr(backend, "get_async_client", lambda: sdk)
            with pytest.raises(ModelError) as error:
                await backend.get_completion_async("synthetic input")
        assert error.value.kind is ModelErrorKind.TRANSIENT_NETWORK_ERROR
    finally:
        unregister_event_subscriber("test-timeout-metric")
    assert requests == ["/v1/chat/completions"]
    assert observations == ["timeout"]

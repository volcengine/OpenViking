"""An interrupted consumer must settle the selected provider response before returning."""

import json
from types import SimpleNamespace
from unittest.mock import patch

import httpx
import openai
import pytest
from vikingbot.providers.vlm_adapter import VLMProviderAdapter

from openviking.models.vlm.backends.openai_vlm import OpenAIVLM
from openviking.models.vlm.base import FailoverVLM, MultiCredentialVLM


class TrackingStream(httpx.AsyncByteStream):
    def __init__(self):
        self.closes = 0

    async def __aiter__(self):
        event = {
            "id": "synthetic-chunk",
            "object": "chat.completion.chunk",
            "created": 1,
            "model": "synthetic",
            "choices": [{"index": 0, "delta": {"content": "first"}, "finish_reason": None}],
        }
        yield b"data: " + json.dumps(event).encode() + b"\n\n"
        yield b"data: [DONE]\n\n"

    async def aclose(self):
        self.closes += 1


@pytest.mark.parametrize("mode", ["single", "primary_backup", "credentials"])
async def test_closing_bot_stream_settles_started_provider(mode):
    response_body = TrackingStream()
    requests = []

    async def respond(request):
        model = json.loads(request.content)["model"]
        requests.append(model)
        if mode != "single" and model == "primary":
            return httpx.Response(503, json={"error": {"message": "synthetic unavailable"}})
        return httpx.Response(
            200, headers={"Content-Type": "text/event-stream"}, stream=response_body
        )

    http = httpx.AsyncClient(transport=httpx.MockTransport(respond))
    async with openai.AsyncOpenAI(
        api_key="synthetic", base_url="https://provider.invalid/v1", max_retries=0, http_client=http
    ) as client:
        providers = [
            OpenAIVLM(
                {"provider": "openai", "model": name, "api_key": "synthetic", "max_retries": 0}
            )
            for name in ("primary", "backup")
        ]
        primary, backup = providers
        if mode == "primary_backup":
            vlm = FailoverVLM(primary, backup)
        elif mode == "credentials":
            vlm = MultiCredentialVLM(providers, credential_ids=["primary", "backup"])
        else:
            vlm = primary
        adapter = VLMProviderAdapter(vlm, "primary", langfuse_client=SimpleNamespace(enabled=False))
        with (
            patch.object(primary, "get_async_client", return_value=client),
            patch.object(backup, "get_async_client", return_value=client),
        ):
            response = adapter.chat_stream(messages=[{"role": "user", "content": "Hi"}])
            event = await anext(response)
            assert event.type == "content_delta" and event.content == "first"
            assert response_body.closes == 0
            await response.aclose()
            assert response_body.closes == 1
    assert requests == (["primary"] if mode == "single" else ["primary", "backup"])

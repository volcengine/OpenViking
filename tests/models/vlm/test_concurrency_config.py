import asyncio
from types import SimpleNamespace

import pytest

from openviking.models.vlm.backends.openai_vlm import OpenAIVLM
from openviking_cli.utils.config.vlm_config import VLMConfig


class BlockingCompletions:
    def __init__(self, limit: int):
        self.limit = limit
        self.active = 0
        self.peak = 0
        self.limit_reached = asyncio.Event()
        self.release = asyncio.Event()

    async def create(self, **_kwargs):
        self.active += 1
        self.peak = max(self.peak, self.active)
        if self.active == self.limit:
            self.limit_reached.set()
        try:
            await self.release.wait()
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))],
                usage=None,
            )
        finally:
            self.active -= 1


@pytest.mark.asyncio
async def test_max_concurrent_limits_text_and_vision_calls_together(monkeypatch):
    completions = BlockingCompletions(limit=2)
    client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    vlm = OpenAIVLM(
        {
            "provider": "openai",
            "model": "test-model",
            "api_key": "test-key",
            "max_concurrent": 2,
            "max_retries": 0,
        }
    )
    monkeypatch.setattr(vlm, "get_async_client", lambda: client)

    tasks = [
        asyncio.create_task(vlm.get_completion_async(prompt="one")),
        asyncio.create_task(vlm.get_vision_completion_async(prompt="two")),
        asyncio.create_task(vlm.get_completion_async(prompt="three")),
        asyncio.create_task(vlm.get_vision_completion_async(prompt="four")),
    ]

    await asyncio.wait_for(completions.limit_reached.wait(), timeout=1)
    await asyncio.sleep(0)
    assert completions.peak == 2

    completions.release.set()
    assert await asyncio.gather(*tasks) == ["ok", "ok", "ok", "ok"]


def test_vlm_config_forwards_max_concurrent_to_provider_instances():
    config = VLMConfig(
        provider="openai",
        model="test-model",
        api_key="test-key",
        max_concurrent=3,
    )

    assert config.get_vlm_instance().max_concurrent == 3

    credential_config = VLMConfig(
        model="test-model",
        max_concurrent=5,
        credentials=[
            {
                "provider": "openai",
                "api_key": "test-key",
            }
        ],
    )
    assert credential_config.get_vlm_instance().max_concurrent == 5

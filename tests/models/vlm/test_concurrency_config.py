# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Regression coverage for the runtime-wide VLM concurrency budget."""

import asyncio
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from openviking.models.vlm import VLMAsyncConcurrencyBudget
from openviking_cli.utils.config.vlm_config import VLMConfig, VLMCredential


class _RecordingCompletions:
    def __init__(self, *, release_after: int):
        self.release_after = release_after
        self.active = 0
        self.peak = 0
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def create(self, **_kwargs):
        self.active += 1
        self.peak = max(self.peak, self.active)
        if self.active >= self.release_after:
            self.entered.set()
        try:
            await self.release.wait()
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))],
                usage=None,
            )
        finally:
            self.active -= 1


def _client(completions: _RecordingCompletions):
    return SimpleNamespace(chat=SimpleNamespace(completions=completions))


def _config(*, max_concurrent: int = 1, credentials: int = 2) -> VLMConfig:
    return VLMConfig(
        model="gpt-4o-mini",
        max_concurrent=max_concurrent,
        max_retries=0,
        credentials=[
            VLMCredential(id=f"credential-{index}", provider="openai", api_key=f"sk-{index}")
            for index in range(credentials)
        ],
    )


def test_vlm_config_rejects_non_positive_concurrency():
    with pytest.raises(ValidationError):
        _config(max_concurrent=0)


@pytest.mark.asyncio
async def test_credentials_share_one_text_and_vision_concurrency_budget(monkeypatch):
    vlm = _config().get_vlm_instance()
    first, second = vlm._vlm_instances
    completions = _RecordingCompletions(release_after=1)
    client = _client(completions)
    monkeypatch.setattr(first, "get_async_client", lambda: client)
    monkeypatch.setattr(second, "get_async_client", lambda: client)

    text_task = asyncio.create_task(first.get_completion_async(prompt="text"))
    await completions.entered.wait()
    vision_task = asyncio.create_task(second.get_vision_completion_async(prompt="vision"))
    await asyncio.sleep(0)

    assert vlm.async_concurrency_budget is first.async_concurrency_budget
    assert first.async_concurrency_budget is second.async_concurrency_budget
    assert first.async_concurrency_budget.snapshot()["waiting"] == 1
    assert completions.active == 1

    completions.release.set()
    assert await asyncio.gather(text_task, vision_task) == ["ok", "ok"]
    assert completions.peak == 1


@pytest.mark.asyncio
async def test_independent_vlm_configs_do_not_share_concurrency_budget(monkeypatch):
    first = _config(credentials=1).get_vlm_instance()
    second = _config(credentials=1).get_vlm_instance()
    completions = _RecordingCompletions(release_after=2)
    client = _client(completions)
    monkeypatch.setattr(first, "get_async_client", lambda: client)
    monkeypatch.setattr(second, "get_async_client", lambda: client)

    tasks = [
        asyncio.create_task(first.get_completion_async(prompt="first")),
        asyncio.create_task(second.get_completion_async(prompt="second")),
    ]
    await asyncio.wait_for(completions.entered.wait(), timeout=1)

    assert first.async_concurrency_budget is not second.async_concurrency_budget
    assert completions.peak == 2

    completions.release.set()
    assert await asyncio.gather(*tasks) == ["ok", "ok"]


@pytest.mark.asyncio
async def test_cancelled_waiter_does_not_leak_budget_state():
    budget = VLMAsyncConcurrencyBudget(1)
    holder_entered = asyncio.Event()
    release_holder = asyncio.Event()

    async def hold_slot():
        async with budget.slot():
            holder_entered.set()
            await release_holder.wait()

    holder = asyncio.create_task(hold_slot())
    await holder_entered.wait()
    waiter = asyncio.create_task(budget.slot().__aenter__())
    await asyncio.sleep(0)
    assert budget.snapshot() == {"limit": 1, "waiting": 1, "in_flight": 1}

    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter
    assert budget.snapshot() == {"limit": 1, "waiting": 0, "in_flight": 1}

    release_holder.set()
    await holder
    async with budget.slot():
        assert budget.snapshot() == {"limit": 1, "waiting": 0, "in_flight": 1}
    assert budget.snapshot() == {"limit": 1, "waiting": 0, "in_flight": 0}

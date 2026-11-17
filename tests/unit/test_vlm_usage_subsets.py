# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Provider entrypoint -> official event -> Prometheus, without model calls."""

import re
from types import SimpleNamespace

import pytest

from openviking.metrics.collectors.vlm import VLMCollector
from openviking.metrics.core.registry import MetricRegistry
from openviking.metrics.exporters.prometheus import PrometheusExporter
from openviking.models.vlm.backends.litellm_vlm import LiteLLMVLMProvider
from openviking.models.vlm.backends.openai_vlm import OpenAIVLM
from openviking.models.vlm.backends.volcengine_vlm import VolcEngineVLM
from openviking.observability.events import (
    make_payload_subscriber,
    register_event_subscriber,
    unregister_event_subscriber,
)


@pytest.mark.parametrize(
    "backend_type,provider",
    [(OpenAIVLM, "openai"), (LiteLLMVLMProvider, "litellm"), (VolcEngineVLM, "volcengine")],
)
@pytest.mark.parametrize("cached,reasoning", [(None, None), (0, 0), (5, 2), (5, None), (None, 2)])
@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("tools", [None, []])
@pytest.mark.asyncio
async def test_provider_subsets_reach_events_and_consumer(
    monkeypatch, backend_type, provider, cached, reasoning, asynchronous, tools
):
    usage = SimpleNamespace(
        prompt_tokens=10,
        completion_tokens=3,
        total_tokens=13,
        prompt_tokens_details=None if cached is None else SimpleNamespace(cached_tokens=cached),
        completion_tokens_details=None
        if reasoning is None
        else SimpleNamespace(reasoning_tokens=reasoning),
    )
    response = SimpleNamespace(
        usage=usage,
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content="synthetic answer", tool_calls=None),
                finish_reason="stop",
            )
        ],
    )

    def create(**kwargs):
        return response

    async def acreate(**kwargs):
        return response

    def post(*args, **kwargs):
        return response

    async def apost(*args, **kwargs):
        return response

    backend = backend_type({"provider": provider, "model": "synthetic"})
    if provider == "litellm":
        monkeypatch.setattr("openviking.models.vlm.backends.litellm_vlm.completion", create)
        monkeypatch.setattr("openviking.models.vlm.backends.litellm_vlm.acompletion", acreate)
    else:
        monkeypatch.setattr(
            backend,
            "get_client",
            lambda: SimpleNamespace(
                chat=SimpleNamespace(completions=SimpleNamespace(create=create)), post=post
            ),
        )
        monkeypatch.setattr(
            backend,
            "get_async_client",
            lambda: SimpleNamespace(
                chat=SimpleNamespace(completions=SimpleNamespace(create=acreate)), post=apost
            ),
        )
    events = []
    registry, collector = MetricRegistry(), VLMCollector()

    def capture(event):
        if event.event_name == "vlm.call":
            events.append(event)

    register_event_subscriber("subset-acceptance-capture", capture)
    register_event_subscriber(
        "subset-acceptance-consumer",
        make_payload_subscriber(lambda name, payload: collector.receive(name, payload, registry)),
    )
    try:
        result = (
            await backend.get_completion_async("synthetic", tools=tools)
            if asynchronous
            else backend.get_completion("synthetic", tools=tools)
        )
    finally:
        unregister_event_subscriber("subset-acceptance-capture")
        unregister_event_subscriber("subset-acceptance-consumer")
    assert str(result) == "synthetic answer"
    assert len(events) == 1
    payload = events[0].payload
    assert payload["prompt_cached_tokens"] == cached
    assert payload["completion_reasoning_tokens"] == reasoning
    text = PrometheusExporter(registry=registry).render()
    assert re.search(r"openviking_vlm_tokens_total\{[^}]*\} 13(?:\.0)?\n", text)
    for subset, value, parent in [
        ("cached_prompt", cached, 10),
        ("reasoning_completion", reasoning, 3),
    ]:
        if value is None:
            assert f'subset="{subset}"' not in text
            continue
        for metric, amount in [("tokens", value), ("parent_tokens", parent), ("reports", 1)]:
            if amount == 0:
                assert not re.search(
                    r"openviking_vlm_usage_subset_" + metric + r'_total\{[^}]*subset="' + subset,
                    text,
                )
                continue
            assert re.search(
                r"openviking_vlm_usage_subset_"
                + metric
                + r'_total\{[^}]*subset="'
                + subset
                + r'"[^}]*\} '
                + str(amount)
                + r"(?:\.0)?\n",
                text,
            )


@pytest.mark.parametrize(
    "usage,expected",
    [
        ({"prompt_cache_hit_tokens": 5}, 5),
        ({"prompt_tokens_details": {"cached_tokens": 0}}, 0),
        ({"prompt_tokens_details": {"cached_tokens": 5}, "prompt_cache_hit_tokens": 5}, 5),
        ({"prompt_tokens_details": {"cached_tokens": 5}, "prompt_cache_hit_tokens": 4}, None),
        ({"prompt_cache_hit_tokens": 11}, None),
        ({"prompt_cache_hit_tokens": True}, None),
        ({"prompt_cache_miss_tokens": 5, "cache_creation_input_tokens": 5}, None),
    ],
)
def test_deepseek_aliases_are_one_fact_and_invalid_or_conflicting_values_stay_unknown(
    usage, expected
):
    from openviking.metrics.datasources.model_usage import response_usage_subsets

    assert response_usage_subsets(usage, 10, 3)["prompt_cached_tokens"] == expected


def test_responses_api_media_usage_reaches_the_same_event():
    from openviking.models.vlm.backends.volcengine_media import _record_usage

    response = SimpleNamespace(
        usage=SimpleNamespace(
            input_tokens=10,
            output_tokens=3,
            input_tokens_details=SimpleNamespace(cached_tokens=5),
            output_tokens_details=SimpleNamespace(reasoning_tokens=2),
        )
    )
    events = []
    register_event_subscriber(
        "media-subsets",
        lambda event: events.append(event) if event.event_name == "vlm.call" else None,
    )
    try:
        _record_usage(VolcEngineVLM({"model": "synthetic"}), response, 0.1)
    finally:
        unregister_event_subscriber("media-subsets")
    assert len(events) == 1
    assert events[0].payload["prompt_cached_tokens"] == 5
    assert events[0].payload["completion_reasoning_tokens"] == 2


def test_retry_and_failover_wrappers_forward_subsets_without_another_event():
    from openviking.models.vlm.base import FailoverVLM
    from openviking.models.vlm.retry_vlm import RetryVLM

    primary = OpenAIVLM({"provider": "openai", "model": "primary"})
    backup = OpenAIVLM({"provider": "openai", "model": "backup"})
    wrapper = RetryVLM(FailoverVLM(primary, backup))
    events = []
    register_event_subscriber(
        "wrapper-subsets",
        lambda event: events.append(event) if event.event_name == "vlm.call" else None,
    )
    try:
        wrapper.update_token_usage(
            "primary", "openai", 10, 3, prompt_cached_tokens=5, completion_reasoning_tokens=2
        )
    finally:
        unregister_event_subscriber("wrapper-subsets")
    assert len(events) == 1
    assert events[0].payload["prompt_cached_tokens"] == 5
    assert primary.get_token_usage()["total_usage"]["total_tokens"] == 13
    assert backup.get_token_usage()["total_usage"]["total_tokens"] == 0

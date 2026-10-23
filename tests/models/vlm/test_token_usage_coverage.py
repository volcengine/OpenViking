# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Tests for VLM token-usage coverage telemetry."""

from types import SimpleNamespace

import pytest

from openviking.models.vlm.backends.litellm_vlm import LiteLLMVLMProvider
from openviking.models.vlm.backends.openai_vlm import OpenAIVLM
from openviking.models.vlm.backends.volcengine_media import _record_usage
from openviking.models.vlm.backends.volcengine_vlm import VolcEngineVLM
from openviking.telemetry.backends.memory import MemoryOperationTelemetry
from openviking.telemetry.context import bind_telemetry


def _response(prompt_tokens: int | None, completion_tokens: int = 0):
    if prompt_tokens is None:
        return SimpleNamespace(usage=None)
    return SimpleNamespace(
        usage=SimpleNamespace(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            prompt_tokens_details=None,
            completion_tokens_details=None,
        )
    )


@pytest.mark.parametrize(
    ("provider_type", "config"),
    [
        (OpenAIVLM, {"provider": "openai", "model": "test-model"}),
        (LiteLLMVLMProvider, {"provider": "litellm", "model": "test-model"}),
    ],
    ids=["openai", "litellm"],
)
@pytest.mark.parametrize(
    ("responses", "expected_status", "expected_counts"),
    [
        ([(None, 0)], "missing", (1, 0, 1)),
        ([(0, 0)], "complete", (1, 1, 0)),
        ([(10, 4), (None, 0)], "partial", (2, 1, 1)),
    ],
    ids=["missing", "explicit-zero", "partial"],
)
def test_chat_response_token_usage_coverage(
    provider_type,
    config,
    responses,
    expected_status,
    expected_counts,
):
    telemetry = MemoryOperationTelemetry(operation="resources.add_resource", enabled=True)
    provider = provider_type(config)

    with bind_telemetry(telemetry):
        for prompt_tokens, completion_tokens in responses:
            provider._update_token_usage_from_response(_response(prompt_tokens, completion_tokens))

    summary = telemetry.finish().summary
    coverage = summary["tokens"]["llm"]["coverage"]
    successful, reported, missing = expected_counts
    assert coverage == {
        "status": expected_status,
        "successful_response_count": successful,
        "reported_response_count": reported,
        "missing_report_count": missing,
    }
    if responses == [(10, 4), (None, 0)]:
        assert summary["tokens"]["llm"]["total"] == 14


@pytest.mark.parametrize(
    ("usage", "expected_status"),
    [(None, "missing"), ((0, 0), "complete")],
    ids=["missing", "explicit-zero"],
)
def test_volcengine_media_response_token_usage_coverage(usage, expected_status):
    telemetry = MemoryOperationTelemetry(operation="resources.add_resource", enabled=True)
    provider = VolcEngineVLM({"provider": "volcengine", "model": "test-model"})
    if usage is None:
        response = SimpleNamespace(usage=None)
    else:
        input_tokens, output_tokens = usage
        response = SimpleNamespace(
            usage=SimpleNamespace(
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                input_tokens_details=None,
                output_tokens_details=None,
            )
        )

    with bind_telemetry(telemetry):
        _record_usage(provider, response, duration_seconds=0.1)

    assert summary_coverage(telemetry) == {
        "status": expected_status,
        "successful_response_count": 1,
        "reported_response_count": int(usage is not None),
        "missing_report_count": int(usage is None),
    }


def test_operation_without_successful_llm_response_omits_coverage():
    telemetry = MemoryOperationTelemetry(operation="resources.add_resource", enabled=True)

    summary = telemetry.finish().summary

    assert "tokens" not in summary


def summary_coverage(telemetry: MemoryOperationTelemetry):
    return telemetry.finish().summary["tokens"]["llm"]["coverage"]

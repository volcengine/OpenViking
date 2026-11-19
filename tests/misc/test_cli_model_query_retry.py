# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Verify CI query retries without a server, credentials, or real waiting."""

import importlib.util
import json
from pathlib import Path
from unittest.mock import Mock

import pytest

HELPER = Path(__file__).resolve().parents[1] / "cli/model_query_retry.py"
spec = importlib.util.spec_from_file_location("model_query_retry", HELPER)
retry = importlib.util.module_from_spec(spec)
spec.loader.exec_module(retry)


def failure(message, *, code="RESOURCE_EXHAUSTED", stream="stderr", details=None):
    payload = {"ok": False, "error": {"code": code, "message": message, "details": details}}
    return {"exit_code": 1, "json": None, stream: json.dumps(payload)}


@pytest.fixture
def waits(monkeypatch):
    delays = []
    monkeypatch.setattr(retry.time, "sleep", delays.append)
    monkeypatch.setattr(retry.random, "uniform", lambda low, high: high)
    return delays


@pytest.mark.parametrize("stream", ["stdout", "stderr"])
def test_tpm_limit_recovers_after_cooldown(stream, waits, capsys):
    limited = failure("TPM (Tokens Per Minute) limit of the model is exceeded", stream=stream)
    success = {"exit_code": 0, "json": {"ok": True, "result": {"resources": []}}}
    call = Mock(side_effect=[limited, success])
    assert retry.retry_model_query(call) is success
    assert call.call_count == 2
    assert waits == [30]
    assert "retry 1/2" in capsys.readouterr().out


def test_persistent_rate_limit_returns_last_failure(waits):
    failures = [failure(f"ModelAccountTpmRateLimitExceeded request-{i}") for i in range(3)]
    call = Mock(side_effect=failures)
    assert retry.retry_model_query(call) is failures[-1]
    assert call.call_count == 3
    assert waits == [30, 60]


@pytest.mark.parametrize(
    "message,code",
    [
        ("Insufficient balance (HTTP 429)", "RESOURCE_EXHAUSTED"),
        ("Monthly token quota exceeded (HTTP 429)", "RESOURCE_EXHAUSTED"),
        ("Upstream model quota or rate limit exceeded (HTTP 429)", "RESOURCE_EXHAUSTED"),
        ("RESOURCE_EXHAUSTED", "RESOURCE_EXHAUSTED"),
        ("Invalid API key", "UNAUTHENTICATED"),
        ("TPM (Tokens Per Minute) limit", "INTERNAL"),
        ("Invalid search arguments", "INVALID_ARGUMENT"),
        ("Service unavailable", "UNAVAILABLE"),
    ],
)
def test_permanent_or_unrelated_errors_are_not_retried(message, code, waits):
    result = failure(message, code=code)
    call = Mock(return_value=result)
    assert retry.retry_model_query(call) is result
    assert call.call_count == 1
    assert waits == []


def test_success_is_called_once(waits):
    result = {"exit_code": 0, "json": {"ok": True}}
    call = Mock(return_value=result)
    assert retry.retry_model_query(call) is result
    assert call.call_count == 1
    assert waits == []


@pytest.mark.parametrize(
    "message",
    ["RPM (Requests Per Minute) limit", "ModelAccountRpmRateLimitExceeded"],
)
def test_structured_rate_limit_details_are_recognized(message):
    result = failure(
        "Upstream model quota or rate limit exceeded",
        details={"upstream_message": message, "upstream_status_code": 429},
    )
    result["json"] = json.loads(result["stderr"])
    assert retry.is_model_rate_limit(result)


@pytest.mark.parametrize("payload", ["not JSON 429", '{"error":', "[]"])
def test_malformed_errors_are_not_retried(payload, waits):
    result = {"exit_code": 1, "stderr": payload}
    call = Mock(return_value=result)
    assert retry.retry_model_query(call) is result
    assert call.call_count == 1
    assert waits == []

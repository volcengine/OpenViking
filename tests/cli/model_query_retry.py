# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Bounded retries for model rate limits in CLI query integration tests."""

import json
import random
import time
from collections.abc import Callable


def is_model_rate_limit(result: dict) -> bool:
    """Require an explicit TPM/RPM error, not just RESOURCE_EXHAUSTED or HTTP 429."""
    payload = result.get("json")
    if not isinstance(payload, dict):
        for stream in ("stderr", "stdout"):
            text = result.get(stream) or ""
            start = text.find("{")
            if start < 0:
                continue
            try:
                payload, _ = json.JSONDecoder().raw_decode(text[start:])
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                break
    if not isinstance(payload, dict):
        return False
    error = payload.get("error")
    if not isinstance(error, dict) or error.get("code") != "RESOURCE_EXHAUSTED":
        return False
    details = error.get("details")
    upstream_message = details.get("upstream_message") if isinstance(details, dict) else None
    message = str(upstream_message or error.get("message") or "").lower()
    return any(
        marker in message
        for marker in (
            "tpm (tokens per minute) limit",
            "rpm (requests per minute) limit",
            "modelaccounttpmratelimitexceeded",
            "modelaccountrpmratelimitexceeded",
        )
    )


def retry_model_query(call: Callable[[], dict]) -> dict:
    """Try at most three times, backing off across a minute-scale quota window."""
    result = call()
    for attempt, base_delay in enumerate((30, 60), start=1):
        if result["exit_code"] == 0 or not is_model_rate_limit(result):
            return result
        delay = base_delay * random.uniform(0.9, 1.0)
        print(
            f"CLI query hit model TPM/RPM limit; retry {attempt}/2 in {delay:.1f}s",
            flush=True,
        )
        time.sleep(delay)
        result = call()
    return result

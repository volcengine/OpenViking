# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Per-upstream accounting evidence, independent of client-visible usage."""

import math
import time
from datetime import datetime, timezone
from urllib.parse import urlsplit

HEADER = "x-openviking-request-id"
SCHEMA = "openviking-upstream-billing-v1"
TIERS = {"default", "standard", "priority", "fast", "flex", "batch", "ultrafast"}
USAGE_FIELDS = {
    "input_tokens",
    "output_tokens",
    "total_tokens",
    "prompt_tokens",
    "completion_tokens",
    "cached_tokens",
    "cache_write_tokens",
    "cache_creation_input_tokens",
    "cache_read_input_tokens",
    "cache_write_input_tokens",
    "prompt_cache_hit_tokens",
    "prompt_cache_miss_tokens",
    "reasoning_tokens",
}
DETAIL_FIELDS = {
    "input_tokens_details",
    "output_tokens_details",
    "prompt_tokens_details",
    "completion_tokens_details",
}


def clock():
    return datetime.now(timezone.utc).isoformat()


def reported_usage(usage):
    """Keep only reported counters; absent fields never turn into reported zero."""
    if not isinstance(usage, dict):
        return {}
    result = {
        key: value
        for key, value in usage.items()
        if key in USAGE_FIELDS
        and isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
        and value >= 0
    }
    for key in DETAIL_FIELDS:
        if isinstance(usage.get(key), dict):
            result[key] = reported_usage(usage[key])
            if isinstance(usage[key].get("cache_ttl"), str) and usage[key]["cache_ttl"] in (
                "30m",
                "12h",
            ):
                result[key]["cache_ttl"] = usage[key]["cache_ttl"]
    return result


def cache_ttl(body):
    options = body.get("prompt_cache_options")
    ttl = options.get("ttl", "30m") if isinstance(options, dict) else "30m"
    items = body.get("input")
    for item in items if isinstance(items, list) else []:
        content = item.get("content") if isinstance(item, dict) else None
        for block in content if isinstance(content, list) else []:
            point = block.get("prompt_cache_breakpoint") if isinstance(block, dict) else None
            if isinstance(point, dict) and "ttl" in point and point["ttl"] != ttl:
                return None
    return ttl if isinstance(ttl, str) and ttl in {"30m", "12h"} else None


class UpstreamBilling:
    """One ledger per gateway request; records contain no prompts or credentials."""

    def __init__(self, metrics):
        self.metrics = metrics
        self.calls = []
        metrics.update(upstream_billing_schema=SCHEMA, upstream_calls=self.calls)
        self.starts = {}

    def begin(self, kind, upstream, body, authenticated=False, account_header=False):
        body = body if isinstance(body, dict) else {}
        host = urlsplit(upstream["base_url"]).hostname or ""
        call = {
            "kind": kind,
            "upstream_host": host if "{api_key}" not in host else "",
            "model": body.get("model") if isinstance(body.get("model"), str) else None,
            "auth_mode": "api_key"
            if authenticated and not account_header and not upstream.get("coding_plan")
            else "unknown",
            "cache_ttl": cache_ttl(body),
            "service_tier": body.get("service_tier", "default"),
            "started_at": clock(),
            "complete": False,
            "usage": {},
        }
        if not isinstance(call["service_tier"], str) or call["service_tier"] not in TIERS:
            call["service_tier"] = None
        self.calls.append(call)
        self.starts[id(call)] = time.monotonic()
        return call

    def end(self, call, *, status=None, envelope=None, usage=None, complete=False, error=""):
        started = self.starts.pop(id(call), None)
        if started is None:
            return
        envelope = envelope if isinstance(envelope, dict) else {}
        call.update(
            status=status,
            complete=complete,
            usage=reported_usage(usage),
            finished_at=clock(),
            duration_ms=round((time.monotonic() - started) * 1000, 2),
        )
        response_id = envelope.get("id")
        if isinstance(response_id, str):
            call["response_id"] = response_id
        tier = envelope.get("service_tier")
        if isinstance(tier, str) and tier in TIERS:
            call["service_tier"] = tier
        details = call["usage"].get("input_tokens_details") or {}
        if details.get("cache_ttl") in {"30m", "12h"}:
            call["cache_ttl"] = details["cache_ttl"]
        if error:
            call["error"] = error

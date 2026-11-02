# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Management overview: request-log aggregates and prompt-cache alerts."""

# A conversation this long (input tokens) that misses the cache this many calls in a row
# raises an alert; the page lists the newest few.
LONG_CONTEXT = 32_000
MISS_STREAK = 3
LISTED_MISSES = 3
# Seconds; Anthropic's default cache lifetime, the shortest among providers.
CACHE_TTL = 300
# A call this many times the size of the next one was cut down by the client
# (compacted or cleared), so the next could not hit.
CUT_RATIO = 1.3
# Seconds; older calls neither raise alerts nor show an upstream hits.
STALE_AFTER = 24 * 3600
# Degradations that change the prompt prefix, so the next call cannot hit; persistent
# ones show on the degraded-requests card instead.
PREFIX_RESETS = {
    "upstream_changed",
    "missing_injection_record",
    "hidden_tool_history_unavailable",
    "ark_cache_parameters_changed",
}


def first_upstream(log, name, default=None):
    """The first upstream call's usage field, falling back to the request's own."""
    return log.get(f"first_upstream_{name}", log.get(name, default))


def started(log):
    """When the request reached the gateway; logs are written once it finishes."""
    return log["time"] - log.get("duration_ms", 0) / 1000


def miss_streak(calls):
    """The newest calls of one conversation that each sent a long prompt and missed a cache
    the call before should have written."""
    streak = []
    for call in sorted(calls, key=started, reverse=True):
        size = first_upstream(call, "input_tokens")
        if streak:
            newer = streak[-1]
            gap = started(newer) - call["time"]
            if gap < 0:
                continue  # Concurrent with the newer call, so neither could warm the other.
            if (
                gap > CACHE_TTL
                or size > first_upstream(newer, "input_tokens") * CUT_RATIO
                or any(
                    call.get(name) != newer.get(name)
                    for name in ("model", "upstream_id", "tool_skip_reason")
                )
                or bool(call.get("tools_tokens")) != bool(newer.get("tools_tokens"))
            ):
                break
        if (
            size < LONG_CONTEXT
            or first_upstream(call, "cached_tokens")
            or call.get("window_reset")
            or call.get("compaction_tokens")
            or call.get("degradation") in PREFIX_RESETS
        ):
            break
        streak.append(call)
    return streak


def cache_alerts(requests, now):
    """Long conversations that keep missing the prompt cache, and upstreams that have not
    hit it lately."""
    recent = [log for log in requests if now - log["time"] <= STALE_AFTER]
    hit_fields = ("first_upstream_cached_tokens", "hidden_upstream_cached_tokens", "cached_tokens")
    hits = {log["upstream_id"] for log in recent if any(log.get(n) for n in hit_fields)}
    conversations = {}
    for log in recent:
        if (
            log.get("kind") in {"user", "continuation"}
            and log.get("session")
            # Only answered upstream calls carry usage, along with their status and upstream.
            and first_upstream(log, "input_tokens") is not None
            and 200 <= log["status"] < 300
        ):
            key = log["credential_id"], log["protocol"], log["session"]
            conversations.setdefault(key, []).append(log)
    misses, unreported = [], set()
    for (_, protocol, _), calls in conversations.items():
        streak = miss_streak(calls)
        if len(streak) < MISS_STREAK:
            continue
        newest = streak[0]
        if newest["upstream_id"] not in hits:
            unreported.add(newest["upstream_id"])
            continue
        misses.append(
            {
                "upstream_id": newest["upstream_id"],
                "model": newest.get("model"),
                "streak": len(streak),
                "last_at": newest["time"],
                "input_tokens": min(first_upstream(call, "input_tokens") for call in streak),
                # Anthropic writes the cache whenever a request asks for it, so no write at all
                # means the client never asked (no cache_control) or the relay dropped the count.
                "no_writes": protocol == "anthropic"
                and not any(first_upstream(call, "cache_write_tokens") for call in streak),
            }
        )
    if not misses and not unreported:
        return None
    misses.sort(key=lambda miss: miss["last_at"], reverse=True)
    listed = ("upstream_id", "model", "streak", "last_at")
    return {
        # The page lists a few conversations but speaks for all of them.
        "misses": {
            "conversations": len(misses),
            "streak": min(miss["streak"] for miss in misses),
            "input_tokens": min(miss["input_tokens"] for miss in misses),
            "no_writes": all(miss["no_writes"] for miss in misses),
            "recent": [{k: miss[k] for k in listed} for miss in misses[:LISTED_MISSES]],
        }
        if misses
        else None,
        "unreported": sorted(unreported),
    }


def overview_summary(logs, now):
    """Aggregate newest-first request-log records into the management overview."""
    requests = [log for log in logs if log.get("kind") != "capture"]
    groups = {}
    for label, kinds in (("first_call", {"user"}), ("continuation", {"continuation"})):
        subset = [log for log in requests if log.get("kind") in kinds]
        total = sum(first_upstream(log, "input_tokens", 0) for log in subset)
        cached = sum(first_upstream(log, "cached_tokens", 0) for log in subset)
        hidden_calls = 0
        if label == "continuation":
            total += sum(log.get("hidden_upstream_input_tokens", 0) for log in requests)
            cached += sum(log.get("hidden_upstream_cached_tokens", 0) for log in requests)
            hidden_calls = sum(log.get("hidden_upstream_calls", 0) for log in requests)
        groups[label] = {
            "requests": len(subset) + hidden_calls,
            "input_tokens": total,
            "cached_tokens": cached,
            "cache_hit_ratio": cached / total if total else 0,
        }
    degradations = {}
    for log in logs:
        if reason := log.get("degradation"):
            degradations[reason] = degradations.get(reason, 0) + 1
    # "disabled" means recall was skipped (off, budget spent or query too short).
    recalls = [log for log in requests if log.get("recall_reason", "disabled") != "disabled"]
    capture = {}
    for log in logs:
        if log.get("session") and log.get("capture_status"):
            capture.setdefault((log["session"], log.get("protocol")), log["capture_status"])
    return {
        "requests": len(requests),
        "last_request_at": requests[0].get("time") if requests else None,
        "output_tokens": sum(log.get("output_tokens", 0) for log in requests),
        "cache": groups,
        "degradations": degradations,
        "recall_count": sum(log.get("recall_count", 0) for log in requests),
        "recall_requests": len(recalls),
        "recall_ms": sum(log.get("recall_ms", 0) for log in recalls) / len(recalls)
        if recalls
        else 0,
        "capture_issues": {
            status: sum(1 for value in capture.values() if value == status)
            for status in ("retrying", "paused")
        },
        "cache_alerts": cache_alerts(requests, now),
        "sample_limit": 10000,
    }

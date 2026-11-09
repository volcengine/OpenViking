"""Shared event-bus adapter. Raw OV identities are mapped only at the exporter."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import uuid

from openviking.observability.events import register_event_subscriber, unregister_event_subscriber

from .config import load_configuration
from .reporter import KafkaEventReporter

logger = logging.getLogger(__name__)
_reporter = None
_SUBSCRIBER = "remote_event_reporting"
_NAMES = {
    "http.request": "http_request",
    "vlm.call": "vlm_call",
    "embedding.call": "embedding_call",
}
_FIELDS = frozenset(
    "task_id source_task_ids task_type mode uri old_uri skill_uri operation change_type is_dir is_new_file scope output_role visibility_scope owner_user_id context_type level memory_type memory_group template_id peer_id source_type usage_purpose cost_bearer status created_at updated_at model_name provider prompt_tokens completion_tokens error_code".split()
)


def is_reporting_enabled():
    return _reporter is not None


def make_subscriber(reporter, config):
    deployment = config.get("telemetry", {}).get("deployment", {})

    def subscriber(event):
        name = _NAMES.get(event.event_name, event.event_name)
        if name not in {
            "http_request",
            "vlm_call",
            "embedding_call",
            "understanding_call",
            "task_terminal",
            "file_mutation",
            "skill_mutation",
            "public_asset_attribution",
        }:
            return
        source = event.payload
        payload = {key: source[key] for key in _FIELDS if key in source}
        payload.update(
            schema_version="1.0",
            event_name=name,
            log_source="ov",
            timestamp=int(event.timestamp.timestamp() * 1000),
            account_id=deployment.get("account_id") or os.getenv("OV_METRICS_ACCOUNT_ID"),
            resource_id=deployment.get("resource_id") or os.getenv("OV_RESOURCE_ID"),
            region=deployment.get("region") or os.getenv("OV_REGION"),
            ov_account_id=event.account_id,
            ov_user_id=event.user_id,
            request_id=event.request_id,
        )
        if name == "http_request":
            payload.update(
                http_method=source.get("method"),
                http_route=source.get("route"),
                http_status_code=int(source.get("status", 0)),
                duration_ms=float(source.get("duration_seconds", 0)) * 1000,
            )
        elif "duration_seconds" in source:
            payload["duration_ms"] = float(source["duration_seconds"]) * 1000
        # Keep the original event id on retransmission. Task terminal ids are deterministic.
        payload["event_id"] = source.get("event_id") or "evt_" + uuid.uuid4().hex
        if name == "task_terminal" and source.get("task_id"):
            identity = "|".join(
                str(payload.get(k) or "")
                for k in (
                    "account_id",
                    "resource_id",
                    "ov_account_id",
                    "task_id",
                    "status",
                    "updated_at",
                )
            )
            payload["event_id"] = "evt_" + hashlib.sha256(identity.encode()).hexdigest()
            payload["timestamp"] = int(source.get("updated_at") or payload["timestamp"])
        elif name == "file_mutation" and source.get("operation") == "compile_commit":
            payload["timestamp"] = int(source.get("updated_at") or payload["timestamp"])
        if name in {"vlm_call", "embedding_call"}:
            # An unclassified billing owner stays unknown, rather than silently charged to OV.
            mappings = config.get("telemetry", {}).get("model_cost_bearers", [])
            matching = [
                item
                for item in mappings
                if item.get("provider") == source.get("provider")
                and item.get("model_name") == source.get("model_name")
                and item.get("cost_bearer") in {"ov", "customer"}
                and all(
                    not item.get(key) or item[key] == payload.get(key)
                    for key in ("account_id", "resource_id", "ov_account_id")
                )
            ]

            def specificity(item):
                return sum(
                    bool(item.get(key)) for key in ("account_id", "resource_id", "ov_account_id")
                )

            rank = max((specificity(item) for item in matching), default=-1)
            bearers = {item["cost_bearer"] for item in matching if specificity(item) == rank}
            # Explicit per-credential provenance wins over library defaults and
            # compatibility rules. An explicit unknown must not become customer.
            if payload.get("cost_bearer") is None:
                default_bearer = deployment.get("model_cost_bearer") or os.getenv(
                    "OV_MODEL_COST_BEARER"
                )
                payload["cost_bearer"] = (
                    next(iter(bearers))
                    if len(bearers) == 1
                    else default_bearer
                    if not matching and default_bearer in {"ov", "customer"}
                    else "unknown"
                )
            elif payload["cost_bearer"] not in {"ov", "customer", "unknown"}:
                payload["cost_bearer"] = "unknown"
        tasks = source.get("metric_tasks") if name == "http_request" else None
        if tasks:
            for task in tasks:
                task_event_id = (
                    "evt_"
                    + hashlib.sha256(
                        (payload["event_id"] + "|" + task["task_id"]).encode()
                    ).hexdigest()
                )
                reporter.report({**payload, **task, "event_id": task_event_id})
        else:
            reporter.report(payload)

    return subscriber


async def start_reporting(settings):
    global _reporter
    if not settings.enabled:
        return None
    try:
        config = await asyncio.to_thread(load_configuration, settings)
        if not config.get("telemetry", {}).get("enabled", False):
            return None
        reporter = await asyncio.to_thread(KafkaEventReporter, config)
        register_event_subscriber(_SUBSCRIBER, make_subscriber(reporter, config))
        _reporter = reporter
        return reporter
    except Exception:
        # Credentials and raw connection exceptions must not reach public logs.
        logger.error("Remote reporting initialization failed; business service remains available")
        return None


async def stop_reporting():
    global _reporter
    unregister_event_subscriber(_SUBSCRIBER)
    reporter, _reporter = _reporter, None
    if reporter is not None:
        try:
            await asyncio.to_thread(reporter.close)
        except Exception:
            logger.error("Remote reporting shutdown did not finish flushing")

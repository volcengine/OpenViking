# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Resolve the same library TTL policy for every object creation path."""

from openviking.config.merge import apply_three_state_patch
from openviking_cli.utils.config.ttl_config import TTLConfig


def merge_ttl_config(current: dict, patch: dict) -> dict:
    """Merge sparse TTL policies without retaining another mode's parameters.

    Null removes an override. A concrete mode selects a policy variant;
    unrelated scopes and directories retain ordinary PATCH semantics.
    A day/timestamp-only edit selects its variant for a fresh sparse override.
    Conflicting request fields remain present so validation rejects them.
    """
    merged = apply_three_state_patch(current, patch)

    def policy(base, changes):
        result = apply_three_state_patch(base or {}, changes)
        mode = changes.get("mode")
        if mode is None and changes.get("ttl_days") is not None:
            mode = "days"
        elif mode is None and changes.get("ttl_absolute") is not None:
            mode = "absolute"
        if mode is not None:
            result["mode"] = mode
            for field, variant in (("ttl_days", "days"), ("ttl_absolute", "absolute")):
                if mode != variant and field not in changes:
                    result.pop(field, None)
        return result

    for key, changes in patch.items():
        if not isinstance(changes, dict):
            continue
        if key == "directories":
            previous = current.get(key) or {}
            for uri, value in changes.items():
                if isinstance(value, dict):
                    merged.setdefault(key, {})[uri] = policy(previous.get(uri), value)
        elif key in {
            "global",
            "global_default",
            "user_events",
            "peer_events",
            "sessions",
            "resources",
        }:
            merged[key] = policy(current.get(key), changes)
    return merged


def merge_runtime_settings(current: dict | None, patch: dict) -> dict:
    """Apply the generic PATCH contract, with TTL's discriminated policies."""
    result = apply_three_state_patch(current, patch)
    if isinstance(patch.get("ttl"), dict):
        result["ttl"] = merge_ttl_config((current or {}).get("ttl") or {}, patch["ttl"])
    return result


def effective_ttl_config(cluster, account) -> TTLConfig:
    override = account.ttl
    return TTLConfig.model_validate(
        merge_ttl_config(
            cluster.ttl.model_dump(by_alias=True),
            override.model_dump(by_alias=True, exclude_unset=True) if override else {},
        )
    )


async def resolve_ttl_config(fs, account_id: str) -> TTLConfig | None:
    manager = getattr(fs, "runtime_config_manager", None)
    if manager is None:
        return None  # The pure TTL helpers fall back to ov.conf at startup.

    def resolve(view):
        return effective_ttl_config(view.cluster, view.account)

    return await manager.resolve_account(account_id, resolve)

# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Resolve the same library TTL policy for every object creation path."""

import asyncio

from openviking.config.merge import apply_three_state_patch
from openviking.config.scope import ConfigScope
from openviking.config.validate import filter_runtime_fields, normalize_config_keys
from openviking_cli.utils.config.ttl_config import TTLConfig


def validate_ttl_policy_modes(settings: dict) -> dict:
    """Require an explicit mode on each policy object in an API request."""
    ttl = settings.get("ttl")
    if isinstance(ttl, dict):
        policies = [
            (f"ttl.{key}", ttl[key])
            for key in ("global", "global_default", "user_events", "peer_events", "sessions")
            if key in ttl
        ]
        if isinstance(ttl.get("directories"), dict):
            policies.extend(
                (f"ttl.directories.{uri}", value) for uri, value in ttl["directories"].items()
            )
        for path, policy in policies:
            if isinstance(policy, dict) and policy.get("mode") is None:
                raise ValueError(f"{path}.mode is required")
    return settings


def merge_ttl_config(current: dict, patch: dict) -> dict:
    """Merge sparse TTL policies without retaining another mode's parameters.

    Null removes an override. A concrete mode selects a policy variant;
    unrelated scopes and directories retain ordinary PATCH semantics.
    Legacy persisted day/timestamp-only overrides still select their variant;
    API requests must provide mode before reaching this merge.
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


async def resolve_ttl_config(fs, account_id: str, *, fresh: bool = False) -> TTLConfig | None:
    manager = getattr(fs, "runtime_config_manager", None)
    if manager is None:
        return None  # The pure TTL helpers fall back to ov.conf at startup.

    if fresh:
        # Only lifetime initialization/application needs current source values.
        # Resolve TTL alone; do not rebuild/publish unrelated runtime sections.
        cluster, account = await asyncio.gather(
            manager.get_settings(ConfigScope.cluster()),
            manager.get_settings(ConfigScope.account(account_id)),
        )
        settings = manager.base_config.ttl.model_dump(by_alias=True)
        if "ttl" in cluster:
            settings = (
                merge_ttl_config(
                    settings,
                    filter_runtime_fields(
                        TTLConfig, normalize_config_keys(TTLConfig, cluster["ttl"])
                    ),
                )
                if cluster["ttl"] is not None
                else {}
            )
        # Account nulls remove its override, not the inherited cluster policy.
        override = merge_ttl_config(
            {},
            filter_runtime_fields(
                TTLConfig, normalize_config_keys(TTLConfig, account.get("ttl") or {})
            ),
        )
        # Normalize each scope (including directory URIs) before composition,
        # just as the cached cluster/account models do.
        settings = TTLConfig.model_validate(settings).model_dump(by_alias=True)
        override = TTLConfig.model_validate(override).model_dump(by_alias=True, exclude_unset=True)
        return TTLConfig.model_validate(merge_ttl_config(settings, override))

    def resolve(view):
        return effective_ttl_config(view.cluster, view.account)

    return await manager.resolve_account(account_id, resolve)

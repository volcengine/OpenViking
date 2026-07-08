# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""PolicyUpdater component implementations."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from openviking.session.memory.dataclass import (
    MemoryFile,
    ResolvedOperation,
    ResolvedOperations,
    StoredLink,
)
from openviking.session.memory.memory_type_registry import create_default_registry
from openviking.session.memory.memory_updater import MemoryUpdater
from openviking.session.train.domain import (
    Policy,
    PolicyApplyResult,
    PolicyPlanItem,
    PolicySet,
    PolicyUpdatePlan,
)
from openviking.storage.viking_fs import get_viking_fs
from openviking.telemetry import tracer

_EXPERIENCE_NAME_RE = re.compile(r"[^a-zA-Z0-9_.-]+")


@dataclass(slots=True)
class DryRunPolicyUpdater:
    """PolicyUpdater that records a plan without writing files.

    Unlike a pure no-op, this updater simulates executable plan items into an
    updated ExperienceSet snapshot, which makes tests and offline review useful
    before enabling a writing updater.
    """

    simulate: bool = True

    @tracer("train.policy_updater.dry_run.apply", ignore_result=True, ignore_args=True)
    async def apply(
        self,
        plan: PolicyUpdatePlan,
        policy_set: PolicySet,
        context: Any = None,
        *,
        transaction_handle: Any = None,
    ) -> PolicyApplyResult:
        del transaction_handle
        del context
        updated_policy_set = (
            _apply_items_to_snapshot(plan.items, policy_set)
            if self.simulate and plan.items
            else policy_set
        )
        return PolicyApplyResult(
            updated_policy_set=updated_policy_set,
            written_uris=[],
            metadata={
                "dry_run": True,
                "simulated": self.simulate,
                "plan": plan.metadata,
                "item_count": len(plan.items),
            },
        )


@dataclass(slots=True)
class MemoryFilePolicyUpdater:
    """PolicyUpdater that writes policy files via VikingFS.

    It consumes executable ``upsert`` and ``delete`` plan items. The updater
    performs a lightweight base-content guard when ``before_content`` is
    available to avoid blindly overwriting or deleting a diverged policy set
    snapshot.
    """

    viking_fs: Any = None
    vikingdb: Any = None

    @tracer("train.policy_updater.memory_file.apply", ignore_result=True, ignore_args=True)
    async def apply(
        self,
        plan: PolicyUpdatePlan,
        policy_set: PolicySet,
        context: Any = None,
        *,
        transaction_handle: Any = None,
    ) -> PolicyApplyResult:
        viking_fs = self.viking_fs or get_viking_fs()
        if viking_fs is None:
            raise RuntimeError("VikingFS is required to apply policy update plans")

        updated_policy_set = _apply_items_to_snapshot(plan.items, policy_set)
        operations, preflight_errors = _plan_to_resolved_operations(
            plan=plan,
            policy_set=policy_set,
            updated_policy_set=updated_policy_set,
        )
        updater = MemoryUpdater(
            registry=create_default_registry(),
            vikingdb=self.vikingdb,
            transaction_handle=transaction_handle,
        )
        updater._viking_fs = viking_fs

        apply_result = await updater.apply_operations(
            operations,
            context,
            extract_context=None,
            isolation_handler=None,
        )
        errors = [*preflight_errors, *[f"{uri}: {exc}" for uri, exc in apply_result.errors]]

        return PolicyApplyResult(
            updated_policy_set=updated_policy_set if not errors else policy_set,
            written_uris=list(apply_result.written_uris + apply_result.edited_uris),
            deleted_uris=list(apply_result.deleted_uris),
            errors=errors,
            metadata={
                "dry_run": False,
                "item_count": len(plan.items),
                "operation_upsert_count": len(operations.upsert_operations),
                "operation_delete_count": len(operations.delete_file_contents),
            },
        )


def _apply_items_to_snapshot(
    items: list[PolicyPlanItem], policy_set: PolicySet
) -> PolicySet:
    policies_by_uri = {policy.uri: policy for policy in policy_set.policies}
    result = list(policy_set.policies)

    for item in items:
        uri = _target_uri(item, policy_set.root_uri)

        if item.kind == "delete":
            existing = policies_by_uri.get(uri) or _find_policy(
                PolicySet(
                    policy_set.root_uri,
                    result,
                    metadata=dict(policy_set.metadata),
                    viking_fs=policy_set.viking_fs,
                    request_context=policy_set.request_context,
                ),
                uri=None,
                name=item.target_name,
            )
            remove_uri = existing.uri if existing is not None else uri
            result = [
                policy
                for policy in result
                if policy.uri != remove_uri and policy.name != item.target_name
            ]
            policies_by_uri.pop(remove_uri, None)
            policies_by_uri.pop(uri, None)
            continue

        if item.kind != "upsert" or item.after_content is None:
            continue
        existing = policies_by_uri.get(uri) or _find_policy(
            PolicySet(
                policy_set.root_uri,
                result,
                metadata=dict(policy_set.metadata),
                viking_fs=policy_set.viking_fs,
                request_context=policy_set.request_context,
            ),
            uri=None,
            name=item.target_name,
        )
        metadata = dict(existing.metadata) if existing is not None else {}
        metadata.update(item.metadata.get("patch_metadata", {}))
        metadata.setdefault("memory_type", item.memory_type or "experiences")
        metadata["experience_name"] = item.target_name
        version = (existing.version + 1) if existing is not None else 1
        updated = Policy(
            name=item.target_name,
            uri=uri,
            version=version,
            status=(existing.status if existing is not None else "draft"),
            content=item.after_content,
            metadata=metadata,
            links=list(existing.links or []) if existing is not None else [],
            backlinks=list(existing.backlinks or []) if existing is not None else [],
        )
        if existing is None:
            result.append(updated)
        else:
            result = [updated if policy.uri == existing.uri else policy for policy in result]
        policies_by_uri[uri] = updated

    result.sort(key=lambda policy: policy.uri)
    return PolicySet(
        root_uri=policy_set.root_uri,
        policies=result,
        metadata=dict(policy_set.metadata),
        viking_fs=policy_set.viking_fs,
        request_context=policy_set.request_context,
    )


def _find_policy(
    policy_set: PolicySet,
    *,
    uri: str | None,
    name: str,
) -> Policy | None:
    for policy in policy_set.policies:
        if uri and policy.uri == uri:
            return policy
        if not uri and policy.name == name:
            return policy
    return None


def _target_uri(item: PolicyPlanItem, root_uri: str) -> str:
    if item.target_uri:
        return item.target_uri
    return f"{root_uri.rstrip('/')}/{_safe_experience_filename(item.target_name)}.md"



def _plan_to_resolved_operations(
    *,
    plan: PolicyUpdatePlan,
    policy_set: PolicySet,
    updated_policy_set: PolicySet,
) -> tuple[ResolvedOperations, list[str]]:
    upserts: list[ResolvedOperation] = []
    deletes: list[MemoryFile] = []
    links: list[StoredLink] = []
    errors: list[str] = []

    for item in plan.items:
        uri = _target_uri(item, policy_set.root_uri)
        current = _find_policy(policy_set, uri=uri, name=item.target_name)
        if (
            current is not None
            and item.before_content is not None
            and _normalize_guard_content(current.content)
            != _normalize_guard_content(item.before_content)
        ):
            errors.append(
                "base content mismatch for "
                f"{item.target_name}: expected gradient before_content"
            )
            continue

        if item.kind == "delete":
            deletes.append(_policy_or_plan_item_memory_file(item, uri=uri, current=current))
            continue

        if item.kind != "upsert":
            continue
        if item.after_content is None:
            errors.append(f"missing after_content for {item.target_name}")
            continue

        updated = _find_policy(updated_policy_set, uri=uri, name=item.target_name)
        if updated is None:
            errors.append(
                f"planned policy not found after simulation: {item.target_name}"
            )
            continue

        upserts.append(
            ResolvedOperation(
                old_memory_file_content=_policy_to_memory_file(current)
                if current is not None
                else None,
                memory_fields={
                    **dict(updated.metadata),
                    "memory_type": item.memory_type or "experiences",
                    "experience_name": updated.name,
                    "content": updated.content,
                    "status": updated.status,
                },
                memory_type=item.memory_type or "experiences",
                uris=[uri],
            )
        )
        links.extend(_source_trajectory_links(exp_uri=uri, links=item.links))

    return (
        ResolvedOperations(
            upsert_operations=upserts,
            delete_file_contents=deletes,
            errors=[],
            resolved_links=links,
        ),
        errors,
    )


def _policy_or_plan_item_memory_file(
    item: PolicyPlanItem,
    *,
    uri: str,
    current: Policy | None,
) -> MemoryFile:
    if current is not None:
        return _policy_to_memory_file(current)
    return MemoryFile(
        uri=uri,
        content=item.before_content or "",
        memory_type=item.memory_type or "experiences",
        extra_fields={
            "memory_type": item.memory_type or "experiences",
            "experience_name": item.target_name,
            **({"version": item.base_version} if item.base_version is not None else {}),
        },
    )


def _policy_to_memory_file(policy: Policy | None) -> MemoryFile | None:
    if policy is None:
        return None
    return MemoryFile(
        uri=policy.uri,
        content=policy.content,
        links=list(policy.links or []),
        backlinks=list(policy.backlinks or []),
        memory_type="experiences",
        extra_fields={
            **dict(policy.metadata),
            "memory_type": "experiences",
            "experience_name": policy.name,
            "version": policy.version,
            "status": policy.status,
        },
    )


def _source_trajectory_links(
    *,
    exp_uri: str,
    links: list[StoredLink],
) -> list[StoredLink]:
    result: list[StoredLink] = []
    seen: set[tuple[str, str | None]] = set()
    for link in links or []:
        if (
            link.link_type != "derived_from"
            or not link.to_uri
            or "/memories/trajectories/" not in link.to_uri
        ):
            continue
        key = (link.to_uri, link.match_text)
        if key in seen:
            continue
        seen.add(key)
        update = {"from_uri": exp_uri, "match_text": None, "description": ""}
        if not link.created_at:
            update["created_at"] = datetime.now(timezone.utc).isoformat()
        result.append(link.model_copy(update=update))
    return result


def _safe_experience_filename(name: str) -> str:
    filename = _EXPERIENCE_NAME_RE.sub("_", name.strip()).strip("._-")
    return filename or "new_experience"


def _normalize_guard_content(content: str) -> str:
    return content.strip()

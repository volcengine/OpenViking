# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""PolicyUpdater adapters."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from openviking.session.memory.dataclass import MemoryFile
from openviking.session.memory.utils.memory_file_utils import MemoryFileUtils
from openviking.session.train.domain import (
    ApplyResult,
    Experience,
    ExperienceSet,
    PolicyPlanItem,
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
        policy_set: ExperienceSet,
        context: Any = None,
    ) -> ApplyResult:
        del context
        updated_policy_set = (
            _apply_items_to_snapshot(plan.items, policy_set)
            if self.simulate and plan.items
            else policy_set
        )
        return ApplyResult(
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
    """PolicyUpdater that writes experience files via VikingFS.

    It consumes ``upsert_experience`` items containing full after-content.  The
    updater performs a lightweight base-content guard when ``before_content`` is
    available to avoid blindly overwriting a diverged ExperienceSet snapshot.
    """

    viking_fs: Any = None

    @tracer("train.policy_updater.memory_file.apply", ignore_result=True, ignore_args=True)
    async def apply(
        self,
        plan: PolicyUpdatePlan,
        policy_set: ExperienceSet,
        context: Any = None,
    ) -> ApplyResult:
        viking_fs = self.viking_fs or get_viking_fs()
        if viking_fs is None:
            raise RuntimeError("VikingFS is required to apply policy update plans")

        updated_policy_set = _apply_items_to_snapshot(plan.items, policy_set)
        written_uris: list[str] = []
        errors: list[str] = []

        for item in plan.items:
            if item.kind != "upsert_experience":
                continue
            if item.after_content is None:
                errors.append(f"missing after_content for {item.target_experience_name}")
                continue
            uri = _target_uri(item, policy_set.root_uri)
            current = _find_policy(policy_set, uri=uri, name=item.target_experience_name)
            if (
                current is not None
                and item.before_content is not None
                and _normalize_guard_content(current.content)
                != _normalize_guard_content(item.before_content)
            ):
                errors.append(
                    "base content mismatch for "
                    f"{item.target_experience_name}: expected gradient before_content"
                )
                continue
            updated = _find_policy(updated_policy_set, uri=uri, name=item.target_experience_name)
            if updated is None:
                errors.append(
                    f"planned policy not found after simulation: {item.target_experience_name}"
                )
                continue
            raw = MemoryFileUtils.write(
                MemoryFile(
                    uri=uri,
                    content=updated.content,
                    memory_type="experiences",
                    extra_fields={
                        **dict(updated.metadata),
                        "memory_type": "experiences",
                        "experience_name": updated.name,
                        "version": updated.version,
                        "status": updated.status,
                    },
                )
            )
            try:
                await viking_fs.write_file(uri, raw, ctx=context)
                written_uris.append(uri)
            except Exception as exc:  # pragma: no cover - defensive adapter boundary
                errors.append(f"failed to write {uri}: {exc}")

        return ApplyResult(
            updated_policy_set=updated_policy_set,
            written_uris=written_uris,
            errors=errors,
            metadata={"dry_run": False, "item_count": len(plan.items)},
        )


def _apply_items_to_snapshot(
    items: list[PolicyPlanItem], policy_set: ExperienceSet
) -> ExperienceSet:
    policies_by_uri = {policy.uri: policy for policy in policy_set.policies}
    result = list(policy_set.policies)

    for item in items:
        if item.kind != "upsert_experience" or item.after_content is None:
            continue
        uri = _target_uri(item, policy_set.root_uri)
        existing = policies_by_uri.get(uri) or _find_policy(
            ExperienceSet(
                policy_set.root_uri,
                result,
                metadata=dict(policy_set.metadata),
                viking_fs=policy_set.viking_fs,
                request_context=policy_set.request_context,
            ),
            uri=None,
            name=item.target_experience_name,
        )
        metadata = dict(existing.metadata) if existing is not None else {}
        metadata.update(item.metadata.get("patch_metadata", {}))
        metadata.setdefault("memory_type", "experiences")
        metadata["experience_name"] = item.target_experience_name
        metadata["source_gradient"] = {
            "confidence": item.confidence,
            "evidence_trajectory_uris": list(item.evidence_trajectory_uris),
            "rationale": item.metadata.get("rationale"),
        }
        version = (existing.version + 1) if existing is not None else 1
        updated = Experience(
            name=item.target_experience_name,
            uri=uri,
            version=version,
            status=(existing.status if existing is not None else "draft"),
            content=item.after_content,
            metadata=metadata,
        )
        if existing is None:
            result.append(updated)
        else:
            result = [updated if policy.uri == existing.uri else policy for policy in result]
        policies_by_uri[uri] = updated

    result.sort(key=lambda policy: policy.uri)
    return ExperienceSet(
        root_uri=policy_set.root_uri,
        policies=result,
        metadata=dict(policy_set.metadata),
        viking_fs=policy_set.viking_fs,
        request_context=policy_set.request_context,
    )


def _find_policy(
    policy_set: ExperienceSet,
    *,
    uri: str | None,
    name: str,
) -> Experience | None:
    for policy in policy_set.policies:
        if uri and policy.uri == uri:
            return policy
        if not uri and policy.name == name:
            return policy
    return None


def _target_uri(item: PolicyPlanItem, root_uri: str) -> str:
    if item.target_experience_uri:
        return item.target_experience_uri
    return f"{root_uri.rstrip('/')}/{_safe_experience_filename(item.target_experience_name)}.md"


def _safe_experience_filename(name: str) -> str:
    filename = _EXPERIENCE_NAME_RE.sub("_", name.strip()).strip("._-")
    return filename or "new_experience"


def _normalize_guard_content(content: str) -> str:
    return content.strip()

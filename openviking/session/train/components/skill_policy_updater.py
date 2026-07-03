# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""PolicyUpdater that writes skill files via SkillProcessor / SkillOperationUpdater."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from openviking.core.skill_loader import SkillLoader
from openviking.server.identity import RequestContext
from openviking.session.memory.dataclass import (
    MemoryFile,
    ResolvedOperation,
    ResolvedOperations,
)
from openviking.session.skill import SkillOperationUpdater
from openviking.session.skill.session_skill_context_provider import (
    SESSION_SKILL_MEMORY_TYPE,
    load_skill_extract_registry,
)
from openviking.session.train.domain import (
    Policy,
    PolicyApplyResult,
    PolicyPlanItem,
    PolicySet,
    PolicyUpdatePlan,
)
from openviking.storage.viking_fs import get_viking_fs
from openviking.telemetry import tracer
from openviking.utils.skill_processor import SkillProcessor
from openviking_cli.utils import get_logger

logger = get_logger(__name__)


@dataclass(slots=True)
class SkillPolicyUpdater:
    """PolicyUpdater that writes skill files to a skills directory.

    For new skills (no existing file) the full ``SkillProcessor.process_skill``
    pipeline is used (validation, privacy, overview, index).  For existing
    skills, the merged content is serialized to SKILL.md and written via
    ``ContentWriteCoordinator``.

    ``delete`` operations remove the entire skill subdirectory.
    """

    skill_processor: SkillProcessor | None = None
    viking_fs: Any = None
    vikingdb: Any = None
    memory_type: str = "skills"

    @tracer("train.policy_updater.skill.apply", ignore_result=True, ignore_args=True)
    async def apply(
        self,
        plan: PolicyUpdatePlan,
        policy_set: PolicySet,
        context: Any = None,
        *,
        transaction_handle: Any = None,
    ) -> PolicyApplyResult:
        ctx = _coerce_request_context(context)
        if ctx is None:
            raise ValueError("SkillPolicyUpdater.apply requires a request context")
        viking_fs = self.viking_fs or get_viking_fs()
        if viking_fs is None:
            raise RuntimeError("VikingFS is required to apply skill policy updates")

        updated_policy_set = _apply_items_to_snapshot(plan.items, policy_set)
        operations = _plan_to_resolved_operations(
            plan=plan,
            policy_set=policy_set,
            updated_policy_set=updated_policy_set,
        )
        if not operations.upsert_operations and not operations.delete_file_contents:
            return PolicyApplyResult(
                updated_policy_set=policy_set,
                written_uris=[],
                deleted_uris=[],
                errors=[],
                metadata={"dry_run": False, "item_count": 0, "memory_type": self.memory_type},
            )

        registry = load_skill_extract_registry()
        processor = self.skill_processor or SkillProcessor()
        updater = SkillOperationUpdater(
            registry=registry,
            skill_processor=processor,
            viking_fs=viking_fs,
        )
        result = await updater.apply_operations(operations, ctx)

        errors = [f"{uri}: {exc}" for uri, exc in result.errors]

        # Handle deletes (SkillOperationUpdater doesn't support delete ops)
        delete_errors: list[str] = []
        deleted_uris: list[str] = []
        for old_file in operations.delete_file_contents:
            if not old_file.uri:
                continue
            try:
                skill_root = _root_uri_from_skill_md(old_file.uri)
                await viking_fs.rm(skill_root, ctx=ctx, lock_handle=transaction_handle)
                deleted_uris.append(old_file.uri)
            except Exception as exc:
                delete_errors.append(f"{old_file.uri}: {exc}")

        all_errors = [*errors, *delete_errors]
        return PolicyApplyResult(
            updated_policy_set=updated_policy_set if not all_errors else policy_set,
            written_uris=list(result.written_uris + result.edited_uris),
            deleted_uris=deleted_uris,
            errors=all_errors,
            metadata={
                "dry_run": False,
                "item_count": len(plan.items),
                "memory_type": self.memory_type,
                "operation_upsert_count": len(operations.upsert_operations),
                "operation_delete_count": len(operations.delete_file_contents),
            },
        )


def _coerce_request_context(context: Any) -> RequestContext | None:
    if context is None:
        return None
    if isinstance(context, dict):
        return context.get("request_context") or context.get("ctx")
    # Try duck-typing for common context wrappers
    for attr in ("request_context", "ctx", "apply_context"):
        value = getattr(context, attr, None)
        if value is not None:
            return value
    # If it quacks like a RequestContext…
    if hasattr(context, "user_id") or hasattr(context, "account_id"):
        return context  # type: ignore[return-value]
    return None


def _apply_items_to_snapshot(
    items: list[PolicyPlanItem], policy_set: PolicySet
) -> PolicySet:
    policies_by_uri = {policy.uri: policy for policy in policy_set.policies}
    result = list(policy_set.policies)

    for item in items:
        uri = _target_uri(item, policy_set.root_uri)

        if item.kind == "delete":
            existing = policies_by_uri.get(uri) or _find_policy(
                policy_set, uri=None, name=item.target_name
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
            policy_set, uri=None, name=item.target_name
        )
        metadata = dict(existing.metadata) if existing is not None else {}
        metadata.update(item.metadata.get("patch_metadata", {}))
        metadata.setdefault("memory_type", item.memory_type or "skills")
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


def _find_policy(policy_set: PolicySet, *, uri: str | None, name: str) -> Policy | None:
    for policy in policy_set.policies:
        if uri and policy.uri == uri:
            return policy
        if not uri and policy.name == name:
            return policy
    return None


def _target_uri(item: PolicyPlanItem, root_uri: str) -> str:
    if item.target_uri:
        return item.target_uri
    skill_name = _safe_skill_dirname(item.target_name)
    return f"{root_uri.rstrip('/')}/{skill_name}/SKILL.md"


def _plan_to_resolved_operations(
    *,
    plan: PolicyUpdatePlan,
    policy_set: PolicySet,
    updated_policy_set: PolicySet,
) -> ResolvedOperations:
    upserts: list[ResolvedOperation] = []
    deletes: list[MemoryFile] = []
    errors: list[str] = []

    for item in plan.items:
        uri = _target_uri(item, policy_set.root_uri)
        current = _find_policy(policy_set, uri=uri, name=item.target_name)

        if item.kind == "delete":
            if current is not None:
                deletes.append(_policy_to_memory_file(current))
            continue

        if item.kind != "upsert":
            continue
        if item.after_content is None:
            errors.append(f"missing after_content for {item.target_name}")
            continue

        updated = _find_policy(updated_policy_set, uri=uri, name=item.target_name)
        if updated is None:
            errors.append(
                f"planned skill policy not found after simulation: {item.target_name}"
            )
            continue

        old_mf = _policy_to_memory_file(current) if current is not None else None
        # Build memory_fields in the shape expected by SkillOperationUpdater.
        # Skill schema uses "skill_name" / "description" / "content" fields.
        memory_fields: dict[str, Any] = {
            "skill_name": updated.name,
            "content": updated.content,
            "description": updated.metadata.get("description", ""),
        }
        # Carry over other metadata
        for key in ("allowed_tools", "tags"):
            value = updated.metadata.get(key)
            if value is not None:
                memory_fields[key] = value

        upserts.append(
            ResolvedOperation(
                old_memory_file_content=old_mf,
                memory_fields=memory_fields,
                memory_type=SESSION_SKILL_MEMORY_TYPE,
                uris=[uri],
            )
        )

    return ResolvedOperations(
        upsert_operations=upserts,
        delete_file_contents=deletes,
        errors=errors,
        resolved_links=[],
    )


def _policy_to_memory_file(policy: Policy | None) -> MemoryFile | None:
    if policy is None:
        return None
    # Serialize policy content + metadata into a SKILL.md-shaped MemoryFile.
    skill_dict = {
        "name": policy.name,
        "description": policy.metadata.get("description", ""),
        "content": policy.content,
        "allowed_tools": policy.metadata.get("allowed_tools", []),
        "tags": policy.metadata.get("tags", []),
    }
    serialized = SkillLoader.to_skill_md(skill_dict)
    return MemoryFile(
        uri=policy.uri,
        content=serialized,
        links=list(policy.links or []),
        backlinks=list(policy.backlinks or []),
        memory_type="skills",
        extra_fields={
            **dict(policy.metadata),
            "memory_type": "skills",
            "skill_name": policy.name,
            "version": policy.version,
            "status": policy.status,
        },
    )


def _safe_skill_dirname(name: str) -> str:
    import re

    cleaned = re.sub(r"[^a-zA-Z0-9_\-一-鿿]+", "_", name.strip()).strip("._-")
    return cleaned or "new_skill"


def _root_uri_from_skill_md(skill_md_uri: str) -> str:
    suffix = "/SKILL.md"
    if skill_md_uri.endswith(suffix):
        return skill_md_uri[: -len(suffix)]
    return skill_md_uri.rstrip("/")

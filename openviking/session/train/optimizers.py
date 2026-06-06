# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Policy optimizer implementations."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from openviking.message import Message
from openviking.server.identity import RequestContext
from openviking.session.memory.dataclass import MemoryFile
from openviking.session.memory.extract_loop import ExtractLoop
from openviking.session.memory.memory_isolation_handler import MemoryIsolationHandler
from openviking.session.memory.memory_updater import ExtractContext
from openviking.session.memory.patch_merge_context_provider import (
    PatchMergeContextProvider,
    PatchMergePatch,
)
from openviking.session.train.domain import (
    Experience,
    ExperienceSet,
    PolicyPlanItem,
    PolicyUpdatePlan,
)
from openviking.session.train.interfaces import SemanticGradient
from openviking.telemetry import tracer
from openviking_cli.utils import get_logger
from openviking_cli.utils.config import get_openviking_config

logger = get_logger(__name__)


@dataclass(slots=True)
class GroupingPolicyOptimizer:
    """Group semantic gradients into an executable patch-oriented update plan.

    This conservative first optimizer does not attempt LLM-based merge/split
    synthesis.  It groups gradients, emits diagnostics, and creates one
    ``upsert_experience`` plan item per patch gradient.  Later optimizers can
    replace this with conflict-aware merge and decomposition logic while keeping
    the same PolicyUpdater boundary.
    """

    @tracer("train.policy_optimizer.grouping.plan", ignore_result=True, ignore_args=True)
    async def plan(
        self,
        gradients: list[SemanticGradient],
        policy_set: ExperienceSet,
        context: Any = None,
    ) -> PolicyUpdatePlan:
        del context
        groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
        policy_uris = {policy.uri for policy in policy_set.policies}
        policy_names = {policy.name for policy in policy_set.policies}
        unresolved: list[dict[str, Any]] = []
        conflicts: list[dict[str, Any]] = []
        items: list[PolicyPlanItem] = []

        for idx, gradient in enumerate(gradients):
            target_uri = gradient.target_experience_uri
            target_name = gradient.target_experience_name
            key = target_uri or f"new:{target_name}"
            item = _gradient_to_dict(idx, gradient)
            groups[key].append(item)
            if target_uri and target_uri not in policy_uris:
                unresolved.append(
                    {
                        "gradient_index": idx,
                        "target_experience_uri": target_uri,
                        "reason": "target URI not found in ExperienceSet",
                    }
                )
            elif not target_uri and target_name in policy_names:
                unresolved.append(
                    {
                        "gradient_index": idx,
                        "target_experience_name": target_name,
                        "reason": "name exists but gradient has no target URI",
                    }
                )

            plan_item = _gradient_to_plan_item(gradient, policy_set)
            if plan_item is not None:
                items.append(plan_item)

        for target, target_gradients in groups.items():
            after_contents = {
                gradient["patch"]["after_content"]
                for gradient in target_gradients
                if gradient.get("patch") and gradient["patch"].get("after_content") is not None
            }
            if len(target_gradients) > 1 and len(after_contents) > 1:
                conflicts.append(
                    {
                        "target": target,
                        "gradient_count": len(target_gradients),
                        "reason": "multiple patch gradients propose different after_content",
                    }
                )

        return PolicyUpdatePlan(
            items=items,
            metadata={
                "gradient_count": len(gradients),
                "groups": [
                    {
                        "target": target,
                        "gradient_count": len(group_items),
                        "gradients": group_items,
                    }
                    for target, group_items in sorted(groups.items(), key=lambda item: item[0])
                ],
                "unresolved": unresolved,
                "conflicts": conflicts,
            },
        )


@dataclass(slots=True)
class MergeAwarePolicyOptimizerContext:
    """Context for MergeAwarePolicyOptimizer."""

    request_context: RequestContext
    messages: list[Message] = field(default_factory=list)
    strict_merge_errors: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class MergeAwarePolicyOptimizer:
    """Merge patch gradients with ExtractLoop before producing update plan items."""

    viking_fs: Any = None
    vlm: Any = None
    memory_type: str = "experiences"

    @tracer(
        "train.policy_optimizer.merge_aware.plan",
        ignore_result=True,
        ignore_args=True,
    )
    async def plan(
        self,
        gradients: list[SemanticGradient],
        policy_set: ExperienceSet,
        context: MergeAwarePolicyOptimizerContext | Any = None,
    ) -> PolicyUpdatePlan:
        if context is None or getattr(context, "request_context", None) is None:
            raise ValueError("MergeAwarePolicyOptimizerContext.request_context is required")

        groups = _group_patch_gradients(gradients)
        items: list[PolicyPlanItem] = []
        merge_errors: list[dict[str, Any]] = []
        skipped_groups: list[dict[str, Any]] = []

        fast_path_groups: list[dict[str, Any]] = []

        for target, group_gradients in groups.items():
            try:
                fast_path_item = _single_clean_patch_fast_path_item(group_gradients, policy_set)
                if fast_path_item is not None:
                    items.append(fast_path_item)
                    fast_path_groups.append(
                        {
                            "target": target,
                            "reason": "single_clean_patch",
                            "gradient_count": len(group_gradients),
                        }
                    )
                    continue

                operations = await self._run_merge_extract_loop(
                    gradients=group_gradients,
                    policy_set=policy_set,
                    context=context,
                    target=target,
                )
                group_items = _operations_to_plan_items(
                    operations=operations,
                    gradients=group_gradients,
                    policy_set=policy_set,
                    memory_type=self.memory_type,
                )
                _log_merge_output(
                    target=target,
                    operations=operations,
                    plan_items=group_items,
                    console=_merge_console_enabled(context),
                )
                if not group_items:
                    skipped_groups.append(
                        {
                            "target": target,
                            "reason": "merge_produced_no_plan_items",
                            "gradient_count": len(group_gradients),
                        }
                    )
                items.extend(group_items)
            except Exception as exc:  # pragma: no cover - defensive adapter boundary
                logger.exception("Policy patch merge failed for target %s", target)
                error = {
                    "target": target,
                    "reason": "merge_failed",
                    "error": str(exc),
                    "gradient_count": len(group_gradients),
                }
                merge_errors.append(error)
                skipped_groups.append(error)
                if getattr(context, "strict_merge_errors", False):
                    raise

        return PolicyUpdatePlan(
            items=items,
            metadata={
                "optimizer": "merge_aware",
                "memory_type": self.memory_type,
                "gradient_count": len(gradients),
                "group_count": len(groups),
                "groups": [
                    {
                        "target": target,
                        "gradient_count": len(group_gradients),
                        "gradients": [
                            _gradient_to_dict(idx, gradient)
                            for idx, gradient in enumerate(group_gradients)
                        ],
                    }
                    for target, group_gradients in sorted(groups.items(), key=lambda item: item[0])
                ],
                "fast_path_groups": fast_path_groups,
                "merge_errors": merge_errors,
                "skipped_groups": skipped_groups,
            },
        )

    @tracer(
        "train.policy_optimizer.merge_aware.extract_loop",
        ignore_result=True,
        ignore_args=True,
    )
    async def _run_merge_extract_loop(
        self,
        *,
        gradients: list[SemanticGradient],
        policy_set: ExperienceSet,
        context: MergeAwarePolicyOptimizerContext,
        target: str | None = None,
    ):
        config = get_openviking_config()
        vlm = self.vlm or config.vlm.get_vlm_instance()
        viking_fs = self.viking_fs or policy_set.viking_fs
        if viking_fs is None:
            raise RuntimeError("VikingFS is required for merge-aware policy optimization")

        extract_context = ExtractContext(list(context.messages or []))
        provider = PatchMergeContextProvider(
            memory_type=self.memory_type,
            original_file_uris=_original_file_uris(gradients, policy_set),
            patches=[_gradient_to_merge_patch(gradient) for gradient in gradients],
        )
        provider._ctx = context.request_context
        provider._viking_fs = viking_fs
        provider._extract_context = extract_context

        isolation_handler = MemoryIsolationHandler(
            context.request_context,
            extract_context,
            allowed_memory_types={self.memory_type},
        )
        isolation_handler.prepare_messages()
        provider._isolation_handler = isolation_handler

        _seed_read_file_contents(provider, gradients, policy_set)
        prefetch_messages = await provider.prefetch()
        provider.prefetch = _constant_prefetch(prefetch_messages)
        _log_merge_input(
            target=target or "unknown",
            provider=provider,
            gradients=gradients,
            prefetch_messages=prefetch_messages,
            console=_merge_console_enabled(context),
        )

        orchestrator = ExtractLoop(
            vlm=vlm,
            viking_fs=viking_fs,
            ctx=context.request_context,
            context_provider=provider,
            isolation_handler=isolation_handler,
            max_iterations=1,
        )
        operations, _ = await orchestrator.run()
        return operations


def _merge_console_enabled(context: Any) -> bool:
    metadata = getattr(context, "metadata", {}) or {}
    return bool(metadata.get("merge_trace_console", True))


def _constant_prefetch(messages: list[dict[str, Any]]):
    async def prefetch() -> list[dict[str, Any]]:
        return list(messages)

    return prefetch


def _log_merge_input(
    *,
    target: str,
    provider: PatchMergeContextProvider,
    gradients: list[SemanticGradient],
    prefetch_messages: list[dict[str, Any]],
    console: bool,
) -> None:
    lines = [
        "\n========== MergeAwarePolicyOptimizer Input =========",
        f"target: {target}",
        f"memory_type: {provider.memory_type}",
        f"original_file_uris: {provider.original_file_uris}",
        f"gradient_count: {len(gradients)}",
    ]
    for idx, gradient in enumerate(gradients):
        patch = getattr(gradient, "patch", None)
        lines.extend(
            [
                "",
                f"[Gradient {idx}]",
                f"target_experience_name: {gradient.target_experience_name}",
                f"target_experience_uri: {gradient.target_experience_uri}",
                f"base_version: {gradient.base_version}",
                f"confidence: {gradient.confidence}",
                f"evidence_trajectory_uris: {list(gradient.evidence_trajectory_uris)}",
                f"rationale: {gradient.rationale}",
            ]
        )
        if patch is not None:
            lines.extend(
                [
                    "patch.before_content:",
                    str(patch.before_content),
                    "patch.after_content:",
                    patch.after_content,
                    f"patch.metadata: {dict(patch.metadata)}",
                ]
            )
    lines.extend(["", "[Prefetch Messages]"])
    for idx, message in enumerate(prefetch_messages):
        lines.extend(
            [f"--- message {idx} role={message.get('role')} ---", str(message.get("content"))]
        )
    lines.append("===================================================\n")
    tracer.info("\n".join(lines), console=console)


def _log_merge_output(
    *,
    target: str,
    operations: Any,
    plan_items: list[PolicyPlanItem],
    console: bool,
) -> None:
    lines = [
        "\n========== MergeAwarePolicyOptimizer Output =========",
        f"target: {target}",
        "[Resolved Operations]",
        _dump_model_or_value(operations),
        "",
        "[Policy Plan Items]",
    ]
    for idx, item in enumerate(plan_items):
        lines.extend(
            [
                f"--- item {idx} ---",
                f"kind: {item.kind}",
                f"target_experience_name: {item.target_experience_name}",
                f"target_experience_uri: {item.target_experience_uri}",
                f"base_version: {item.base_version}",
                f"confidence: {item.confidence}",
                f"evidence_trajectory_uris: {item.evidence_trajectory_uris}",
                "before_content:",
                str(item.before_content),
                "after_content:",
                str(item.after_content),
                f"metadata: {item.metadata}",
            ]
        )
    lines.append("====================================================\n")
    tracer.info("\n".join(lines), console=console)


def _dump_model_or_value(value: Any) -> str:
    dumper = getattr(value, "model_dump_json", None)
    if dumper is not None:
        try:
            return str(dumper(indent=2))
        except TypeError:
            return str(dumper())
    return str(value)


def _gradient_to_dict(index: int, gradient: SemanticGradient) -> dict[str, Any]:
    result = {
        "index": index,
        "target_experience_name": gradient.target_experience_name,
        "target_experience_uri": gradient.target_experience_uri,
        "base_version": gradient.base_version,
        "rationale": gradient.rationale,
        "evidence_trajectory_uris": list(gradient.evidence_trajectory_uris),
        "confidence": gradient.confidence,
        "metadata": dict(gradient.metadata),
    }
    patch = getattr(gradient, "patch", None)
    if patch is not None:
        result["patch"] = {
            "before_content": patch.before_content,
            "after_content": patch.after_content,
            "metadata": dict(patch.metadata),
        }
    return result


def _single_clean_patch_fast_path_item(
    gradients: list[SemanticGradient],
    policy_set: ExperienceSet,
) -> PolicyPlanItem | None:
    """Bypass LLM merge for one patch whose base matches the current policy."""

    if len(gradients) != 1:
        return None
    gradient = gradients[0]
    patch = getattr(gradient, "patch", None)
    if patch is None:
        return None
    target_uri = gradient.target_experience_uri
    if not target_uri:
        return None
    current = _find_policy_by_uri(policy_set, target_uri)
    if current is None:
        return None
    if patch.before_content is None:
        return None
    if _normalize_policy_content(patch.before_content) != _normalize_policy_content(
        current.content
    ):
        return None
    item = _gradient_to_plan_item(gradient, policy_set)
    if item is not None:
        item.metadata["optimizer_fast_path"] = "single_clean_patch"
    return item


def _normalize_policy_content(content: str) -> str:
    return content.strip()


def _gradient_to_plan_item(
    gradient: SemanticGradient,
    policy_set: ExperienceSet,
) -> PolicyPlanItem | None:
    patch = getattr(gradient, "patch", None)
    if patch is None:
        return None
    target_name = gradient.target_experience_name
    target_uri = gradient.target_experience_uri
    before_content = patch.before_content
    policy_uris = {policy.uri for policy in policy_set.policies}
    if target_uri and target_uri not in policy_uris:
        superseded = _find_superseded_policy(patch.metadata.get("supersedes"), policy_set)
        if superseded is not None:
            target_name = superseded.name
            target_uri = superseded.uri
            if before_content is None:
                before_content = superseded.content
    return PolicyPlanItem(
        kind="upsert_experience",
        target_experience_name=target_name,
        target_experience_uri=target_uri,
        before_content=before_content,
        after_content=patch.after_content,
        base_version=gradient.base_version,
        confidence=gradient.confidence,
        evidence_trajectory_uris=list(gradient.evidence_trajectory_uris),
        metadata={
            "rationale": gradient.rationale,
            "gradient_metadata": dict(gradient.metadata),
            "patch_metadata": dict(patch.metadata),
        },
    )


def _group_patch_gradients(gradients: list[SemanticGradient]) -> dict[str, list[SemanticGradient]]:
    groups: dict[str, list[SemanticGradient]] = defaultdict(list)
    for gradient in gradients:
        if getattr(gradient, "patch", None) is None:
            continue
        key = gradient.target_experience_uri or f"new:{gradient.target_experience_name}"
        groups[key].append(gradient)
    return groups


def _gradient_to_merge_patch(gradient: SemanticGradient) -> PatchMergePatch:
    patch = getattr(gradient, "patch", None)
    if patch is None:
        raise ValueError(f"SemanticGradient has no patch: {gradient.target_experience_name}")
    return PatchMergePatch(
        target_name=gradient.target_experience_name,
        target_uri=gradient.target_experience_uri,
        before_content=patch.before_content,
        after_content=patch.after_content,
        metadata={
            "base_version": gradient.base_version,
            "rationale": gradient.rationale,
            "evidence_trajectory_uris": list(gradient.evidence_trajectory_uris),
            "confidence": gradient.confidence,
            "gradient_metadata": dict(gradient.metadata),
            "patch_metadata": dict(patch.metadata),
        },
    )


def _original_file_uris(
    gradients: list[SemanticGradient],
    policy_set: ExperienceSet,
) -> list[str]:
    uris: list[str] = []
    for gradient in gradients:
        uri = gradient.target_experience_uri
        if not uri:
            superseded = _find_superseded_policy(
                getattr(getattr(gradient, "patch", None), "metadata", {}).get("supersedes"),
                policy_set,
            )
            uri = superseded.uri if superseded is not None else None
        if uri and uri not in uris:
            uris.append(uri)
    return uris


def _seed_read_file_contents(
    provider: PatchMergeContextProvider,
    gradients: list[SemanticGradient],
    policy_set: ExperienceSet,
) -> None:
    for policy in policy_set.policies:
        if policy.uri in provider.original_file_uris:
            provider.read_file_contents[policy.uri] = _experience_to_memory_file(policy)
    for gradient in gradients:
        patch = getattr(gradient, "patch", None)
        if patch is None or gradient.target_experience_uri in provider.read_file_contents:
            continue
        if gradient.target_experience_uri and patch.before_content is not None:
            provider.read_file_contents[gradient.target_experience_uri] = MemoryFile(
                uri=gradient.target_experience_uri,
                content=patch.before_content,
                memory_type="experiences",
                extra_fields={
                    "experience_name": gradient.target_experience_name,
                    "version": gradient.base_version or 1,
                    "status": "production",
                },
            )


def _experience_to_memory_file(experience: Experience) -> MemoryFile:
    return MemoryFile(
        uri=experience.uri,
        content=experience.content,
        memory_type="experiences",
        extra_fields={
            **dict(experience.metadata),
            "memory_type": "experiences",
            "experience_name": experience.name,
            "version": experience.version,
            "status": experience.status,
        },
    )


def _operations_to_plan_items(
    *,
    operations: Any,
    gradients: list[SemanticGradient],
    policy_set: ExperienceSet,
    memory_type: str,
) -> list[PolicyPlanItem]:
    items: list[PolicyPlanItem] = []
    evidence_uris = sorted(
        {uri for gradient in gradients for uri in list(gradient.evidence_trajectory_uris)}
    )
    confidence_values = [float(gradient.confidence) for gradient in gradients]
    confidence = max(confidence_values) if confidence_values else None

    for op in getattr(operations, "upsert_operations", []) or []:
        if getattr(op, "memory_type", None) != memory_type:
            continue
        fields = dict(getattr(op, "memory_fields", {}) or {})
        after_content = str(fields.get("content") or "")
        if not after_content.strip():
            continue
        target_name = str(fields.get("experience_name") or _fallback_experience_name(op))
        target_uri = _first_uri(getattr(op, "uris", []) or [])
        old_file = getattr(op, "old_memory_file_content", None)
        before_content = old_file.plain_content() if old_file is not None else None
        if before_content is None and target_uri:
            policy = _find_policy_by_uri(policy_set, target_uri)
            before_content = policy.content if policy is not None else None
        items.append(
            PolicyPlanItem(
                kind="upsert_experience",
                target_experience_name=target_name,
                target_experience_uri=target_uri,
                before_content=before_content,
                after_content=after_content,
                base_version=_base_version_from_old_file_or_policy(
                    old_file,
                    target_uri,
                    policy_set,
                ),
                confidence=confidence,
                evidence_trajectory_uris=evidence_uris,
                metadata={
                    "rationale": "PatchMergeContextProvider merged semantic gradients via ExtractLoop.",
                    "merge_gradient_count": len(gradients),
                    "merge_memory_fields": fields,
                },
            )
        )

    for old_file in getattr(operations, "delete_file_contents", []) or []:
        target_uri = old_file.uri
        target_name = str(
            (old_file.extra_fields or {}).get("experience_name")
            or (target_uri.rstrip("/").split("/")[-1].removesuffix(".md") if target_uri else "")
        )
        items.append(
            PolicyPlanItem(
                kind="delete_experience",
                target_experience_name=target_name,
                target_experience_uri=target_uri,
                before_content=old_file.plain_content(),
                after_content=None,
                confidence=confidence,
                evidence_trajectory_uris=evidence_uris,
                metadata={
                    "rationale": "PatchMergeContextProvider merge requested memory deletion.",
                    "merge_gradient_count": len(gradients),
                },
            )
        )
    return items


def _find_policy_by_uri(policy_set: ExperienceSet, uri: str) -> Experience | None:
    for policy in policy_set.policies:
        if policy.uri == uri:
            return policy
    return None


def _base_version_from_old_file_or_policy(
    old_file: Any, target_uri: str | None, policy_set: ExperienceSet
) -> int | None:
    if old_file is not None:
        version = _safe_int((getattr(old_file, "extra_fields", {}) or {}).get("version"))
        if version is not None:
            return version
    if target_uri:
        policy = _find_policy_by_uri(policy_set, target_uri)
        return policy.version if policy is not None else None
    return None


def _safe_int(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _first_uri(uris: list[str]) -> str | None:
    return uris[0] if uris else None


def _fallback_experience_name(op: Any) -> str:
    uri = _first_uri(getattr(op, "uris", []) or [])
    if uri:
        return uri.rstrip("/").split("/")[-1].removesuffix(".md")
    return "unknown_experience"


def _find_superseded_policy(supersedes: Any, policy_set: ExperienceSet):
    names: list[str]
    if isinstance(supersedes, str):
        names = [supersedes]
    elif isinstance(supersedes, list):
        names = [str(item) for item in supersedes]
    else:
        names = []
    for name in names:
        for policy in policy_set.policies:
            if policy.name == name:
                return policy
    return None

# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Policy optimizer implementations."""

from __future__ import annotations

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
class PatchMergePolicyOptimizerContext:
    """Context for PatchMergePolicyOptimizer."""

    request_context: RequestContext
    messages: list[Message] = field(default_factory=list)


@dataclass(slots=True)
class PatchMergePolicyOptimizer:
    """Merge patch gradients with ExtractLoop before producing update plan items."""

    viking_fs: Any = None
    vlm: Any = None
    memory_type: str = "experiences"

    @tracer(
        "train.policy_optimizer.patch_merge.plan",
        ignore_result=True,
        ignore_args=True,
    )
    async def plan(
        self,
        gradients: list[SemanticGradient],
        policy_set: ExperienceSet,
        context: PatchMergePolicyOptimizerContext | None = None,
    ) -> PolicyUpdatePlan:
        if context is None:
            raise ValueError("PatchMergePolicyOptimizerContext.request_context is required")

        patch_gradients = list(gradients)
        if not patch_gradients:
            return PolicyUpdatePlan(
                items=[],
                metadata={
                    "optimizer": "patch_merge",
                    "memory_type": self.memory_type,
                    "gradient_count": len(gradients),
                    "patch_gradient_count": 0,
                },
            )

        operations = await self._run_merge_extract_loop(
            gradients=patch_gradients,
            policy_set=policy_set,
            context=context,
        )
        items = _operations_to_plan_items(
            operations=operations,
            gradients=patch_gradients,
            policy_set=policy_set,
            memory_type=self.memory_type,
        )
        _log_merge_output(
            target="all",
            operations=operations,
            plan_items=items,
            console=False,
        )

        return PolicyUpdatePlan(
            items=items,
            metadata={
                "optimizer": "patch_merge",
                "memory_type": self.memory_type,
                "gradient_count": len(gradients),
                "patch_gradient_count": len(patch_gradients),
                "gradients": [
                    _gradient_to_dict(idx, gradient)
                    for idx, gradient in enumerate(patch_gradients)
                ],
            },
        )

    @tracer(
        "train.policy_optimizer.patch_merge.extract_loop",
        ignore_result=True,
        ignore_args=True,
    )
    async def _run_merge_extract_loop(
        self,
        *,
        gradients: list[SemanticGradient],
        policy_set: ExperienceSet,
        context: PatchMergePolicyOptimizerContext,
    ):
        config = get_openviking_config()
        vlm = self.vlm or config.vlm.get_vlm_instance()
        viking_fs = self.viking_fs or policy_set.viking_fs
        if viking_fs is None:
            raise RuntimeError("VikingFS is required for patch-merge policy optimization")

        extract_context = ExtractContext(list(context.messages or []))
        provider = PatchMergeContextProvider(
            memory_type=self.memory_type,
            required_file_uris=_required_file_uris(gradients, policy_set),
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
            target="all",
            provider=provider,
            gradients=gradients,
            prefetch_messages=prefetch_messages,
            console=False,
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
        "\n========== PatchMergePolicyOptimizer Input =========",
        f"target: {target}",
        f"memory_type: {provider.memory_type}",
        f"required_file_uris: {provider.required_file_uris}",
        f"gradient_count: {len(gradients)}",
    ]
    for idx, gradient in enumerate(gradients):
        before_file = gradient.before_file
        after_file = gradient.after_file
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
        if after_file is not None:
            lines.extend(
                [
                    "before_file:",
                    _memory_file_summary(before_file),
                    "after_file:",
                    _memory_file_summary(after_file),
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
        "\n========== PatchMergePolicyOptimizer Output =========",
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

def _memory_file_summary(file: MemoryFile | None) -> str:
    if file is None:
        return "None"
    return _dump_model_or_value(
        {
            "uri": file.uri,
            "memory_type": file.memory_type,
            "content": file.content,
            "links": file.links,
            "backlinks": file.backlinks,
            "extra_fields": file.extra_fields,
        }
    )

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
    before_file = gradient.before_file
    after_file = gradient.after_file
    if before_file is not None:
        result["before_file"] = _memory_file_to_dict(before_file)
    if after_file is not None:
        result["after_file"] = _memory_file_to_dict(after_file)
    return result

def _memory_file_to_dict(file: MemoryFile) -> dict[str, Any]:
    return {
        "uri": file.uri,
        "memory_type": file.memory_type,
        "content": file.content,
        "links": list(file.links or []),
        "backlinks": list(file.backlinks or []),
        "extra_fields": dict(file.extra_fields or {}),
    }

def _gradient_to_merge_patch(gradient: SemanticGradient) -> PatchMergePatch:
    return PatchMergePatch(
        before_file=gradient.before_file,
        after_file=gradient.after_file,
        metadata={
            "base_version": gradient.base_version,
            "rationale": gradient.rationale,
            "evidence_trajectory_uris": list(gradient.evidence_trajectory_uris),
            "confidence": gradient.confidence,
            "gradient_metadata": _compact_gradient_metadata(gradient.metadata),
        },
    )


def _compact_gradient_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    compact = dict(metadata)
    memory_fields = compact.get("memory_fields")
    if isinstance(memory_fields, dict) and "content" in memory_fields:
        compact["memory_fields"] = {
            key: value for key, value in memory_fields.items() if key != "content"
        }
    return compact


def _required_file_uris(
    gradients: list[SemanticGradient],
    policy_set: ExperienceSet,
) -> list[str]:
    uris: list[str] = []
    for gradient in gradients:
        uri = gradient.target_experience_uri
        if not uri:
            superseded = _find_superseded_policy(_gradient_supersedes(gradient), policy_set)
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
        if policy.uri in provider.required_file_uris:
            provider.read_file_contents[policy.uri] = _experience_to_memory_file(policy)
    for gradient in gradients:
        before_file = gradient.before_file
        target_uri = gradient.target_experience_uri
        if before_file is None or target_uri in provider.read_file_contents:
            continue
        if target_uri:
            provider.read_file_contents[target_uri] = before_file

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

def _gradient_supersedes(gradient: SemanticGradient) -> Any:
    metadata = dict(getattr(gradient, "metadata", {}) or {})
    if metadata.get("supersedes") is not None:
        return metadata.get("supersedes")
    return (gradient.after_file.extra_fields or {}).get("supersedes")

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

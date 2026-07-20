# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Policy optimizer implementations."""

from __future__ import annotations

import json
from collections import defaultdict
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any

from openviking.message import Message
from openviking.server.identity import RequestContext
from openviking.session.memory.dataclass import MemoryFile, MemoryTypeSchema, StoredLink
from openviking.session.memory.extract_loop import ExtractLoop, PostValidationRetryDecision
from openviking.session.memory.memory_isolation_handler import MemoryIsolationHandler
from openviking.session.memory.memory_type_registry import (
    MemoryTypeRegistry,
    create_default_registry,
)
from openviking.session.memory.memory_updater import ExtractContext, render_operation_after_file
from openviking.session.memory.patch_merge_context_provider import (
    PatchMergeContextProvider,
    PatchMergePatch,
)
from openviking.session.train.domain import (
    Policy,
    PolicyPlanItem,
    PolicySet,
    PolicyUpdatePlan,
    RolloutAnalysis,
)
from openviking.session.train.gates import (
    GateRunner,
    build_gate_retry_instruction,
    candidate_retry_draft,
)
from openviking.session.train.interfaces import SemanticGradient
from openviking.session.train.utils import first_uri, safe_int
from openviking.telemetry import tracer
from openviking_cli.utils import get_logger
from openviking_cli.utils.config import get_openviking_config

logger = get_logger(__name__)

_EXPERIENCE_PLAN_POST_VALIDATION_MAX_RETRIES = 3


@dataclass(slots=True)
class PatchMergePolicyOptimizerContext:
    """Context for PatchMergePolicyOptimizer."""

    request_context: RequestContext
    messages: list[Message] = field(default_factory=list)
    analyses: list[RolloutAnalysis] = field(default_factory=list)
    gate_runner: GateRunner | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class PatchMergePolicyOptimizer:
    """Merge patch gradients with ExtractLoop before producing update plan items."""

    viking_fs: Any = None
    vlm: Any = None
    memory_type: str = "experiences"
    registry: MemoryTypeRegistry | None = None

    def _get_registry(self) -> MemoryTypeRegistry:
        if self.registry is None:
            self.registry = create_default_registry()
        return self.registry

    def _get_schema(self) -> MemoryTypeSchema:
        schema = self._get_registry().get(self.memory_type)
        if schema is None or not schema.enabled:
            raise ValueError(f"Memory schema not found or disabled: {self.memory_type}")
        return schema

    @tracer(
        "train.policy_optimizer.patch_merge.plan",
        ignore_result=True,
        ignore_args=True,
    )
    async def plan(
        self,
        gradients: list[SemanticGradient],
        policy_set: PolicySet,
        context: PatchMergePolicyOptimizerContext | None = None,
    ) -> PolicyUpdatePlan:
        if context is None:
            raise ValueError("PatchMergePolicyOptimizerContext.request_context is required")

        context.metadata.pop("gate_retry_reports", None)
        context.metadata.pop("post_validation_retries", None)
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
            schema=self._get_schema(),
        )
        _log_merge_output(
            target="all",
            operations=operations,
            plan_items=items,
            console=False,
        )

        metadata = {
            "optimizer": "patch_merge",
            "memory_type": self.memory_type,
            "gradient_count": len(gradients),
            "patch_gradient_count": len(patch_gradients),
            "gradients": [
                _gradient_to_dict(idx, gradient) for idx, gradient in enumerate(patch_gradients)
            ],
        }
        gate_retry_reports = list(context.metadata.get("gate_retry_reports", []) or [])
        gate_retry_events = list(context.metadata.get("post_validation_retries", []) or [])
        if gate_retry_reports:
            metadata["gate_reports"] = gate_retry_reports
            metadata["gate_retry_reports"] = gate_retry_reports
        if gate_retry_events:
            metadata["post_validation_retries"] = gate_retry_events

        return PolicyUpdatePlan(items=items, metadata=metadata)

    @tracer(
        "train.policy_optimizer.patch_merge.extract_loop",
        ignore_result=True,
        ignore_args=True,
    )
    async def _run_merge_extract_loop(
        self,
        *,
        gradients: list[SemanticGradient],
        policy_set: PolicySet,
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
            patches=[
                _gradient_to_merge_patch(gradient, schema=self._get_schema())
                for gradient in gradients
            ],
        )
        provider._registry = self._get_registry()
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

        retained_valid_upserts: dict[tuple[str, str], Any] = {}
        retained_valid_deletes: dict[str, Any] = {}
        gate_retry_history: list[Any] = []

        async def post_validation_hook(
            operations: Any,
            retry_count: int,
            *,
            messages: list[dict[str, Any]] | None = None,
            latest_draft: Any = None,
        ):
            gate_runner = context.gate_runner
            if gate_runner is None or self.memory_type != "experiences":
                return None
            items = _operations_to_plan_items(
                operations=operations,
                gradients=gradients,
                policy_set=policy_set,
                memory_type=self.memory_type,
                schema=self._get_schema(),
            )
            gated, report = await gate_runner.filter_plan(
                items,
                analyses=list(context.analyses or []),
                policy_set=policy_set,
            )
            _remember_gated_plan_operations(
                operations,
                gated,
                schema=self._get_schema(),
                retained_upserts=retained_valid_upserts,
                retained_deletes=retained_valid_deletes,
            )
            instruction = build_gate_retry_instruction(
                report,
                prior_reports=gate_retry_history,
            )
            if not instruction:
                if report.rejected_count or (
                    retry_count and (retained_valid_upserts or retained_valid_deletes)
                ):
                    _restore_gated_plan_operations(
                        operations,
                        retained_upserts=retained_valid_upserts,
                        retained_deletes=retained_valid_deletes,
                    )
                if retry_count:
                    context.metadata.setdefault("post_validation_retries", []).append(
                        _post_validation_retry_event(
                            stage="post_plan",
                            retry_index=retry_count,
                            report=report.to_dict(),
                            instruction="",
                            final_outcome="passed_after_retry",
                            candidate_draft=latest_draft,
                        )
                    )
                return None
            report_dict = report.to_dict()
            gate_retry_history.append(report)
            context.metadata.setdefault("gate_retry_reports", []).append(report_dict)
            final_outcome = (
                "discarded_after_max_retries"
                if retry_count >= _EXPERIENCE_PLAN_POST_VALIDATION_MAX_RETRIES
                else "retry_requested"
            )
            context.metadata.setdefault("post_validation_retries", []).append(
                _post_validation_retry_event(
                    stage="post_plan",
                    retry_index=retry_count,
                    report=report_dict,
                    instruction=instruction,
                    final_outcome=final_outcome,
                    candidate_draft=latest_draft,
                )
            )
            if retry_count >= _EXPERIENCE_PLAN_POST_VALIDATION_MAX_RETRIES:
                if retained_valid_upserts or retained_valid_deletes:
                    _restore_gated_plan_operations(
                        operations,
                        retained_upserts=retained_valid_upserts,
                        retained_deletes=retained_valid_deletes,
                    )
                    event = context.metadata["post_validation_retries"][-1]
                    event["final_outcome"] = "accepted_valid_subset_after_max_retries"
                    event["retained_count"] = len(retained_valid_upserts) + len(
                        retained_valid_deletes
                    )
                    return None
                return PostValidationRetryDecision(discard=True)
            return PostValidationRetryDecision(
                retry=True,
                instruction=instruction,
                include_latest_draft=True,
                latest_draft_override=candidate_retry_draft(
                    latest_draft,
                    target_names=set(report.retriable_rejected_targets()),
                ),
            )

        orchestrator = ExtractLoop(
            vlm=vlm,
            viking_fs=viking_fs,
            ctx=context.request_context,
            context_provider=provider,
            isolation_handler=isolation_handler,
            max_iterations=1,
            thinking=self.memory_type in {"trajectories", "experiences"},
            post_validation_hook=post_validation_hook,
            max_post_validation_retries=_EXPERIENCE_PLAN_POST_VALIDATION_MAX_RETRIES,
        )
        operations, _ = await orchestrator.run()
        return operations


def _retain_gated_plan_operations(
    operations: Any,
    gated: list[PolicyPlanItem],
    *,
    schema: MemoryTypeSchema,
) -> None:
    """Keep independently valid plan operations when a sibling exhausts retries."""

    allowed_upserts = {
        (
            str(item.target_uri or ""),
            str(item.target_name or ""),
            str(item.after_content or ""),
        )
        for item in gated
        if item.kind == "upsert"
    }
    allowed_deletes = {
        str(item.target_uri or "") for item in gated if item.kind == "delete" and item.target_uri
    }

    retained_upserts = []
    for operation in getattr(operations, "upsert_operations", []) or []:
        if getattr(operation, "memory_type", None) != "experiences":
            retained_upserts.append(operation)
            continue
        fields = dict(getattr(operation, "memory_fields", {}) or {})
        target_uri = str(first_uri(getattr(operation, "uris", []) or []) or "")
        target_name = str(
            fields.get("experience_name")
            or fields.get("name")
            or _fallback_policy_name(operation, memory_type="experiences")
        )
        after_content = render_operation_after_file(operation, schema=schema).content
        if (target_uri, target_name, after_content) in allowed_upserts:
            retained_upserts.append(operation)

    operations.upsert_operations = retained_upserts
    operations.delete_file_contents = [
        memory_file
        for memory_file in (getattr(operations, "delete_file_contents", []) or [])
        if str(getattr(memory_file, "uri", "") or "") in allowed_deletes
    ]


def _remember_gated_plan_operations(
    operations: Any,
    gated: list[PolicyPlanItem],
    *,
    schema: MemoryTypeSchema,
    retained_upserts: dict[tuple[str, str], Any],
    retained_deletes: dict[str, Any],
) -> None:
    """Carry independently valid plan items across model repair attempts."""

    selected = deepcopy(operations)
    _retain_gated_plan_operations(selected, gated, schema=schema)
    for operation in getattr(selected, "upsert_operations", []) or []:
        if getattr(operation, "memory_type", None) != "experiences":
            continue
        fields = dict(getattr(operation, "memory_fields", {}) or {})
        target_uri = str(first_uri(getattr(operation, "uris", []) or []) or "")
        target_name = str(
            fields.get("experience_name")
            or fields.get("name")
            or _fallback_policy_name(operation, memory_type="experiences")
        )
        retained_upserts[(target_uri, target_name)] = operation
        retained_deletes.pop(target_uri, None)
    for memory_file in getattr(selected, "delete_file_contents", []) or []:
        target_uri = str(getattr(memory_file, "uri", "") or "")
        if target_uri:
            for key in [key for key in retained_upserts if key[0] == target_uri]:
                retained_upserts.pop(key, None)
            retained_deletes[target_uri] = memory_file


def _restore_gated_plan_operations(
    operations: Any,
    *,
    retained_upserts: dict[tuple[str, str], Any],
    retained_deletes: dict[str, Any],
) -> None:
    """Replace the latest partial draft with all candidates already proven valid."""

    non_experience = [
        operation
        for operation in (getattr(operations, "upsert_operations", []) or [])
        if getattr(operation, "memory_type", None) != "experiences"
    ]
    operations.upsert_operations = non_experience + list(retained_upserts.values())
    operations.delete_file_contents = list(retained_deletes.values())


def _post_validation_retry_event(
    *,
    stage: str,
    retry_index: int,
    report: dict[str, Any],
    instruction: str,
    final_outcome: str = "retry_requested",
    candidate_draft: Any = None,
) -> dict[str, Any]:
    return {
        "stage": stage,
        "retry_index": retry_index,
        "evaluated_count": int(report.get("evaluated_count") or 0),
        "allowed_count": int(report.get("allowed_count") or 0),
        "rejected_count": int(report.get("rejected_count") or 0),
        "warning_count": int(report.get("warning_count") or 0),
        "retriable": bool(str(instruction or "").strip()),
        "final_outcome": final_outcome,
        "instruction_preview": _preview_instruction(instruction),
        "gate_report": report,
        "candidate_preview": _preview_candidate(candidate_draft),
    }


def _preview_instruction(instruction: str, *, limit: int = 500) -> str:
    text = " ".join(str(instruction or "").split())
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def _preview_candidate(candidate: Any, *, limit: int = 4000) -> str:
    if candidate is None:
        return ""
    try:
        text = json.dumps(candidate, ensure_ascii=False, default=str, sort_keys=True)
    except (TypeError, ValueError):
        text = str(candidate)
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


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
                f"target_name: {gradient.target_name}",
                f"target_uri: {gradient.target_uri}",
                f"base_version: {gradient.base_version}",
                f"confidence: {gradient.confidence}",
                f"links: {_links_to_dicts(gradient.links)}",
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
                f"memory_type: {item.memory_type}",
                f"target_name: {item.target_name}",
                f"target_uri: {item.target_uri}",
                f"base_version: {item.base_version}",
                f"confidence: {item.confidence}",
                f"links: {_links_to_dicts(item.links)}",
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
        "target_name": gradient.target_name,
        "target_uri": gradient.target_uri,
        "base_version": gradient.base_version,
        "rationale": gradient.rationale,
        "links": _links_to_dicts(gradient.links),
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


def _links_to_dicts(links: list[StoredLink] | None) -> list[dict[str, Any]]:
    return [link.model_dump() for link in links or []]


def _gradient_to_merge_patch(
    gradient: SemanticGradient,
    *,
    schema: MemoryTypeSchema,
) -> PatchMergePatch:
    return PatchMergePatch(
        before_file=gradient.before_file,
        after_file=gradient.after_file,
        metadata={
            "base_version": gradient.base_version,
            "rationale": gradient.rationale,
            "links": _links_to_dicts(gradient.links),
            "confidence": gradient.confidence,
            "gradient_metadata": _compact_gradient_metadata(gradient.metadata, schema=schema),
        },
    )


def _compact_gradient_metadata(
    metadata: dict[str, Any],
    *,
    schema: MemoryTypeSchema,
) -> dict[str, Any]:
    compact = dict(metadata)
    memory_fields = compact.get("memory_fields")
    hidden_content_fields = {"content", *schema.content_field_names()}
    if isinstance(memory_fields, dict) and hidden_content_fields.intersection(memory_fields):
        compact["memory_fields"] = {
            key: value for key, value in memory_fields.items() if key not in hidden_content_fields
        }
    return compact


def _required_file_uris(
    gradients: list[SemanticGradient],
    policy_set: PolicySet,
) -> list[str]:
    uris: list[str] = []
    for gradient in gradients:
        uri = gradient.target_uri
        if not uri:
            superseded = _find_superseded_policy(_gradient_supersedes(gradient), policy_set)
            uri = superseded.uri if superseded is not None else None
        if uri and uri not in uris:
            uris.append(uri)
    return uris


def _seed_read_file_contents(
    provider: PatchMergeContextProvider,
    gradients: list[SemanticGradient],
    policy_set: PolicySet,
) -> None:
    for policy in policy_set.policies:
        if policy.uri in provider.required_file_uris:
            provider.read_file_contents[policy.uri] = _policy_to_memory_file(
                policy, memory_type=provider.memory_type
            )
    for gradient in gradients:
        before_file = gradient.before_file
        target_uri = gradient.target_uri
        if before_file is None or target_uri in provider.read_file_contents:
            continue
        if target_uri:
            provider.read_file_contents[target_uri] = before_file


def _policy_to_memory_file(policy: Policy, *, memory_type: str = "experiences") -> MemoryFile:
    name_field = _name_field_for_memory_type(memory_type)
    extra_fields = dict(policy.metadata)
    extra_fields["memory_type"] = memory_type
    extra_fields[name_field] = policy.name
    extra_fields.setdefault("version", policy.version)
    extra_fields.setdefault("status", policy.status)
    return MemoryFile(
        uri=policy.uri,
        content=policy.content,
        links=list(policy.links or []),
        backlinks=list(policy.backlinks or []),
        memory_type=memory_type,
        extra_fields=extra_fields,
    )


def _operations_to_plan_items(
    *,
    operations: Any,
    gradients: list[SemanticGradient],
    policy_set: PolicySet,
    memory_type: str,
    schema: MemoryTypeSchema,
) -> list[PolicyPlanItem]:
    items: list[PolicyPlanItem] = []
    source_links_by_target = _source_trajectory_links_by_target(gradients, policy_set)
    superseded_policies = _superseded_policies_for_gradients(gradients, policy_set)
    confidence_values = [float(gradient.confidence) for gradient in gradients]
    confidence = max(confidence_values) if confidence_values else None
    name_field = _name_field_for_memory_type(memory_type)

    upsert_output_count = _upsert_output_count(
        operations,
        memory_type=memory_type,
        schema=schema,
    )
    single_source_trajectory = _source_trajectory_count(source_links_by_target) == 1
    replacement_source_uris_by_target = _replacement_source_uris_by_target(operations)
    upsert_target_uris: set[str] = set()
    for op in getattr(operations, "upsert_operations", []) or []:
        if getattr(op, "memory_type", None) != memory_type:
            continue
        fields = dict(getattr(op, "memory_fields", {}) or {})
        old_file = getattr(op, "old_memory_file_content", None)
        after_file = render_operation_after_file(op, schema=schema)
        plan_fields = dict(fields)
        for field_name in schema.content_field_names():
            if field_name == "content":
                continue
            plan_fields.pop(field_name, None)
            if field_name in after_file.extra_fields:
                plan_fields[field_name] = after_file.extra_fields[field_name]
        if not _memory_file_has_schema_content(after_file, schema=schema):
            continue
        after_content = after_file.content
        if not after_content.strip():
            continue
        target_name = str(
            fields.get(name_field)
            or fields.get("name")
            or _fallback_policy_name(op, memory_type=memory_type)
        )
        target_uri = first_uri(getattr(op, "uris", []) or [])
        before_content = old_file.plain_content() if old_file is not None else None
        if before_content is None and target_uri:
            policy = _find_policy_by_uri(policy_set, target_uri)
            before_content = policy.content if policy is not None else None
        item_links = _source_trajectory_links_for_plan_item(
            target_uri=target_uri or "",
            target_name=target_name,
            before_content=before_content,
            after_content=after_content,
            trigger_code=str(fields.get("trigger_code") or ""),
            source_links_by_target=source_links_by_target,
            replacement_source_uris=replacement_source_uris_by_target.get(
                target_uri or "",
                [],
            ),
            include_all_sources=(upsert_output_count == 1 or single_source_trajectory),
        )
        items.append(
            PolicyPlanItem(
                kind="upsert",
                memory_type=memory_type,
                target_name=target_name,
                target_uri=target_uri,
                before_content=before_content,
                after_content=after_content,
                base_version=_base_version_from_old_file_or_policy(
                    old_file,
                    target_uri,
                    policy_set,
                ),
                confidence=confidence,
                links=item_links,
                metadata={
                    "rationale": "PatchMergeContextProvider merged semantic gradients via ExtractLoop.",
                    "merge_gradient_count": len(gradients),
                    "merge_memory_fields": plan_fields,
                    "superseded_experience_uris": [policy.uri for policy in superseded_policies],
                    **_plan_quality_review_metadata(
                        memory_type=memory_type,
                        before_content=before_content,
                        after_content=after_content,
                        links=item_links,
                        gradients=gradients,
                    ),
                },
            )
        )
        if target_uri:
            upsert_target_uris.add(target_uri)

    delete_uris: set[str] = set()
    for old_file in getattr(operations, "delete_file_contents", []) or []:
        target_uri = old_file.uri
        target_name = str(
            (old_file.extra_fields or {}).get(name_field)
            or (target_uri.rstrip("/").split("/")[-1].removesuffix(".md") if target_uri else "")
        )
        if not target_uri or target_uri in upsert_target_uris:
            continue
        items.append(
            PolicyPlanItem(
                kind="delete",
                memory_type=memory_type,
                target_name=target_name,
                target_uri=target_uri,
                before_content=old_file.plain_content(),
                after_content=None,
                confidence=confidence,
                links=_source_trajectory_links_for_plan_item(
                    target_uri=target_uri,
                    target_name=target_name,
                    before_content=old_file.plain_content(),
                    after_content=None,
                    trigger_code=str((old_file.extra_fields or {}).get("trigger_code") or ""),
                    source_links_by_target=source_links_by_target,
                    replacement_source_uris=[],
                ),
                metadata={
                    "rationale": "PatchMergeContextProvider merge requested memory deletion.",
                    "merge_gradient_count": len(gradients),
                },
            )
        )
        delete_uris.add(target_uri)

    for policy in superseded_policies:
        if policy.uri in upsert_target_uris or policy.uri in delete_uris:
            continue
        items.append(
            PolicyPlanItem(
                kind="delete",
                memory_type=memory_type,
                target_name=policy.name,
                target_uri=policy.uri,
                before_content=policy.content,
                after_content=None,
                base_version=policy.version,
                confidence=confidence,
                links=_source_trajectory_links_from_experience(policy),
                metadata={
                    "rationale": "Superseded by broader experience from semantic gradient.",
                    "merge_gradient_count": len(gradients),
                    "superseded_by": [
                        item.target_uri or item.target_name
                        for item in items
                        if item.kind == "upsert"
                    ],
                },
            )
        )
        delete_uris.add(policy.uri)
    return items


def _plan_quality_review_metadata(
    *,
    memory_type: str,
    before_content: str | None,
    after_content: str,
    links: list[StoredLink],
    gradients: list[SemanticGradient],
) -> dict[str, Any]:
    """Describe whether final merge output needs an additional semantic review."""

    if memory_type != "experiences":
        return {
            "plan_quality_review_required": False,
            "plan_quality_review_reason": "not_experience",
        }
    normalized_after = _normalized_policy_content(after_content)
    normalized_before = _normalized_policy_content(before_content or "")
    if before_content is not None and normalized_after == normalized_before:
        return {
            "plan_quality_review_required": False,
            "plan_quality_review_reason": "content_unchanged",
        }
    source_trajectory_uris = {
        str(getattr(link, "to_uri", "") or "")
        for link in links
        if str(getattr(link, "link_type", "") or "") == "derived_from"
        and str(getattr(link, "to_uri", "") or "")
    }
    if len(source_trajectory_uris) > 1:
        return {
            "plan_quality_review_required": True,
            "plan_quality_review_reason": "multiple_sources_merged",
        }
    if before_content is not None:
        return {
            "plan_quality_review_required": True,
            "plan_quality_review_reason": "existing_experience_changed",
        }
    source_contents: set[str] = set()
    for gradient in gradients:
        after_file = getattr(gradient, "after_file", None)
        if after_file is None:
            continue
        gradient_source_uris = {
            str(getattr(link, "to_uri", "") or "")
            for link in list(getattr(gradient, "links", []) or [])
            if str(getattr(link, "link_type", "") or "") == "derived_from"
            and str(getattr(link, "to_uri", "") or "")
        }
        if source_trajectory_uris and not source_trajectory_uris.intersection(gradient_source_uris):
            continue
        source_contents.add(_normalized_policy_content(after_file.plain_content()))
    if normalized_after in source_contents:
        return {
            "plan_quality_review_required": False,
            "plan_quality_review_reason": "single_candidate_unchanged",
        }
    return {
        "plan_quality_review_required": True,
        "plan_quality_review_reason": "materially_rewritten",
    }


def _normalized_policy_content(content: str) -> str:
    return "\n".join(line.rstrip() for line in str(content or "").strip().splitlines())


def _name_field_for_memory_type(memory_type: str) -> str:
    """Return the extra_fields key for the policy name in a given memory type."""
    if memory_type == "experiences":
        return "experience_name"
    if memory_type == "skills":
        return "skill_name"
    if memory_type.endswith("s"):
        return f"{memory_type[:-1]}_name"
    return f"{memory_type}_name"


def _fallback_policy_name(op: Any, *, memory_type: str) -> str:
    uri = first_uri(getattr(op, "uris", []) or [])
    if uri:
        # For skills: path/to/skills/my_skill/SKILL.md → my_skill
        if memory_type == "skills" and uri.endswith("/SKILL.md"):
            parts = uri.rstrip("/").split("/")
            if len(parts) >= 2:
                return parts[-2]
        return uri.rstrip("/").split("/")[-1].removesuffix(".md")
    return f"unknown_{memory_type.rstrip('s')}"


def _source_trajectory_links_from_experience(policy: Policy | None) -> list[StoredLink]:
    if policy is None:
        return []
    links: list[StoredLink] = []
    for link in list(getattr(policy, "links", []) or []):
        try:
            stored_link = link if isinstance(link, StoredLink) else StoredLink(**dict(link))
        except Exception:
            continue
        if _is_source_trajectory_link(stored_link):
            links.append(stored_link)
    return links


def _superseded_policies_for_gradients(
    gradients: list[SemanticGradient],
    policy_set: PolicySet,
) -> list[Policy]:
    policies: list[Policy] = []
    seen: set[str] = set()
    for gradient in gradients:
        policy = _find_superseded_policy(_gradient_supersedes(gradient), policy_set)
        if policy is None or policy.uri in seen:
            continue
        seen.add(policy.uri)
        policies.append(policy)
    return policies


def _merge_source_trajectory_links(links: list[StoredLink]) -> list[StoredLink]:
    merged: dict[tuple[str, str, str | None], StoredLink] = {}
    for link in links:
        if not _is_source_trajectory_link(link):
            continue
        key = (link.from_uri, link.to_uri, link.match_text)
        if key in merged:
            existing = merged[key]
            update: dict[str, Any] = {"weight": max(existing.weight, link.weight)}
            if link.description:
                update["description"] = link.description
            if link.created_at and not existing.created_at:
                update["created_at"] = link.created_at
            merged[key] = existing.model_copy(update=update)
        else:
            merged[key] = link
    return list(merged.values())


def _remap_source_trajectory_links(
    links: list[StoredLink],
    *,
    target_uri: str,
) -> list[StoredLink]:
    return [
        link.model_copy(update={"from_uri": target_uri}) if target_uri else link
        for link in links
        if _is_source_trajectory_link(link) and link.to_uri
    ]


def _source_trajectory_links_for_plan_item(
    *,
    target_uri: str,
    target_name: str,
    before_content: str | None,
    after_content: str | None,
    trigger_code: str = "",
    source_links_by_target: dict[tuple[str, str], list[StoredLink]],
    replacement_source_uris: list[str] | None = None,
    include_all_sources: bool = False,
) -> list[StoredLink]:
    """Return only source trajectory links whose patch target maps to this plan item.

    Patch merge can reconcile several independent patch proposals into one or
    more final policy files.  Source links belong to the patch proposal that
    produced them, not to the whole merge batch.  Therefore link propagation must
    follow proposal-target/replacement provenance instead of broadcasting all
    gradient links to every upsert.
    """

    links: list[StoredLink] = []
    seen_source_keys: set[tuple[str, str]] = set()
    candidate_keys = _plan_item_source_keys(
        target_uri=target_uri,
        target_name=target_name,
        before_content=before_content,
        after_content=after_content,
        trigger_code=trigger_code,
        source_links_by_target=source_links_by_target,
        replacement_source_uris=replacement_source_uris or [],
        include_all_sources=include_all_sources,
    )
    for key in candidate_keys:
        if key in seen_source_keys:
            continue
        seen_source_keys.add(key)
        links.extend(source_links_by_target.get(key, []))
    return _merge_source_trajectory_links(
        _remap_source_trajectory_links(links, target_uri=target_uri)
    )


def _plan_item_source_keys(
    *,
    target_uri: str,
    target_name: str,
    before_content: str | None,
    after_content: str | None,
    trigger_code: str = "",
    source_links_by_target: dict[tuple[str, str], list[StoredLink]],
    replacement_source_uris: list[str] | None = None,
    include_all_sources: bool = False,
) -> list[tuple[str, str]]:
    keys: list[tuple[str, str]] = []
    all_keys = list(source_links_by_target.keys())

    def add(key: tuple[str, str]) -> None:
        if key in source_links_by_target and key not in keys:
            keys.append(key)

    uri = str(target_uri or "")
    name = str(target_name or "")
    trigger = str(trigger_code or "").strip()
    if uri:
        add(("uri", uri))
    if name:
        add(("name", name))
    if trigger:
        add(("trigger_code", trigger))
    for source_uri in replacement_source_uris or []:
        add(("uri", source_uri))

    # Existing-file updates and replacement deletes should inherit links from
    # the previous canonical file that the merge output is editing/replacing.
    for key in all_keys:
        kind, value = key
        if kind == "uri" and uri and value == uri:
            add(key)
        elif kind == "name" and name and value == name:
            add(key)

    # New proposals that keep their target URI/name may not have old content.
    # If there is exactly one source candidate with the same rendered content,
    # treat it as this plan item's source.  This handles URI/name normalization
    # without turning duplicate-content batches into a broadcast.
    content = str(after_content or "").strip()
    if content:
        matches = [key for key in all_keys if key[0] == "content" and key[1].strip() == content]
        if len(matches) == 1:
            add(matches[0])

    source_identities = _source_identity_keys(source_links_by_target)
    if include_all_sources:
        for key in source_identities:
            add(key)

    # Single-patch merge: if the final URI/name was normalized, there is still
    # only one possible source, so carry its provenance forward.
    if not keys and len(source_identities) == 1:
        keys.extend(source_identities)

    return keys


def _source_trajectory_links_by_target(
    gradients: list[SemanticGradient],
    policy_set: PolicySet,
) -> dict[tuple[str, str], list[StoredLink]]:
    result: dict[tuple[str, str], list[StoredLink]] = defaultdict(list)
    seen: set[tuple[tuple[str, str], str, str | None]] = set()
    for gradient in gradients:
        links = _merge_source_trajectory_links(
            [
                *list(getattr(gradient, "links", []) or []),
                *_superseded_source_trajectory_links(gradient, policy_set),
            ]
        )
        if not links:
            continue
        for key in _gradient_source_keys(gradient, policy_set):
            for link in links:
                dedupe_key = (key, link.to_uri, link.match_text)
                if dedupe_key in seen:
                    continue
                seen.add(dedupe_key)
                result[key].append(link)
    return dict(result)


def _source_trajectory_count(
    source_links_by_target: dict[tuple[str, str], list[StoredLink]],
) -> int:
    return len(
        {
            link.to_uri
            for links in source_links_by_target.values()
            for link in links
            if _is_source_trajectory_link(link) and link.to_uri
        }
    )


def _gradient_source_keys(
    gradient: SemanticGradient,
    policy_set: PolicySet,
) -> list[tuple[str, str]]:
    keys: list[tuple[str, str]] = []

    def add(kind: str, value: Any) -> None:
        text = str(value or "").strip()
        if text and (kind, text) not in keys:
            keys.append((kind, text))

    add("uri", gradient.target_uri)
    add("name", gradient.target_name)
    after_file = getattr(gradient, "after_file", None)
    if after_file is not None:
        add("content", getattr(after_file, "content", ""))
        add("trigger_code", (getattr(after_file, "extra_fields", {}) or {}).get("trigger_code"))
    before_file = getattr(gradient, "before_file", None)
    if before_file is not None:
        add("uri", getattr(before_file, "uri", None))
        fields = getattr(before_file, "extra_fields", {}) or {}
        add("name", fields.get("experience_name") or fields.get("name"))
        add("content", getattr(before_file, "content", ""))
        add("trigger_code", fields.get("trigger_code"))

    superseded_policy = _find_superseded_policy(_gradient_supersedes(gradient), policy_set)
    if superseded_policy is not None:
        add("uri", superseded_policy.uri)
        add("name", superseded_policy.name)
        add("content", superseded_policy.content)
        add("trigger_code", superseded_policy.metadata.get("trigger_code"))

    return keys


def _superseded_source_trajectory_links(
    gradient: SemanticGradient,
    policy_set: PolicySet,
) -> list[StoredLink]:
    superseded_policy = _find_superseded_policy(_gradient_supersedes(gradient), policy_set)
    return _source_trajectory_links_from_experience(superseded_policy)


def _upsert_output_count(
    operations: Any,
    *,
    memory_type: str,
    schema: MemoryTypeSchema,
) -> int:
    count = 0
    for op in getattr(operations, "upsert_operations", []) or []:
        if getattr(op, "memory_type", None) != memory_type:
            continue
        after_file = render_operation_after_file(op, schema=schema)
        if _memory_file_has_schema_content(after_file, schema=schema):
            count += 1
    return count


def _memory_file_has_schema_content(
    memory_file: MemoryFile,
    *,
    schema: MemoryTypeSchema,
) -> bool:
    for field_name in schema.content_field_names():
        value = (
            memory_file.content
            if field_name == "content"
            else memory_file.extra_fields.get(field_name)
        )
        if str(value or "").strip():
            return True
    return False


def _replacement_source_uris_by_target(operations: Any) -> dict[str, list[str]]:
    replacements = getattr(operations, "delete_replacements", {}) or {}
    if not isinstance(replacements, dict):
        return {}
    result: dict[str, list[str]] = defaultdict(list)
    for source_uri, target_uri in replacements.items():
        source = str(source_uri or "").strip()
        target = str(target_uri or "").strip()
        if not source or not target or source == target:
            continue
        if source not in result[target]:
            result[target].append(source)
    return dict(result)


def _source_identity_keys(
    source_links_by_target: dict[tuple[str, str], list[StoredLink]],
) -> list[tuple[str, str]]:
    result: list[tuple[str, str]] = []
    for key in source_links_by_target:
        if key[0] in {"uri", "name"} and key not in result:
            result.append(key)
    return result


def _is_source_trajectory_link(link: StoredLink) -> bool:
    return (
        link.link_type == "derived_from"
        and bool(link.to_uri)
        and "/memories/trajectories/" in link.to_uri
    )


def _find_policy_by_uri(policy_set: PolicySet, uri: str) -> Policy | None:
    for policy in policy_set.policies:
        if policy.uri == uri:
            return policy
    return None


def _base_version_from_old_file_or_policy(
    old_file: Any, target_uri: str | None, policy_set: PolicySet
) -> int | None:
    if old_file is not None:
        version = safe_int((getattr(old_file, "extra_fields", {}) or {}).get("version"))
        if version is not None:
            return version
    if target_uri:
        policy = _find_policy_by_uri(policy_set, target_uri)
        return policy.version if policy is not None else None
    return None


def _gradient_supersedes(gradient: SemanticGradient) -> Any:
    metadata = dict(getattr(gradient, "metadata", {}) or {})
    if metadata.get("supersedes") is not None:
        return metadata.get("supersedes")
    return (gradient.after_file.extra_fields or {}).get("supersedes")


def _find_superseded_policy(supersedes: Any, policy_set: PolicySet) -> Policy | None:
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

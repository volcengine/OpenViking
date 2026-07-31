# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Policy optimizer implementations."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from openviking.message import Message
from openviking.server.identity import RequestContext
from openviking.session.memory.dataclass import MemoryFile, StoredLink
from openviking.session.memory.extract_loop import ExtractLoop
from openviking.session.memory.memory_isolation_handler import MemoryIsolationHandler
from openviking.session.memory.memory_updater import ExtractContext
from openviking.session.memory.patch_merge_context_provider import (
    PatchMergeContextProvider,
    PatchMergePatch,
)
from openviking.session.train.domain import (
    Policy,
    PolicyPlanItem,
    PolicySet,
    PolicyUpdatePlan,
)
from openviking.session.train.interfaces import SemanticGradient
from openviking.session.train.utils import first_uri, safe_int
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
        policy_set: PolicySet,
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
            thinking=self.memory_type in {"trajectories", "experiences"},
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

def _gradient_to_merge_patch(gradient: SemanticGradient) -> PatchMergePatch:
    return PatchMergePatch(
        before_file=gradient.before_file,
        after_file=gradient.after_file,
        metadata={
            "base_version": gradient.base_version,
            "rationale": gradient.rationale,
            "links": _links_to_dicts(gradient.links),
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
) -> list[PolicyPlanItem]:
    items: list[PolicyPlanItem] = []
    source_links_by_target = _source_trajectory_links_by_target(gradients, policy_set)
    superseded_policies = _superseded_policies_for_gradients(gradients, policy_set)
    confidence_values = [float(gradient.confidence) for gradient in gradients]
    confidence = max(confidence_values) if confidence_values else None
    name_field = _name_field_for_memory_type(memory_type)

    upsert_output_count = _upsert_output_count(operations, memory_type=memory_type)
    replacement_source_uris_by_target = _replacement_source_uris_by_target(operations)
    upsert_target_uris: set[str] = set()
    for op in getattr(operations, "upsert_operations", []) or []:
        if getattr(op, "memory_type", None) != memory_type:
            continue
        fields = dict(getattr(op, "memory_fields", {}) or {})
        after_content = str(fields.get("content") or "")
        if not after_content.strip():
            continue
        target_name = str(
            fields.get(name_field)
            or fields.get("name")
            or _fallback_policy_name(op, memory_type=memory_type)
        )
        target_uri = first_uri(getattr(op, "uris", []) or [])
        old_file = getattr(op, "old_memory_file_content", None)
        before_content = old_file.plain_content() if old_file is not None else None
        if before_content is None and target_uri:
            policy = _find_policy_by_uri(policy_set, target_uri)
            before_content = policy.content if policy is not None else None
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
                links=_source_trajectory_links_for_plan_item(
                    target_uri=target_uri or "",
                    target_name=target_name,
                    before_content=before_content,
                    after_content=after_content,
                    source_links_by_target=source_links_by_target,
                    replacement_source_uris=replacement_source_uris_by_target.get(
                        target_uri or "",
                        [],
                    ),
                    include_all_sources=upsert_output_count == 1,
                ),
                metadata={
                    "rationale": "PatchMergeContextProvider merged semantic gradients via ExtractLoop.",
                    "merge_gradient_count": len(gradients),
                    "merge_memory_fields": fields,
                    "superseded_experience_uris": [
                        policy.uri for policy in superseded_policies
                    ],
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
        link.model_copy(update={"from_uri": target_uri})
        for link in links
        if target_uri and _is_source_trajectory_link(link) and link.to_uri
    ]


def _source_trajectory_links_for_plan_item(
    *,
    target_uri: str,
    target_name: str,
    before_content: str | None,
    after_content: str | None,
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
    if uri:
        add(("uri", uri))
    if name:
        add(("name", name))
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
        matches = [
            key
            for key in all_keys
            if key[0] == "content" and key[1].strip() == content
        ]
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
    before_file = getattr(gradient, "before_file", None)
    if before_file is not None:
        add("uri", getattr(before_file, "uri", None))
        fields = getattr(before_file, "extra_fields", {}) or {}
        add("name", fields.get("experience_name") or fields.get("name"))
        add("content", getattr(before_file, "content", ""))

    superseded_policy = _find_superseded_policy(_gradient_supersedes(gradient), policy_set)
    if superseded_policy is not None:
        add("uri", superseded_policy.uri)
        add("name", superseded_policy.name)
        add("content", superseded_policy.content)

    return keys


def _superseded_source_trajectory_links(
    gradient: SemanticGradient,
    policy_set: PolicySet,
) -> list[StoredLink]:
    superseded_policy = _find_superseded_policy(_gradient_supersedes(gradient), policy_set)
    return _source_trajectory_links_from_experience(superseded_policy)


def _upsert_output_count(operations: Any, *, memory_type: str) -> int:
    count = 0
    for op in getattr(operations, "upsert_operations", []) or []:
        if getattr(op, "memory_type", None) != memory_type:
            continue
        fields = dict(getattr(op, "memory_fields", {}) or {})
        if str(fields.get("content") or "").strip():
            count += 1
    return count


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

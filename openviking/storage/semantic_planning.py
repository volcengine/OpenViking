# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Resource/Skill action ownership and lazy semantic input preparation.

No model calls, queue operations, task settlement, or lease ownership live here.
Queue imports are deliberately local to freshness admission to avoid queuefs's
package initialization cycle.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from typing import Any, Awaitable, Callable, Dict, List, Mapping, Optional

from openviking.core.namespace import classify_uri
from openviking.server.error_mapping import is_not_found_error
from openviking.storage.abstract_overview import (
    AbstractOverviewFormatError,
    body_for_preview,
    deterministic_sample,
    parse_overview_file_summaries,
    plan_abstract_overview_refresh,
    read_abstract_overview_pending_snapshot,
)
from openviking.storage.context_update_plan import (
    IndexAction,
    NodeInputBinding,
    ResolvedSemanticNode,
    SemanticAction,
    SemanticExecutionPlan,
    SemanticPlan,
    SemanticRefreshIntent,
)
from openviking.storage.errors import LockAcquisitionError
from openviking.storage.viking_fs import LS_ALL_NODES
from openviking.utils.ingest_options import IngestOptions
from openviking_cli.utils import VikingURI
from openviking_cli.utils.config import get_openviking_config
from openviking_cli.utils.logger import get_logger

logger = get_logger(__name__)
_SKIP_FILENAMES = frozenset({"messages.jsonl"})


def resolve_semantic_execution(
    request: SemanticPlan | SemanticRefreshIntent,
    *,
    root_uri: str | None = None,
    incremental_update: bool | None = None,
    changes: Mapping[str, list[str]] | None = None,
    separate_directory_refresh: bool = False,
    generation_trigger: str | None = None,
) -> SemanticExecutionPlan:
    """Retain committed actions, or prepare a lazy refresh's runtime scope."""
    bindings: dict[str, NodeInputBinding] = {}
    children: dict[str, tuple[list[str], list[str]]] = {}
    active: set[str] = set()
    fixed = isinstance(request, SemanticPlan)
    if isinstance(request, SemanticPlan):
        root = request.root_uri
        for entry in request.tree.entries:
            uri = root if not entry.relative_path else f"{root}/{entry.relative_path}"
            abstracts = {slot.level: slot.abstract for slot in entry.index_slots}
            bindings[uri] = NodeInputBinding(
                uri,
                entry.kind,
                entry.semantic_action,
                entry.index_slots,
                abstracts.get(2 if entry.kind == "file" else 0, ""),
                abstracts.get(1, ""),
                entry.repair,
                entry.membership_changed,
                entry.md5,
            )
            if entry.relative_path:
                dirs, files = children.setdefault(uri.rsplit("/", 1)[0], ([], []))
                (dirs if entry.kind == "directory" else files).append(uri)
            if entry.semantic_action is not SemanticAction.REUSE:
                current = uri if entry.kind == "directory" else uri.rsplit("/", 1)[0]
                while current in bindings or current == root or current.startswith(root + "/"):
                    active.add(current)
                    if current == root:
                        break
                    current = current.rsplit("/", 1)[0]
        refresh = SemanticRefreshIntent(
            uri=root,
            context_type=request.context_type,
            target_uri=root,
            incremental_update=True,
            skip_vectorization=not request.vectorize,
            is_code_repo=request.file_vector_source.value == "summary_when_available",
            ingest_options=request.ingest_options,
            source=request.source_metadata,
            generation_trigger=generation_trigger or "semantic_refresh",
            changes={
                "modified": [
                    uri
                    for uri, node in bindings.items()
                    if node.semantic_action is not SemanticAction.REUSE
                ]
            },
            file_md5s={uri: node.md5 for uri, node in bindings.items() if node.md5},
            file_abstracts={
                uri: node.abstract
                for uri, node in bindings.items()
                if node.kind == "file" and node.abstract
            },
        )
        roots = request.execution_root_uris()
    else:
        root = root_uri or request.target_uri or request.uri
        refresh = replace(
            request,
            uri=root,
            target_uri=root if incremental_update else request.target_uri,
            incremental_update=(
                request.incremental_update if incremental_update is None else incremental_update
            ),
            changes=request.changes if changes is None else changes,
        )
        roots = (root,)
    defer = (
        separate_directory_refresh
        and not fixed
        and refresh.generation_trigger == "content_write"
        and refresh.context_type == "resource"
        and not refresh.recursive
        and refresh.changes is not None
        and refresh.aggregate_directory
    )
    return SemanticExecutionPlan(
        root,
        refresh.context_type,
        refresh,
        roots,
        bindings,
        {uri: (sorted(dirs), sorted(files)) for uri, (dirs, files) in children.items()},
        frozenset(active),
        fixed_actions=fixed,
        fail_dispatch=fixed or refresh.generation_trigger == "content_copy",
        fail_directory_write=fixed,
        record_failures=refresh.context_type == "skill",
        tolerate_index_failure=refresh.context_type == "skill",
        index_failed_file=not fixed and refresh.generation_trigger != "parent_refresh",
        index_changed_body_only=fixed,
        directory_only=refresh.generation_trigger == "parent_refresh",
        ready_inputs_only=refresh.generation_trigger == "content_copy",
        defer_directory_refresh=defer,
        observe_parent_aggregation=(
            not fixed
            and refresh.context_type == "resource"
            and refresh.aggregate_directory
            and not defer
            and not refresh.recursive
            and refresh.generation_trigger
            in {"content_write", "content_write_parent_refresh", "parent_refresh"}
        ),
    )


async def load_transfer_file_summaries(viking_fs, file_paths, *, ctx):
    """Read target L2 abstracts; backend failures are not missing summaries."""
    if not file_paths:
        return {}
    vector_store = viking_fs._get_vector_store()
    if vector_store is None:
        return {}
    return await vector_store.get_l2_abstracts_by_uris(file_paths, ctx=ctx)


async def prepare_committed_refresh(
    intent: SemanticRefreshIntent,
    *,
    viking_fs,
    ctx,
    force_refresh: bool | None = None,
    changed_entries: int | None = None,
    pending_only: bool = False,
    skip_busy: bool = True,
):
    """Admit committed changes once; node preparation and retries never call this.

    None bypasses freshness entirely. False records debt and checks the threshold;
    True records debt and requests the root. Transfers record debt but retain
    their unconditional, ready-input-only parent refresh.
    """
    from openviking.storage.queuefs.semantic_ops.freshness_policy import FreshnessAction

    action = FreshnessAction.REFRESH_NOW
    if force_refresh is not None or pending_only:
        config = get_openviking_config().semantic
        try:
            decision = await plan_abstract_overview_refresh(
                viking_fs=viking_fs,
                dir_uri=intent.uri,
                ctx=ctx,
                changed_entries=(
                    changed_entries
                    if changed_entries is not None
                    else len({uri for paths in (intent.changes or {}).values() for uri in paths})
                ),
                overview_sample_limit=getattr(config, "overview_sample_limit", 32),
                refresh_ratio=getattr(config, "freshness_refresh_ratio", 0.10),
                force_refresh=bool(force_refresh),
            )
            if not pending_only:
                action = decision.action
        except LockAcquisitionError:
            if not skip_busy:
                raise
            logger.info("Skipping busy parent semantic refresh: %s", intent.uri)
            action = FreshnessAction.NOOP
    effective = replace(intent, aggregate_directory=action is FreshnessAction.REFRESH_NOW)
    if not effective.aggregate_directory and not any(
        (intent.changes or {}).get(kind) for kind in ("added", "modified")
    ):
        return None, action
    return effective, action


async def prepare_parent_followup(
    request: SemanticPlan | SemanticRefreshIntent,
    uri: str,
    *,
    viking_fs_factory,
    ctx,
    l0_body_changed: bool,
    propagate_to_parent: bool = True,
) -> SemanticRefreshIntent | None:
    """Choose upward scope and freshness from the actual L0 write result."""
    from openviking.storage.queuefs.semantic_ops.freshness_policy import FreshnessAction

    if isinstance(request, SemanticRefreshIntent):
        if request.generation_trigger == "content_copy" or not request.propagate_to_parent:
            return None
    if request.context_type not in {"resource", "skill"} or not propagate_to_parent:
        return None
    parent = VikingURI(uri).parent
    if parent is None:
        return None
    parent_uri = parent.uri.rstrip("/")
    if request.context_type == "skill":
        classification = classify_uri(parent_uri)
        if (
            not classification.is_skill
            or classification.is_skill_namespace
            or classification.is_skill_root
        ):
            return None
    if (
        not parent_uri
        or parent_uri in {"viking://", "viking:", "viking://user", "viking://agent"}
        or parent_uri == uri.rstrip("/")
    ):
        return None
    config = get_openviking_config().semantic
    try:
        decision = await plan_abstract_overview_refresh(
            viking_fs=viking_fs_factory(),
            dir_uri=parent_uri,
            changed_entries=1,
            ctx=ctx,
            l0_body_changed=l0_body_changed,
            force_refresh=False,
            overview_sample_limit=getattr(config, "overview_sample_limit", 32),
            refresh_ratio=getattr(config, "freshness_refresh_ratio", 0.10),
            lock_timeout_secs=1.0,
        )
    except LockAcquisitionError:
        logger.info("Skipping busy parent freshness update: %s", parent_uri)
        return None
    if decision.action is not FreshnessAction.REFRESH_NOW:
        return None
    return SemanticRefreshIntent(
        uri=parent_uri,
        context_type=request.context_type,
        recursive=False,
        changes={"modified": [uri]},
        generation_trigger="parent_refresh",
    )


async def prepare_node(
    preparation: SemanticNodePreparation,
    uri: str,
    *,
    phase: str,
    parent_uri: str = "",
) -> ResolvedSemanticNode:
    """Read inputs only at the scheduler's directory, file, or overview boundary."""
    if phase == "directory":
        return await preparation.prepare_directory(uri)
    if phase == "file":
        return await preparation.prepare_file(uri, parent_uri)
    if phase == "overview":
        return await preparation.prepare_overview(uri)
    raise ValueError(f"unknown semantic preparation phase: {phase}")


def directory_index_arguments(
    plan: SemanticExecutionPlan, node: ResolvedSemanticNode, overview: str, abstract: str
) -> dict[str, Any]:
    """Compile exact publication commands after semantic output is known."""
    if not plan.index_changed_body_only:
        return {}
    from openviking.utils.content_hash import content_md5

    binding = node.binding
    slots = {slot.level: slot for slot in binding.index_slots}

    def emit(level: int, body: str) -> bool:
        slot = slots.get(level)
        return bool(
            slot
            and slot.action in {IndexAction.UPSERT, IndexAction.MERGE}
            and (
                binding.semantic_action is SemanticAction.REUSE
                or binding.repair
                or not slot.abstract
                or slot.abstract != body
            )
        )

    return {
        "scalar_overrides": {level: slot.scalar_override() for level, slot in slots.items()},
        "actions": {
            level: slot.action
            for level, slot in slots.items()
            if slot.action in {IndexAction.UPSERT, IndexAction.MERGE}
        },
        "field_patches": {
            level: slot.field_patch for level, slot in slots.items() if slot.field_patch
        },
        "include_abstract": emit(0, abstract),
        "include_overview": emit(1, overview),
        "md5s": {
            0: content_md5(abstract.encode("utf-8")),
            1: content_md5(overview.encode("utf-8")),
        },
    }


class SemanticNodePreparation:
    """Per-execution input state, independent of Processor and message lifecycle."""

    def __init__(
        self,
        plan: SemanticExecutionPlan,
        *,
        viking_fs,
        ctx,
        lock=None,
        source_contents=None,
        source_raw_contents=None,
        load_file_summaries: Callable[..., Awaitable[dict[str, str]]] | None = None,
        parse_file_summaries: Callable[[str], dict[str, str]] = parse_overview_file_summaries,
    ):
        self.plan = plan
        request = plan.refresh
        self._viking_fs, self._ctx, self._lock = viking_fs, ctx, lock
        self._root_uri = plan.root_uri
        self._incremental_update = request.incremental_update
        self._target_uri = request.target_uri or None
        self._aggregate_directory = request.aggregate_directory and not plan.defer_directory_refresh
        self._changes_provided = request.changes is not None
        self._changed_paths = {uri for paths in (request.changes or {}).values() for uri in paths}
        self._tree_changed_paths = (
            {uri for uri, node in plan.bindings.items() if node.membership_changed}
            if plan.fixed_actions
            else {
                uri.rstrip("/")
                for kind in ("added", "deleted")
                for uri in (request.changes or {}).get(kind, [])
            }
        )
        self._file_abstracts = dict(request.file_abstracts)
        self._artifact_files = [path.strip("/") for path in request.artifact_files if path]
        self._source_contents = dict(source_contents or {})
        self._source_raw_contents = source_raw_contents
        self._overview_bodies: dict[str, str] = {}
        self._overview_cache: dict[str, dict[str, str]] = {}
        self._overview_cache_lock = asyncio.Lock()
        self._pending_abstract_dirs: set[str] = set()
        self.file_change_status: dict[str, bool] = {}
        self.dir_change_status: dict[str, bool] = {}
        self.directories: dict[str, ResolvedSemanticNode] = {}
        self.files: dict[str, ResolvedSemanticNode] = {}
        self._load_file_summaries = load_file_summaries or (
            lambda paths, *, ctx: load_transfer_file_summaries(viking_fs, paths, ctx=ctx)
        )
        self._parse_file_summaries = parse_file_summaries

    @staticmethod
    def _entry_record_abstract(entry: NodeInputBinding, level: int) -> str:
        slot = entry.slot(level)
        return slot.abstract if slot else ""

    def binding(self, uri: str, kind: str) -> NodeInputBinding:
        if node := self.plan.bindings.get(uri.rstrip("/")):
            return node
        action = SemanticAction.GENERATE if kind == "file" else SemanticAction.REUSE
        if kind == "directory" and (
            self._aggregate_directory
            or (
                self.plan.context_type == "skill"
                and classify_uri(uri).is_skill_root
                and f"{uri}/SKILL.md" in self._changed_paths
            )
        ):
            action = SemanticAction.AGGREGATE
        return NodeInputBinding(uri, kind, action)

    async def prepare_directory(self, uri: str) -> ResolvedSemanticNode:
        children, files = await self._list_dir(uri, "prepare_node")
        if self.plan.ready_inputs_only:
            resolved = await self._prepare_transfer_node(uri, children, files)
            self.directories[uri] = resolved
            return resolved
        binding = self.binding(uri, "directory")
        aggregate = binding.semantic_action is SemanticAction.AGGREGATE
        sampled = (
            deterministic_sample(
                sorted(
                    [("directory", path) for path in children] + [("file", path) for path in files],
                    key=lambda item: item[1].rsplit("/", 1)[-1],
                ),
                get_openviking_config().semantic.overview_sample_limit,
            )
            if aggregate
            else []
        )
        resolved = ResolvedSemanticNode(
            binding,
            children_dirs=children,
            file_paths=files,
            sampled_files={path for kind, path in sampled if kind == "file"},
            sampled_directories={path for kind, path in sampled if kind == "directory"},
            file_summaries=[None] * len(files),
            children_abstracts=[None] * len(children),
        )
        if aggregate:
            try:
                resolved.pending_snapshot = await read_abstract_overview_pending_snapshot(
                    viking_fs=self._viking_fs,
                    dir_uri=uri,
                    ctx=self._ctx,
                    lock=self._lock,
                    existing_raw=(
                        {
                            level: raw
                            for level in (0, 1)
                            if (raw := self._source_raw_contents.get((uri, level))) is not None
                        }
                        if self._source_raw_contents is not None
                        else None
                    ),
                    **(
                        {"overview_body_out": self._overview_bodies}
                        if self.plan.observe_parent_aggregation
                        else {}
                    ),
                )
            except LockAcquisitionError:
                if self.plan.refresh.generation_trigger != "content_write":
                    raise
                self._aggregate_directory = False
                aggregate = False
                resolved.binding = replace(binding, semantic_action=SemanticAction.REUSE)
                logger.info("Skipping busy parent semantic refresh: %s", uri)
        if self.plan.fixed_actions:
            resolved.required_files = {
                path
                for path in files
                if self.plan.bindings[path].semantic_action is not SemanticAction.REUSE
                or any(
                    slot.action is not IndexAction.NONE
                    for slot in self.plan.bindings[path].index_slots
                )
            }
            resolved.required_directories = set(children) & self.plan.active_directories
            # Structural ancestors do not need aggregate inputs or sidecars.
            for idx, path in enumerate(files):
                if path in resolved.required_files or path not in resolved.sampled_files:
                    continue
                abstract = self.plan.bindings[path].abstract
                if not abstract:
                    raise RuntimeError(f"Semantic plan lacks L2 abstract for {path}")
                resolved.file_summaries[idx] = {
                    "name": path.rsplit("/", 1)[-1],
                    "summary": abstract,
                }
            for idx, path in enumerate(children):
                if (
                    path in resolved.required_directories
                    or path not in resolved.sampled_directories
                ):
                    continue
                abstract = self.plan.bindings[path].abstract
                if not abstract:
                    raise RuntimeError(f"Semantic plan lacks L0 abstract for {path}")
                resolved.children_abstracts[idx] = {
                    "name": path.rsplit("/", 1)[-1],
                    "abstract": abstract,
                }
        else:
            resolved.required_files = (
                set(files)
                if self.plan.refresh.recursive
                else (
                    set(files) & self._changed_paths
                    if not aggregate
                    else (
                        resolved.sampled_files | (set(files) & self._changed_paths)
                        if self._incremental_update
                        else resolved.sampled_files
                    )
                )
            )
            resolved.required_directories = set(children) if self.plan.refresh.recursive else set()
            reuse_pending = (
                self.plan.observe_parent_aggregation
                and aggregate
                and self._incremental_update
                and resolved.pending_snapshot > 0
            )
            if reuse_pending:
                self._pending_abstract_dirs.add(uri)
            if (
                reuse_pending or self.plan.directory_only
            ) and not self.plan.refresh.skip_vectorization:
                missing = resolved.required_files - {
                    path for path, abstract in self._file_abstracts.items() if abstract.strip()
                }
                if not self.plan.directory_only:
                    missing -= self._changed_paths
                if missing:
                    try:
                        self._file_abstracts.update(
                            await self._load_file_summaries(sorted(missing), ctx=self._ctx)
                        )
                    except LockAcquisitionError:
                        raise
                    except Exception as exc:
                        logger.warning(
                            "Failed to load L2 abstracts for %s; falling back to old overview: %s",
                            uri,
                            exc,
                        )
        self.directories[uri] = resolved
        return resolved

    async def prepare_file(self, uri: str, parent_uri: str) -> ResolvedSemanticNode:
        binding = self.binding(uri, "file")
        resolved = ResolvedSemanticNode(
            binding,
            vectorize=not self.plan.directory_only and not self.plan.refresh.skip_vectorization,
            ingest_options=self._ingest_options_for_file(uri),
        )
        # Preserve already selected publication/options if a later input read fails.
        self.files[uri] = resolved
        if self.plan.fixed_actions:
            resolved.input_changed = binding.semantic_action is not SemanticAction.REUSE
            if binding.semantic_action is SemanticAction.REUSE:
                resolved.summary = {"name": uri.rsplit("/", 1)[-1], "summary": binding.abstract}
                resolved.summary_source = "abstract"
            slot = binding.slot(2)
            resolved.vectorize = bool(
                not self.plan.refresh.skip_vectorization
                and slot
                and slot.action in {IndexAction.UPSERT, IndexAction.MERGE}
            )
            if slot:
                resolved.index_action = slot.action.value
                resolved.scalar_override = slot.scalar_override()
                resolved.field_patch = slot.field_patch
            resolved.ingest_options = IngestOptions(
                acl_update=self.plan.refresh.ingest_options.acl_update
            )
        elif self._incremental_update:
            resolved.input_changed = await self._check_file_content_changed(uri)
            directory = self.directories.get(parent_uri)
            regenerate = bool(
                self._aggregate_directory
                and directory
                and directory.pending_snapshot > 0
                and uri in directory.sampled_files
            )
            reuse = self.plan.directory_only or parent_uri in self._pending_abstract_dirs
            if (not resolved.input_changed or self.plan.directory_only) and (
                not regenerate or reuse
            ):
                resolved.summary = await self._read_existing_summary(uri, require_nonempty=reuse)
                if resolved.summary is not None:
                    resolved.vectorize = False
                    abstract = self._file_abstracts.get(uri, "")
                    resolved.summary_source = (
                        "abstract" if (abstract.strip() if reuse else abstract) else "overview"
                    )
                elif regenerate:
                    resolved.vectorize = False
                else:
                    resolved.input_changed = True
            elif not resolved.input_changed:
                resolved.vectorize = False
        else:
            resolved.input_changed = True
        self.file_change_status[uri] = resolved.input_changed
        if not self.plan.fixed_actions:
            resolved.index_action = self.plan.refresh.file_vector_actions.get(
                uri.rstrip("/"), "merge"
            )
        resolved.vectorize = resolved.vectorize and not self.plan.refresh.skip_vectorization
        if resolved.summary is None:
            content = self._source_contents.get((uri.rstrip("/"), 2))
            if content is not None and not isinstance(content, bytes):
                raise ValueError(f"file vector source must be bytes: {uri}")
            if content is None and hasattr(self._viking_fs, "read_file_bytes"):
                content = await self._viking_fs.read_file_bytes(uri, ctx=self._ctx)
            resolved.file_content = content
            resolved.summary_source = "generated"
        return resolved

    async def prepare_overview(self, uri: str) -> ResolvedSemanticNode:
        directory = self.directories[uri]
        binding = directory.binding
        result = ResolvedSemanticNode(binding, ingest_options=self._ingest_options_for_directory())
        if binding.semantic_action is SemanticAction.REUSE:
            result.abstract = binding.abstract
            result.overview = binding.overview
            result.vectorize = not self.plan.refresh.skip_vectorization and any(
                slot.action in {IndexAction.UPSERT, IndexAction.MERGE}
                for slot in binding.index_slots
            )
            return result
        result.write_semantics = True
        result.vectorize = not self.plan.refresh.skip_vectorization
        result.input_changed = True
        result.use_direct_media = not self.plan.ready_inputs_only
        if self.plan.context_type == "skill" and classify_uri(uri).is_skill_root:
            changed = f"{uri}/SKILL.md" in self._changed_paths
            result.definition_source = True
            result.regenerate_definition = (
                self.plan.refresh.generation_trigger == "reindex" or changed or binding.repair
            )
            result.write_semantics = False
            result.vectorize = result.vectorize and (
                not self._incremental_update or changed or binding.repair
            )
            result.input_changed = changed or binding.repair
        elif self.plan.ready_inputs_only:
            result.write_semantics = directory.write_semantics
            result.vectorize = result.vectorize and directory.vectorize
        elif self._incremental_update:
            result.input_changed = await self._check_dir_children_changed(
                uri, directory.file_paths, directory.children_dirs
            )
            if not result.input_changed:
                result.overview, result.abstract = await self._read_existing_overview_abstract(uri)
                result.write_semantics = result.overview is None or result.abstract is None
                if binding.repair:
                    result.overview = result.abstract = None
                    result.write_semantics = True
                result.vectorize = result.vectorize and result.write_semantics
                result.input_changed = result.write_semantics
        if self.plan.fixed_actions:
            result.ingest_options = IngestOptions(
                acl_update=self.plan.refresh.ingest_options.acl_update
            )
        return result

    async def aggregation_inputs(
        self, uri: str
    ) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
        """Finish sampled inputs after direct child work has reported its results."""
        node = self.directories[uri]
        files = [
            item if item is not None else {"name": path.rsplit("/", 1)[-1], "summary": ""}
            for path, item in zip(node.file_paths, node.file_summaries, strict=True)
            if path in node.sampled_files
        ]
        children = []
        for path, item in zip(node.children_dirs, node.children_abstracts, strict=True):
            if path not in node.sampled_directories:
                continue
            if item is None:
                try:
                    abstract = await self._viking_fs.abstract(path, ctx=self._ctx)
                except Exception as exc:
                    if (
                        self.plan.context_type in {"resource", "skill"}
                        and not isinstance(exc, KeyError)
                        and not is_not_found_error(exc)
                    ):
                        raise
                    abstract = ""
                item = {"name": path.rsplit("/", 1)[-1], "abstract": abstract}
            children.append(item)
        return files, children

    @staticmethod
    def _transfer_summary_ready(value: str) -> bool:
        summary = value.strip()
        return bool(summary) and not summary.endswith(
            ("[Directory abstract is not ready]", "[Directory overview is not ready]")
        )

    async def _list_dir(self, uri: str, from_hint: str) -> tuple[list[str], list[str]]:
        """List directory entries and return (child_dirs, file_paths)."""
        if self.plan.fixed_actions:
            return self.plan.children.get(uri.rstrip("/"), ([], []))
        if self._artifact_files and self._root_uri:
            return self._list_artifact_dir(uri)
        try:
            entries = await self._viking_fs.ls(uri, node_limit=LS_ALL_NODES, ctx=self._ctx)
        except Exception as e:
            logger.warning(
                f"[SemanticTreeExecutor] Failed to list directory {uri}: {e} from {from_hint}"
            )
            raise

        children_dirs: List[str] = []
        file_paths: List[str] = []

        for entry in entries:
            name = entry.get("name", "")
            if not name or name.startswith(".") or name in [".", ".."] or name in _SKIP_FILENAMES:
                continue

            item_uri = VikingURI(uri).join(name).uri
            if entry.get("isDir", False):
                children_dirs.append(item_uri)
            else:
                file_paths.append(item_uri)

        return sorted(children_dirs), sorted(file_paths)

    def _list_artifact_dir(self, uri: str) -> tuple[list[str], list[str]]:
        root = self._root_uri.rstrip("/")
        current = uri.rstrip("/")
        if current == root:
            prefix = ""
        elif current.startswith(f"{root}/"):
            prefix = current[len(root) + 1 :] + "/"
        else:
            return [], []

        child_dirs: set[str] = set()
        file_paths: list[str] = []
        for rel_path in self._artifact_files:
            if not rel_path.startswith(prefix):
                continue
            remainder = rel_path[len(prefix) :]
            if not remainder:
                continue
            head, separator, _ = remainder.partition("/")
            if not head or head.startswith(".") or head in _SKIP_FILENAMES:
                continue
            child_uri = VikingURI(current).join(head).uri
            if separator:
                child_dirs.add(child_uri)
            else:
                file_paths.append(child_uri)
        return sorted(child_dirs), sorted(file_paths)

    def _get_target_file_path(self, current_uri: str) -> Optional[str]:
        if not self._incremental_update or not self._target_uri or not self._root_uri:
            logger.warning(
                "Invalid target_uri or root_uri for incremental update: "
                f"target_uri={self._target_uri}, root_uri={self._root_uri}"
            )
            return None
        if self._target_uri != self._root_uri:
            logger.warning(
                "Incremental semantic update expects target_uri == root_uri: "
                f"target_uri={self._target_uri}, root_uri={self._root_uri}"
            )
            return None
        return current_uri

    def _is_direct_incremental_update(self) -> bool:
        return self.plan.fixed_actions or (
            self._incremental_update
            and self._changes_provided
            and self._target_uri == self._root_uri
        )

    def _directory_entries_changed(self, dir_uri: str) -> bool:
        """Return whether this directory subtree gained or lost an entry."""
        normalized = dir_uri.rstrip("/")
        prefix = normalized + "/"
        return any(
            path == normalized or path.startswith(prefix) for path in self._tree_changed_paths
        )

    def _ingest_options_for_file(self, file_path: str) -> IngestOptions:
        if self.plan.refresh.generation_trigger == "content_write":
            options = self.plan.refresh.file_ingest_options.get(file_path.rstrip("/"))
            if options is not None:
                return options
        if (
            self.plan.refresh.generation_trigger == "content_write"
            and file_path not in self._changed_paths
        ):
            return IngestOptions(acl_update=self.plan.refresh.ingest_options.acl_update)
        return self.plan.refresh.ingest_options

    def _ingest_options_for_directory(self) -> IngestOptions:
        if self.plan.fixed_actions or self.plan.refresh.generation_trigger == "content_write":
            return IngestOptions(acl_update=self.plan.refresh.ingest_options.acl_update)
        return self.plan.refresh.ingest_options

    async def _check_file_content_changed(self, file_path: str) -> bool:
        if self._is_direct_incremental_update():
            return file_path in self._changed_paths
        target_path = self._get_target_file_path(file_path)
        if not target_path:
            return True
        try:
            current_stat = await self._viking_fs.stat(file_path, ctx=self._ctx, skip_count=True)
            target_stat = await self._viking_fs.stat(target_path, ctx=self._ctx, skip_count=True)
            current_size = current_stat.get("size") if isinstance(current_stat, dict) else None
            target_size = target_stat.get("size") if isinstance(target_stat, dict) else None
            if current_size is not None and target_size is not None and current_size != target_size:
                return True
            current_content = await self._viking_fs.read_file(file_path, ctx=self._ctx)
            target_content = await self._viking_fs.read_file(target_path, ctx=self._ctx)
            return current_content != target_content
        except Exception:
            return True

    async def _read_existing_summary(
        self, file_path: str, *, require_nonempty: bool = False
    ) -> Optional[Dict[str, str]]:
        """Read a supplied L2 abstract or a cached old parent overview entry.

        Args:
            file_path: Current file path

        Returns:
            Summary dict with 'name' and 'summary' keys, or None if not found
        """
        target_path = self._get_target_file_path(file_path)
        if not target_path:
            return None
        vector_abstract = self._file_abstracts.get(target_path.rstrip("/"))
        if vector_abstract and (not require_nonempty or vector_abstract.strip()):
            return {
                "name": file_path.rsplit("/", 1)[-1],
                "summary": vector_abstract,
            }

        try:
            parent_uri = "/".join(target_path.rsplit("/", 1)[:-1])
            if not parent_uri:
                return None

            if parent_uri not in self._overview_cache:
                try:
                    from openviking.metrics.datasources.cache import CacheEventDataSource

                    CacheEventDataSource.record_miss("L1")
                except Exception:
                    pass
                async with self._overview_cache_lock:
                    if parent_uri not in self._overview_cache:
                        if parent_uri in self._overview_bodies:
                            overview_content = self._overview_bodies[parent_uri]
                        else:
                            overview_path = f"{parent_uri}/.overview.md"
                            overview_content = await self._viking_fs.read_file(
                                overview_path, ctx=self._ctx
                            )
                        if overview_content:
                            self._overview_cache[parent_uri] = self._parse_file_summaries(
                                body_for_preview(overview_content)
                            )
                        else:
                            self._overview_cache[parent_uri] = {}
            else:
                try:
                    from openviking.metrics.datasources.cache import CacheEventDataSource

                    CacheEventDataSource.record_hit("L1")
                except Exception:
                    pass

            existing_summaries = self._overview_cache.get(parent_uri, {})
            file_name = file_path.split("/")[-1]

            if file_name in existing_summaries and (
                not require_nonempty or existing_summaries[file_name].strip()
            ):
                return {"name": file_name, "summary": existing_summaries[file_name]}

        except Exception as e:
            if (
                self.plan.context_type in {"resource", "skill"}
                and not isinstance(e, (KeyError, AbstractOverviewFormatError))
                and not is_not_found_error(e)
            ):
                raise
            logger.debug(f"Failed to read existing summary from overview.md for {file_path}: {e}")

        return None

    async def _check_dir_children_changed(
        self, dir_uri: str, current_files: List[str], current_dirs: List[str]
    ) -> bool:
        if self._is_direct_incremental_update():
            node = self.directories.get(dir_uri)
            if node is not None and node.pending_snapshot > 0:
                return True
            if self._directory_entries_changed(dir_uri):
                return True
            for current_file in current_files:
                default_changed = (
                    self.plan.bindings[current_file].semantic_action is not SemanticAction.REUSE
                    if self.plan.fixed_actions
                    else True
                )
                if self.file_change_status.get(current_file, default_changed):
                    return True
            for current_dir in current_dirs:
                default_changed = (
                    self.plan.bindings[current_dir].semantic_action is not SemanticAction.REUSE
                    if self.plan.fixed_actions
                    else True
                )
                if self.dir_change_status.get(current_dir, default_changed):
                    return True
            return False

        target_path = self._get_target_file_path(dir_uri)
        if not target_path:
            return True
        try:
            target_dirs, target_files = await self._list_dir(
                target_path, "_check_dir_children_changed"
            )
            current_file_names = {f.split("/")[-1] for f in current_files}
            target_file_names = {f.split("/")[-1] for f in target_files}
            if current_file_names != target_file_names:
                return True
            current_dir_names = {d.split("/")[-1] for d in current_dirs}
            target_dir_names = {d.split("/")[-1] for d in target_dirs}
            if current_dir_names != target_dir_names:
                return True
            for current_file in current_files:
                if self.file_change_status.get(current_file, True):
                    return True
            for current_dir in current_dirs:
                if self.dir_change_status.get(current_dir, True):
                    return True
            return False
        except Exception:
            return True

    async def _read_existing_overview_abstract(
        self, dir_uri: str
    ) -> tuple[Optional[str], Optional[str]]:
        if self.plan.fixed_actions:
            snapshot_overview = self._source_contents.get((dir_uri.rstrip("/"), 1))
            snapshot_abstract = self._source_contents.get((dir_uri.rstrip("/"), 0))
            if snapshot_overview is not None or snapshot_abstract is not None:
                return (
                    str(snapshot_overview) if snapshot_overview is not None else None,
                    str(snapshot_abstract) if snapshot_abstract is not None else None,
                )
            entry = self.plan.bindings.get(dir_uri.rstrip("/"))
            if entry is None:
                return None, None
            overview = self._entry_record_abstract(entry, 1) or None
            abstract = self._entry_record_abstract(entry, 0) or None
            return overview, abstract
        target_path = self._get_target_file_path(dir_uri)
        if not target_path:
            return None, None
        try:
            overview = await self._viking_fs.read_file(f"{target_path}/.overview.md", ctx=self._ctx)
            abstract = await self._viking_fs.read_file(f"{target_path}/.abstract.md", ctx=self._ctx)
            return body_for_preview(overview), body_for_preview(abstract)
        except Exception as exc:
            if (
                self.plan.context_type in {"resource", "skill"}
                and not isinstance(exc, (KeyError, AbstractOverviewFormatError))
                and not is_not_found_error(exc)
            ):
                raise
            return None, None

    async def _load_transfer_candidates(
        self,
        candidates: List[tuple[str, str]],
    ) -> Dict[tuple[str, str], Dict[str, str]]:
        file_paths = [uri for kind, uri in candidates if kind == "file"]
        file_summaries = await self._load_file_summaries(file_paths, ctx=self._ctx)
        loaded: Dict[tuple[str, str], Dict[str, str]] = {}
        for file_path in file_paths:
            summary = str(file_summaries.get(file_path) or "").strip()
            if self._transfer_summary_ready(summary):
                loaded[("file", file_path)] = {
                    "name": file_path.rsplit("/", 1)[-1],
                    "summary": summary,
                }

        child_uris = [uri for kind, uri in candidates if kind == "directory"]
        if child_uris:
            results = await asyncio.gather(
                *[self._viking_fs.abstract(uri, ctx=self._ctx) for uri in child_uris],
                return_exceptions=True,
            )
            for child_uri, result in zip(child_uris, results, strict=True):
                if isinstance(result, BaseException):
                    if isinstance(result, KeyError) or (
                        isinstance(result, Exception) and is_not_found_error(result)
                    ):
                        continue
                    raise result
                abstract = str(result or "").strip()
                if self._transfer_summary_ready(abstract):
                    loaded[("directory", child_uri)] = {
                        "name": child_uri.rsplit("/", 1)[-1],
                        "abstract": abstract,
                    }
        return loaded

    async def _prepare_transfer_node(
        self,
        dir_uri: str,
        children_dirs: List[str],
        file_paths: List[str],
    ) -> ResolvedSemanticNode:
        """Sample the target directory first, then read only existing summaries."""
        candidates = sorted(
            [("file", uri) for uri in file_paths] + [("directory", uri) for uri in children_dirs],
            key=lambda item: (item[1].rsplit("/", 1)[-1], item[0], item[1]),
        )
        primary = deterministic_sample(
            candidates, get_openviking_config().semantic.overview_sample_limit
        )
        loaded = await self._load_transfer_candidates(primary)
        selected = [candidate for candidate in primary if candidate in loaded]

        inspected = set(primary)
        selected.sort(key=lambda item: (item[1].rsplit("/", 1)[-1], item[0], item[1]))
        selected_files = [uri for kind, uri in selected if kind == "file"]
        selected_dirs = [uri for kind, uri in selected if kind == "directory"]
        missing_count = (
            sum(1 for candidate in inspected if candidate not in loaded)
            if len(inspected) == len(candidates)
            else None
        )
        pending_snapshot = await read_abstract_overview_pending_snapshot(
            viking_fs=self._viking_fs,
            dir_uri=dir_uri,
            ctx=self._ctx,
            lock=self._lock,
        )
        return ResolvedSemanticNode(
            binding=self.binding(dir_uri, "directory"),
            children_dirs=selected_dirs,
            file_paths=selected_files,
            file_summaries=[loaded[("file", uri)] for uri in selected_files],
            children_abstracts=[loaded[("directory", uri)] for uri in selected_dirs],
            sampled_files=set(selected_files),
            sampled_directories=set(selected_dirs),
            pending_snapshot=pending_snapshot,
            total_entries=len(candidates),
            missing_summary_entries=missing_count,
            write_semantics=not candidates or bool(selected),
            vectorize=not candidates or bool(selected),
        )

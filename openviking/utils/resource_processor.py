# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
Context Processor for OpenViking.

Handles coordinated writes and self-iteration processes
as described in the OpenViking design document.
"""

import asyncio
import inspect
import os
import time
from collections.abc import Callable
from dataclasses import replace
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Union

from openviking.core.context import ContextLevel
from openviking.core.namespace import context_type_for_uri
from openviking.parse.image_rewrite import rewrite_image_uris
from openviking.parse.mode import ParseMode, normalize_parse_mode
from openviking.parse.tree_builder import TreeBuilder
from openviking.resource.processing_mode import (
    DEFAULT_PROCESSING_MODE,
    VECTORS_ONLY,
    ProcessingMode,
    normalize_processing_mode,
)
from openviking.server.identity import RequestContext
from openviking.storage.acl import AclAction, AclSpec, AclUpdate
from openviking.storage.errors import LockAcquisitionError
from openviking.storage.expr import And, Eq, PathScope
from openviking.storage.index_action import FieldPatch
from openviking.storage.internal_names import is_storage_internal_name
from openviking.storage.queuefs.semantic_processor import SemanticProcessor
from openviking.storage.resource_rnfv import RequestIntent
from openviking.storage.viking_fs import LS_ALL_NODES, get_viking_fs
from openviking.storage.vikingdb_manager import VikingDBManager
from openviking.telemetry import get_current_telemetry
from openviking.utils import is_github_url
from openviking.utils.embedding_utils import (
    index_resource,
    vectorize_directory_meta,
    vectorize_file,
)
from openviking.utils.git_auth import is_git_https_url
from openviking.utils.ingest_options import IngestOptions
from openviking.utils.log_correlation import log_correlation
from openviking.utils.summarizer import Summarizer
from openviking_cli.exceptions import OpenVikingError
from openviking_cli.utils import VikingURI, get_logger
from openviking_cli.utils.config import get_openviking_config
from openviking_cli.utils.storage import StoragePath

if TYPE_CHECKING:
    from openviking.config.vlm import VLMResolver
    from openviking.parse.accessors.base import LocalResource
    from openviking.parse.vlm import VLMProcessor

logger = get_logger(__name__)
_MAX_FILE_VECTORIZATION_CONCURRENCY = 64
VECTORDB_MAX_QUERY_LIMIT = 100_000


class _DocRelStore:
    """Adapt a parse output store so commit reads use target-relative paths.

    Resource plans key files relative to the target root, while the underlying
    artifact may store them below ``<doc_rel>/...``.
    """

    def __init__(self, store: Any, doc_rel: str) -> None:
        self._store = store
        base = (doc_rel or "").strip("/")
        self._base = base

    async def read_bytes(self, ref: Any, rel_path: str) -> bytes:
        normalized = str(rel_path or "").strip("/")
        artifact_rel = (
            normalized
            if not self._base or normalized == self._base or normalized.startswith(self._base + "/")
            else f"{self._base}/{normalized}"
        )
        return await self._store.read_bytes(ref, artifact_rel)


class ResourceProcessor:
    """
    Handles coordinated write operations.

    When new data is added, automatically:
    1. Download if URL (prefer PDF format)
    2. Parse and structure the content (Parser writes to temp directory)
    3. Extract images/tables for mixed content
    4. Use VLM to understand non-text content
    5. TreeBuilder finalizes from temp (move to AGFS)
    6. SemanticQueue generates L0/L1 and vectorizes asynchronously
    """

    def __init__(
        self,
        vikingdb: VikingDBManager,
        media_storage: Optional["StoragePath"] = None,
        max_context_size: int = 2000,
        max_split_depth: int = 3,
        runtime_config_manager: Optional[Any] = None,
        vlm_resolver: Optional["VLMResolver"] = None,
    ):
        """Initialize coordinated writer."""
        self.vikingdb = vikingdb
        self.media_storage = media_storage
        self.runtime_config_manager = runtime_config_manager
        self.vlm_resolver = vlm_resolver
        self.tree_builder = TreeBuilder()
        self._vlm_processor = None
        self._media_processor = None
        self._summarizer = None

    async def github_token_for(
        self,
        source: str,
        ctx: RequestContext,
    ) -> Optional[str]:
        """Resolve a GitHub token for one request without persisting it."""
        if not is_git_https_url(source) or not is_github_url(source):
            return None
        if self.runtime_config_manager is not None:
            account_github = await self.runtime_config_manager.get_account(ctx.account_id, "github")
            if account_github is not None and account_github.token:
                return account_github.token
        return os.environ.get("GITHUB_TOKEN") or None

    async def _source_config_kwargs(
        self,
        source: str,
        ctx: RequestContext,
        kwargs: dict[str, Any],
    ) -> dict[str, Any]:
        """Add account-scoped source configuration without persisting it."""
        from openviking.parse.accessors.feishu_accessor import FeishuAccessor

        if FeishuAccessor._is_feishu_url(source):
            return await self._feishu_source_config_kwargs(ctx, kwargs)
        return await self._github_source_config_kwargs(source, ctx, kwargs)

    async def _github_source_config_kwargs(
        self,
        source: str,
        ctx: RequestContext,
        kwargs: dict[str, Any],
    ) -> dict[str, Any]:
        """Add the account-scoped GitHub token for one request."""
        github_token = await self.github_token_for(source, ctx)
        if not github_token:
            return kwargs
        result = dict(kwargs)
        result["github_token"] = github_token
        return result

    async def _feishu_source_config_kwargs(
        self,
        ctx: RequestContext,
        kwargs: dict[str, Any],
    ) -> dict[str, Any]:
        """Add the account-scoped Feishu config for one request."""
        if self.runtime_config_manager is None:
            raise RuntimeError("Runtime config manager is not initialized")
        from openviking.config.feishu import get_effective_feishu_config

        feishu_config = await get_effective_feishu_config(
            self.runtime_config_manager,
            ctx.account_id,
        )
        result = dict(kwargs)
        result["feishu_config"] = feishu_config
        return result

    def _get_summarizer(self) -> "Summarizer":
        """Lazy initialization of the standalone Summarizer."""
        if self._summarizer is None:
            self._summarizer = Summarizer(self._get_vlm_processor())
        return self._summarizer

    async def _summarizer_for(self, ctx: RequestContext) -> "Summarizer":
        """Build a lightweight account-bound summarizer."""
        if self._summarizer is not None:
            return self._summarizer
        if self.vlm_resolver is None:
            raise RuntimeError(
                "ResourceProcessor requires a VLM resolver for account-owned work"
            )
        return Summarizer(await self._vlm_processor_for(ctx))

    def _get_vlm_processor(self) -> "VLMProcessor":
        """Return an explicitly configured standalone VLM processor."""
        if self._vlm_processor is None:
            raise RuntimeError(
                "ResourceProcessor requires an explicitly configured VLMProcessor"
            )
        return self._vlm_processor

    async def _vlm_processor_for(self, ctx: RequestContext) -> "VLMProcessor":
        from openviking.parse.vlm import VLMProcessor

        if self.vlm_resolver is None:
            raise RuntimeError(
                "ResourceProcessor requires a VLM resolver for account-owned work"
            )
        return VLMProcessor(vlm=await self.vlm_resolver.get_vlm(ctx.account_id))

    def _get_media_processor(self):
        """Lazy initialization of unified media processor."""
        if self._media_processor is None:
            from openviking.utils.media_processor import UnifiedResourceProcessor

            self._media_processor = UnifiedResourceProcessor(
                storage=self.media_storage,
            )
        return self._media_processor

    async def _media_processor_for(self, ctx: RequestContext):
        from openviking.utils.media_processor import UnifiedResourceProcessor

        if self.vlm_resolver is None and self._media_processor is not None:
            return self._media_processor
        if self.vlm_resolver is None:
            return self._get_media_processor()
        return UnifiedResourceProcessor(
            vlm_processor=await self._vlm_processor_for(ctx),
            storage=self.media_storage,
        )

    def _build_parse_output_store(self):
        """Return the configured parse output store, or None for AGFS mode.

        Local artifacts are consumed and committed to formal AGFS storage in
        this synchronous request phase. They never cross an asynchronous queue
        boundary, so parse and commit only need to run in the same process.
        AGFS (the default) returns None to preserve its temp-tree path.
        """
        try:
            parse_output = get_openviking_config().storage.parse_output
        except Exception:
            return None
        if getattr(parse_output, "mode", "agfs") != "local":
            return None
        from openviking.parse.output import build_parse_output_store

        return build_parse_output_store(
            backend="local", local_root=parse_output.resolved_local_root()
        )

    @staticmethod
    def _artifact_doc_rel(artifact_ref: Any, temp_doc_uri: Optional[str]) -> str:
        """Return the document root's path relative to the artifact root.

        ``temp_doc_uri`` is ``<artifact_root>/<doc_rel>`` (finalize built it), so
        the relative document path is the suffix after the artifact root.
        """
        root = artifact_ref.root.rstrip("/")
        doc = str(temp_doc_uri or "").rstrip("/")
        if doc.startswith(f"{root}/"):
            return doc[len(root) + 1 :]
        return str(getattr(artifact_ref, "resource_rel", "") or "").strip("/")

    @staticmethod
    def _ensure_parse_artifact_ref(parse_result: Any) -> Any:
        """Resolve an artifact ref for every supported parser result."""
        artifact_ref = getattr(parse_result, "artifact_ref", None)
        ensure = getattr(parse_result, "ensure_artifact_ref", None)
        if callable(ensure):
            artifact_ref = ensure()
        if artifact_ref is not None:
            return artifact_ref
        temp_dir_path = str(getattr(parse_result, "temp_dir_path", "") or "")
        if not temp_dir_path.startswith("viking://temp/"):
            raise RuntimeError("add_resources requires a parse artifact")
        from openviking.parse.output import ParseArtifactRef

        return ParseArtifactRef(backend="agfs", root=temp_dir_path, root_type="dir")

    @staticmethod
    async def _cleanup_parse_result_artifact(
        parse_result: Any, *, output_store: Any, viking_fs: Any, ctx: RequestContext
    ) -> None:
        artifact_ref = getattr(parse_result, "artifact_ref", None)
        if artifact_ref is not None:
            from openviking.parse.output import store_for_artifact_ref

            store = (
                output_store
                if output_store is not None and output_store.backend == artifact_ref.backend
                else store_for_artifact_ref(artifact_ref, viking_fs=viking_fs, ctx=ctx)
            )
            await store.cleanup(artifact_ref)
        elif parse_result.temp_dir_path:
            await viking_fs.delete_temp(parse_result.temp_dir_path, ctx=ctx)

    @staticmethod
    def _store_for_parse_artifact(
        artifact_ref: Any, *, output_store: Any, viking_fs: Any, ctx: RequestContext
    ) -> Any:
        if output_store is not None and output_store.backend == artifact_ref.backend:
            return output_store
        from openviking.parse.output import store_for_artifact_ref

        return store_for_artifact_ref(artifact_ref, viking_fs=viking_fs, ctx=ctx)

    async def _commit_directory_artifact_with_plan(
        self,
        *,
        output_store: Any,
        artifact_ref: Any,
        doc_rel: str,
        root_uri: str,
        target_preexisting: bool,
        root_is_file: bool = False,
        ctx: RequestContext,
        lease_ref: Optional[Dict[str, Any]],
        vectorize: bool,
        summarize: bool,
        processing_mode: ProcessingMode,
        is_code_repo: bool,
        ingest_options: IngestOptions,
        source_metadata: Optional[Dict[str, str]],
    ) -> Any:
        """Resolve and commit one artifact through the canonical update plan."""
        from openviking.metrics.datasources.resource import ResourceIngestionEventDataSource
        from openviking.storage.context_update_plan import (
            build_context_update_plan_from_snapshot,
            execute_content_tree_actions,
        )
        from openviking.storage.resource_diff import (
            build_rnfv_snapshot,
            count_tree_entry_kinds,
            prepare_artifact_inventory,
        )
        from openviking.storage.resource_target import AgfsResourceTarget

        target = AgfsResourceTarget(
            viking_fs=get_viking_fs(),
            root_uri=root_uri,
            ctx=ctx,
            lease_ref=lease_ref,
        )
        telemetry = get_current_telemetry()
        artifact_backend = str(getattr(artifact_ref, "backend", "unknown"))
        update_plan_started_at = time.perf_counter()
        try:
            with telemetry.measure("resource.update_plan.artifact_inventory"):
                artifact_inventory = await prepare_artifact_inventory(
                    output_store,
                    artifact_ref,
                    doc_rel=doc_rel,
                    target_root_uri=root_uri,
                    root_is_file=root_is_file,
                )
            plan_processing_mode = (
                processing_mode
                if processing_mode == VECTORS_ONLY or summarize or vectorize
                else VECTORS_ONLY
            )
            request = RequestIntent.from_ingest_options(
                target_uri=root_uri,
                processing_mode=plan_processing_mode,
                ingest_options=ingest_options,
                vectorize=vectorize,
            )
            with telemetry.measure("resource.update_plan.rnfv_snapshot"):
                rnfv = await build_rnfv_snapshot(
                    viking_fs=get_viking_fs(),
                    vikingdb=self.vikingdb,
                    store=output_store,
                    artifact_ref=artifact_ref,
                    target_uri=root_uri,
                    ctx=ctx,
                    doc_rel=doc_rel,
                    request_intent=request,
                    target_preexisting=target_preexisting,
                    artifact_inventory=artifact_inventory,
                    root_is_file=root_is_file,
                )
            from collections import Counter

            n_files, n_dirs, _ = count_tree_entry_kinds(rnfv.new.entries)
            f_files, f_dirs, _ = count_tree_entry_kinds(rnfv.formal.entries)
            logger.debug(
                "[RNFVSnapshot] %s target=%s artifact_backend=%s root_is_file=%s "
                "target_preexisting=%s n_files=%d n_dirs=%d f_files=%d f_dirs=%d "
                "v_records=%d v_levels=%s",
                log_correlation(),
                root_uri,
                artifact_backend,
                root_is_file,
                target_preexisting,
                n_files,
                n_dirs,
                f_files,
                f_dirs,
                len(rnfv.vectors.records_by_id),
                dict(Counter(record.level for record in rnfv.vectors.records_by_id.values())),
            )
            with telemetry.measure("resource.update_plan.diff_and_compile"):
                diff, context_plan = await build_context_update_plan_from_snapshot(
                    snapshot=rnfv,
                    store=_DocRelStore(output_store, doc_rel),
                    artifact_ref=artifact_ref,
                    target=target,
                    vikingdb=self.vikingdb,
                    context_type=context_type_for_uri(root_uri),
                    is_code_repo=is_code_repo,
                    account_id=ctx.account_id,
                    ctx=ctx,
                    root_preexisting=target_preexisting,
                    artifact_paths=artifact_inventory.artifact_paths,
                    ingest_options=ingest_options,
                    source_metadata=source_metadata,
                    root_is_file=root_is_file,
                )
        except Exception:
            telemetry.set(
                "resource.update_plan.duration_ms",
                (time.perf_counter() - update_plan_started_at) * 1000.0,
            )
            ResourceIngestionEventDataSource.record_stage(
                stage="update_plan",
                status="error",
                duration_seconds=time.perf_counter() - update_plan_started_at,
                account_id=ctx.account_id,
            )
            raise
        telemetry.set(
            "resource.update_plan.duration_ms",
            (time.perf_counter() - update_plan_started_at) * 1000.0,
        )
        ResourceIngestionEventDataSource.record_stage(
            stage="update_plan",
            status="ok",
            duration_seconds=time.perf_counter() - update_plan_started_at,
            account_id=ctx.account_id,
        )

        content_commit_started_at = time.perf_counter()
        try:
            with telemetry.measure("resource.content_commit"):
                await execute_content_tree_actions(
                    context_plan.content_tree_actions,
                    store=_DocRelStore(output_store, doc_rel),
                    artifact_ref=artifact_ref,
                    target=target,
                )
        except Exception as exc:
            ResourceIngestionEventDataSource.record_stage(
                stage="content_commit",
                status="error",
                duration_seconds=time.perf_counter() - content_commit_started_at,
                account_id=ctx.account_id,
            )
            logger.exception(
                "[ContentTreeCommitFailed] %s target=%s artifact_backend=%s actions=%d error=%s",
                log_correlation(),
                root_uri,
                artifact_backend,
                len(context_plan.content_tree_actions),
                exc,
            )
            raise
        ResourceIngestionEventDataSource.record_stage(
            stage="content_commit",
            status="ok",
            duration_seconds=time.perf_counter() - content_commit_started_at,
            account_id=ctx.account_id,
        )
        self._log_content_tree_commit(
            root_uri=root_uri,
            artifact_backend=artifact_backend,
            actions=context_plan.content_tree_actions,
            duration_ms=(time.perf_counter() - content_commit_started_at) * 1000.0,
        )
        self._log_context_update_plan(context_plan)
        self._log_context_commit_summary(
            diff,
            content_actions=context_plan.content_tree_actions,
            root_uri=root_uri,
            is_initial=not target_preexisting,
        )
        return context_plan.after_content_commit()

    @staticmethod
    def _log_content_tree_commit(
        *, root_uri: str, artifact_backend: str, actions: Any, duration_ms: float
    ) -> None:
        log = logger.info if actions else logger.debug
        log(
            "[ContentTreeCommit] %s target=%s artifact_backend=%s uploaded_files=%d "
            "created_dirs=%d deleted_paths=%d replaced_kinds=%d duration_ms=%.3f",
            log_correlation(),
            root_uri,
            artifact_backend,
            sum(action.new_kind == "file" for action in actions),
            sum(action.new_kind == "directory" for action in actions),
            sum(action.operation.value == "delete" for action in actions),
            sum(action.operation.value == "replace_kind" for action in actions),
            duration_ms,
        )

    @staticmethod
    def _log_context_update_plan(plan: Any) -> None:
        from collections import Counter

        content = Counter(action.operation.value for action in plan.content_tree_actions)
        direct = Counter(action.action.value for action in plan.direct_index_actions)
        entries = plan.semantic_plan.tree.entries if plan.semantic_plan is not None else ()
        semantic = Counter(entry.semantic_action.value for entry in entries)
        slots = Counter(slot.action.value for entry in entries for slot in entry.index_slots)
        logger.debug(
            "[ContextUpdatePlan] %s root=%s content=%s semantic=%s direct_index=%s "
            "index_slots=%s "
            "semantic_entries=%d execution_roots=%d",
            log_correlation(),
            plan.root_uri,
            dict(content),
            dict(semantic),
            dict(direct),
            dict(slots),
            len(entries),
            len(plan.semantic_plan.execution_root_uris()) if plan.semantic_plan is not None else 0,
        )

    @staticmethod
    def _log_context_commit_summary(
        diff: Any, *, content_actions: Any, root_uri: str, is_initial: bool
    ) -> None:
        from collections import Counter

        states = Counter(entry.content_state.value for entry in diff.entries.values())
        uploaded_files = sum(action.new_kind == "file" for action in content_actions)
        created_dirs = sum(action.new_kind == "directory" for action in content_actions)
        if is_initial:
            logger.debug(
                "[add_resource] %s initial import committed root=%s files=%d dirs=%d",
                log_correlation(),
                root_uri,
                uploaded_files,
                created_dirs,
            )
            return
        logger.debug(
            "[add_resource] %s incremental diff committed root=%s states=%s uploaded=%d",
            log_correlation(),
            root_uri,
            dict(states),
            uploaded_files,
        )

    @staticmethod
    def _empty_directory_error(meta: Dict[str, Any]) -> str:
        """Build a bounded error message for a directory with no successful files."""
        failed_files = meta.get("failed_files")
        failures = failed_files if isinstance(failed_files, list) else []
        try:
            total_processable = int(meta.get("total_processable", 0) or 0)
        except (TypeError, ValueError):
            total_processable = 0

        if total_processable > 0:
            message = (
                "Directory import produced no content: "
                f"all {total_processable} processable file(s) failed"
            )
        else:
            message = "Directory import produced no content: no processable files were selected"

        details = ResourceProcessor._failed_file_details(failures)
        if details:
            message += "; failed files: " + "; ".join(details)
            if len(failures) > len(details):
                message += f"; ... {len(failures) - len(details)} more"
        return message

    @staticmethod
    def _directory_parse_failures(meta: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Return files selected for parsing whose parser did not produce content."""
        failed_files = meta.get("failed_files")
        if not isinstance(failed_files, list):
            return []
        return [
            item
            for item in failed_files
            if isinstance(item, dict)
            and (item.get("status") == "failed" or (not item.get("status") and "error" in item))
        ]

    @staticmethod
    def _incomplete_directory_error(failures: List[Dict[str, Any]]) -> str:
        message = f"Directory import incomplete: {len(failures)} file(s) failed to parse"
        details = ResourceProcessor._failed_file_details(failures)
        if details:
            message += "; failed files: " + "; ".join(details)
            if len(failures) > len(details):
                message += f"; ... {len(failures) - len(details)} more"
        return message

    @staticmethod
    def _failed_file_details(failures: List[Dict[str, Any]]) -> List[str]:
        details: List[str] = []
        for item in failures[:5]:
            if not isinstance(item, dict):
                continue
            path = str(item.get("path") or "<unknown>")
            reason = str(item.get("error") or item.get("reason") or "failed")
            remote_ids = [
                f"{key}={item[key]}" for key in ("file_id", "response_id") if item.get(key)
            ]
            if remote_ids:
                path = f"{path} ({', '.join(remote_ids)})"
            details.append(f"{path}: {reason[:120]}")
        return details

    async def prepare_durable_source(
        self,
        path: str,
        ctx: RequestContext,
        *,
        snapshot_required: bool = False,
        allow_local_path_resolution: bool = True,
        **kwargs,
    ) -> Optional["LocalResource"]:
        """Freeze a source when durable routing cannot safely defer access."""
        media_processor = await self._media_processor_for(ctx)
        if not snapshot_required and not media_processor.durable_route_requires_preparation(
            path, **kwargs
        ):
            return None
        kwargs = await self._source_config_kwargs(path, ctx, kwargs)
        with get_viking_fs().bind_request_context(ctx):
            return await media_processor.prepare(
                path,
                allow_local_path_resolution=allow_local_path_resolution,
                **kwargs,
            )

    def understanding_api_enabled(self) -> bool:
        return self._get_media_processor().understanding_api_enabled()

    def should_use_understanding_api(self, source: Union[str, "LocalResource"]) -> bool:
        return self._get_media_processor().should_use_understanding_api(source)

    def should_use_understanding_directly(self, source: str, **kwargs) -> bool:
        return self._get_media_processor().should_use_understanding_directly(source, **kwargs)

    async def submit_understanding(self, source: Union[str, "LocalResource"], **kwargs) -> str:
        return await self._get_media_processor().submit_understanding(source, **kwargs)

    async def upload_understanding_file(self, source: Union[str, "LocalResource"]) -> str:
        return await self._get_media_processor().upload_understanding_file(source)

    async def build_index(
        self, resource_uris: List[str], ctx: RequestContext, **kwargs
    ) -> Dict[str, Any]:
        """Expose index building as a standalone method."""
        ingest_options = IngestOptions.from_value(kwargs.get("ingest_options"))
        if ingest_options.search_tags is None and kwargs.get("search_tags") is not None:
            ingest_options = IngestOptions.from_search_tags(
                kwargs.get("search_tags"),
                mode=kwargs.get("search_tag_mode", "replace"),
            )
        for uri in resource_uris:
            await index_resource(
                uri,
                ctx,
                ingest_options=ingest_options,
            )
        return {"status": "success", "message": f"Indexed {len(resource_uris)} resources"}

    async def summarize(
        self, resource_uris: List[str], ctx: RequestContext, **kwargs
    ) -> Dict[str, Any]:
        """Expose summarization as a standalone method."""
        return await (await self._summarizer_for(ctx)).summarize(resource_uris, ctx, **kwargs)

    async def process_resource(
        self,
        path: str,
        ctx: RequestContext,
        reason: str = "",
        instruction: str = "",
        scope: str = "resources",
        user: Optional[str] = None,
        to: Optional[str] = None,
        parent: Optional[str] = None,
        summarize: bool = False,
        stage_callback: Optional[Callable[[str], Any]] = None,
        prepared_resource: Optional["LocalResource"] = None,
        tags: Optional[List[str]] = None,
        tag_mode: str = "replace",
        acl: AclSpec | Dict[str, Any] | None = None,
        **kwargs,
    ) -> Dict[str, Any]:
        """
        Process and store a new resource.

        Workflow:
        1. Parse source (writes to temp directory)
        2. TreeBuilder builds final URI metadata
        3. Source commit moves temp content to the final path
        4. (Optional) Build vector index
        5. (Optional) Summarize
        """
        result = {
            "status": "success",
            "errors": [],
            "source_path": None,
        }
        defer_post_processing = bool(kwargs.pop("defer_post_processing", False))
        preacquired_lock = kwargs.pop("resource_lock", None)
        ingest_options = IngestOptions.from_search_tags(tags, mode=tag_mode)
        to_is_directory = bool(kwargs.pop("to_is_directory", False))
        telemetry = get_current_telemetry()
        metrics_account_id = getattr(ctx, "account_id", None)

        async def _set_stage(stage: str) -> None:
            if stage_callback is None:
                return
            result = stage_callback(stage)
            if inspect.isawaitable(result):
                await result

        with telemetry.measure("resource.source_execute"):
            # ============ Phase 1: Parse source and writes to temp viking fs ============
            try:
                from openviking.metrics.datasources.resource import (
                    ResourceIngestionEventDataSource,
                )

                media_processor = await self._media_processor_for(ctx)
                viking_fs = get_viking_fs()
                # Use reason as instruction fallback so it influences L0/L1
                # generation and improves search relevance as documented.
                effective_instruction = instruction or reason
                # Local artifact mode writes parse output to this worker's disk
                # instead of AGFS temp. The same synchronous request commits all
                # required bytes to formal AGFS before any async queue handoff.
                # The store is task-scoped; default AGFS mode leaves kwargs untouched.
                output_store = self._build_parse_output_store()
                if output_store is not None:
                    kwargs.setdefault("parse_output_store", output_store)
                if path.startswith(("http://", "https://", "git@", "ssh://", "git://")):
                    await _set_stage("fetching")
                else:
                    await _set_stage("parsing")
                kwargs = await self._source_config_kwargs(path, ctx, kwargs)
                with viking_fs.bind_request_context(ctx):
                    parse_result = await media_processor.process(
                        source=path,
                        instruction=effective_instruction,
                        prepared_resource=prepared_resource,
                        _metrics_account_id=metrics_account_id,
                        **kwargs,
                    )
                result["source_path"] = parse_result.source_path or path
                result["meta"] = parse_result.meta

                # Only abort when no temp content was produced at all.
                # For directory imports partial success (some files failed) is
                # normal - finalization should still proceed.
                if not parse_result.temp_dir_path:
                    result["status"] = "error"
                    result["errors"].extend(
                        parse_result.warnings or ["Parse failed: no content generated"],
                    )
                    return result

                parse_meta = parse_result.meta if isinstance(parse_result.meta, dict) else {}
                is_directory_aggregate = all(
                    key in parse_meta
                    for key in (
                        "file_count",
                        "total_processable",
                        "processed_files",
                        "failed_files",
                    )
                )
                if is_directory_aggregate and parse_meta.get("file_count") == 0:
                    result["status"] = "error"
                    result["errors"].append(self._empty_directory_error(parse_meta))
                    try:
                        await self._cleanup_parse_result_artifact(
                            parse_result, output_store=output_store, viking_fs=viking_fs, ctx=ctx
                        )
                    except Exception as exc:
                        logger.warning(
                            "[ResourceProcessor] Failed to clean empty directory temp %s: %s",
                            parse_result.temp_dir_path,
                            exc,
                        )
                    return result

                parse_failures = self._directory_parse_failures(parse_meta)
                if is_directory_aggregate and parse_failures:
                    result["status"] = "error"
                    result["errors"].append(self._incomplete_directory_error(parse_failures))
                    try:
                        await self._cleanup_parse_result_artifact(
                            parse_result, output_store=output_store, viking_fs=viking_fs, ctx=ctx
                        )
                    except Exception as exc:
                        logger.warning(
                            "[ResourceProcessor] Failed to clean incomplete directory temp %s: %s",
                            parse_result.temp_dir_path,
                            exc,
                        )
                    return result

                if parse_result.warnings and kwargs.get("strict", False):
                    result.setdefault("warnings", []).extend(parse_result.warnings)

            except OpenVikingError:
                raise
            except Exception as e:
                result["status"] = "error"
                error_message = f"Parse error: {e}"
                error_meta = getattr(e, "meta", {})
                if isinstance(error_meta, dict) and error_meta.get("response_id"):
                    error_message += f" (response_id={error_meta['response_id']})"
                result["errors"].append(error_message)
                logger.error(f"[ResourceProcessor] Parse error: {e}")
                telemetry.set_error("resource_processor.parse_artifact", "PROCESSING_ERROR", str(e))
                import traceback

                traceback.print_exc()
                return result

            # parse_result contains:
            # - root: ResourceNode tree (with L0/L1 in meta)
            # - temp_dir_path: Temporary directory path (Parser wrote all files)
            # - source_path, source_format

            # ============ Phase 3: TreeBuilder finalizes from temp (scan + move to AGFS) ============
            try:
                await _set_stage("target_resolve")
                stage_start = time.perf_counter()
                stage_status = "ok"
                finalize_start = time.perf_counter()
                artifact_ref = getattr(parse_result, "artifact_ref", None)
                artifact_output_store = (
                    self._store_for_parse_artifact(
                        artifact_ref,
                        output_store=output_store,
                        viking_fs=viking_fs,
                        ctx=ctx,
                    )
                    if artifact_ref is not None
                    else None
                )
                with get_viking_fs().bind_request_context(ctx):
                    context_tree = await self.tree_builder.finalize_from_temp(
                        temp_dir_path=parse_result.temp_dir_path,
                        ctx=ctx,
                        scope=scope,
                        to_uri=to,
                        parent_uri=parent,
                        source_path=parse_result.source_path,
                        source_format=parse_result.source_format,
                        create_parent=kwargs.get("create_parent", False),
                        flatten_single_file=(
                            normalize_parse_mode(kwargs.get("parse_mode", ParseMode.DEFAULT))
                            is ParseMode.NO_SPLIT
                            and parse_result.source_format not in {"directory", "repository"}
                            and not to_is_directory
                        ),
                        artifact_ref=artifact_ref,
                        output_store=artifact_output_store,
                    )
                    if context_tree and context_tree.root:
                        result["root_uri"] = context_tree.root.uri
                        result["temp_uri"] = context_tree.root.temp_uri
                    root_is_file = bool(getattr(context_tree, "_root_is_file", False))
                telemetry.set(
                    "resource.target_resolve.duration_ms",
                    round((time.perf_counter() - finalize_start) * 1000, 3),
                )
            except Exception as e:
                result["status"] = "error"
                result["errors"].append(f"Finalize from temp error: {e}")
                telemetry.set_error("resource_processor.target_resolve", "PROCESSING_ERROR", str(e))
                stage_status = "error"

                # Cleanup the parser-owned artifact through its own backend.
                try:
                    await self._cleanup_parse_result_artifact(
                        parse_result,
                        output_store=output_store,
                        viking_fs=get_viking_fs(),
                        ctx=ctx,
                    )
                except Exception:
                    pass

                return result
            finally:
                try:
                    ResourceIngestionEventDataSource.record_stage(
                        stage="target_resolve",
                        status=str(stage_status),
                        duration_seconds=float(time.perf_counter() - stage_start),
                        account_id=getattr(ctx, "account_id", None),
                    )
                except Exception:
                    pass

            # ============ Phase 3.5: Source commit + resource lock ============
            root_uri = result.get("root_uri")
            temp_uri = result.get("temp_uri")  # temp_doc_uri
            original_temp_uri = temp_uri  # 保存原始 temp_uri 用于最终输出
            candidate_uri = getattr(context_tree, "_candidate_uri", None) if context_tree else None
            resource_lock: Optional[Dict[str, Any]] = preacquired_lock
            target_preexisting = False
            source_committed = False
            local_artifact_doc_rel = ""
            incremental_noop = False
            context_update_plan = None

            if root_uri and temp_uri:
                viking_fs = get_viking_fs()
                try:
                    if candidate_uri:
                        if resource_lock is not None:
                            root_uri = candidate_uri
                        else:
                            root_uri, resource_lock = await self.reserve_unique_candidate(
                                candidate_uri=candidate_uri,
                                ctx=ctx,
                                root_is_file=root_is_file,
                            )
                            result["root_uri"] = root_uri
                            if root_uri != candidate_uri:
                                result.setdefault("warnings", []).append(
                                    f"'{candidate_uri}' already exists. Creating '{root_uri}'. "
                                    f"Tip: Use --to <path> to specify exact target."
                                )
                    else:
                        target_preexisting = await viking_fs.exists(root_uri, ctx=ctx)
                        if target_preexisting:
                            try:
                                stat = await viking_fs.stat(root_uri, ctx=ctx, skip_count=True)
                                if isinstance(stat, dict) and stat.get("isDir"):
                                    entries = await viking_fs.ls(
                                        root_uri,
                                        show_all_hidden=True,
                                        node_limit=LS_ALL_NODES,
                                        ctx=ctx,
                                    )
                                    names: list[str] = []
                                    for entry in entries:
                                        name = entry.get("name", "")
                                        if not name or name in {".", ".."}:
                                            continue
                                        names.append(str(name))
                                    if all(is_storage_internal_name(name) for name in names):
                                        target_preexisting = False
                            except Exception:
                                pass
                        if resource_lock is None:
                            dst_path = viking_fs._uri_to_path(root_uri, ctx=ctx)
                            resource_lock = await self.acquire_resource_lock(
                                dst_path,
                                uri=root_uri,
                                root_is_file=root_is_file,
                            )
                    if acl is not None:
                        ingest_options = replace(
                            ingest_options,
                            acl_update=await viking_fs.prepare_acl_update(root_uri, acl, ctx),
                        )
                    artifact_ref = self._ensure_parse_artifact_ref(parse_result)
                    artifact_store = self._store_for_parse_artifact(
                        artifact_ref, output_store=output_store, viking_fs=viking_fs, ctx=ctx
                    )
                    local_artifact_doc_rel = self._artifact_doc_rel(artifact_ref, temp_uri)
                    context_update_plan = await self._commit_directory_artifact_with_plan(
                        output_store=artifact_store,
                        artifact_ref=artifact_ref,
                        doc_rel=local_artifact_doc_rel,
                        root_uri=root_uri,
                        target_preexisting=target_preexisting,
                        root_is_file=root_is_file,
                        ctx=ctx,
                        lease_ref=resource_lock,
                        vectorize=bool(kwargs.get("build_index", True)),
                        summarize=summarize,
                        processing_mode=normalize_processing_mode(kwargs.get("processing_mode")),
                        ingest_options=ingest_options,
                        is_code_repo=parse_result.source_format == "repository",
                        source_metadata=self._semantic_source_metadata(
                            path=path,
                            prepared_resource=prepared_resource,
                            source_format=parse_result.source_format,
                        ),
                    )
                    incremental_noop = target_preexisting and context_update_plan.is_noop()
                    temp_uri = root_uri
                    source_committed = True
                except BaseException:
                    # Mirror the Phase 3 (finalize) on-error cleanup: a lock or
                    # persist failure here would otherwise orphan the
                    # viking://temp tree with no GC (#2478). Skip when the temp
                    # tree was already persisted + deleted on the success path.
                    try:
                        if not source_committed:
                            await self._cleanup_parse_result_artifact(
                                parse_result,
                                output_store=output_store,
                                viking_fs=get_viking_fs(),
                                ctx=ctx,
                            )
                    except Exception:
                        pass
                    finally:
                        # The caller still owns a preacquired lease. Release
                        # leases acquired here if commit fails before handoff.
                        if resource_lock is not None and preacquired_lock is None:
                            await viking_fs._async_agfs.pathlock_release(resource_lock)
                    raise

            if artifact_ref is not None:
                artifact_store = self._store_for_parse_artifact(
                    artifact_ref,
                    output_store=output_store,
                    viking_fs=get_viking_fs(),
                    ctx=ctx,
                )
                try:
                    await artifact_store.cleanup(artifact_ref)
                except Exception as exc:
                    logger.warning(
                        "[ResourceProcessor] Failed to clean committed parse artifact %s: %s",
                        artifact_ref.root,
                        exc,
                    )
                artifact_ref = None
            prepared_artifact_ref = artifact_ref.to_dict() if artifact_ref is not None else None
            if prepared_artifact_ref is not None and artifact_ref.backend == "local":
                prepared_artifact_ref["resource_rel"] = local_artifact_doc_rel
            prepared = {
                "root_uri": root_uri,
                "temp_uri": temp_uri or parse_result.temp_dir_path,
                "temp_dir_path": parse_result.temp_dir_path,
                "artifact_ref": prepared_artifact_ref,
                "source_committed": source_committed,
                "target_preexisting": target_preexisting,
                "is_code_repo": parse_result.source_format == "repository",
                "root_is_file": root_is_file,
                "incremental_noop": incremental_noop,
                "acl_update": ingest_options.acl_update.model_dump(mode="json")
                if ingest_options.acl_update
                else None,
                "context_update_plan": (
                    context_update_plan.to_dict() if context_update_plan is not None else None
                ),
                "semantic_source": self._semantic_source_metadata(
                    path=path,
                    prepared_resource=prepared_resource,
                    source_format=parse_result.source_format,
                ),
            }
            if defer_post_processing:
                result["_post_process"] = prepared
                result["_resource_lock"] = resource_lock
            else:
                post_result = await self.finish_prepared_resource(
                    prepared,
                    ctx=ctx,
                    resource_lock=resource_lock,
                    summarize=summarize,
                    ingest_options=ingest_options,
                    build_index=bool(kwargs.get("build_index", True)),
                    processing_mode=normalize_processing_mode(kwargs.get("processing_mode")),
                )
                if post_result.get("warnings"):
                    result.setdefault("warnings", []).extend(post_result["warnings"])

            # 恢复原始 temp_uri 用于输出
            if original_temp_uri is not None:
                result["temp_uri"] = original_temp_uri

            return result

    async def finish_prepared_resource(
        self,
        prepared: Dict[str, Any],
        *,
        ctx: RequestContext,
        resource_lock: Optional[Dict[str, Any]] = None,
        summarize: bool = False,
        processing_mode: ProcessingMode = DEFAULT_PROCESSING_MODE,
        build_index: bool = True,
        ingest_options: IngestOptions | None = None,
    ) -> Dict[str, Any]:
        """Run the queue-producing phase for a resource already stored in VikingFS."""
        from openviking.metrics.datasources.resource import ResourceIngestionEventDataSource

        root_uri = str(prepared.get("root_uri") or "")
        temp_uri = prepared.get("temp_uri")
        temp_dir_path = prepared.get("temp_dir_path")
        # Canonical plans contain only final resource URIs. An artifact ref is
        # present here only for legacy non-plan paths that still need it.
        artifact_ref_data = prepared.get("artifact_ref")
        artifact_ref = None
        if artifact_ref_data is not None:
            from openviking.parse.output import ParseArtifactRef

            artifact_ref = ParseArtifactRef.from_dict(artifact_ref_data)
            if artifact_ref.backend not in {"agfs", "local"}:
                raise ValueError(
                    f"Unsupported parse artifact backend in post-process: {artifact_ref.backend}"
                )
        source_committed = bool(prepared.get("source_committed"))
        metrics_account_id = getattr(ctx, "account_id", None)
        target_preexisting = bool(prepared.get("target_preexisting"))
        processing_mode = normalize_processing_mode(processing_mode)
        vectors_only = processing_mode == VECTORS_ONLY
        root_is_file = bool(prepared.get("root_is_file"))
        ingest_options = ingest_options or IngestOptions()
        if prepared.get("acl_update") is not None:
            ingest_options = replace(
                ingest_options, acl_update=AclUpdate.model_validate(prepared["acl_update"])
            )
        semantic_source = prepared.get("semantic_source")
        context_update_plan_data = prepared.get("context_update_plan")
        context_update_plan = None
        if context_update_plan_data is not None:
            from openviking.storage.context_update_plan import ContextUpdatePlan

            context_update_plan = ContextUpdatePlan.from_dict(context_update_plan_data)
            semantic_plan = (
                context_update_plan.semantic_plan.to_dict()
                if context_update_plan.semantic_plan is not None
                else None
            )
            direct_index_actions = context_update_plan.direct_index_actions
        else:
            semantic_plan = None
            direct_index_actions = ()
        uses_context_update_plan = context_update_plan_data is not None
        should_summarize = (
            not root_is_file
            and not vectors_only
            and (summarize or build_index)
            and (not uses_context_update_plan or semantic_plan is not None)
        )
        if context_update_plan is not None:
            file_refresh = context_update_plan.file_refresh
        elif root_is_file and source_committed and not vectors_only and (summarize or build_index):
            from openviking.storage.context_update_plan import FileRefreshIntent

            file_refresh = FileRefreshIntent(
                root_uri, (prepared.get("file_md5s") or {}).get(root_uri)
            )
        else:
            file_refresh = None
        should_refresh_file_parent = file_refresh is not None and not vectors_only
        result: Dict[str, Any] = {"status": "success", "root_uri": root_uri}
        artifact_cleaned = False

        async def cleanup_artifact_if_owned() -> None:
            nonlocal artifact_cleaned
            if artifact_cleaned or artifact_ref is None:
                return
            from openviking.parse.output import AgfsParseOutputStore

            # Local mode reuses the processor's own store seam (test-injectable);
            # agfs mode builds a temp-backed store on demand.
            output_store = (
                self._build_parse_output_store()
                if artifact_ref.backend == "local"
                else AgfsParseOutputStore(viking_fs=get_viking_fs(), ctx=ctx)
            )
            if output_store is not None:
                await output_store.cleanup(artifact_ref)
                artifact_cleaned = True

        derived_enqueue_started_at = time.perf_counter()
        derived_enqueue_status = "ok"
        try:
            with get_current_telemetry().measure("resource.derived_enqueue"):
                if prepared.get("incremental_noop") and not direct_index_actions:
                    if ingest_options.acl_update:
                        await get_viking_fs().acl_manager.apply_indexed_update(
                            ingest_options.acl_update, ctx
                        )
                    await cleanup_artifact_if_owned()
                    if resource_lock is not None:
                        await get_viking_fs()._async_agfs.pathlock_release(resource_lock)
                        resource_lock = None
                    return result

                if direct_index_actions:
                    await self._enqueue_index_actions(
                        direct_index_actions, ctx=ctx, ingest_options=ingest_options
                    )

                if should_summarize:
                    try:
                        summary_result = await (await self._summarizer_for(ctx)).summarize(
                            resource_uris=[root_uri],
                            ctx=ctx,
                            skip_vectorization=not build_index,
                            lock=resource_lock,
                            temp_uris=[temp_uri],
                            is_code_repo=bool(prepared.get("is_code_repo")),
                            ingest_options=ingest_options,
                            semantic_source=semantic_source,
                            generation_trigger="resource_ingest",
                            semantic_plan=semantic_plan,
                        )
                        if semantic_plan is not None and summary_result.get("status") != "success":
                            raise RuntimeError(
                                str(summary_result.get("message") or "semantic plan enqueue failed")
                            )
                        if (
                            resource_lock is not None
                            and summary_result.get("status") == "success"
                            and summary_result.get("enqueued_count", 0) > 0
                        ):
                            await get_viking_fs()._async_agfs.pathlock_handoff(resource_lock)
                            resource_lock = None
                        if semantic_plan is not None and (
                            summary_result.get("status") == "success"
                            and summary_result.get("enqueued_count", 0) > 0
                        ):
                            await cleanup_artifact_if_owned()
                    except Exception as exc:
                        logger.error("Semantic enqueue failed: %s", exc)
                        if semantic_plan is not None:
                            raise
                        result["warnings"] = [f"Semantic enqueue failed: {exc}"]
        except BaseException:
            derived_enqueue_status = "error"
            try:
                await cleanup_artifact_if_owned()
            finally:
                if resource_lock is not None:
                    await get_viking_fs()._async_agfs.pathlock_release(resource_lock)
                    resource_lock = None
            raise
        finally:
            ResourceIngestionEventDataSource.record_stage(
                stage="derived_enqueue",
                status=derived_enqueue_status,
                duration_seconds=time.perf_counter() - derived_enqueue_started_at,
                account_id=metrics_account_id,
            )

        if resource_lock is not None:
            try:
                sync_deleted_files: list[str] = []
                sync_deleted_dirs: list[str] = []
                if not should_summarize and temp_uri and not source_committed:
                    viking_fs = get_viking_fs()
                    if vectors_only and target_preexisting and not root_is_file:
                        diff = await SemanticProcessor(
                            vlm_resolver=self.vlm_resolver
                        )._sync_topdown_recursive(
                            temp_uri, root_uri, ctx=ctx, lock=resource_lock
                        )
                        sync_deleted_files = list(getattr(diff, "deleted_files", []))
                        sync_deleted_dirs = list(getattr(diff, "deleted_dirs", []))
                    else:
                        await viking_fs.persist_temp_tree(
                            temp_uri, root_uri, ctx=ctx, lease_ref=resource_lock
                        )
                    if not root_is_file:
                        await rewrite_image_uris(
                            root_uri,
                            ctx=ctx,
                            lease_ref=resource_lock,
                        )
                    if temp_dir_path:
                        await viking_fs.delete_temp(temp_dir_path, ctx=ctx)
                if (
                    context_update_plan is None
                    and vectors_only
                    and (sync_deleted_files or sync_deleted_dirs)
                ):
                    await self._delete_removed_resource_vectors(
                        files=sync_deleted_files, dirs=sync_deleted_dirs, ctx=ctx
                    )
                if should_refresh_file_parent:
                    await (await self._summarizer_for(ctx)).refresh_file_parent(
                        file_uri=file_refresh.file_uri,
                        ctx=ctx,
                        skip_vectorization=not build_index,
                        ingest_options=ingest_options,
                        created=not target_preexisting,
                        file_md5=file_refresh.md5,
                        file_abstract="",
                    )
                elif build_index and context_update_plan is None:
                    if root_is_file:
                        await self._vectorize_resource_file(
                            root_uri,
                            ctx=ctx,
                            ingest_options=ingest_options,
                            file_md5=(prepared.get("file_md5s") or {}).get(root_uri),
                        )
                    elif vectors_only:
                        await self._vectorize_resource_files(
                            root_uri, ctx=ctx, ingest_options=ingest_options
                        )
            except BaseException:
                await cleanup_artifact_if_owned()
                raise
            finally:
                await get_viking_fs()._async_agfs.pathlock_release(resource_lock)
        elif should_refresh_file_parent:
            try:
                await (await self._summarizer_for(ctx)).refresh_file_parent(
                    file_uri=file_refresh.file_uri,
                    ctx=ctx,
                    skip_vectorization=not build_index,
                    ingest_options=ingest_options,
                    created=not target_preexisting,
                    file_md5=file_refresh.md5,
                    file_abstract="",
                )
            except BaseException:
                await cleanup_artifact_if_owned()
                raise
        elif build_index and context_update_plan is None and root_is_file:
            try:
                await self._vectorize_resource_file(
                    root_uri,
                    ctx=ctx,
                    ingest_options=ingest_options,
                    file_md5=(prepared.get("file_md5s") or {}).get(root_uri),
                )
            except BaseException:
                await cleanup_artifact_if_owned()
                raise
        elif build_index and context_update_plan is None and vectors_only:
            try:
                artifact_store = (
                    self._build_parse_output_store() if artifact_ref is not None else None
                )
                await self._vectorize_resource_files(
                    root_uri,
                    ctx=ctx,
                    ingest_options=ingest_options,
                    artifact_store=artifact_store,
                    artifact_ref=artifact_ref,
                    artifact_files=prepared.get("artifact_files"),
                    file_md5s=prepared.get("file_md5s"),
                )
            except BaseException:
                await cleanup_artifact_if_owned()
                raise
        if ingest_options.acl_update:
            await get_viking_fs().acl_manager.apply_indexed_update(ingest_options.acl_update, ctx)
        await cleanup_artifact_if_owned()
        return result

    @staticmethod
    def _semantic_source_metadata(
        *,
        path: str,
        prepared_resource: Optional["LocalResource"],
        source_format: Optional[str],
    ) -> Dict[str, str]:
        """Return the stable origin metadata carried only by the import root."""

        if prepared_resource is not None:
            return {
                "kind": str(prepared_resource.source_type),
                "uri": str(prepared_resource.original_source),
            }
        if source_format == "repository":
            kind = "git"
        elif path.startswith(("http://", "https://")):
            kind = "http"
        elif path.startswith(("git@", "ssh://", "git://")):
            kind = "git"
        else:
            kind = "local"
        return {"kind": kind, "uri": str(path)}

    async def _enqueue_index_actions(
        self,
        actions: Any,
        *,
        ctx: RequestContext,
        ingest_options: IngestOptions | None = None,
        source_contents: Optional[Dict[tuple[str, int], str | bytes]] = None,
        source_metadata: Optional[Dict[str, Any]] = None,
    ) -> int:
        from collections import Counter

        from openviking.core.skill_loader import SkillLoader
        from openviking.storage.index_action import IndexAction
        from openviking.storage.queuefs import get_queue_manager
        from openviking.storage.queuefs.embedding_msg import EmbeddingMsg
        from openviking.telemetry import get_current_telemetry
        from openviking.utils.embedding_utils import _enqueue_embedding_message

        if source_contents is not None:
            missing_sources = [
                (action.uri, action.level)
                for action in actions
                if action.action in {IndexAction.UPSERT, IndexAction.MERGE}
                and (action.uri, action.level) not in source_contents
            ]
            if missing_sources:
                uri, level = missing_sources[0]
                source_name = (
                    f"directory L{level}"
                    if level in {int(ContextLevel.ABSTRACT), int(ContextLevel.OVERVIEW)}
                    else "file"
                )
                raise ValueError(f"{source_name} vector source is unavailable: {uri}")

        queue_manager = get_queue_manager()
        embedding_queue = queue_manager.get_queue(queue_manager.EMBEDDING, allow_create=True)
        telemetry_id = get_current_telemetry().telemetry_id
        action_counts = Counter(action.action.value for action in actions)
        completed_actions = 0
        delete_ids = [action.record_id for action in actions if action.action == IndexAction.DELETE]
        if delete_ids:
            message = EmbeddingMsg.for_delete(
                record_ids=delete_ids,
                context_data={
                    "uri": actions[0].uri,
                    "account_id": ctx.account_id,
                    "owner_user_id": ctx.user.user_id,
                },
                telemetry_id=telemetry_id,
            )
            await _enqueue_embedding_message(
                embedding_queue,
                message,
                failure_message="Failed to enqueue planned vector deletes",
            )
            completed_actions += len(delete_ids)
        directory_actions: dict[str, dict[int, Any]] = {}
        for action in actions:
            if action.action in {IndexAction.UPSERT, IndexAction.MERGE}:
                if action.level in {
                    int(ContextLevel.ABSTRACT),
                    int(ContextLevel.OVERVIEW),
                }:
                    directory_actions.setdefault(action.uri, {})[action.level] = action
                    continue
                file_kwargs = {
                    "ctx": ctx,
                    "file_md5": action.md5,
                    "ingest_options": ingest_options,
                    "scalar_override": {
                        **dict(action.upsert_fields),
                        "_record_id": action.record_id,
                    },
                    "action": action.action.value,
                    "field_patch": action.field_patch,
                }
                source = (source_contents or {}).get((action.uri, action.level))
                if source is not None:
                    if not isinstance(source, bytes):
                        raise ValueError(f"file vector source must be bytes: {action.uri}")
                    file_kwargs["file_content"] = source
                    if getattr(self.vikingdb, "uses_content_field", False):
                        file_kwargs["materialize_content"] = True
                if await self._vectorize_resource_file(action.uri, **file_kwargs):
                    completed_actions += 1
                continue
            if action.action != IndexAction.UPDATE_FIELDS:
                continue
            assert action.field_patch is not None
            message = EmbeddingMsg.for_update_fields(
                record_id=action.record_id,
                field_patch=action.field_patch,
                context_data={
                    "uri": action.uri,
                    "level": action.level,
                    "account_id": ctx.account_id,
                    "owner_user_id": ctx.user.user_id,
                },
                telemetry_id=telemetry_id,
            )
            if await _enqueue_embedding_message(
                embedding_queue,
                message,
                failure_message=f"Failed to enqueue scalar update for {action.uri}",
            ):
                completed_actions += 1
        for uri, levels in directory_actions.items():
            abstract = (source_contents or {}).get((uri, int(ContextLevel.ABSTRACT)), "")
            overview = (source_contents or {}).get((uri, int(ContextLevel.OVERVIEW)), "")
            if isinstance(abstract, bytes):
                abstract = abstract.decode("utf-8")
            if isinstance(overview, bytes):
                overview = overview.decode("utf-8")
            if int(ContextLevel.ABSTRACT) in levels and not isinstance(abstract, str):
                raise ValueError(f"directory L0 vector source is unavailable: {uri}")
            if int(ContextLevel.OVERVIEW) in levels and not isinstance(overview, str):
                raise ValueError(f"directory L1 vector source is unavailable: {uri}")
            meta = None
            if context_type_for_uri(uri) == "skill" and abstract:
                try:
                    skill = SkillLoader.parse(str(abstract))
                except ValueError:
                    skill = None
                if skill is not None:
                    meta = {
                        "name": skill["name"],
                        "description": skill.get("description", ""),
                        "tags": skill.get("tags", []),
                        "allowed_tools": skill.get("allowed_tools", []),
                    }
                else:
                    try:
                        import yaml

                        parsed = yaml.safe_load(str(abstract))
                    except Exception:
                        parsed = None
                    if isinstance(parsed, dict):
                        meta = {
                            key: parsed.get(key, [] if key in {"tags", "allowed_tools"} else "")
                            for key in ("name", "description", "tags", "allowed_tools")
                        }
                if meta is not None and source_metadata is not None:
                    meta["source_path"] = str(source_metadata.get("uri") or "")
            enqueued_levels = await vectorize_directory_meta(
                uri,
                abstract,
                overview,
                context_type=context_type_for_uri(uri),
                ctx=ctx,
                include_abstract=int(ContextLevel.ABSTRACT) in levels,
                include_overview=int(ContextLevel.OVERVIEW) in levels,
                content_is_body=True,
                actions={level: action.action.value for level, action in levels.items()},
                scalar_overrides={
                    level: {
                        **dict(action.upsert_fields),
                        "_record_id": action.record_id,
                    }
                    for level, action in levels.items()
                },
                md5s={
                    level: action.md5 for level, action in levels.items() if action.md5 is not None
                },
                **({"meta": meta} if meta is not None else {}),
            )
            completed_actions += len(enqueued_levels)
        logger.debug(
            "[DirectIndexActions] %s root=%s action_counts=%s action_count=%d",
            log_correlation(),
            actions[0].uri if actions else "",
            dict(action_counts),
            len(actions),
        )
        return completed_actions

    async def _delete_removed_resource_vectors(
        self,
        *,
        files: list[str],
        dirs: list[str],
        ctx: RequestContext,
    ) -> None:
        for uri in dict.fromkeys(files):
            records = await self.vikingdb.get_context_by_uri(
                uri=uri,
                level=int(ContextLevel.DETAIL),
                limit=100,
                ctx=ctx,
            )
            ids = [str(record["id"]) for record in records if record.get("id")]
            if ids:
                await self.vikingdb.delete(ids, ctx=ctx)
        for uri in dict.fromkeys(dirs):
            records = await self.vikingdb.filter(
                filter=And(
                    [
                        PathScope("uri", uri, depth=-1),
                        Eq("level", int(ContextLevel.DETAIL)),
                        Eq("account_id", ctx.account_id),
                    ]
                ),
                limit=VECTORDB_MAX_QUERY_LIMIT,
                output_fields=["id"],
                ctx=ctx,
            )
            ids = [str(record["id"]) for record in records if record.get("id")]
            if ids:
                await self.vikingdb.delete(ids, ctx=ctx)

    async def _vectorize_resource_files(
        self,
        root_uri: str,
        *,
        ctx: RequestContext,
        ingest_options: IngestOptions | None = None,
        artifact_store: Any = None,
        artifact_ref: Any = None,
        artifact_files: Optional[List[str]] = None,
        file_md5s: Optional[Dict[str, str]] = None,
    ) -> None:
        ingest_options = IngestOptions.from_value(ingest_options)
        viking_fs = get_viking_fs()
        files: list[tuple[str, str, str]] = []
        if artifact_files is not None:
            for rel_path in artifact_files:
                entry_uri = VikingURI(root_uri).join(rel_path).uri
                parent = VikingURI(entry_uri).parent
                if parent is not None:
                    files.append((entry_uri, rel_path.rsplit("/", 1)[-1], parent.uri))
        else:
            entries = await viking_fs.tree(
                root_uri,
                node_limit=None,
                level_limit=None,
                ctx=ctx,
            )
            for entry in entries:
                entry_uri = entry.get("uri") if isinstance(entry, dict) else None
                if not entry_uri or entry.get("isDir"):
                    continue
                name = entry.get("name") or entry_uri.rsplit("/", 1)[-1]
                if str(name).startswith("."):
                    continue
                parent = VikingURI(entry_uri).parent
                if parent is None:
                    continue
                files.append((entry_uri, str(name), parent.uri))

        config = get_openviking_config().queue_workers.add_resource
        concurrency = max(
            1,
            min(
                int(config.file_vectorization_concurrency),
                _MAX_FILE_VECTORIZATION_CONCURRENCY,
            ),
        )

        async def vectorize(entry_uri: str, name: str, parent_uri: str) -> None:
            file_content = None
            if artifact_store is not None and artifact_ref is not None:
                rel_path = entry_uri[len(root_uri.rstrip("/")) + 1 :]
                artifact_rel = (
                    f"{artifact_ref.resource_rel.strip('/')}/{rel_path}"
                    if artifact_ref.resource_rel
                    else rel_path
                )
                file_content = await artifact_store.read_bytes(artifact_ref, artifact_rel)
            await vectorize_file(
                file_path=entry_uri,
                summary_dict={"name": name, "summary": ""},
                parent_uri=parent_uri,
                context_type=context_type_for_uri(entry_uri),
                ctx=ctx,
                ingest_options=ingest_options,
                file_md5=(file_md5s or {}).get(entry_uri),
                file_content=file_content,
            )

        for start in range(0, len(files), concurrency):
            tasks = [
                asyncio.create_task(vectorize(entry_uri, name, parent_uri))
                for entry_uri, name, parent_uri in files[start : start + concurrency]
            ]
            try:
                await asyncio.gather(*tasks)
            except BaseException:
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                raise

    async def _vectorize_resource_file(
        self,
        file_uri: str,
        *,
        ctx: RequestContext,
        ingest_options: IngestOptions | None = None,
        file_md5: str | None = None,
        scalar_override: Optional[Dict[str, Any]] = None,
        field_patch: FieldPatch | None = None,
        action: str = "merge",
        file_content: bytes | None = None,
        materialize_content: bool = False,
    ) -> bool:
        parent = VikingURI(file_uri).parent
        if parent is None:
            return False
        name = file_uri.rsplit("/", 1)[-1]
        return await vectorize_file(
            file_path=file_uri,
            summary_dict={"name": name, "summary": ""},
            parent_uri=parent.uri,
            context_type=context_type_for_uri(file_uri),
            ctx=ctx,
            ingest_options=IngestOptions.from_value(ingest_options),
            file_md5=file_md5,
            scalar_override=scalar_override,
            field_patch=field_patch,
            action=action,
            file_content=file_content,
            materialize_content=materialize_content,
        )

    async def reserve_unique_candidate(
        self,
        *,
        candidate_uri: str,
        ctx: RequestContext,
        max_attempts: int = 100,
        root_is_file: bool = False,
    ) -> tuple[str, Dict[str, Any]]:
        """Pick the first free candidate URI and reserve it with a type-aware lock."""
        from openviking.storage.errors import ResourceBusyError

        viking_fs = get_viking_fs()
        last_busy_error: Optional[ResourceBusyError] = None
        await self.ensure_candidate_parent_write_access(candidate_uri=candidate_uri, ctx=ctx)

        for attempt in range(max_attempts + 1):
            root_uri = candidate_uri if attempt == 0 else f"{candidate_uri}_{attempt}"
            if await viking_fs.exists(root_uri, ctx=ctx):
                continue

            dst_path = viking_fs._uri_to_path(root_uri, ctx=ctx)
            try:
                resource_lock = await self.acquire_resource_lock(
                    dst_path,
                    uri=root_uri,
                    timeout=0.0,
                    root_is_file=root_is_file,
                )
                return root_uri, resource_lock
            except ResourceBusyError as exc:
                last_busy_error = exc
                continue

        if last_busy_error is not None:
            raise ResourceBusyError(
                f"All auto-named candidates are temporarily busy for {candidate_uri} "
                f"after checking {max_attempts + 1} candidates",
                uri=candidate_uri,
                conflict_type="auto_name_reservation_busy",
                retryable=True,
            ) from last_busy_error

        raise FileExistsError(
            f"Cannot resolve unique name for {candidate_uri} after {max_attempts} attempts"
        )

    async def ensure_candidate_parent_write_access(
        self,
        *,
        candidate_uri: str,
        ctx: RequestContext,
    ) -> None:
        """Require create permission for an auto-named resource candidate."""
        parent_uri = VikingURI(candidate_uri).parent
        if parent_uri is None:
            raise ValueError(f"Resource candidate must have a parent: {candidate_uri}")
        await get_viking_fs()._ensure_access(
            parent_uri.uri,
            ctx,
            action=AclAction.WRITE,
        )

    @staticmethod
    async def acquire_resource_lock(
        path: str,
        *,
        uri: str = "",
        timeout: float = 0.0,
        root_is_file: bool = False,
    ) -> Dict[str, Any]:
        """Acquire a file-exact or directory-tree resource lock."""
        from openviking.storage.errors import ResourceBusyError

        try:
            pathlock = get_viking_fs()._async_agfs
            acquire = (
                pathlock.pathlock_acquire_exact if root_is_file else pathlock.pathlock_acquire_tree
            )
            return await acquire(path, timeout_secs=timeout)
        except LockAcquisitionError as exc:
            logger.warning(f"[ResourceProcessor] Failed to acquire resource lock on {path}")
            raise ResourceBusyError(
                f"Resource is busy: {uri or path}",
                uri=uri or path,
                conflict_type="path_busy",
                retryable=True,
            ) from exc

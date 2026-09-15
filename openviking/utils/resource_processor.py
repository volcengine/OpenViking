# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
Context Processor for OpenViking.

Handles coordinated writes and self-iteration processes
as described in the OpenViking design document.
"""

import asyncio
import inspect
import time
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, Dict, List, Mapping, Optional, Union

from openviking.core.context import ContextLevel
from openviking.core.namespace import context_type_for_uri
from openviking.parse.image_rewrite import rewrite_image_uris
from openviking.parse.mode import ParseMode, normalize_parse_mode
from openviking.parse.tree_builder import TreeBuilder
from openviking.resource.processing_mode import (
    DEFAULT_PROCESSING_MODE,
    SEMANTIC_AND_VECTORS,
    VECTORS_ONLY,
    ProcessingMode,
    normalize_processing_mode,
)
from openviking.server.identity import RequestContext
from openviking.storage.acl import AclAction, CreatorAclGrant
from openviking.storage.errors import LockAcquisitionError
from openviking.storage.expr import And, Eq, PathScope
from openviking.storage.internal_names import STORAGE_INTERNAL_ENTRY_NAMES
from openviking.storage.queuefs.semantic_processor import SemanticProcessor
from openviking.storage.resource_rnfv import RequestIntent
from openviking.storage.viking_fs import LS_ALL_NODES, get_viking_fs
from openviking.storage.vikingdb_manager import VikingDBManager
from openviking.telemetry import get_current_telemetry
from openviking.utils.embedding_utils import index_resource, vectorize_file
from openviking.utils.ingest_options import IngestOptions
from openviking.utils.summarizer import Summarizer
from openviking_cli.exceptions import OpenVikingError
from openviking_cli.utils import VikingURI, get_logger
from openviking_cli.utils.config import get_openviking_config
from openviking_cli.utils.storage import StoragePath

if TYPE_CHECKING:
    from openviking.parse.accessors.base import LocalResource
    from openviking.parse.vlm import VLMProcessor

logger = get_logger(__name__)
_MAX_FILE_VECTORIZATION_CONCURRENCY = 64
VECTORDB_MAX_QUERY_LIMIT = 100_000


class _DocRelStore:
    """Adapts a parse output store so apply reads use target-relative paths.

    The DiffPlan keys files relative to the resource root (doc_rel stripped),
    but the underlying artifact stores them under ``<doc_rel>/...``. This wrapper
    re-adds the prefix on read so apply_diff_plan can stay backend-agnostic.
    """

    def __init__(self, store: Any, doc_rel: str) -> None:
        self._store = store
        base = (doc_rel or "").strip("/")
        self._base = base

    async def read_bytes(self, ref: Any, rel_path: str) -> bytes:
        artifact_rel = f"{self._base}/{rel_path}" if rel_path else self._base
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
    ):
        """Initialize coordinated writer."""
        self.vikingdb = vikingdb
        self.embedder = vikingdb.get_embedder()
        self.media_storage = media_storage
        self.tree_builder = TreeBuilder()
        self._vlm_processor = None
        self._media_processor = None
        self._summarizer = None

    def _get_summarizer(self) -> "Summarizer":
        """Lazy initialization of Summarizer."""
        if self._summarizer is None:
            self._summarizer = Summarizer(self._get_vlm_processor())
        return self._summarizer

    def _get_vlm_processor(self) -> "VLMProcessor":
        """Lazy initialization of VLM processor."""
        if self._vlm_processor is None:
            from openviking.parse.vlm import VLMProcessor

            self._vlm_processor = VLMProcessor()
        return self._vlm_processor

    def _get_media_processor(self):
        """Lazy initialization of unified media processor."""
        if self._media_processor is None:
            from openviking.utils.media_processor import UnifiedResourceProcessor

            self._media_processor = UnifiedResourceProcessor(
                vlm_processor=self._get_vlm_processor(),
                storage=self.media_storage,
            )
        return self._media_processor

    def _build_parse_output_store(self):
        """Return the configured parse output store, or None for AGFS mode.

        Local mode keeps parse artifacts on a shared local dir; it is only safe
        when every worker in the parse/persist chain shares that path, which the
        operator asserts via config. AGFS (the default) returns None so callers
        keep the legacy temp-tree behaviour untouched.
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
        return ""

    async def _persist_local_artifact(
        self,
        *,
        output_store: Any,
        artifact_ref: Any,
        doc_rel: str,
        root_uri: str,
        root_is_file: bool = False,
        ctx: RequestContext,
        lease_ref: Optional[Dict[str, Any]],
    ) -> Any:
        """Upload a local parse artifact into the final AGFS resource location."""
        from openviking.storage.resource_diff_apply import apply_full_artifact_upload
        from openviking.storage.resource_target import AgfsResourceTarget

        target = AgfsResourceTarget(
            viking_fs=get_viking_fs(),
            vikingdb=self.vikingdb,
            root_uri=root_uri,
            ctx=ctx,
            lease_ref=lease_ref,
        )
        return await apply_full_artifact_upload(
            store=output_store,
            artifact_ref=artifact_ref,
            doc_rel=doc_rel,
            target=target,
            root_is_file=root_is_file,
        )

    async def _apply_local_incremental(
        self,
        *,
        output_store: Any,
        artifact_ref: Any,
        doc_rel: str,
        root_uri: str,
        root_is_file: bool = False,
        ctx: RequestContext,
        lease_ref: Optional[Dict[str, Any]],
        vectorize: bool = True,
        ingest_options: IngestOptions | None = None,
        processing_mode: ProcessingMode = DEFAULT_PROCESSING_MODE,
    ) -> Any:
        """Apply a local artifact incrementally against an existing resource tree.

        Only added/modified files are uploaded and removed files/vectors are
        deleted, decided by the DiffPlan (artifact md5 manifest vs vector-store
        md5). The completeness gate inside build_resource_diff_plan refuses
        deletions from an incomplete target snapshot, so an unreadable target
        never causes data loss.
        """
        from openviking.storage.resource_diff import build_resource_diff_snapshot
        from openviking.storage.resource_diff_apply import apply_diff_plan
        from openviking.storage.resource_target import AgfsResourceTarget
        from openviking.storage.viking_fs._diff_plan import apply_request_scalar_intents

        snapshot = await build_resource_diff_snapshot(
            viking_fs=get_viking_fs(),
            vikingdb=self.vikingdb,
            store=output_store,
            artifact_ref=artifact_ref,
            target_uri=root_uri,
            ctx=ctx,
            doc_rel=doc_rel,
            root_is_file=root_is_file,
            require_vectors=vectorize,
            request_intent=RequestIntent.from_ingest_options(
                target_uri=root_uri,
                processing_mode=processing_mode,
                ingest_options=ingest_options,
                vectorize=vectorize,
            ),
        )
        target = AgfsResourceTarget(
            viking_fs=get_viking_fs(),
            vikingdb=self.vikingdb,
            root_uri=root_uri,
            ctx=ctx,
            lease_ref=lease_ref,
        )
        # The store reads/writes artifact-relative paths, but the manifest walk
        # keys diff results without the doc_rel prefix; apply reads bytes via the
        # same store, so wrap it to re-add the prefix on read.
        result = await apply_diff_plan(
            snapshot.plan,
            store=_DocRelStore(output_store, doc_rel),
            artifact_ref=artifact_ref,
            target=target,
        )
        resolved_plan = self._resolved_diff_plan(snapshot.plan, result)
        apply_request_scalar_intents(snapshot.rnfv, resolved_plan)
        result.scalar_updates = list(resolved_plan.scalar_updates)
        return result

    async def _commit_directory_artifact_with_plan(
        self,
        *,
        output_store: Any,
        artifact_ref: Any,
        doc_rel: str,
        root_uri: str,
        target_preexisting: bool,
        ctx: RequestContext,
        lease_ref: Optional[Dict[str, Any]],
        vectorize: bool,
        is_code_repo: bool,
        ingest_options: IngestOptions,
        source_metadata: Optional[Dict[str, str]],
    ) -> tuple[Any, Any]:
        """Commit one directory artifact and compile its self-contained semantic plan."""
        from openviking.storage.queuefs.semantic_plan_builder import (
            build_initial_semantic_plan,
            build_semantic_plan,
        )
        from openviking.storage.resource_diff import build_resource_diff_snapshot
        from openviking.storage.resource_diff_apply import apply_diff_plan
        from openviking.storage.resource_target import AgfsResourceTarget
        from openviking.storage.viking_fs._diff_plan import apply_request_scalar_intents

        target = AgfsResourceTarget(
            viking_fs=get_viking_fs(),
            vikingdb=self.vikingdb,
            root_uri=root_uri,
            ctx=ctx,
            lease_ref=lease_ref,
        )
        from openviking.parse.image_rewrite import rewrite_artifact_image_uris

        await rewrite_artifact_image_uris(
            output_store,
            artifact_ref,
            doc_rel=doc_rel,
            target_root_uri=root_uri,
        )
        if not target_preexisting:
            apply_result = await self._persist_local_artifact(
                output_store=output_store,
                artifact_ref=artifact_ref,
                doc_rel=doc_rel,
                root_uri=root_uri,
                ctx=ctx,
                lease_ref=lease_ref,
            )
            plan = await build_initial_semantic_plan(
                root_uri=root_uri,
                context_type=context_type_for_uri(root_uri),
                store=output_store,
                artifact_ref=artifact_ref,
                doc_rel=doc_rel,
                md5_by_rel=apply_result.md5_by_rel,
                vectorize=vectorize,
                is_code_repo=is_code_repo,
                ingest_options=ingest_options,
                source_metadata=source_metadata,
            )
            return apply_result, plan

        snapshot = await build_resource_diff_snapshot(
            viking_fs=get_viking_fs(),
            vikingdb=self.vikingdb,
            store=output_store,
            artifact_ref=artifact_ref,
            target_uri=root_uri,
            ctx=ctx,
            doc_rel=doc_rel,
            require_vectors=vectorize,
            request_intent=RequestIntent.from_ingest_options(
                target_uri=root_uri,
                processing_mode=SEMANTIC_AND_VECTORS,
                ingest_options=ingest_options,
                vectorize=vectorize,
            ),
        )
        apply_result = await apply_diff_plan(
            snapshot.plan,
            store=_DocRelStore(output_store, doc_rel),
            artifact_ref=artifact_ref,
            target=target,
            delete_vectors=False,
        )
        committed_new = {
            path: type(entry)(
                md5=str(apply_result.md5_by_rel.get(path) or entry.md5),
                is_dir=entry.is_dir,
            )
            for path, entry in snapshot.new.items()
        }
        resolved_diff_plan = self._resolved_diff_plan(snapshot.plan, apply_result)
        apply_request_scalar_intents(snapshot.rnfv, resolved_diff_plan)
        plan = await build_semantic_plan(
            root_uri=root_uri,
            context_type=context_type_for_uri(root_uri),
            new=committed_new,
            target_files=snapshot.target_files,
            diff_plan=resolved_diff_plan,
            inventory=snapshot.vector_inventory,
            vikingdb=self.vikingdb,
            ctx=ctx,
            vectorize=vectorize,
            is_code_repo=is_code_repo,
            root_preexisting=True,
            ingest_options=ingest_options,
            source_metadata=source_metadata,
        )
        return apply_result, plan

    @staticmethod
    def _resolved_diff_plan(diff_plan: Any, apply_result: Any) -> Any:
        """Copy a plan with body comparisons replaced by their applied result."""
        from dataclasses import replace

        return replace(
            diff_plan,
            added=list(apply_result.added),
            added_dirs=list(apply_result.added_dirs),
            modified=list(apply_result.modified),
            deleted=list(apply_result.deleted),
            deleted_dirs=list(apply_result.deleted_dirs),
            unchanged=list(apply_result.unchanged),
            repair=list(apply_result.repair),
            orphan_vectors=list(apply_result.orphan_vectors),
            structural=list(apply_result.structural),
            needs_body_compare=[],
            new_md5s=dict(apply_result.md5_by_rel),
        )

    @staticmethod
    def _apply_result_to_changes(apply_result: Any, root_uri: str) -> Dict[str, List[str]]:
        """Convert an ApplyResult into target-URI-keyed semantic changes.

        The DAG matches its changed-path set against target URIs, so relative
        paths are joined onto ``root_uri``. Only files that actually changed
        (uploaded) or were removed are reported; unchanged files are omitted so
        the DAG reuses their summaries.
        """
        base = root_uri.rstrip("/")

        def _uris(rels: List[str]) -> List[str]:
            return sorted(base if not rel else f"{base}/{rel}" for rel in rels)

        changes: Dict[str, List[str]] = {}
        if apply_result.added:
            changes["added"] = _uris(apply_result.added)
        if apply_result.modified:
            changes["modified"] = _uris(apply_result.modified)
        if apply_result.repair:
            changes.setdefault("modified", []).extend(_uris(apply_result.repair))
            changes["modified"] = sorted(set(changes["modified"]))
        if apply_result.deleted:
            changes["deleted"] = _uris(apply_result.deleted)
        return changes

    @staticmethod
    def _apply_result_to_file_md5s(apply_result: Any, root_uri: str) -> Dict[str, str]:
        """Map uploaded files' md5 to target URIs for the DAG's re-vectorization."""
        base = root_uri.rstrip("/")
        return {
            (base if not rel else f"{base}/{rel}"): md5
            for rel, md5 in apply_result.md5_by_rel.items()
            if md5
        }

    @staticmethod
    def _apply_result_to_file_abstracts(apply_result: Any, root_uri: str) -> Dict[str, str]:
        base = root_uri.rstrip("/")
        return {
            (base if not rel else f"{base}/{rel}"): abstract
            for rel, abstract in apply_result.abstracts_by_rel.items()
            if abstract
        }

    @staticmethod
    def _apply_result_is_noop(apply_result: Any) -> bool:
        """Return whether diff application performed no target or index work."""
        return not any(
            getattr(apply_result, field, ())
            for field in (
                "uploaded",
                "added",
                "added_dirs",
                "modified",
                "deleted",
                "deleted_dirs",
                "orphan_vectors",
                "structural",
                "repair",
            )
        )

    @staticmethod
    def _log_commit_summary(apply_result: Any, *, root_uri: str, is_initial: bool) -> None:
        """Emit one structured line summarizing what the commit landed.

        Initial import has no target to diff against, so it reports the total
        file/dir count; an incremental commit reports the per-state file counts
        from the DiffPlan so operators can see how much the diff actually touched.
        """
        if is_initial:
            logger.info(
                "[add_resource] initial import committed root=%s files=%d dirs=%d",
                root_uri,
                len(apply_result.files),
                len(apply_result.added_dirs),
            )
            return
        logger.info(
            "[add_resource] incremental diff committed root=%s "
            "added=%d modified=%d deleted=%d unchanged=%d repair=%d "
            "structural=%d orphan_vectors=%d added_dirs=%d deleted_dirs=%d",
            root_uri,
            len(apply_result.added),
            len(apply_result.modified),
            len(apply_result.deleted),
            len(apply_result.unchanged),
            len(apply_result.repair),
            len(apply_result.structural),
            len(apply_result.orphan_vectors),
            len(apply_result.added_dirs),
            len(apply_result.deleted_dirs),
        )

    async def _vectorize_prepared_files(
        self,
        prepared: Dict[str, Any],
        root_uri: str,
        *,
        local_artifact: Any,
        ingest_options: IngestOptions | None,
        ctx: RequestContext,
    ) -> None:
        """Vectorize a committed directory's files (full tree or local changed subset).

        Shared by the ``vectors_only`` semantic-plan branch and the plain
        vectors-only path so both resolve the local artifact store + changed-file
        subset identically instead of duplicating the derivation.
        """
        local_store = self._build_parse_output_store() if local_artifact is not None else None
        artifact_files = prepared.get("artifact_files")
        if prepared.get("changes") is not None:
            base = root_uri.rstrip("/") + "/"
            changed_uris = {
                uri
                for kind in ("added", "modified")
                for uri in prepared["changes"].get(kind, [])
            }
            artifact_files = sorted(
                uri[len(base) :] for uri in changed_uris if uri.startswith(base)
            )
        await self._vectorize_resource_files(
            root_uri,
            ctx=ctx,
            ingest_options=ingest_options,
            artifact_store=local_store,
            artifact_ref=local_artifact,
            artifact_files=artifact_files,
            file_md5s=prepared.get("file_md5s"),
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
        return [item for item in failed_files if isinstance(item, dict) and "error" in item]

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
        media_processor = self._get_media_processor()
        if not snapshot_required and not media_processor.durable_route_requires_preparation(
            path, **kwargs
        ):
            return None
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
        return await self._get_summarizer().summarize(resource_uris, ctx, **kwargs)

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
        ingest_options = IngestOptions.from_value(kwargs.pop("ingest_options", None))
        to_is_directory = bool(kwargs.pop("to_is_directory", False))
        telemetry = get_current_telemetry()

        async def _set_stage(stage: str) -> None:
            if stage_callback is None:
                return
            result = stage_callback(stage)
            if inspect.isawaitable(result):
                await result

        with telemetry.measure("resource.process"):
            # ============ Phase 1: Parse source and writes to temp viking fs ============
            try:
                from openviking.metrics.datasources.resource import (
                    ResourceIngestionEventDataSource,
                )

                parse_start = time.perf_counter()
                stage_start = time.perf_counter()
                stage_status = "ok"
                media_processor = self._get_media_processor()
                viking_fs = get_viking_fs()
                # Use reason as instruction fallback so it influences L0/L1
                # generation and improves search relevance as documented.
                effective_instruction = instruction or reason
                # Local artifact mode (single-machine deployments) writes parse
                # artifacts to a shared local dir instead of AGFS temp. The store
                # is task-scoped and threaded through parse kwargs so concurrent
                # tasks never share it. Default agfs mode leaves kwargs untouched.
                output_store = self._build_parse_output_store()
                if output_store is not None:
                    kwargs.setdefault("parse_output_store", output_store)
                if path.startswith(("http://", "https://", "git@", "ssh://", "git://")):
                    await _set_stage("fetching")
                else:
                    await _set_stage("parsing")
                with viking_fs.bind_request_context(ctx):
                    parse_result = await media_processor.process(
                        source=path,
                        instruction=effective_instruction,
                        prepared_resource=prepared_resource,
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
                    stage_status = "error"
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
                        await viking_fs.delete_temp(parse_result.temp_dir_path, ctx=ctx)
                    except Exception as exc:
                        logger.warning(
                            "[ResourceProcessor] Failed to clean empty directory temp %s: %s",
                            parse_result.temp_dir_path,
                            exc,
                        )
                    stage_status = "error"
                    return result

                parse_failures = self._directory_parse_failures(parse_meta)
                use_directory_semantic_plan = (
                    is_directory_aggregate
                    and normalize_processing_mode(kwargs.get("processing_mode"))
                    == SEMANTIC_AND_VECTORS
                    and (summarize or bool(kwargs.get("build_index", True)))
                )
                if use_directory_semantic_plan and parse_failures:
                    result["status"] = "error"
                    result["errors"].append(self._incomplete_directory_error(parse_failures))
                    try:
                        parse_artifact_ref = getattr(parse_result, "artifact_ref", None)
                        if parse_artifact_ref is not None and output_store is not None:
                            await output_store.cleanup(parse_artifact_ref)
                        else:
                            await viking_fs.delete_temp(parse_result.temp_dir_path, ctx=ctx)
                    except Exception as exc:
                        logger.warning(
                            "[ResourceProcessor] Failed to clean incomplete directory temp %s: %s",
                            parse_result.temp_dir_path,
                            exc,
                        )
                    stage_status = "error"
                    return result

                if parse_result.warnings and kwargs.get("strict", False):
                    result.setdefault("warnings", []).extend(parse_result.warnings)

                telemetry.set(
                    "resource.parse.duration_ms",
                    round((time.perf_counter() - parse_start) * 1000, 3),
                )
                telemetry.set("resource.parse.warnings_count", len(parse_result.warnings or []))

            except OpenVikingError:
                stage_status = "error"
                raise
            except Exception as e:
                result["status"] = "error"
                error_message = f"Parse error: {e}"
                error_meta = getattr(e, "meta", {})
                if isinstance(error_meta, dict) and error_meta.get("response_id"):
                    error_message += f" (response_id={error_meta['response_id']})"
                result["errors"].append(error_message)
                logger.error(f"[ResourceProcessor] Parse error: {e}")
                telemetry.set_error("resource_processor.parse", "PROCESSING_ERROR", str(e))
                import traceback

                traceback.print_exc()
                stage_status = "error"
                return result
            finally:
                try:
                    ResourceIngestionEventDataSource.record_stage(
                        stage="parse",
                        status=str(stage_status),
                        duration_seconds=float(time.perf_counter() - stage_start),
                        account_id=getattr(ctx, "account_id", None),
                    )
                except Exception:
                    pass

            # parse_result contains:
            # - root: ResourceNode tree (with L0/L1 in meta)
            # - temp_dir_path: Temporary directory path (Parser wrote all files)
            # - source_path, source_format

            # ============ Phase 3: TreeBuilder finalizes from temp (scan + move to AGFS) ============
            try:
                await _set_stage("finalizing")
                stage_start = time.perf_counter()
                stage_status = "ok"
                finalize_start = time.perf_counter()
                artifact_ref = getattr(parse_result, "artifact_ref", None)
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
                        output_store=output_store if artifact_ref is not None else None,
                    )
                    if context_tree and context_tree.root:
                        result["root_uri"] = context_tree.root.uri
                        result["temp_uri"] = context_tree.root.temp_uri
                    root_is_file = bool(getattr(context_tree, "_root_is_file", False))
                telemetry.set(
                    "resource.finalize.duration_ms",
                    round((time.perf_counter() - finalize_start) * 1000, 3),
                )
            except Exception as e:
                result["status"] = "error"
                result["errors"].append(f"Finalize from temp error: {e}")
                telemetry.set_error("resource_processor.finalize", "PROCESSING_ERROR", str(e))
                stage_status = "error"

                # Cleanup the parser-owned artifact through its own backend.
                try:
                    if artifact_ref is not None and artifact_ref.backend == "local":
                        await output_store.cleanup(artifact_ref)
                    elif parse_result.temp_dir_path:
                        await get_viking_fs().delete_temp(parse_result.temp_dir_path, ctx=ctx)
                except Exception:
                    pass

                return result
            finally:
                try:
                    ResourceIngestionEventDataSource.record_stage(
                        stage="finalize",
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
            local_incremental_changes: Optional[Dict[str, List[str]]] = None
            local_incremental_file_md5s: Dict[str, str] = {}
            local_file_abstracts: Dict[str, str] = {}
            local_artifact_files: List[str] = []
            local_artifact_doc_rel = ""
            incremental_noop = False
            semantic_plan = None
            apply_result = None
            rnfv_artifact_committed = False

            if root_uri and temp_uri:
                stage_start = time.perf_counter()
                stage_status = "ok"
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
                                    if all(name in STORAGE_INTERNAL_ENTRY_NAMES for name in names):
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
                    use_semantic_plan = (
                        not root_is_file
                        and (
                            artifact_ref is not None
                            or callable(getattr(parse_result, "ensure_artifact_ref", None))
                        )
                        and normalize_processing_mode(kwargs.get("processing_mode"))
                        == SEMANTIC_AND_VECTORS
                        and (summarize or bool(kwargs.get("build_index", True)))
                    )
                    if use_semantic_plan:
                        ensure_artifact_ref = getattr(parse_result, "ensure_artifact_ref", None)
                        artifact_ref = (
                            ensure_artifact_ref() if callable(ensure_artifact_ref) else artifact_ref
                        )
                        if artifact_ref is None:
                            raise RuntimeError("semantic plan requires a parse artifact")
                        from openviking.parse.output import store_for_artifact_ref

                        # Local mode already has a task-scoped store; agfs mode
                        # builds the matching store from the ref's backend.
                        artifact_store = output_store or store_for_artifact_ref(
                            artifact_ref, viking_fs=viking_fs, ctx=ctx
                        )
                        local_artifact_doc_rel = self._artifact_doc_rel(artifact_ref, temp_uri)
                        semantic_source = self._semantic_source_metadata(
                            path=path,
                            prepared_resource=prepared_resource,
                            source_format=parse_result.source_format,
                        )
                        (
                            apply_result,
                            semantic_plan,
                        ) = await self._commit_directory_artifact_with_plan(
                            output_store=artifact_store,
                            artifact_ref=artifact_ref,
                            doc_rel=local_artifact_doc_rel,
                            root_uri=root_uri,
                            target_preexisting=target_preexisting,
                            ctx=ctx,
                            lease_ref=resource_lock,
                            vectorize=bool(kwargs.get("build_index", True)),
                            ingest_options=ingest_options,
                            is_code_repo=parse_result.source_format == "repository",
                            source_metadata=semantic_source,
                        )
                        local_artifact_files = list(apply_result.files)
                        local_incremental_file_md5s = self._apply_result_to_file_md5s(
                            apply_result, root_uri
                        )
                        self._log_commit_summary(
                            apply_result,
                            root_uri=root_uri,
                            is_initial=not target_preexisting,
                        )
                        incremental_noop = target_preexisting and semantic_plan.is_noop()
                        if incremental_noop:
                            semantic_plan = None
                        temp_uri = root_uri
                        source_committed = True
                    elif not target_preexisting:
                        if artifact_ref is not None and artifact_ref.backend == "local":
                            # Local artifacts are not in AGFS temp, so persist by
                            # uploading every file under the document root to the
                            # final resource location (initial import = all added).
                            # rewrite_image_uris still runs afterwards against the
                            # now-uploaded AGFS files, identical to the temp path.
                            local_artifact_doc_rel = self._artifact_doc_rel(artifact_ref, temp_uri)
                            apply_result = await self._persist_local_artifact(
                                output_store=output_store,
                                artifact_ref=artifact_ref,
                                doc_rel=local_artifact_doc_rel,
                                root_uri=root_uri,
                                root_is_file=root_is_file,
                                ctx=ctx,
                                lease_ref=resource_lock,
                            )
                            local_artifact_files = list(apply_result.files)
                            local_incremental_file_md5s = self._apply_result_to_file_md5s(
                                apply_result, root_uri
                            )
                            self._log_commit_summary(
                                apply_result, root_uri=root_uri, is_initial=True
                            )
                        else:
                            await viking_fs.persist_temp_tree(
                                temp_uri,
                                root_uri,
                                ctx=ctx,
                                lease_ref=resource_lock,
                            )
                        if not root_is_file:
                            await rewrite_image_uris(
                                root_uri,
                                ctx=ctx,
                                lease_ref=resource_lock,
                            )
                        if artifact_ref is None or artifact_ref.backend != "local":
                            await viking_fs.delete_temp(
                                parse_result.temp_dir_path,
                                ctx=ctx,
                            )
                        temp_uri = root_uri
                        source_committed = True
                    elif artifact_ref is not None:
                        # Incremental artifact import: the target already exists, so
                        # only upload changed files and delete removed ones,
                        # decided by DiffPlan (artifact md5 vs vector-store md5).
                        from openviking.parse.output import store_for_artifact_ref

                        artifact_store = output_store or store_for_artifact_ref(
                            artifact_ref, viking_fs=viking_fs, ctx=ctx
                        )
                        apply_result = await self._apply_local_incremental(
                            output_store=artifact_store,
                            artifact_ref=artifact_ref,
                            doc_rel=self._artifact_doc_rel(artifact_ref, temp_uri),
                            root_uri=root_uri,
                            root_is_file=root_is_file,
                            ctx=ctx,
                            lease_ref=resource_lock,
                            vectorize=bool(kwargs.get("build_index", True)),
                            ingest_options=ingest_options,
                            processing_mode=normalize_processing_mode(
                                kwargs.get("processing_mode")
                            ),
                        )
                        rnfv_artifact_committed = True
                        local_artifact_doc_rel = self._artifact_doc_rel(artifact_ref, temp_uri)
                        local_artifact_files = list(apply_result.files)
                        # Hand the applied change set to post-processing so the
                        # semantic DAG only re-summarizes/re-vectorizes changed
                        # files (target-URI keyed), like the content_write path.
                        local_incremental_changes = self._apply_result_to_changes(
                            apply_result, root_uri
                        )
                        local_incremental_file_md5s = self._apply_result_to_file_md5s(
                            apply_result, root_uri
                        )
                        local_file_abstracts = self._apply_result_to_file_abstracts(
                            apply_result, root_uri
                        )
                        incremental_noop = self._apply_result_is_noop(apply_result)
                        self._log_commit_summary(
                            apply_result, root_uri=root_uri, is_initial=False
                        )
                        if not root_is_file and not incremental_noop:
                            await rewrite_image_uris(
                                root_uri,
                                ctx=ctx,
                                lease_ref=resource_lock,
                            )
                        temp_uri = root_uri
                        source_committed = True
                except Exception:
                    stage_status = "error"
                    # Mirror the Phase 3 (finalize) on-error cleanup: a lock or
                    # persist failure here would otherwise orphan the
                    # viking://temp tree with no GC (#2478). Skip when the temp
                    # tree was already persisted + deleted on the success path.
                    if (
                        not source_committed
                        and artifact_ref is not None
                        and artifact_ref.backend == "local"
                    ):
                        try:
                            await output_store.cleanup(artifact_ref)
                        except Exception:
                            pass
                    elif not source_committed and parse_result.temp_dir_path:
                        try:
                            await get_viking_fs().delete_temp(parse_result.temp_dir_path, ctx=ctx)
                        except Exception:
                            pass
                    raise
                finally:
                    try:
                        ResourceIngestionEventDataSource.record_stage(
                            stage="persist",
                            status=str(stage_status),
                            duration_seconds=float(time.perf_counter() - stage_start),
                            account_id=getattr(ctx, "account_id", None),
                        )
                    except Exception:
                        pass

            ensure_artifact_ref = getattr(parse_result, "ensure_artifact_ref", None)
            if callable(ensure_artifact_ref):
                artifact_ref = ensure_artifact_ref()
            prepared_artifact_ref = artifact_ref.to_dict() if artifact_ref is not None else None
            if prepared_artifact_ref is not None and (
                artifact_ref.backend == "local" or semantic_plan is not None
            ):
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
                # For local incremental commits, the changed-file set is already
                # known (DiffPlan), so post-processing only re-summarizes those.
                "changes": local_incremental_changes,
                "file_md5s": local_incremental_file_md5s,
                "file_abstracts": local_file_abstracts,
                "artifact_files": local_artifact_files,
                "incremental_noop": incremental_noop,
                "scalar_updates": [
                    {
                        "record_id": update.record_id,
                        "uri": update.uri,
                        "level": update.level,
                        "fields": dict(update.fields),
                    }
                    for update in getattr(apply_result, "scalar_updates", ())
                ]
                if apply_result is not None
                else [],
                "semantic_plan": semantic_plan.to_dict() if semantic_plan is not None else None,
                "plan_artifact_committed": use_semantic_plan or rnfv_artifact_committed,
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
                    **kwargs,
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
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """Run the queue-producing phase for a resource already stored in VikingFS."""
        from openviking.metrics.datasources.resource import ResourceIngestionEventDataSource

        root_uri = str(prepared.get("root_uri") or "")
        temp_uri = prepared.get("temp_uri")
        temp_dir_path = prepared.get("temp_dir_path")
        # Plan artifacts stay available until the SemanticMsg is durable. Legacy
        # local messages hand the artifact to the semantic worker instead.
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
        target_preexisting = bool(prepared.get("target_preexisting"))
        build_index = bool(kwargs.get("build_index", True))
        processing_mode = normalize_processing_mode(processing_mode)
        vectors_only = processing_mode == VECTORS_ONLY
        root_is_file = bool(prepared.get("root_is_file"))
        ingest_options = IngestOptions.from_value(kwargs.pop("ingest_options", None))
        semantic_source = prepared.get("semantic_source")
        semantic_plan = prepared.get("semantic_plan")
        plan_artifact_committed = bool(prepared.get("plan_artifact_committed"))
        should_summarize = not root_is_file and not vectors_only and (summarize or build_index)
        should_refresh_file_parent = (
            root_is_file and not vectors_only and (summarize or build_index)
        )
        result: Dict[str, Any] = {"status": "success", "root_uri": root_uri}
        local_artifact = (
            artifact_ref
            if artifact_ref_data is not None and artifact_ref.backend == "local"
            else None
        )
        local_artifact_handed_off = False
        artifact_cleaned = False
        scalar_updates = list(prepared.get("scalar_updates") or [])

        async def cleanup_artifact_if_owned() -> None:
            nonlocal artifact_cleaned
            if artifact_cleaned or artifact_ref is None:
                return
            if not plan_artifact_committed and (
                local_artifact is None or local_artifact_handed_off
            ):
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

        if prepared.get("incremental_noop") and not scalar_updates:
            try:
                await cleanup_artifact_if_owned()
            finally:
                if resource_lock is not None:
                    await get_viking_fs()._async_agfs.pathlock_release(resource_lock)
            return result

        if should_summarize:
            stage_start = time.perf_counter()
            stage_status = "ok"
            try:
                with get_current_telemetry().measure("resource.summarize"):
                    summary_result = await self._get_summarizer().summarize(
                        resource_uris=[root_uri],
                        ctx=ctx,
                        skip_vectorization=not build_index,
                        lock=resource_lock,
                        temp_uris=[temp_uri],
                        is_code_repo=bool(prepared.get("is_code_repo")),
                        target_preexisting=target_preexisting,
                        ingest_options=ingest_options,
                        semantic_source=semantic_source,
                        generation_trigger="resource_ingest",
                        changes=prepared.get("changes"),
                        file_md5s=prepared.get("file_md5s"),
                        file_abstracts=prepared.get("file_abstracts"),
                        artifact_ref=(
                            artifact_ref_data
                            if semantic_plan is None and local_artifact is not None
                            else None
                        ),
                        artifact_files=(
                            prepared.get("artifact_files") if semantic_plan is None else None
                        ),
                        semantic_plan=semantic_plan,
                        **kwargs,
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
                    local_artifact_handed_off = (
                        semantic_plan is None
                        and local_artifact is not None
                        and summary_result.get("status") == "success"
                        and summary_result.get("enqueued_count", 0) > 0
                    )
                    if semantic_plan is not None and (
                        summary_result.get("status") == "success"
                        and summary_result.get("enqueued_count", 0) > 0
                    ):
                        await cleanup_artifact_if_owned()
            except Exception as exc:
                logger.error("Summarization failed: %s", exc)
                stage_status = "error"
                if semantic_plan is not None:
                    try:
                        await cleanup_artifact_if_owned()
                    finally:
                        if resource_lock is not None:
                            await get_viking_fs()._async_agfs.pathlock_release(resource_lock)
                            resource_lock = None
                    raise
                result["warnings"] = [f"Summarization failed: {exc}"]
            finally:
                try:
                    ResourceIngestionEventDataSource.record_stage(
                        stage="summarize",
                        status=stage_status,
                        duration_seconds=float(time.perf_counter() - stage_start),
                        account_id=getattr(ctx, "account_id", None),
                    )
                except Exception:
                    pass

        if resource_lock is not None:
            try:
                sync_deleted_files: list[str] = []
                sync_deleted_dirs: list[str] = []
                if not should_summarize and temp_uri and not source_committed:
                    viking_fs = get_viking_fs()
                    if vectors_only and target_preexisting and not root_is_file:
                        diff = await SemanticProcessor()._sync_topdown_recursive(
                            temp_uri,
                            root_uri,
                            ctx=ctx,
                            lock=resource_lock,
                        )
                        sync_deleted_files = list(getattr(diff, "deleted_files", []))
                        sync_deleted_dirs = list(getattr(diff, "deleted_dirs", []))
                    else:
                        await viking_fs.persist_temp_tree(
                            temp_uri,
                            root_uri,
                            ctx=ctx,
                            lease_ref=resource_lock,
                        )
                    if not root_is_file:
                        await rewrite_image_uris(
                            root_uri,
                            ctx=ctx,
                            lease_ref=resource_lock,
                        )
                    if temp_dir_path:
                        await viking_fs.delete_temp(temp_dir_path, ctx=ctx)
                if scalar_updates:
                    await self._enqueue_scalar_updates(scalar_updates, ctx=ctx)
                if vectors_only:
                    if sync_deleted_files or sync_deleted_dirs:
                        await self._delete_removed_resource_vectors(
                            files=sync_deleted_files,
                            dirs=sync_deleted_dirs,
                            ctx=ctx,
                        )
                if should_refresh_file_parent:
                    await self._get_summarizer().refresh_file_parent(
                        file_uri=root_uri,
                        ctx=ctx,
                        skip_vectorization=not build_index,
                        ingest_options=ingest_options,
                        created=not target_preexisting,
                        file_md5=(prepared.get("file_md5s") or {}).get(root_uri),
                        file_abstract=(prepared.get("file_abstracts") or {}).get(root_uri, ""),
                    )
                elif build_index:
                    if root_is_file:
                        await self._vectorize_resource_file(
                            root_uri,
                            ctx=ctx,
                            ingest_options=ingest_options,
                            creator_acl_grant=(
                                CreatorAclGrant.DIRECT if not target_preexisting else None
                            ),
                            file_md5=(prepared.get("file_md5s") or {}).get(root_uri),
                        )
                    elif vectors_only:
                        await self._vectorize_prepared_files(
                            prepared,
                            root_uri,
                            local_artifact=local_artifact,
                            ingest_options=ingest_options,
                            ctx=ctx,
                        )
            except BaseException:
                await cleanup_artifact_if_owned()
                raise
            finally:
                await get_viking_fs()._async_agfs.pathlock_release(resource_lock)
        elif should_refresh_file_parent:
            try:
                await self._get_summarizer().refresh_file_parent(
                    file_uri=root_uri,
                    ctx=ctx,
                    skip_vectorization=not build_index,
                    ingest_options=ingest_options,
                    created=not target_preexisting,
                    file_md5=(prepared.get("file_md5s") or {}).get(root_uri),
                    file_abstract=(prepared.get("file_abstracts") or {}).get(root_uri, ""),
                )
            except BaseException:
                await cleanup_artifact_if_owned()
                raise
        elif vectors_only or root_is_file:
            if not build_index:
                await cleanup_artifact_if_owned()
                return result
            if root_is_file:
                try:
                    await self._vectorize_resource_file(
                        root_uri,
                        ctx=ctx,
                        ingest_options=ingest_options,
                        creator_acl_grant=(
                            CreatorAclGrant.DIRECT if not target_preexisting else None
                        ),
                        file_md5=(prepared.get("file_md5s") or {}).get(root_uri),
                    )
                except BaseException:
                    await cleanup_artifact_if_owned()
                    raise
            else:
                try:
                    await self._vectorize_prepared_files(
                        prepared,
                        root_uri,
                        local_artifact=local_artifact,
                        ingest_options=ingest_options,
                        ctx=ctx,
                    )
                except BaseException:
                    await cleanup_artifact_if_owned()
                    raise
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

    async def _enqueue_scalar_updates(
        self, scalar_updates: List[Mapping[str, Any]], *, ctx: RequestContext
    ) -> None:
        from openviking.storage.queuefs import get_queue_manager
        from openviking.storage.queuefs.embedding_msg import EmbeddingMsg
        from openviking.telemetry import get_current_telemetry
        from openviking.utils.embedding_utils import _enqueue_embedding_message
        from openviking.utils.time_utils import get_current_timestamp

        queue_manager = get_queue_manager()
        embedding_queue = queue_manager.get_queue(queue_manager.EMBEDDING, allow_create=True)
        for update in scalar_updates:
            fields = dict(update.get("fields") or {})
            fields["updated_at"] = get_current_timestamp()
            uri = str(update.get("uri") or "")
            message = EmbeddingMsg.for_update_fields(
                record_id=str(update["record_id"]),
                fields=fields,
                context_data={
                    "uri": uri,
                    "level": int(update["level"]),
                    "account_id": ctx.account_id,
                    "owner_user_id": ctx.user.user_id,
                },
                telemetry_id=get_current_telemetry().telemetry_id,
            )
            await _enqueue_embedding_message(
                embedding_queue,
                message,
                failure_message=f"Failed to enqueue scalar update for {uri}",
            )

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
        creator_acl_grant: CreatorAclGrant | None = None,
        file_md5: str | None = None,
    ) -> None:
        parent = VikingURI(file_uri).parent
        if parent is None:
            return
        name = file_uri.rsplit("/", 1)[-1]
        await vectorize_file(
            file_path=file_uri,
            summary_dict={"name": name, "summary": ""},
            parent_uri=parent.uri,
            context_type=context_type_for_uri(file_uri),
            ctx=ctx,
            ingest_options=IngestOptions.from_value(ingest_options),
            creator_acl_grant=creator_acl_grant,
            file_md5=file_md5,
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

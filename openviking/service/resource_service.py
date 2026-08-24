# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
Resource Service for OpenViking.

Provides resource management operations: add_resource, add_skill, wait_processed.
"""

import asyncio
import contextlib
import inspect
import json
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional
from urllib.parse import urlparse
from uuid import uuid4

from openviking.core.namespace import is_content_root_uri
from openviking.parse.backend import ParserBackend, normalize_parser_backend
from openviking.parse.mode import ParseMode, normalize_parse_mode
from openviking.parse.parsers.constants import MPEG_TS_EXTENSION_ALIAS
from openviking.resource.feishu_watch_auth import (
    FEISHU_ACCESS_TOKEN_ARG,
    FEISHU_AUTH_PROVIDER,
    FEISHU_REFRESH_TOKEN_ARG,
    create_feishu_auth_state,
    is_feishu_auth_state,
    load_feishu_app_credentials,
)
from openviking.resource.git_watch_auth import (
    create_git_http_auth_state,
    git_http_auth_config_from_state,
    is_git_http_auth_state,
)
from openviking.resource.processing_mode import (
    DEFAULT_PROCESSING_MODE,
    ProcessingMode,
    normalize_processing_mode,
)
from openviking.server.identity import RequestContext
from openviking.server.local_input_guard import (
    is_remote_resource_source,
    require_remote_resource_source,
)
from openviking.server.user_config import (
    effective_resource_add_target,
    effective_skill_add_target,
)
from openviking.storage.queuefs import QueueManager, get_queue_manager
from openviking.storage.queuefs.add_resource_msg import AddResourcePhase
from openviking.storage.viking_fs import VikingFS
from openviking.storage.vikingdb_manager import VikingDBManager
from openviking.telemetry import get_current_telemetry, register_telemetry, unregister_telemetry
from openviking.telemetry.request_wait_tracker import get_request_wait_tracker
from openviking.telemetry.resource_summary import (
    build_queue_status_payload,
)
from openviking.utils import is_git_repo_url, parse_code_hosting_url
from openviking.utils.git_auth import (
    GitHttpAuthConfig,
    build_git_http_auth_env,
    parse_git_http_auth_config,
    reject_git_http_userinfo,
)
from openviking.utils.ingest_options import IngestOptions
from openviking.utils.media_processor import _smart_stem
from openviking.utils.network_guard import ensure_public_remote_target
from openviking.utils.resource_processor import ResourceProcessor
from openviking.utils.skill_processor import SkillProcessingPreparation, SkillProcessor
from openviking_cli.exceptions import (
    ConflictError,
    DeadlineExceededError,
    InternalError,
    InvalidArgumentError,
    NotInitializedError,
)
from openviking_cli.utils import get_logger

if TYPE_CHECKING:
    from openviking.connector.delegate import ConnectorDelegate
    from openviking.parse.accessors.base import LocalResource
    from openviking.resource.staged_source import StagedSource
    from openviking.resource.watch_manager import WatchManager
    from openviking.resource.watch_scheduler import WatchScheduler
    from openviking.service.resource_memory_link_service import ResourceMemoryLinkService

logger = get_logger(__name__)


_ADD_RESOURCE_ARGS_RESERVED_FIELDS = frozenset(
    {
        "path",
        "ctx",
        "to",
        "to_is_directory",
        "parent",
        "reason",
        "instruction",
        "wait",
        "timeout",
        "build_index",
        "summarize",
        "processing_mode",
        "watch_interval",
        "skip_watch_management",
        "allow_local_path_resolution",
        "enforce_public_remote_targets",
        "resource_lock",
        "stage_callback",
        "args",
        "strict",
        "source_name",
        "ignore_dirs",
        "include",
        "exclude",
        "directly_upload_media",
        "preserve_structure",
        "create_parent",
        "telemetry",
        "request_validator",
        "understanding_response_id",
        "parser_backend",
        "resolved_extension",
        "defer_post_processing",
        "prepared_resource",
        "tags",
        "tag_mode",
    }
)
_ADD_RESOURCE_TRANSIENT_ARGS = frozenset({"tos_signature", "tos_access"})
_ADD_RESOURCE_TAG_MODES = frozenset({"replace", "append"})

_INTERNAL_INGESTION_FIELDS = frozenset(
    {
        "manage_watch",
        "parser_args",
        "resource_lock",
        "route_source",
        "skip_watch_management",
        "stage_callback",
        "to_is_directory",
        "watch_auth_state",
        "understanding_response_id",
        "parser_backend",
        "resolved_extension",
        "prepared_resource",
    }
)


@dataclass
class _ResourceSourceInfo:
    source_name: Optional[str] = None
    source_path: Optional[str] = None
    source_format: Optional[str] = None


@dataclass
class _NormalizedAddResourceArgs:
    processor_kwargs: Dict[str, Any]
    watch_auth_state: Optional[Dict[str, Any]] = None
    parse_mode: ParseMode = ParseMode.DEFAULT


@dataclass
class _SourcePlan:
    path: str
    source_identity: _ResourceSourceInfo
    processor_args: Dict[str, Any]
    task_auth: Dict[str, Any] = field(repr=False)
    staged_source: Optional["StagedSource"] = None
    understanding_response_id: Optional[str] = None
    defer_unnamed_target: bool = False


class ResourceService:
    """Resource management service."""

    def __init__(
        self,
        vikingdb: Optional[VikingDBManager] = None,
        viking_fs: Optional[VikingFS] = None,
        resource_processor: Optional[ResourceProcessor] = None,
        skill_processor: Optional[SkillProcessor] = None,
        watch_scheduler: Optional["WatchScheduler"] = None,
        resource_memory_link_service: Optional["ResourceMemoryLinkService"] = None,
    ):
        self._vikingdb = vikingdb
        self._viking_fs = viking_fs
        self._resource_processor = resource_processor
        self._skill_processor = skill_processor
        self._watch_scheduler = watch_scheduler
        self._resource_memory_link_service = resource_memory_link_service
        self._background_tasks: set[asyncio.Task[Any]] = set()
        self._connector_delegate: Optional["ConnectorDelegate"] = None

    def set_dependencies(
        self,
        vikingdb: VikingDBManager,
        viking_fs: VikingFS,
        resource_processor: ResourceProcessor,
        skill_processor: SkillProcessor,
        watch_scheduler: Optional["WatchScheduler"] = None,
        resource_memory_link_service: Optional["ResourceMemoryLinkService"] = None,
    ) -> None:
        """Set dependencies (for deferred initialization)."""
        self._vikingdb = vikingdb
        self._viking_fs = viking_fs
        self._resource_processor = resource_processor
        self._skill_processor = skill_processor
        self._watch_scheduler = watch_scheduler
        self._resource_memory_link_service = resource_memory_link_service

    def _get_watch_manager(self) -> Optional["WatchManager"]:
        if not self._watch_scheduler:
            return None
        return self._watch_scheduler.watch_manager

    def _sanitize_watch_processor_kwargs(self, processor_kwargs: Dict[str, Any]) -> Dict[str, Any]:
        sanitized: Dict[str, Any] = {}
        for key, value in processor_kwargs.items():
            if key in {
                "auth_config",
                FEISHU_ACCESS_TOKEN_ARG,
                FEISHU_REFRESH_TOKEN_ARG,
                "parser_backend",
                "resolved_extension",
                "understanding_response_id",
                "temp_file_id",
            }:
                continue
            try:
                json.dumps(value, ensure_ascii=False)
            except TypeError:
                continue
            sanitized[key] = value
        return sanitized

    def _watch_processor_kwargs(
        self,
        processor_kwargs: Dict[str, Any],
        tags: Optional[List[str]],
        tag_mode: str,
    ) -> Dict[str, Any]:
        watch_kwargs = dict(processor_kwargs)
        if tags is not None:
            watch_kwargs["tags"] = tags
            watch_kwargs["tag_mode"] = tag_mode
        return watch_kwargs

    def _validate_add_resource_tag_policy(
        self,
        *,
        tags: Optional[List[str]],
        tag_mode: str,
    ) -> None:
        if tags is not None and tag_mode not in _ADD_RESOURCE_TAG_MODES:
            raise InvalidArgumentError(f"unsupported tag mode: {tag_mode}")

    def _add_resource_ingest_tag_kwargs(
        self,
        *,
        tags: Optional[List[str]],
        tag_mode: str,
    ) -> Dict[str, Any]:
        if tags is None:
            return {}
        return {"ingest_options": IngestOptions.from_search_tags(tags, mode=tag_mode)}

    @staticmethod
    def _ensure_single_resource_target(
        *,
        to: Optional[str],
        parent: Optional[str],
    ) -> None:
        if (to or "").strip() and (parent or "").strip():
            raise InvalidArgumentError("Cannot specify both 'to' and 'parent' at the same time.")

    async def _manage_watch_if_needed(
        self,
        *,
        watch_manager: Optional["WatchManager"],
        manage_watch: bool,
        watch_interval: float,
        to: str,
        parent: str,
        to_is_directory: bool,
        root_uri: str,
        path: str,
        reason: str,
        instruction: str,
        build_index: bool,
        summarize: bool,
        processing_mode: ProcessingMode,
        processor_kwargs: Dict[str, Any],
        watch_auth_state: Optional[Dict[str, Any]],
        ctx: RequestContext,
    ) -> None:
        if not watch_manager or not manage_watch:
            return
        telemetry = get_current_telemetry()
        with telemetry.measure("resource.watch"):
            if watch_interval > 0:
                watch_to = to
                parent_uri = parent
                if not watch_to:
                    watch_to = root_uri
                    parent_uri = None
                if not watch_to:
                    raise InvalidArgumentError(
                        "watch_interval > 0 requires a stable target URI. "
                        "Pass 'to' explicitly, or add a resource type that returns root_uri."
                    )
                if processor_kwargs.get("temp_file_id"):
                    # An uploaded source is a static snapshot, so a watch task recorded
                    # against it would re-process the frozen snapshot every interval.
                    raise InvalidArgumentError(
                        "watch_interval > 0 is not supported for uploaded content: an "
                        "upload is a static snapshot, so the watch would re-process "
                        "stale content forever. Watch a URL / "
                        "sitemap / RSS source instead, or re-add the resource when the "
                        "source changes."
                    )
                try:
                    sanitized = self._sanitize_watch_processor_kwargs(processor_kwargs)
                    await self._handle_watch_task_creation(
                        path=path,
                        to_uri=watch_to,
                        to_is_directory=to_is_directory,
                        parent_uri=parent_uri,
                        reason=reason,
                        instruction=instruction,
                        watch_interval=watch_interval,
                        build_index=build_index,
                        summarize=summarize,
                        processing_mode=processing_mode,
                        processor_kwargs=sanitized,
                        auth_state=watch_auth_state,
                        ctx=ctx,
                    )
                except ConflictError:
                    raise
                except Exception as e:
                    logger.warning(
                        f"[ResourceService] Failed to create watch task for {watch_to}: {e}"
                    )
            elif to:
                try:
                    await self._handle_watch_task_cancellation(to_uri=to, ctx=ctx)
                except Exception as e:
                    logger.warning(
                        f"[ResourceService] Failed to cancel watch task for {to}: {e}"
                    )

    def _normalize_add_resource_args(
        self,
        args: Optional[Dict[str, Any]],
        *,
        watch_interval: float,
        allowed_reserved_fields: Optional[set[str]] = None,
    ) -> _NormalizedAddResourceArgs:
        if args is None:
            return _NormalizedAddResourceArgs({})
        if not isinstance(args, dict):
            raise InvalidArgumentError("args must be an object.")
        if not args:
            return _NormalizedAddResourceArgs({})

        reserved_fields = _ADD_RESOURCE_ARGS_RESERVED_FIELDS - (
            allowed_reserved_fields or set()
        )
        reserved = sorted(set(args).intersection(reserved_fields))
        if reserved:
            raise InvalidArgumentError(
                "args cannot contain core add_resource fields: " + ", ".join(reserved)
            )

        normalized = dict(args)
        raw_parse_mode = normalized.pop("parse_mode", ParseMode.DEFAULT)
        try:
            parse_mode = normalize_parse_mode(raw_parse_mode)
        except InvalidArgumentError as exc:
            raise InvalidArgumentError(str(exc).replace("parse_mode", "args.parse_mode")) from exc
        token = normalized.get(FEISHU_ACCESS_TOKEN_ARG)
        refresh_token = normalized.pop(FEISHU_REFRESH_TOKEN_ARG, None)
        watch_auth_state = None
        if token is not None:
            if not isinstance(token, str) or not token.strip():
                raise InvalidArgumentError("args.feishu_access_token must be a non-empty string.")
            token = token.strip()
            normalized[FEISHU_ACCESS_TOKEN_ARG] = token
            if watch_interval > 0:
                if not isinstance(refresh_token, str) or not refresh_token.strip():
                    raise InvalidArgumentError(
                        "args.feishu_refresh_token must be a non-empty string when "
                        "args.feishu_access_token is used with watch_interval > 0."
                    )
                self._ensure_feishu_credentials_for_watch()
                watch_auth_state = create_feishu_auth_state(token, refresh_token.strip())
            elif refresh_token is not None:
                raise InvalidArgumentError(
                    "args.feishu_refresh_token is only supported with "
                    "args.feishu_access_token and watch_interval > 0."
                )
        elif refresh_token is not None:
            raise InvalidArgumentError(
                "args.feishu_refresh_token requires args.feishu_access_token."
            )

        return _NormalizedAddResourceArgs(normalized, watch_auth_state, parse_mode)

    def _ensure_feishu_credentials_for_watch(self) -> None:
        try:
            load_feishu_app_credentials()
        except Exception as exc:
            raise InvalidArgumentError(
                "Feishu user-token watch requires FEISHU_APP_ID and "
                "FEISHU_APP_SECRET, or feishu.app_id and feishu.app_secret in ov.conf."
            ) from exc

    def _ensure_initialized(self) -> None:
        """Ensure all dependencies are initialized."""
        if not self._resource_processor:
            raise NotInitializedError("ResourceProcessor")
        if not self._skill_processor:
            raise NotInitializedError("SkillProcessor")
        if not self._viking_fs:
            raise NotInitializedError("VikingFS")

    async def _lock_to_handoff_payload(self, lock_ref: Any) -> Optional[Dict[str, Any]]:
        """Convert either a native pathlock ref or legacy lease into a handoff payload."""
        if lock_ref is None:
            return None
        async_agfs = getattr(self._viking_fs, "_async_agfs", None)
        if async_agfs is not None:
            return await async_agfs.pathlock_to_handoff(lock_ref)
        to_handoff = getattr(lock_ref, "to_handoff", None)
        if callable(to_handoff):
            handoff = to_handoff()
            return handoff.to_dict() if hasattr(handoff, "to_dict") else handoff
        return lock_ref if isinstance(lock_ref, dict) else None

    async def _handoff_lock_ref(self, lock_ref: Any) -> None:
        """Transfer ownership for either a native pathlock ref or legacy lease."""
        if lock_ref is None:
            return
        async_agfs = getattr(self._viking_fs, "_async_agfs", None)
        if async_agfs is not None:
            await async_agfs.pathlock_handoff(lock_ref)
            return
        handoff = getattr(lock_ref, "handoff", None)
        if callable(handoff):
            result = handoff()
            if inspect.isawaitable(result):
                await result

    async def _release_lock_ref(self, lock_ref: Any) -> None:
        """Release either a native pathlock ref or legacy lease."""
        if lock_ref is None:
            return
        async_agfs = getattr(self._viking_fs, "_async_agfs", None)
        if async_agfs is not None:
            await async_agfs.pathlock_release(lock_ref)
            return
        close = getattr(lock_ref, "close", None)
        if callable(close):
            result = close()
            if inspect.isawaitable(result):
                await result

    async def close_background_tasks(self) -> None:
        """Cancel in-flight connector monitoring tasks during service shutdown."""
        if not self._background_tasks:
            return
        tasks = list(self._background_tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._background_tasks.clear()

    async def _enqueue_add_resource_job(
        self,
        msg: Any,
        *,
        queue_name: str,
        resource_lock: Optional[Dict[str, Any]] = None,
        task_auth: Optional[Dict[str, Any]] = None,
        on_enqueued: Optional[Callable[[], None]] = None,
    ) -> Any:
        """Persist a job and fully own the passed lock until handoff or release completes."""
        from openviking.service.task_tracker import get_task_tracker
        from openviking.storage.queuefs import get_queue_manager

        tracker = get_task_tracker()
        task = None
        enqueued = False
        try:
            task = await tracker.create(
                "add_resource",
                resource_id=None if msg.defer_target_resolution else msg.root_uri,
                account_id=msg.account_id,
                user_id=msg.user_id,
                task_id=msg.task_id,
                meta={"source_path": msg.source_path},
                auth=task_auth,
            )
            await get_queue_manager().enqueue(queue_name, msg.to_dict())
            enqueued = True
            if on_enqueued is not None:
                on_enqueued()
            if resource_lock is not None:
                await self._handoff_lock_ref(resource_lock)
                resource_lock = None
            await tracker.update_stage(
                task.task_id,
                "queued",
                account_id=msg.account_id,
                user_id=msg.user_id,
            )
        except BaseException:
            if resource_lock is not None:
                await self._release_lock_ref(resource_lock)
            if task is not None and not enqueued:
                await tracker.fail(
                    task.task_id,
                    "Failed to enqueue resource processing",
                    account_id=msg.account_id,
                    user_id=msg.user_id,
                )
            raise

        return task

    async def execute_add_resource_job(
        self,
        msg: Any,
        *,
        ctx: RequestContext,
        resource_lock: Optional[Dict[str, Any]],
        stage_callback: Callable[[str], Any],
        task_auth: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Execute one durable add-resource job inside its QueueFS consumer."""
        if msg.job_phase is AddResourcePhase.SOURCE:
            from openviking.resource.staged_source import StagedSource, materialize_source

            target_uri = msg.root_uri
            parent_uri = None
            queued_args = dict(msg.args)
            legacy_backend = normalize_parser_backend(queued_args.pop("parser_backend", None))
            parser_backend = legacy_backend or (
                ParserBackend.UNDERSTANDING
                if msg.understanding_response_id is not None
                else ParserBackend.INTERNAL
            )
            internal_kwargs: Dict[str, Any] = {"parser_backend": parser_backend}
            if "resolved_extension" in queued_args:
                internal_kwargs["resolved_extension"] = queued_args.pop("resolved_extension")
            normalized_args = self._normalize_add_resource_args(
                queued_args,
                watch_interval=msg.watch_interval,
            )
            internal_kwargs.update(normalized_args.processor_kwargs)
            if msg.strict:
                internal_kwargs["strict"] = True
            if msg.ignore_dirs is not None:
                internal_kwargs["ignore_dirs"] = msg.ignore_dirs
            if msg.include is not None:
                internal_kwargs["include"] = msg.include
            if msg.exclude is not None:
                internal_kwargs["exclude"] = msg.exclude
            if not msg.directly_upload_media:
                internal_kwargs["directly_upload_media"] = False
            if msg.preserve_structure is not None:
                internal_kwargs["preserve_structure"] = msg.preserve_structure
            if msg.create_parent:
                internal_kwargs["create_parent"] = True
            if msg.source_name is not None:
                internal_kwargs["source_name"] = msg.source_name
            auth_kwargs, watch_auth_state = self._restore_source_task_auth(
                msg,
                task_auth or {},
            )
            internal_kwargs.update(auth_kwargs)
            prepared_resource = None
            if msg.staged_source is not None:
                prepared_resource = await materialize_source(
                    StagedSource.from_dict(msg.staged_source),
                    viking_fs=self._viking_fs,
                    ctx=ctx,
                )
            if msg.defer_target_resolution:
                from openviking_cli.utils.uri import VikingURI

                target_uri = None
                parent_uri = VikingURI(msg.root_uri).parent.uri
            if msg.understanding_response_id is not None:
                from openviking.parse.understanding_api import PREPARED_RESPONSE_ID_ARG

                internal_kwargs[PREPARED_RESPONSE_ID_ARG] = msg.understanding_response_id
            result = await self._execute_resource_ingestion(
                path=msg.path,
                ctx=ctx,
                to=target_uri,
                parent=parent_uri,
                to_is_directory=msg.to_is_directory,
                reason=msg.reason,
                instruction=msg.instruction,
                defer_post_processing=False,
                timeout=msg.timeout,
                build_index=msg.build_index,
                summarize=msg.summarize,
                processing_mode=msg.processing_mode,
                parse_mode=msg.parse_mode,
                watch_interval=msg.watch_interval,
                manage_watch=not msg.skip_watch_management,
                tags=msg.tags,
                tag_mode=msg.tag_mode,
                allow_local_path_resolution=msg.allow_local_path_resolution,
                enforce_public_remote_targets=msg.enforce_public_remote_targets,
                resource_lock=resource_lock,
                stage_callback=stage_callback,
                watch_auth_state=watch_auth_state,
                prepared_resource=prepared_resource,
                **internal_kwargs,
            )
            if msg.staged_source is not None:
                result["source_path"] = msg.source_path
            stage_result = stage_callback("processing_queue")
            if inspect.isawaitable(stage_result):
                await stage_result
            return result

        stage_result = stage_callback("processing_queue")
        if inspect.isawaitable(stage_result):
            await stage_result
        return await self._resource_processor.finish_prepared_resource(
            msg.prepared,
            ctx=ctx,
            resource_lock=resource_lock,
            summarize=msg.summarize,
            build_index=msg.build_index,
            processing_mode=msg.processing_mode,
            **self._add_resource_ingest_tag_kwargs(
                tags=msg.tags,
                tag_mode=msg.tag_mode,
            ),
        )

    def _restore_source_task_auth(
        self,
        msg: Any,
        task_auth: Dict[str, Any],
    ) -> tuple[Dict[str, Any], Optional[Dict[str, Any]]]:
        """Restore provider-specific request inputs from task-owned auth state."""
        if not task_auth:
            return {}, None
        if is_git_http_auth_state(task_auth):
            auth_config = git_http_auth_config_from_state(task_auth, msg.path)
            watch_auth_state = dict(task_auth) if msg.watch_interval > 0 else None
            return {"auth_config": auth_config}, watch_auth_state
        if is_feishu_auth_state(task_auth):
            token = task_auth.get("access_token")
            if not isinstance(token, str) or not token.strip():
                raise InvalidArgumentError("Stored Feishu task credentials are invalid.")
            if msg.watch_interval > 0:
                refresh_token = task_auth.get("refresh_token")
                if not isinstance(refresh_token, str) or not refresh_token.strip():
                    raise InvalidArgumentError(
                        "Stored Feishu watch credentials are missing a refresh token."
                    )
                watch_auth_state = dict(task_auth)
            else:
                watch_auth_state = None
            auth_kwargs = (
                {FEISHU_ACCESS_TOKEN_ARG: token.strip()}
                if msg.understanding_response_id is None
                else {}
            )
            return auth_kwargs, watch_auth_state
        raise InvalidArgumentError("Unsupported task authentication provider.")

    async def reacquire_add_resource_job_lock(
        self,
        root_uri: str,
        ctx: RequestContext,
    ) -> Dict[str, Any]:
        """Acquire a fresh lock when a recovered job's old handoff was released."""
        if not self._resource_processor or not self._viking_fs:
            raise NotInitializedError("ResourceProcessor")

        dst_path = self._viking_fs._uri_to_path(root_uri, ctx=ctx)
        return await self._viking_fs._async_agfs.pathlock_acquire_tree(
            dst_path,
            timeout_secs=0.0,
        )

    async def _prepare_standard_source_plan(
        self,
        *,
        path: str,
        ctx: RequestContext,
        mode: ParseMode,
        allow_local_path_resolution: bool,
        processor_kwargs: Dict[str, Any],
        watch_auth_state: Optional[Dict[str, Any]],
    ) -> Optional[_SourcePlan]:
        """Freeze one durable standard-pipeline source before it crosses QueueFS."""
        from openviking.parse.accessors.feishu_accessor import FeishuAccessor
        from openviking.resource.staged_source import stage_source

        source_name = processor_kwargs.get("source_name")
        git_source = is_git_repo_url(path)
        feishu_source = FeishuAccessor._is_feishu_url(path)
        remote_source = is_remote_resource_source(path)
        local_source = False
        if allow_local_path_resolution and len(path) <= 1024 and "\n" not in path:
            with contextlib.suppress(OSError, ValueError):
                local_source = Path(path).exists()
        if not (git_source or feishu_source or remote_source or local_source):
            return None

        queued_args = {
            key: value
            for key, value in processor_kwargs.items()
            if key not in _ADD_RESOURCE_ARGS_RESERVED_FIELDS | _ADD_RESOURCE_TRANSIENT_ARGS
        }
        queued_args = self._sanitize_watch_processor_kwargs(queued_args)
        task_auth: Dict[str, Any] = {}
        staged_source = None
        understanding_response_id = None
        defer_unnamed_target = False

        if git_source:
            reject_git_http_userinfo(path)
            from openviking.connector.routing import credential_arg_names

            credential_args = credential_arg_names("git", processor_kwargs)
            if credential_args:
                raise InvalidArgumentError("Native Git credentials must use args.auth_config.")
            git_auth = parse_git_http_auth_config(processor_kwargs.get("auth_config"), path)
            if git_auth is not None:
                task_auth = create_git_http_auth_state(git_auth, path)
            source_info = await self._preflight_git_source(path, auth_config=git_auth)
            source_name = source_name or source_info.source_name
            source_info.source_name = source_name
        elif feishu_source:
            token = processor_kwargs.get(FEISHU_ACCESS_TOKEN_ARG)
            if isinstance(token, str) and token.strip():
                task_auth = dict(
                    watch_auth_state
                    or {
                        "provider": FEISHU_AUTH_PROVIDER,
                        "access_token": token.strip(),
                    }
                )
            preflight = await FeishuAccessor().preflight_source(
                path,
                feishu_access_token=token.strip() if isinstance(token, str) else None,
            )
            source_name = source_name or preflight.source_name
            source_info = _ResourceSourceInfo(
                source_name=source_name,
                source_path=path,
                source_format=preflight.source_format,
            )
            defer_unnamed_target = True
            direct_understanding = bool(
                mode is ParseMode.DEFAULT
                and self._resource_processor.should_use_understanding_directly(
                    path,
                    **processor_kwargs,
                )
            )
            if direct_understanding:
                understanding_response_id = await self._resource_processor.submit_understanding(
                    path,
                    **processor_kwargs,
                )
                if watch_auth_state is None:
                    task_auth = {}
        else:
            prepared = await self._resource_processor.prepare_durable_source(
                path,
                ctx,
                snapshot_required=local_source
                or bool(processor_kwargs.get("tos_signature") or processor_kwargs.get("tos_access")),
                parse_mode=mode,
                allow_local_path_resolution=allow_local_path_resolution,
                **processor_kwargs,
            )
            if prepared is None:
                parsed_path = Path(urlparse(path).path)
                source_name = source_name or parsed_path.name or None
                source_info = _ResourceSourceInfo(
                    source_name=source_name,
                    source_path=path,
                    source_format=parsed_path.suffix.lower().lstrip(".") or "file",
                )
            else:
                try:
                    resolved_extension, source_info = self._prepared_source_info(
                        prepared,
                        path,
                        source_name,
                        use_path_name=local_source,
                    )
                    if resolved_extension:
                        queued_args["resolved_extension"] = resolved_extension
                    if (
                        mode is ParseMode.DEFAULT
                        and self._resource_processor.should_use_understanding_api(prepared)
                    ):
                        understanding_response_id = (
                            await self._resource_processor.submit_understanding(
                                prepared,
                                **processor_kwargs,
                            )
                        )
                    else:
                        staged_source = await stage_source(
                            prepared,
                            viking_fs=self._viking_fs,
                            ctx=ctx,
                        )
                finally:
                    prepared.cleanup()

        return _SourcePlan(
            path=path,
            source_identity=source_info,
            processor_args=queued_args,
            task_auth=task_auth,
            staged_source=staged_source,
            understanding_response_id=understanding_response_id,
            defer_unnamed_target=defer_unnamed_target,
        )

    @staticmethod
    def _prepared_source_info(
        resource: "LocalResource",
        path: str,
        source_name: Optional[str],
        *,
        use_path_name: bool = False,
    ) -> tuple[str, _ResourceSourceInfo]:
        resolved_extension = str(
            resource.meta.get("resolved_extension")
            or resource.meta.get("extension")
            or resource.path.suffix
            or ""
        )
        resolved_name = (
            source_name
            or resource.meta.get("original_filename")
            or resource.meta.get("resolved_name")
        )
        if not resolved_name and use_path_name:
            resolved_name = resource.path.name
        source_format = "directory" if resource.path.is_dir() else resolved_extension.lstrip(".")
        if not source_format:
            source_format = "file"
        if resolved_extension.lower().lstrip(".") == MPEG_TS_EXTENSION_ALIAS:
            source_format = "video"
        return (
            resolved_extension,
            _ResourceSourceInfo(
                source_name=resolved_name,
                source_path=resource.original_source or path,
                source_format=source_format,
            ),
        )

    async def _enqueue_source_plan(
        self,
        plan: _SourcePlan,
        *,
        ctx: RequestContext,
        to: str,
        parent: str,
        create_parent: bool,
        reason: str,
        instruction: str,
        timeout: Optional[float],
        build_index: bool,
        summarize: bool,
        processing_mode: ProcessingMode,
        mode: ParseMode,
        watch_interval: float,
        manage_watch: bool,
        tags: Optional[List[str]],
        tag_mode: str,
        to_is_directory: Optional[bool],
        allow_local_path_resolution: bool,
        enforce_public_remote_targets: bool,
        processor_kwargs: Dict[str, Any],
    ) -> Dict[str, Any]:
        from openviking.storage.queuefs.add_resource_msg import AddResourceMsg

        planned_to_is_directory = (
            to_is_directory if to_is_directory is not None else bool(to)
        )
        target_to_is_exact = bool(to and not is_content_root_uri(to, kind="resource"))
        defer_candidate_resolution = bool(
            (
                plan.defer_unnamed_target
                and plan.source_identity.source_name is None
                and not to
            )
            or (mode is ParseMode.NO_SPLIT and not target_to_is_exact)
        )
        root_uri = ""
        resource_lock = None
        defer_target_resolution = False
        staged_enqueued = False
        try:
            root_uri, resource_lock, defer_target_resolution = await self._plan_source_job_target(
                path=plan.path,
                ctx=ctx,
                to=to,
                parent=parent,
                create_parent=create_parent,
                source_info=plan.source_identity,
                defer_candidate_resolution=defer_candidate_resolution,
            )
            lock_handoff = await self._lock_to_handoff_payload(resource_lock)
            msg = AddResourceMsg(
                task_id=str(uuid4()),
                job_phase=AddResourcePhase.SOURCE,
                path=plan.path,
                source_path=(plan.source_identity.source_name or "")
                if processor_kwargs.get("temp_file_id")
                else plan.source_identity.source_path or plan.path,
                root_uri=root_uri,
                staged_source=(
                    plan.staged_source.to_dict() if plan.staged_source is not None else None
                ),
                telemetry_id=get_current_telemetry().telemetry_id or None,
                account_id=ctx.account_id,
                user_id=ctx.user.user_id,
                role=str(ctx.role),
                actor_peer_id=ctx.actor_peer_id,
                lock_handoff=lock_handoff,
                reason=reason,
                instruction=instruction,
                timeout=timeout,
                build_index=build_index,
                summarize=summarize,
                processing_mode=processing_mode,
                parse_mode=mode.value,
                watch_interval=watch_interval,
                skip_watch_management=not manage_watch,
                tags=tags,
                tag_mode=tag_mode,
                allow_local_path_resolution=(
                    True if plan.staged_source is not None else allow_local_path_resolution
                ),
                enforce_public_remote_targets=(
                    enforce_public_remote_targets and plan.staged_source is None
                ),
                strict=bool(processor_kwargs.get("strict", False)),
                ignore_dirs=processor_kwargs.get("ignore_dirs"),
                include=processor_kwargs.get("include"),
                exclude=processor_kwargs.get("exclude"),
                directly_upload_media=bool(processor_kwargs.get("directly_upload_media", True)),
                preserve_structure=processor_kwargs.get("preserve_structure"),
                create_parent=create_parent,
                source_name=plan.source_identity.source_name,
                to_is_directory=planned_to_is_directory,
                args=plan.processor_args,
                defer_target_resolution=defer_target_resolution,
                understanding_response_id=plan.understanding_response_id,
            )

            def transfer_staged_source() -> None:
                nonlocal staged_enqueued
                staged_enqueued = True

            queue_name = (
                QueueManager.EXTERNAL_PARSE
                if plan.understanding_response_id is not None
                else QueueManager.ADD_RESOURCE
            )
            enqueue_lock = resource_lock
            resource_lock = None
            task = await self._enqueue_add_resource_job(
                msg,
                queue_name=queue_name,
                resource_lock=enqueue_lock,
                task_auth=plan.task_auth,
                on_enqueued=(transfer_staged_source if plan.staged_source is not None else None),
            )
        except BaseException:
            if resource_lock is not None:
                await self._release_lock_ref(resource_lock)
            if plan.staged_source is not None and not staged_enqueued:
                await self._viking_fs.delete_temp(plan.staged_source.temp_uri, ctx=ctx)
            raise

        response = {"status": "success", "task_id": task.task_id}
        if not defer_target_resolution:
            response["root_uri"] = root_uri
        return response

    async def _plan_source_job_target(
        self,
        *,
        path: str,
        ctx: RequestContext,
        to: str,
        parent: str,
        create_parent: bool,
        source_info: _ResourceSourceInfo,
        defer_candidate_resolution: bool,
    ) -> tuple[str, Optional[Dict[str, Any]], bool]:
        if not self._resource_processor or not self._viking_fs:
            raise NotInitializedError("ResourceProcessor")

        doc_name = self._target_doc_name(path, source_info)
        source_path = source_info.source_path or source_info.source_name or path
        root_uri, candidate_uri = await self._resource_processor.tree_builder.resolve_target_uri(
            ctx=ctx,
            doc_name=doc_name,
            scope="resources",
            to_uri=to,
            parent_uri=parent,
            source_path=source_path,
            source_format=source_info.source_format,
            create_parent=create_parent,
        )
        if candidate_uri and defer_candidate_resolution:
            return root_uri, None, True
        if candidate_uri:
            root_uri, resource_lock = await self._resource_processor.reserve_unique_candidate(
                candidate_uri=candidate_uri,
                ctx=ctx,
            )
            return root_uri, resource_lock, False

        dst_path = self._viking_fs._uri_to_path(root_uri, ctx=ctx)
        resource_lock = await self._viking_fs._async_agfs.pathlock_acquire_tree(
            dst_path,
            timeout_secs=0.0,
        )
        return root_uri, resource_lock, False

    @staticmethod
    def _target_doc_name(
        path: str,
        source_info: _ResourceSourceInfo,
    ) -> str:
        if source_info.source_name:
            return _smart_stem(source_info.source_name)
        if source_info.source_format == "repository":
            parsed = parse_code_hosting_url(path)
            if parsed:
                return parsed.rsplit("/", 1)[-1]
        return _smart_stem(Path(path).name or "resource")

    async def _preflight_git_source(
        self,
        source: str,
        *,
        auth_config: Optional[GitHttpAuthConfig] = None,
    ) -> _ResourceSourceInfo:
        proc = None
        try:
            env = build_git_http_auth_env(auth_config, source) if auth_config is not None else None
            proc = await asyncio.create_subprocess_exec(
                "git",
                "ls-remote",
                "--heads",
                source,
                env=env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=10.0)
        except TimeoutError as exc:
            if proc is not None:
                with contextlib.suppress(Exception):
                    proc.kill()
                    await proc.communicate()
            raise InvalidArgumentError(
                "Cannot access Git repository; the preflight timed out after 10s."
            ) from exc
        except Exception as exc:
            raise InvalidArgumentError("Cannot access Git repository during preflight.") from exc

        if proc.returncode != 0:
            raise InvalidArgumentError("Cannot access Git repository; git ls-remote failed.")
        repo_name = parse_code_hosting_url(source)
        return _ResourceSourceInfo(
            source_name=repo_name.rsplit("/", 1)[-1] if repo_name else None,
            source_path=source,
            source_format="repository",
        )

    async def add_resource(
        self,
        path: str,
        ctx: RequestContext,
        to: Optional[str] = None,
        parent: Optional[str] = None,
        reason: str = "",
        instruction: str = "",
        wait: bool = False,
        timeout: Optional[float] = None,
        build_index: bool = True,
        summarize: bool = False,
        processing_mode: ProcessingMode = DEFAULT_PROCESSING_MODE,
        watch_interval: float = 0,
        tags: Optional[List[str]] = None,
        tag_mode: str = "replace",
        allow_local_path_resolution: bool = True,
        enforce_public_remote_targets: bool = False,
        add_type: Optional[str] = None,
        args: Optional[Dict[str, Any]] = None,
        **kwargs,
    ) -> Dict[str, Any]:
        """Accept and route a new resource-add request."""
        internal_fields = sorted(set(kwargs).intersection(_INTERNAL_INGESTION_FIELDS))
        if internal_fields:
            raise InvalidArgumentError(
                "add_resource does not accept internal execution fields: "
                + ", ".join(internal_fields)
            )
        if isinstance(add_type, str):
            add_type = add_type.strip() or None
        return await self._submit_resource_ingestion(
            path=path,
            ctx=ctx,
            add_type=add_type,
            to=to,
            parent=parent,
            reason=reason,
            instruction=instruction,
            wait=wait,
            timeout=timeout,
            build_index=build_index,
            summarize=summarize,
            processing_mode=processing_mode,
            watch_interval=watch_interval,
            manage_watch=True,
            tags=tags,
            tag_mode=tag_mode,
            allow_local_path_resolution=allow_local_path_resolution,
            enforce_public_remote_targets=enforce_public_remote_targets,
            args=args,
            **kwargs,
        )

    async def refresh_resource(
        self,
        path: str,
        ctx: RequestContext,
        to: Optional[str] = None,
        to_is_directory: Optional[bool] = None,
        parent: Optional[str] = None,
        reason: str = "",
        instruction: str = "",
        timeout: Optional[float] = None,
        build_index: bool = True,
        summarize: bool = False,
        processing_mode: ProcessingMode = DEFAULT_PROCESSING_MODE,
        watch_interval: float = 0,
        allow_local_path_resolution: bool = True,
        enforce_public_remote_targets: bool = False,
        add_type: Optional[str] = None,
        args: Optional[Dict[str, Any]] = None,
        **kwargs,
    ) -> Dict[str, Any]:
        """Submit a scheduled refresh without changing its watch task."""
        return await self._submit_resource_ingestion(
            path=path,
            ctx=ctx,
            to=to,
            to_is_directory=to_is_directory,
            parent=parent,
            reason=reason,
            instruction=instruction,
            wait=False,
            timeout=timeout,
            build_index=build_index,
            summarize=summarize,
            processing_mode=processing_mode,
            watch_interval=watch_interval,
            manage_watch=False,
            allow_local_path_resolution=allow_local_path_resolution,
            enforce_public_remote_targets=enforce_public_remote_targets,
            add_type=add_type,
            args=args,
            **kwargs,
        )

    async def _submit_resource_ingestion(
        self,
        path: str,
        ctx: RequestContext,
        add_type: Optional[str] = None,
        to: Optional[str] = None,
        to_is_directory: Optional[bool] = None,
        parent: Optional[str] = None,
        reason: str = "",
        instruction: str = "",
        wait: bool = False,
        timeout: Optional[float] = None,
        build_index: bool = True,
        summarize: bool = False,
        processing_mode: ProcessingMode = DEFAULT_PROCESSING_MODE,
        watch_interval: float = 0,
        manage_watch: bool = True,
        tags: Optional[List[str]] = None,
        tag_mode: str = "replace",
        parse_mode: ParseMode | str | None = None,
        allow_local_path_resolution: bool = True,
        enforce_public_remote_targets: bool = False,
        args: Optional[Dict[str, Any]] = None,
        **kwargs,
    ) -> Dict[str, Any]:
        """Validate and route one resource ingestion request.

        Args:
            path: Resource path (local file or URL)
            add_type: Explicitly declared Connector source type. Routes the
                request to the Connector integration without probing the path;
                the type must be enabled in connector.allowed_add_types. A
                declared request never degrades to the standard pipeline and
                requires an exact ``to`` target.
            to: Target URI (e.g., "viking://resources/my_resource"). Required
                when ``add_type`` is set.
            parent: Parent URI under which the resource will be stored. Not
                supported when ``add_type`` is set.
            reason: Reason for adding the resource
            instruction: Processing instruction for semantic extraction
            wait: Whether to wait for semantic extraction and vectorization to complete
            timeout: Wait timeout in seconds
            build_index: Whether to build vector index immediately (default: True)
            summarize: Whether to generate summary (default: False)
            processing_mode: Post-ingest processing mode for semantic/vector work
            watch_interval: Watch interval in minutes for automatic resource monitoring.
                - watch_interval > 0: Creates or updates a watch task. The resource will be
                  automatically re-processed at the specified interval by the scheduler.
                - watch_interval = 0: No watch task is created. If a watch task exists for
                  this resource, it will be cancelled (deactivated).
                - watch_interval < 0: Same as watch_interval = 0, cancels any existing watch task.
                Default is 0 (no monitoring).

                Note: Re-adding the same source to the same target updates its active watch
                task in place. A different source targeting an active watch raises
                ConflictError; cancel that watch first with watch_interval <= 0. Connector
                imports create the Watch only after the background import succeeds, so this
                conflict may be reported after both overlapping imports have written data.
            enforce_public_remote_targets: When True, reject non-public remote hosts and
                validate each outbound HTTP request URL during fetch.
            args: Parser/accessor-specific options forwarded to the processing chain.
            **kwargs: Extra options forwarded to the parser chain

        Returns:
            Processing result containing 'root_uri' and other metadata

        Raises:
            ConflictError: If a different source targets an active watch task
            InvalidArgumentError: If the URI scope is not 'resources'
        """
        self._ensure_initialized()
        processing_mode = normalize_processing_mode(processing_mode)
        self._validate_add_resource_tag_policy(tags=tags, tag_mode=tag_mode)
        from openviking.connector.delegate import ConnectorDelegate

        allowed_reserved_fields = ConnectorDelegate.supported_args(path, add_type).intersection(
            _ADD_RESOURCE_ARGS_RESERVED_FIELDS
        )
        normalized_args = self._normalize_add_resource_args(
            args,
            watch_interval=watch_interval,
            allowed_reserved_fields=allowed_reserved_fields,
        )
        mode = (
            normalize_parse_mode(parse_mode)
            if parse_mode is not None
            else normalized_args.parse_mode
        )
        duplicated_fields = sorted(
            field
            for field in allowed_reserved_fields
            if field in normalized_args.processor_kwargs and kwargs.get(field) is not None
        )
        if duplicated_fields:
            raise InvalidArgumentError(
                f"{', '.join(duplicated_fields)} cannot be provided both as a top-level "
                "field and in args."
            )
        kwargs.update(normalized_args.processor_kwargs)
        tos_signature = kwargs.get("tos_signature")
        tos_access = kwargs.get("tos_access")
        if tos_signature is not None or tos_access is not None:
            if not path.startswith(("http://", "https://")):
                raise InvalidArgumentError(
                    "tos_signature and tos_access are only supported for HTTP(S) resource URLs."
                )
            if tos_signature is not None and tos_access is not None:
                raise InvalidArgumentError("tos_signature and tos_access cannot both be provided.")
            for field, value in (("tos_signature", tos_signature), ("tos_access", tos_access)):
                if value is not None:
                    if not isinstance(value, str) or not value.strip():
                        raise InvalidArgumentError(f"args.{field} must be a non-empty string.")
                    kwargs[field] = value.strip()
        git_repo_source = is_git_repo_url(path)
        if git_repo_source:
            reject_git_http_userinfo(path)
        if watch_interval > 0 and kwargs.get("temp_file_id"):
            # Fail fast: a watch on a static upload snapshot can never observe the
            # live source.
            raise InvalidArgumentError(
                "watch_interval > 0 is not supported for uploaded content: an "
                "upload is a static snapshot, so the watch would re-process "
                "stale content forever. Watch a URL / "
                "sitemap / RSS source instead, or re-add the resource when the "
                "source changes."
            )
        if not to and not parent:
            from openviking.server.dependencies import get_server_config

            default_parent = await effective_resource_add_target(
                viking_fs=self._viking_fs,
                ctx=ctx,
                server_config=get_server_config(),
            )
            if default_parent:
                parent = default_parent
                kwargs["create_parent"] = True

        self._ensure_single_resource_target(to=to, parent=parent)
        target_to = to or ""
        target_parent = parent or ""
        target_create_parent = bool(kwargs.get("create_parent", False))

        connector = self._connector
        if connector.should_delegate(
            path,
            ctx=ctx,
            declared_add_type=add_type,
            to=to,
            parent=parent,
            wait=wait,
            instruction=instruction,
            build_index=build_index,
            summarize=summarize,
            processing_mode=processing_mode,
            parse_mode=mode,
            watch_interval=watch_interval,
            connector_args=normalized_args.processor_kwargs,
            kwargs=kwargs,
        ):
            resolved = connector.resolve_add_type(path, add_type)
            if resolved is None:  # pragma: no cover - should_delegate already resolved it
                raise InvalidArgumentError(f"'{path}' does not match any Connector source type.")
            watch_manager = self._get_watch_manager()
            watch_auth_state = None
            defer_watch_creation = bool(watch_manager and manage_watch and watch_interval > 0)
            if defer_watch_creation and watch_manager:
                # Connector imports may run for a long time. This is only a best-effort
                # precheck: the authoritative conflict check happens when on_success
                # creates the Watch, after the Connector may already have written data.
                await watch_manager.get_upsertable_task_by_uri(
                    path=path,
                    to_uri=target_to,
                    account_id=ctx.account_id,
                    user_id=ctx.user.user_id,
                    role=str(ctx.role),
                )
                watch_auth_state = await connector.create_watch_auth_state(
                    api_key=ctx.api_key or "",
                    account_id=ctx.account_id,
                    add_type=resolved[0],
                    path=path,
                    connector_args=normalized_args.processor_kwargs,
                )
            connector_watch_processor_kwargs = self._watch_processor_kwargs(
                {
                    key: value
                    for key, value in kwargs.items()
                    if key not in normalized_args.processor_kwargs
                },
                tags,
                tag_mode,
            )
            on_success: Optional[Callable[[], Awaitable[None]]] = None
            if defer_watch_creation:

                async def create_watch_after_success() -> None:
                    await self._manage_watch_if_needed(
                        watch_manager=watch_manager,
                        manage_watch=True,
                        watch_interval=watch_interval,
                        to=target_to,
                        parent=target_parent,
                        to_is_directory=to_is_directory,
                        root_uri=target_to,
                        path=path,
                        reason=reason,
                        instruction=instruction,
                        build_index=build_index,
                        summarize=summarize,
                        processing_mode=processing_mode,
                        processor_kwargs=connector_watch_processor_kwargs,
                        watch_auth_state=watch_auth_state,
                        ctx=ctx,
                    )

                on_success = create_watch_after_success
            result = await connector.submit(
                path=path,
                ctx=ctx,
                declared_add_type=add_type,
                to=target_to,
                reason=reason,
                connector_args=normalized_args.processor_kwargs,
                tags=tags,
                tag_mode=tag_mode,
                wait_for_completion=not manage_watch and watch_interval > 0,
                on_success=on_success,
                **kwargs,
            )
            if not defer_watch_creation:
                await self._manage_watch_if_needed(
                    watch_manager=watch_manager,
                    manage_watch=manage_watch,
                    watch_interval=watch_interval,
                    to=target_to,
                    parent=target_parent,
                    to_is_directory=to_is_directory,
                    root_uri=target_to,
                    path=path,
                    reason=reason,
                    instruction=instruction,
                    build_index=build_index,
                    summarize=summarize,
                    processing_mode=processing_mode,
                    processor_kwargs=connector_watch_processor_kwargs,
                    watch_auth_state=watch_auth_state,
                    ctx=ctx,
                )
            return result

        if enforce_public_remote_targets and is_remote_resource_source(path):
            path = require_remote_resource_source(path)
            kwargs.setdefault("request_validator", ensure_public_remote_target)

        source_plan = await self._prepare_standard_source_plan(
            path=path,
            ctx=ctx,
            mode=mode,
            allow_local_path_resolution=allow_local_path_resolution,
            processor_kwargs=kwargs,
            watch_auth_state=normalized_args.watch_auth_state,
        )
        if source_plan is not None:
            result = await self._enqueue_source_plan(
                source_plan,
                ctx=ctx,
                to=target_to,
                parent=target_parent,
                create_parent=target_create_parent,
                reason=reason,
                instruction=instruction,
                timeout=timeout,
                build_index=build_index,
                summarize=summarize,
                processing_mode=processing_mode,
                mode=mode,
                watch_interval=watch_interval,
                manage_watch=manage_watch,
                tags=tags,
                tag_mode=tag_mode,
                to_is_directory=to_is_directory,
                allow_local_path_resolution=allow_local_path_resolution,
                enforce_public_remote_targets=enforce_public_remote_targets,
                processor_kwargs=kwargs,
            )
        else:
            result = await self._execute_resource_ingestion(
                path=path,
                ctx=ctx,
                to=target_to,
                to_is_directory=to_is_directory,
                parent=target_parent,
                reason=reason,
                instruction=instruction,
                defer_post_processing=True,
                timeout=timeout,
                build_index=build_index,
                summarize=summarize,
                processing_mode=processing_mode,
                parse_mode=mode,
                watch_interval=watch_interval,
                manage_watch=manage_watch,
                tags=tags,
                tag_mode=tag_mode,
                allow_local_path_resolution=allow_local_path_resolution,
                enforce_public_remote_targets=enforce_public_remote_targets,
                watch_auth_state=normalized_args.watch_auth_state,
                **kwargs,
            )
        get_current_telemetry().set("resource.flags.wait", wait)
        if not wait:
            return result
        if result.get("status") == "error":
            return result
        from openviking.service.task_tracker import TaskStatus, get_task_tracker

        task_id = result["task_id"]
        try:
            task = await get_task_tracker().wait(
                task_id,
                account_id=ctx.account_id,
                user_id=ctx.user.user_id,
                timeout=timeout,
            )
        except TimeoutError as exc:
            raise DeadlineExceededError("add resource task", timeout) from exc

        if task.status == TaskStatus.COMPLETED:
            completed = dict(task.result)
            completed.pop("task_id", None)
            return completed
        if task.status == TaskStatus.CANCELLED:
            return {"status": "cancelled"}
        return {
            "status": "error",
            "errors": [task.error],
        }

    async def _execute_resource_ingestion(
        self,
        path: str,
        ctx: RequestContext,
        defer_post_processing: bool,
        to: Optional[str] = None,
        to_is_directory: Optional[bool] = None,
        parent: Optional[str] = None,
        reason: str = "",
        instruction: str = "",
        timeout: Optional[float] = None,
        build_index: bool = True,
        summarize: bool = False,
        processing_mode: ProcessingMode = DEFAULT_PROCESSING_MODE,
        parse_mode: ParseMode | str = ParseMode.DEFAULT,
        watch_interval: float = 0,
        manage_watch: bool = True,
        tags: Optional[List[str]] = None,
        tag_mode: str = "replace",
        allow_local_path_resolution: bool = True,
        enforce_public_remote_targets: bool = False,
        watch_auth_state: Optional[Dict[str, Any]] = None,
        resource_lock: Optional[Dict[str, Any]] = None,
        stage_callback: Optional[Callable[[str], Any]] = None,
        prepared_resource: Optional["LocalResource"] = None,
        **kwargs,
    ) -> Dict[str, Any]:
        """Execute an already-routed resource ingestion."""
        self._ensure_initialized()
        mode = normalize_parse_mode(parse_mode)
        if mode is ParseMode.NO_SPLIT:
            kwargs["parse_mode"] = mode.value
        request_start = time.perf_counter()
        telemetry = get_current_telemetry()
        telemetry_id = telemetry.telemetry_id
        register_telemetry(telemetry)
        job_enqueued = False
        deferred_lock: Optional[Dict[str, Any]] = None
        ingest_tag_kwargs = self._add_resource_ingest_tag_kwargs(
            tags=tags,
            tag_mode=tag_mode,
        )
        watch_manager = self._get_watch_manager()
        watch_enabled = bool(watch_manager and manage_watch and watch_interval > 0)

        telemetry.set("resource.flags.build_index", build_index)
        telemetry.set("resource.flags.summarize", summarize)
        telemetry.set("resource.flags.watch_enabled", watch_enabled)

        try:
            self._ensure_single_resource_target(to=to, parent=parent)
            target_to = to or ""
            target_parent = parent or ""
            if to_is_directory is None:
                to_is_directory = bool(target_to)
            watch_to_is_directory = to_is_directory
            if enforce_public_remote_targets and is_remote_resource_source(path):
                path = require_remote_resource_source(path)
                kwargs.setdefault("request_validator", ensure_public_remote_target)
            if resource_lock is not None:
                kwargs["resource_lock"] = resource_lock

            result = await self._resource_processor.process_resource(
                path=path,
                ctx=ctx,
                reason=reason,
                instruction=instruction,
                scope="resources",
                to=target_to,
                parent=target_parent,
                to_is_directory=to_is_directory,
                build_index=build_index,
                summarize=summarize,
                processing_mode=processing_mode,
                stage_callback=stage_callback,
                allow_local_path_resolution=allow_local_path_resolution,
                prepared_resource=prepared_resource,
                defer_post_processing=True,
                **ingest_tag_kwargs,
                **kwargs,
            )
            prepared_resource = None

            if result.get("status") == "error":
                return result
            prepared = result.pop("_post_process", None)
            deferred_lock = result.pop("_resource_lock", None)
            if (
                not to_is_directory
                and isinstance(prepared, dict)
                and isinstance(prepared.get("root_is_file"), bool)
            ):
                watch_to_is_directory = not prepared["root_is_file"]
            if not isinstance(prepared, dict):
                raise InternalError("Deferred resource processing payload is missing")
            await self._manage_watch_if_needed(
                watch_manager=watch_manager,
                manage_watch=manage_watch,
                watch_interval=watch_interval,
                to=target_to,
                parent=target_parent,
                to_is_directory=watch_to_is_directory,
                root_uri=str(result.get("root_uri") or ""),
                path=path,
                reason=reason,
                instruction=instruction,
                build_index=build_index,
                summarize=summarize,
                processing_mode=processing_mode,
                processor_kwargs=self._watch_processor_kwargs(kwargs, tags, tag_mode),
                watch_auth_state=watch_auth_state,
                ctx=ctx,
            )
            if defer_post_processing:
                from openviking.storage.queuefs.add_resource_msg import AddResourceMsg

                root_uri = result.get("root_uri", "")
                lock_handoff = await self._lock_to_handoff_payload(deferred_lock)
                msg = AddResourceMsg(
                    task_id=str(uuid4()),
                    job_phase=AddResourcePhase.POST_PROCESS,
                    root_uri=root_uri,
                    prepared=prepared,
                    source_path=str(
                        (kwargs.get("source_name") or "")
                        if kwargs.get("temp_file_id")
                        else result.get("source_path") or ""
                    ),
                    telemetry_id=telemetry_id or None,
                    account_id=ctx.account_id,
                    user_id=ctx.user.user_id,
                    role=str(ctx.role),
                    actor_peer_id=ctx.actor_peer_id,
                    lock_handoff=lock_handoff,
                    reason=reason,
                    instruction=instruction,
                    timeout=timeout,
                    build_index=build_index,
                    summarize=summarize,
                    processing_mode=processing_mode,
                    strict=bool(kwargs.get("strict", False)),
                    ignore_dirs=kwargs.get("ignore_dirs"),
                    include=kwargs.get("include"),
                    exclude=kwargs.get("exclude"),
                    directly_upload_media=bool(kwargs.get("directly_upload_media", True)),
                    preserve_structure=kwargs.get("preserve_structure"),
                    create_parent=bool(kwargs.get("create_parent", False)),
                    allow_local_path_resolution=allow_local_path_resolution,
                    enforce_public_remote_targets=enforce_public_remote_targets,
                    source_name=kwargs.get("source_name"),
                    skip_watch_management=True,
                    tags=tags,
                    tag_mode=tag_mode,
                )
                enqueue_lock = deferred_lock
                deferred_lock = None
                task = await self._enqueue_add_resource_job(
                    msg,
                    queue_name=QueueManager.ADD_RESOURCE,
                    resource_lock=enqueue_lock,
                )
                result["task_id"] = task.task_id
                job_enqueued = True
            else:
                processing_lock = deferred_lock
                deferred_lock = None
                post_process_kwargs = dict(kwargs)
                for key in (
                    "resource_lock",
                    "request_validator",
                    "auth_config",
                    FEISHU_ACCESS_TOKEN_ARG,
                    FEISHU_REFRESH_TOKEN_ARG,
                    "parser_backend",
                    "resolved_extension",
                ):
                    post_process_kwargs.pop(key, None)
                post_result = await self._resource_processor.finish_prepared_resource(
                    prepared,
                    ctx=ctx,
                    resource_lock=processing_lock,
                    summarize=summarize,
                    build_index=build_index,
                    processing_mode=processing_mode,
                    **ingest_tag_kwargs,
                    **post_process_kwargs,
                )
                if post_result.get("warnings"):
                    result.setdefault("warnings", []).extend(post_result["warnings"])
            return result
        except Exception as exc:
            telemetry.set_error(
                "resource_service.add_resource",
                type(exc).__name__,
                str(exc),
            )
            raise
        finally:
            if prepared_resource is not None:
                prepared_resource.cleanup()
            telemetry.set(
                "resource.request.duration_ms",
                round((time.perf_counter() - request_start) * 1000, 3),
            )
            if not telemetry_id or (defer_post_processing and not job_enqueued):
                unregister_telemetry(telemetry_id)
            if deferred_lock is not None:
                await self._release_lock_ref(deferred_lock)

    async def _link_resource_reason_memory(
        self,
        *,
        result: Dict[str, Any],
        ctx: RequestContext,
        reason: str,
        source_name: Optional[str],
        timeout: Optional[float] = None,
    ) -> None:
        if not self._resource_memory_link_service:
            return
        if not (reason or "").strip():
            return
        root_uri = result.get("root_uri")
        if not root_uri:
            return
        try:
            link_result = await self._resource_memory_link_service.on_resource_added(
                ctx=ctx,
                resource_uri=root_uri,
                reason=reason,
                source_name=source_name,
                timeout=timeout,
            )
            result["memory_linking"] = link_result
        except Exception as exc:
            logger.warning("[ResourceService] Failed to link resource reason memory: %s", exc)
            result.setdefault("warnings", []).append(f"Memory linking failed: {exc}")

    async def _monitor_queue_processing(
        self,
        task_id: str,
        telemetry_id: str,
        account_id: str,
        user_id: str,
    ) -> None:
        from openviking.service.task_tracker import get_task_tracker

        task_tracker = get_task_tracker()
        request_wait_tracker = get_request_wait_tracker()
        await task_tracker.start(task_id, account_id=account_id, user_id=user_id)
        try:
            await request_wait_tracker.wait_for_request(telemetry_id)
            status = request_wait_tracker.build_queue_status(telemetry_id)
            errors = sum(int(group.get("error_count", 0) or 0) for group in status.values())
            if errors:
                await task_tracker.fail(
                    task_id,
                    f"queue processing failed: {status}",
                    account_id=account_id,
                    user_id=user_id,
                )
            else:
                await task_tracker.complete(
                    task_id,
                    {"queue_status": status},
                    account_id=account_id,
                    user_id=user_id,
                )
        except Exception as exc:
            await task_tracker.fail(task_id, str(exc), account_id=account_id, user_id=user_id)
        finally:
            request_wait_tracker.cleanup(telemetry_id)
            unregister_telemetry(telemetry_id)

    @staticmethod
    def _raise_queue_status_errors(status: Dict[str, Any]) -> None:
        failed = {
            name: group
            for name, group in status.items()
            if isinstance(group, dict)
            and (int(group.get("error_count", 0) or 0) > 0 or bool(group.get("errors")))
        }
        if failed:
            raise InternalError(f"queue processing failed: {failed}")

    # ── Connector routing ──

    @property
    def _connector(self) -> "ConnectorDelegate":
        """Connector delegation (lazy: viking_fs may be injected after init)."""
        if self._connector_delegate is None:
            from openviking.connector.delegate import ConnectorDelegate

            self._connector_delegate = ConnectorDelegate(
                viking_fs=self._viking_fs,
                background_tasks=self._background_tasks,
                link_reason_memory=self._link_resource_reason_memory,
            )
        return self._connector_delegate

    async def _handle_watch_task_creation(
        self,
        path: str,
        to_uri: str,
        to_is_directory: bool,
        parent_uri: Optional[str],
        reason: str,
        instruction: str,
        watch_interval: float,
        build_index: bool,
        summarize: bool,
        processing_mode: ProcessingMode,
        processor_kwargs: Dict[str, Any],
        auth_state: Optional[Dict[str, Any]],
        ctx: RequestContext,
    ) -> None:
        """Handle creation or update of watch task.

        Args:
            path: Resource path to monitor
            to_uri: Target URI
            parent_uri: Parent URI
            reason: Reason for monitoring
            instruction: Monitoring instruction
            watch_interval: Monitoring interval in minutes
            ctx: Request context with user identity

        Raises:
            ConflictError: If target URI is actively watched from a different source
        """
        watch_manager = self._get_watch_manager()
        if not watch_manager:
            return

        existing_task = await watch_manager.get_upsertable_task_by_uri(
            path=path,
            to_uri=to_uri,
            account_id=ctx.account_id,
            user_id=ctx.user.user_id,
            role=str(ctx.role),
        )
        if existing_task:
            was_active = existing_task.is_active
            await watch_manager.update_task(
                task_id=existing_task.task_id,
                account_id=ctx.account_id,
                user_id=ctx.user.user_id,
                role=str(ctx.role),
                path=path,
                to_uri=to_uri,
                to_is_directory=to_is_directory,
                parent_uri=parent_uri,
                reason=reason,
                instruction=instruction,
                watch_interval=watch_interval,
                build_index=build_index,
                summarize=summarize,
                processing_mode=processing_mode,
                processor_kwargs=processor_kwargs,
                auth_state=auth_state,
                is_active=True,
            )
            logger.info(
                f"[ResourceService] {'Updated active' if was_active else 'Reactivated and updated'} "
                f"watch task {existing_task.task_id} for {to_uri}"
            )
        else:
            task = await watch_manager.create_task(
                path=path,
                account_id=ctx.account_id,
                user_id=ctx.user.user_id,
                original_role=str(ctx.role),
                to_uri=to_uri,
                to_is_directory=to_is_directory,
                parent_uri=parent_uri,
                reason=reason,
                instruction=instruction,
                watch_interval=watch_interval,
                build_index=build_index,
                summarize=summarize,
                processing_mode=processing_mode,
                processor_kwargs=processor_kwargs,
                auth_state=auth_state,
            )
            logger.info(f"[ResourceService] Created watch task {task.task_id} for {to_uri}")

    async def _handle_watch_task_cancellation(self, to_uri: str, ctx: RequestContext) -> None:
        """Handle cancellation of watch task.

        Args:
            to_uri: Target URI to cancel watch for
            ctx: Request context with user identity
        """
        watch_manager = self._get_watch_manager()
        if not watch_manager:
            return

        existing_task = await watch_manager.get_task_by_uri(
            to_uri=to_uri,
            account_id=ctx.account_id,
            user_id=ctx.user.user_id,
            role=str(ctx.role),
        )
        if existing_task:
            await watch_manager.update_task(
                task_id=existing_task.task_id,
                account_id=ctx.account_id,
                user_id=ctx.user.user_id,
                role=str(ctx.role),
                is_active=False,
            )
            logger.info(
                f"[ResourceService] Deactivated watch task {existing_task.task_id} for {to_uri}"
            )

    async def add_skill(
        self,
        data: Any,
        ctx: RequestContext,
        wait: bool = False,
        timeout: Optional[float] = None,
        allow_local_path_resolution: bool = True,
        source_path_hint: Optional[str] = None,
        apply_privacy: bool = True,
        privacy_change_reason: str = "auto-extracted from add_skill",
        target_uri: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Add skill to OpenViking.

        Args:
            data: Skill data (directory path, file path, string, or dict)
            wait: Whether to wait for vectorization to complete
            timeout: Wait timeout in seconds
            target_uri: Optional root URI override (e.g. ``viking://agent/skills``).

        Returns:
            Processing result
        """
        self._ensure_initialized()
        if not target_uri:
            from openviking.server.dependencies import get_server_config

            target_uri = await effective_skill_add_target(
                viking_fs=self._viking_fs,
                ctx=ctx,
                server_config=get_server_config(),
            )
        telemetry_id = get_current_telemetry().telemetry_id
        request_wait_tracker = get_request_wait_tracker()
        monitor_started = False
        if telemetry_id:
            request_wait_tracker.register_request(telemetry_id)

        try:
            if isinstance(data, SkillProcessingPreparation):
                result = await self._skill_processor.process_prepared_skill(
                    data,
                    viking_fs=self._viking_fs,
                    ctx=ctx,
                    apply_privacy=apply_privacy,
                    privacy_change_reason=privacy_change_reason,
                    target_uri=target_uri,
                )
            else:
                result = await self._skill_processor.process_skill(
                    data=data,
                    viking_fs=self._viking_fs,
                    ctx=ctx,
                    allow_local_path_resolution=allow_local_path_resolution,
                    source_path_hint=source_path_hint,
                    apply_privacy=apply_privacy,
                    privacy_change_reason=privacy_change_reason,
                    target_uri=target_uri,
                )
            if isinstance(result, dict) and "root_uri" not in result and result.get("uri"):
                result["root_uri"] = result["uri"]

            if wait:
                wait_start = time.perf_counter()
                try:
                    if telemetry_id:
                        await request_wait_tracker.wait_for_request(telemetry_id, timeout=timeout)
                        status = request_wait_tracker.build_queue_status(telemetry_id)
                    else:
                        qm = get_queue_manager()
                        status = build_queue_status_payload(await qm.wait_complete(timeout=timeout))
                except TimeoutError as exc:
                    get_current_telemetry().set_error(
                        "resource_service.wait_complete",
                        "DEADLINE_EXCEEDED",
                        str(exc),
                    )
                    raise DeadlineExceededError("queue processing", timeout) from exc
                get_current_telemetry().set(
                    "queue.wait.duration_ms",
                    round((time.perf_counter() - wait_start) * 1000, 3),
                )
                result["queue_status"] = status
                self._raise_queue_status_errors(status)
            else:
                from openviking.service.task_tracker import get_task_tracker

                task_tracker = get_task_tracker()
                task = await task_tracker.create(
                    "add_skill",
                    account_id=ctx.account_id,
                    user_id=ctx.user.user_id,
                )
                result["task_id"] = task.task_id
                if telemetry_id:
                    monitor_started = True
                    asyncio.create_task(
                        self._monitor_queue_processing(
                            task.task_id,
                            telemetry_id,
                            ctx.account_id,
                            ctx.user.user_id,
                        )
                    )
                else:
                    await task_tracker.start(
                        task.task_id, account_id=ctx.account_id, user_id=ctx.user.user_id
                    )
                    await task_tracker.complete(
                        task.task_id,
                        {},
                        account_id=ctx.account_id,
                        user_id=ctx.user.user_id,
                    )

            return result
        finally:
            if wait or not telemetry_id or not monitor_started:
                request_wait_tracker.cleanup(telemetry_id)
                unregister_telemetry(telemetry_id)

    async def build_index(
        self, resource_uris: List[str], ctx: RequestContext, **kwargs
    ) -> Dict[str, Any]:
        """Manually trigger index building.

        Args:
            resource_uris: List of resource URIs to index.
            ctx: Request context.

        Returns:
            Processing result
        """
        self._ensure_initialized()
        return await self._resource_processor.build_index(resource_uris, ctx, **kwargs)

    async def summarize(
        self, resource_uris: List[str], ctx: RequestContext, **kwargs
    ) -> Dict[str, Any]:
        """Manually trigger summarization.

        Args:
            resource_uris: List of resource URIs to summarize.
            ctx: Request context.

        Returns:
            Processing result
        """
        self._ensure_initialized()
        return await self._resource_processor.summarize(resource_uris, ctx, **kwargs)

    async def wait_processed(self, timeout: Optional[float] = None) -> Dict[str, Any]:
        """Wait for all queued processing to complete.

        Args:
            timeout: Wait timeout in seconds

        Returns:
            Queue status
        """
        qm = get_queue_manager()
        try:
            status = await qm.wait_complete(timeout=timeout)
        except TimeoutError as exc:
            raise DeadlineExceededError("queue processing", timeout) from exc
        return {
            name: {
                "processed": s.processed,
                "requeue_count": getattr(s, "requeue_count", 0),
                "error_count": s.error_count,
                "errors": [{"message": e.message} for e in s.errors],
            }
            for name, s in status.items()
        }

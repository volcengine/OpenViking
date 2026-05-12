# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
Session Compressor V2 for OpenViking.

Uses the new Memory Templating System with ReAct orchestrator.
Maintains the same interface as compressor.py for backward compatibility.
"""

import asyncio
import json
from datetime import datetime
from typing import Any, Dict, List, Optional

from openviking.core.context import Context
from openviking.core.namespace import (
    agent_space_fragment,
    user_space_fragment,
    to_user_space,
    to_agent_space,
)
from openviking.message import Message
from openviking.server.identity import RequestContext
from openviking.session.memory import ExtractLoop, MemoryUpdater
from openviking.session.memory.memory_isolation_handler import MemoryIsolationHandler
from openviking.session.memory.utils.json_parser import JsonUtils
from openviking.session.memory.utils.messages import parse_memory_file_with_fields
from openviking.session.memory.utils.uri import render_template
from openviking.storage import VikingDBManager
from openviking.storage.viking_fs import VikingFS, get_viking_fs
from openviking.session.memory.dataclass import ResolvedOperations
from openviking.session.memory.memory_updater import MemoryUpdateResult
from openviking.telemetry import get_current_telemetry, tracer
from openviking_cli.session.user_id import UserIdentifier
from openviking_cli.utils import get_logger
from openviking_cli.utils.config import get_openviking_config

logger = get_logger(__name__)

MAX_SOURCE_TRAJECTORIES = 5  # keep only the most recent N trajectory URIs per experience



class SessionCompressorV2:
    """Session memory extractor with v2 templating system."""

    def __init__(
        self,
        vikingdb: VikingDBManager,
    ):
        """Initialize session compressor."""
        self.vikingdb = vikingdb
        pass

    def _get_or_create_react(
        self,
        ctx: Optional[RequestContext] = None,
        messages: Optional[List] = None,
        latest_archive_overview: str = "",
        isolation_handler: Optional[MemoryIsolationHandler] = None,
        transaction_handle=None,
    ) -> ExtractLoop:
        """Create new ExtractLoop instance with current ctx.

        Note: Always create new instance to avoid cross-session isolation issues.
        The ctx contains request-scoped state that must not be shared across requests.
        """
        config = get_openviking_config()
        vlm = config.vlm.get_vlm_instance()
        viking_fs = get_viking_fs()

        # Create context provider with messages (provider 负责加载 schema)
        from openviking.session.memory.session_extract_context_provider import (
            SessionExtractContextProvider,
        )

        context_provider = SessionExtractContextProvider(
            messages=messages,
            latest_archive_overview=latest_archive_overview,
            isolation_handler=isolation_handler,
            ctx=ctx,
            viking_fs=viking_fs,
            transaction_handle=transaction_handle,
        )

        return ExtractLoop(
            vlm=vlm,
            viking_fs=viking_fs,
            ctx=ctx,
            context_provider=context_provider,
            isolation_handler=isolation_handler,
        )

    def _get_or_create_updater(self, registry, transaction_handle=None) -> MemoryUpdater:
        """Create new MemoryUpdater instance for each request.

        Always create new instance to avoid cross-request state pollution.
        """
        return MemoryUpdater(
            registry=registry, vikingdb=self.vikingdb, transaction_handle=transaction_handle
        )

    @tracer()
    async def extract_long_term_memories(
        self,
        messages: List[Message],
        user: Optional["UserIdentifier"] = None,
        session_id: Optional[str] = None,
        ctx: Optional[RequestContext] = None,
        strict_extract_errors: bool = False,
        latest_archive_overview: str = "",
        archive_uri: Optional[str] = None,
    ) -> List[Context]:
        """Extract long-term memories from messages using v2 templating system.

        Note: Returns empty List[Context] because v2 directly writes to storage.
        The list length is used for stats in session.py.

        Args:
            messages: Messages to extract memories from.
            user: User identifier.
            session_id: Session ID.
            ctx: Request context.
            strict_extract_errors: If True, raise exceptions on extraction errors.
            latest_archive_overview: Overview of latest archive for context.
            archive_uri: Archive URI for writing memory_diff.json.
        """

        if not messages:
            return []

        if not ctx:
            logger.warning("No RequestContext provided, skipping memory extraction")
            return []

        tracer.info("Starting v2 memory extraction from conversation")
        tracer.info(f"messages={JsonUtils.dumps(messages)}")
        config = get_openviking_config()

        # Initialize default memory files (soul.md, identity.md) if not exist
        from openviking.session.memory.memory_type_registry import create_default_registry

        registry = create_default_registry()
        await registry.initialize_memory_files(ctx)

        # Initialize telemetry to 0 (matching v1 pattern)
        telemetry = get_current_telemetry()
        telemetry.set("memory.extract.candidates.total", 0)
        telemetry.set("memory.extract.candidates.standard", 0)
        telemetry.set("memory.extract.candidates.tool_skill", 0)
        telemetry.set("memory.extract.created", 0)
        telemetry.set("memory.extract.merged", 0)
        telemetry.set("memory.extract.deleted", 0)
        telemetry.set("memory.extract.skipped", 0)

        from openviking.storage.transaction import get_lock_manager, init_lock_manager
        from openviking.storage.viking_fs import get_viking_fs

        # 初始化锁管理器（仅在有 AGFS 时使用锁机制）
        viking_fs = get_viking_fs()
        lock_manager = None
        transaction_handle = None
        if viking_fs and hasattr(viking_fs, "agfs") and viking_fs.agfs:
            init_lock_manager(viking_fs.agfs)
            lock_manager = get_lock_manager()
            transaction_handle = lock_manager.create_handle()
        else:
            logger.warning("VikingFS or AGFS not available, running without lock mechanism")

        try:
            # Create extract context from messages
            from openviking.session.memory.memory_updater import ExtractContext

            extract_context = ExtractContext(messages)

            # Create MemoryIsolationHandler
            isolation_handler = MemoryIsolationHandler(ctx, extract_context)
            isolation_handler.prepare_messages()
            # 获取所有记忆 schema 目录并加锁（仅在有锁管理器时）
            orchestrator = self._get_or_create_react(
                ctx=ctx,
                messages=messages,
                latest_archive_overview=latest_archive_overview,
                isolation_handler=isolation_handler,
                transaction_handle=transaction_handle,
            )
            read_scope = isolation_handler.get_read_scope()
            if lock_manager:
                # 基于 provider 的 schemas 生成目录列表
                # 固定文件名 schema（如 soul.md、identity.md）只需 POINT 锁，
                # 避免因 SUBTREE 锁阻塞子目录（trajectories/experiences）的并发加锁。
                schemas = orchestrator.context_provider.get_memory_schemas(ctx)
                point_lock_dirs: list = []   # 固定文件名 schema → POINT 锁
                subtree_lock_dirs: list = [] # 变量文件名 schema → SUBTREE 锁
                for schema in schemas:
                    if not schema.directory:
                        continue
                    for user_id in read_scope.user_ids:
                        for agent_id in read_scope.agent_ids:
                            dir_path = render_template(
                                schema.directory,
                                {
                                    "user_space": to_user_space(
                                        ctx.namespace_policy, user_id, agent_id
                                    ),
                                    "agent_space": to_agent_space(
                                        ctx.namespace_policy, user_id, agent_id
                                    ),
                                },
                            )
                            dir_path = viking_fs._uri_to_path(dir_path, ctx)
                            if schema.filename_has_variables():
                                if dir_path not in subtree_lock_dirs:
                                    subtree_lock_dirs.append(dir_path)
                            else:
                                if dir_path not in point_lock_dirs:
                                    point_lock_dirs.append(dir_path)
                logger.debug(
                    f"Memory schema lock dirs: point={point_lock_dirs}, subtree={subtree_lock_dirs}"
                )

                retry_interval = config.memory.v2_lock_retry_interval_seconds
                max_retries = config.memory.v2_lock_max_retries
                retry_count = 0

                # 循环重试获取锁（机制确保不会死锁）
                while True:
                    lock_acquired = await lock_manager.acquire_mixed_batch(
                        transaction_handle,
                        point_paths=point_lock_dirs,
                        subtree_paths=subtree_lock_dirs,
                        timeout=None,
                    )
                    if lock_acquired:
                        break
                    retry_count += 1
                    if max_retries > 0 and retry_count >= max_retries:
                        raise TimeoutError(
                            "Failed to acquire memory locks after "
                            f"{retry_count} retries (max={max_retries})"
                        )

                    logger.warning(
                        "Failed to acquire memory locks, retrying "
                        f"(attempt={retry_count}, max={max_retries or 'unlimited'})..."
                    )
                    if retry_interval > 0:
                        await asyncio.sleep(retry_interval)

            orchestrator._transaction_handle = transaction_handle  # 传递给 ExtractLoop

            # Run ReAct orchestrator
            operations, tools_used = await orchestrator.run()

            if operations is None:
                tracer.info("No memory operations generated")
                return []

            updater = self._get_or_create_updater(registry, transaction_handle)

            # Apply operations with isolation_handler
            result = await updater.apply_operations(
                operations,
                ctx,
                extract_context=extract_context,
                isolation_handler=isolation_handler,
            )

            tracer.info(
                f"Applied memory operations: written={len(result.written_uris)}, "
                f"edited={len(result.edited_uris)}, deleted={len(result.deleted_uris)}, "
                f"errors={len(result.errors)}"
            )

            # Write memory_diff.json to archive directory
            if archive_uri and viking_fs:
                memory_diff = await self._build_memory_diff(
                    result=result,
                    operations=operations,
                    viking_fs=viking_fs,
                    ctx=ctx,
                    archive_uri=archive_uri,
                )
                await viking_fs.write_file(
                    uri=f"{archive_uri}/memory_diff.json",
                    content=json.dumps(memory_diff, ensure_ascii=False, indent=4),
                    ctx=ctx,
                )
                logger.info(f"Wrote memory_diff.json to {archive_uri}")

            # Report telemetry stats (matching v1 pattern)
            telemetry = get_current_telemetry()
            telemetry.set(
                "memory.extract.candidates.total",
                len(result.written_uris) + len(result.edited_uris),
            )
            telemetry.set("memory.extract.created", len(result.written_uris))
            telemetry.set("memory.extract.merged", len(result.edited_uris))
            telemetry.set("memory.extract.deleted", len(result.deleted_uris))
            telemetry.set("memory.extract.skipped", len(result.errors))

            # Build Context objects for stats in session.py
            contexts: List[Context] = []

            # Written memories
            for uri in result.written_uris:
                contexts.append(
                    Context(
                        uri=uri,
                        category="memory_write",
                        context_type="memory",
                    )
                )

            # Edited memories
            for uri in result.edited_uris:
                contexts.append(
                    Context(
                        uri=uri,
                        category="memory_edit",
                        context_type="memory",
                    )
                )

            # Deleted memories
            for uri in result.deleted_uris:
                contexts.append(
                    Context(
                        uri=uri,
                        category="memory_delete",
                        context_type="memory",
                    )
                )

            return contexts

        except Exception as e:
            logger.error(f"Failed to extract memories with v2: {e}", exc_info=True)
            if strict_extract_errors:
                raise
            return []
        finally:
            # 确保释放所有锁（仅在有锁管理器时）
            if lock_manager and transaction_handle:
                try:
                    await lock_manager.release(transaction_handle)
                except Exception as e:
                    logger.warning(f"Failed to release transaction lock: {e}")

    @tracer(ignore_result=True)
    async def extract_agent_memories(
        self,
        messages: List[Message],
        ctx: Optional[RequestContext] = None,
        strict_extract_errors: bool = False,
    ) -> List[Context]:
        """Two-phase agent-scope memory extraction (trajectory + experience).

        Phase 1: extract execution trajectories from the conversation and persist them.
        Phase 2: for each newly written trajectory, decide whether to update an existing
        experience, create a new one, or do nothing.

        Gated by `config.memory.agent_memory_enabled`. Returns [] when disabled.
        """
        config = get_openviking_config()
        if not getattr(config.memory, "agent_memory_enabled", False):
            return []
        if not messages or not ctx:
            return []

        from openviking.session.memory.agent_experience_context_provider import (
            AgentExperienceContextProvider,
        )
        from openviking.session.memory.agent_trajectory_context_provider import (
            AgentTrajectoryContextProvider,
        )

        contexts: List[Context] = []

        # Phase 1: trajectory extraction
        traj_provider = AgentTrajectoryContextProvider(messages=messages)
        traj_result = await self._run_extract_phase(
            provider=traj_provider,
            messages=messages,
            ctx=ctx,
            strict_extract_errors=strict_extract_errors,
            phase_label="trajectory",
        )
        if traj_result is None:
            return []

        written_trajectory_uris, _, traj_contexts, _ = traj_result
        contexts.extend(traj_contexts)

        # Deduplicate: LLM may output the same trajectory_name twice in one call,
        # producing identical URIs. Without this, experience extraction would run
        # once per duplicate and generate near-identical experiences.
        written_trajectory_uris = list(dict.fromkeys(written_trajectory_uris))

        if not written_trajectory_uris:
            tracer.info("No trajectories extracted; skipping experience phase")
            return contexts

        # Phase 2: for each new trajectory, consolidate into experiences.
        viking_fs = get_viking_fs()
        for traj_uri in written_trajectory_uris:
            try:
                from openviking.session.memory.utils.content import deserialize_content as _deser_content
                traj_content = _deser_content(await viking_fs.read_file(traj_uri, ctx=ctx) or "")
            except Exception as e:
                logger.warning(f"Failed to read new trajectory {traj_uri}: {e}")
                continue

            exp_provider = AgentExperienceContextProvider(
                messages=messages,
                trajectory_summary=traj_content,
                trajectory_uri=traj_uri,
            )
            exp_result = await self._run_extract_phase(
                provider=exp_provider,
                messages=messages,
                ctx=ctx,
                strict_extract_errors=strict_extract_errors,
                phase_label=f"experience({traj_uri})",
            )

            exp_dir = exp_provider._render_experience_dir(ctx)

            async def _single_existing_experience_uri() -> List[str]:
                if not exp_dir:
                    return []
                try:
                    entries = await viking_fs.ls(exp_dir, output="original", ctx=ctx)
                except Exception:
                    return []
                uris = []
                for e in entries or []:
                    uri = str(e.get("uri", "")) if isinstance(e, dict) else ""
                    name = str(e.get("name", "")) if isinstance(e, dict) else ""
                    if not uri.endswith(".md"):
                        continue
                    if name in {".overview.md", ".abstract.md"}:
                        continue
                    if uri.endswith("/.overview.md") or uri.endswith("/.abstract.md"):
                        continue
                    uris.append(uri)
                uris = list(dict.fromkeys(uris))
                return uris if len(uris) == 1 else []

            if exp_result is None:
                fallback_uris = await _single_existing_experience_uri()
                if fallback_uris:
                    tracer.info(
                        f"[source_traj] phase2 failed; fallback append to sole experience: {fallback_uris[0]}"
                    )
                    await self._append_trajectories_to_experiences(
                        fallback_uris, [traj_uri], ctx, viking_fs
                    )
                continue

            exp_written_uris, exp_edited_uris, exp_contexts, inheritance_map = exp_result
            contexts.extend(exp_contexts)

            all_exp_uris = exp_written_uris + exp_edited_uris
            if not all_exp_uris:
                candidate_uris = list(dict.fromkeys(getattr(exp_provider, "prefetched_uris", []) or []))
                candidate_exp_uris = [
                    uri
                    for uri in candidate_uris
                    if uri.endswith(".md")
                    and not uri.endswith("/.overview.md")
                    and not uri.endswith("/.abstract.md")
                    and "/memories/experiences/" in uri
                ]
                if len(candidate_exp_uris) == 1:
                    all_exp_uris = candidate_exp_uris
                    tracer.info(
                        f"[source_traj] fallback append to sole candidate experience: {candidate_exp_uris[0]}"
                    )
                else:
                    all_exp_uris = await _single_existing_experience_uri()
                    if all_exp_uris:
                        tracer.info(
                            f"[source_traj] fallback append by directory scan: {all_exp_uris[0]}"
                        )

            for exp_uri in all_exp_uris:
                # Only the superseding experience inherits source_trajectories from the old file.
                uri_inherited = inheritance_map.get(exp_uri, [])
                traj_uris = list(dict.fromkeys([traj_uri] + uri_inherited))
                await self._append_trajectories_to_experiences([exp_uri], traj_uris, ctx, viking_fs)

        return contexts

    async def _run_extract_phase(
        self,
        provider,
        messages: List[Message],
        ctx: RequestContext,
        strict_extract_errors: bool,
        phase_label: str,
    ):
        """Run one ExtractLoop phase with its own lock scope, then apply operations.

        Returns (written_uris, edited_uris, contexts, inheritance_map) on success,
        where inheritance_map maps new experience URI → inherited source_trajectory URIs
        (only populated for experiences that supersede an existing one).
        Returns None on failure (unless strict_extract_errors is True, in which case
        the exception is re-raised).
        """
        from openviking.session.memory.memory_updater import ExtractContext
        from openviking.storage.transaction import get_lock_manager, init_lock_manager

        config = get_openviking_config()
        vlm = config.vlm.get_vlm_instance()
        viking_fs = get_viking_fs()

        # Build isolation_handler BEFORE creating the orchestrator so that
        # ExtractLoop.resolve_operations() can call fill_role_ids() correctly.
        extract_context = ExtractContext(messages)
        isolation_handler = MemoryIsolationHandler(ctx, extract_context)
        isolation_handler.prepare_messages()

        # Inject context into provider (mirrors extract_long_term_memories pattern)
        provider._isolation_handler = isolation_handler
        provider._ctx = ctx
        provider._viking_fs = viking_fs

        orchestrator = ExtractLoop(
            vlm=vlm,
            viking_fs=viking_fs,
            ctx=ctx,
            context_provider=provider,
            isolation_handler=isolation_handler,
        )

        lock_manager = None
        transaction_handle = None
        if viking_fs and hasattr(viking_fs, "agfs") and viking_fs.agfs:
            init_lock_manager(viking_fs.agfs)
            lock_manager = get_lock_manager()
            transaction_handle = lock_manager.create_handle()

        try:
            if lock_manager:
                schemas = provider.get_memory_schemas(ctx)
                memory_schema_dirs = []
                for schema in schemas:
                    if not schema.directory:
                        continue

                    if ctx and ctx.user:
                        user_space = to_user_space(
                            ctx.namespace_policy, ctx.user.user_id, ctx.user.agent_id
                        )
                        agent_space = to_agent_space(
                            ctx.namespace_policy, ctx.user.user_id, ctx.user.agent_id
                        )
                    else:
                        user_space = "default"
                        agent_space = "default"
                    import jinja2

                    env = jinja2.Environment(autoescape=False)
                    template = env.from_string(schema.directory)
                    dir_path = template.render(user_space=user_space, agent_space=agent_space)
                    dir_path = viking_fs._uri_to_path(dir_path, ctx)
                    if dir_path not in memory_schema_dirs:
                        memory_schema_dirs.append(dir_path)

                retry_interval = config.memory.v2_lock_retry_interval_seconds
                max_retries = config.memory.v2_lock_max_retries
                retry_count = 0
                while True:
                    lock_acquired = await lock_manager.acquire_subtree_batch(
                        transaction_handle,
                        memory_schema_dirs,
                        timeout=None,
                    )
                    if lock_acquired:
                        break
                    retry_count += 1
                    if max_retries > 0 and retry_count >= max_retries:
                        raise TimeoutError(
                            f"[{phase_label}] Failed to acquire memory locks after "
                            f"{retry_count} retries (max={max_retries})"
                        )
                    if retry_interval > 0:
                        await asyncio.sleep(retry_interval)

            provider._transaction_handle = transaction_handle
            orchestrator._transaction_handle = transaction_handle
            operations, _ = await orchestrator.run()

            if operations is None:
                tracer.info(f"[{phase_label}] No memory operations generated")
                return [], [], [], {}

            # Log raw LLM operations before applying.
            _op_items = [
                f"{op.memory_type}(uris={op.uris!r})"
                for op in getattr(operations, "upsert_operations", [])
            ]
            _delete_uris_raw = [
                dc.uri for dc in getattr(operations, "delete_file_contents", [])
            ]
            tracer.info(
                f"[{phase_label}] LLM operations: ops={_op_items}, delete_uris={_delete_uris_raw}"
            )

            # Resolve supersedes fields (name-based Replace): find old experience URI,
            # queue for deletion, and return per-URI inheritance map so only the
            # superseding experience inherits the old source_trajectories.
            inheritance_map = await self._resolve_supersedes(operations, ctx, viking_fs, provider)



            registry = provider._get_registry()
            updater = self._get_or_create_updater(registry, transaction_handle)
            result = await updater.apply_operations(
                operations, ctx, extract_context=extract_context, isolation_handler=isolation_handler
            )

            tracer.info(
                f"[{phase_label}] Applied: written={len(result.written_uris)}, "
                f"edited={len(result.edited_uris)}, deleted={len(result.deleted_uris)}, "
                f"errors={len(result.errors)}"
            )

            contexts: List[Context] = []
            for uri in result.written_uris:
                contexts.append(
                    Context(uri=uri, category="memory_write", context_type="memory")
                )
            for uri in result.edited_uris:
                contexts.append(
                    Context(uri=uri, category="memory_edit", context_type="memory")
                )
            for uri in result.deleted_uris:
                contexts.append(
                    Context(uri=uri, category="memory_delete", context_type="memory")
                )

            return list(result.written_uris), list(result.edited_uris), contexts, inheritance_map
        except Exception as e:
            logger.error(f"[{phase_label}] Failed to extract: {e}", exc_info=True)
            if strict_extract_errors:
                raise
            return None
        finally:
            if lock_manager and transaction_handle:
                try:
                    await lock_manager.release(transaction_handle)
                except Exception as e:
                    logger.warning(f"[{phase_label}] Failed to release transaction lock: {e}")

    async def _resolve_supersedes(
        self,
        operations: ResolvedOperations,
        ctx,
        viking_fs,
        provider,
    ) -> Dict[str, List[str]]:
        """Resolve supersedes fields in experience upsert operations.

        For each experience with a non-empty `supersedes` field, find the old
        experience file by name, append it to delete_file_contents so
        apply_operations handles deletion uniformly, then pop `supersedes` from
        memory_fields so it is not written to disk.

        Returns a mapping from new experience URI → inherited source_trajectory URIs,
        so the caller can apply inherited trajectories only to the superseding experience,
        not to every experience written in the same batch.
        """
        from openviking.session.memory.dataclass import MemoryFileContent
        from openviking.session.memory.utils.messages import parse_memory_file_with_fields

        inheritance_map: Dict[str, List[str]] = {}

        exp_dir: str = ""
        if hasattr(provider, "_render_experience_dir"):
            exp_dir = provider._render_experience_dir(ctx) or ""

        for op in operations.upsert_operations:
            if op.memory_type != "experiences":
                continue
            supersedes_name = (op.memory_fields.pop("supersedes", None) or "").strip()
            if not supersedes_name:
                continue
            if not exp_dir:
                logger.warning(f"[supersedes] cannot resolve '{supersedes_name}': no experience dir")
                continue

            old_uri = f"{exp_dir.rstrip('/')}/{supersedes_name}.md"

            # Derive the new URI from experience_name (filename_template: "{{ experience_name }}.md")
            new_name = (op.memory_fields.get("experience_name") or "").strip()
            new_uri = f"{exp_dir.rstrip('/')}/{new_name}.md" if new_name else None

            # Guard: never delete the file we are about to write (same-name edge case)
            if old_uri == new_uri or old_uri in (op.uris or []):
                tracer.info(f"[supersedes] skipping self-reference: {old_uri}")
                continue

            try:
                raw = await viking_fs.read_file(old_uri, ctx=ctx) or ""
                parsed = parse_memory_file_with_fields(raw)
                operations.delete_file_contents.append(
                    MemoryFileContent(
                        uri=old_uri,
                        plain_content=parsed.get("content", ""),
                        memory_fields=parsed,
                    )
                )
                tracer.info(f"[supersedes] '{supersedes_name}' → queued for delete: {old_uri}")

                # Map inherited source_trajectories to the new (superseding) URI only.
                if new_uri:
                    existing = parsed.get("source_trajectories", [])
                    if isinstance(existing, list):
                        inherited = list(existing)
                    elif isinstance(existing, str) and existing.strip():
                        inherited = [line.strip() for line in existing.splitlines() if line.strip()]
                    else:
                        inherited = []
                    if inherited:
                        inheritance_map[new_uri] = inherited
            except Exception as e:
                logger.warning(f"[supersedes] failed to read '{old_uri}': {e}")

        return inheritance_map

    async def _append_trajectories_to_experiences(
        self,
        exp_uris: List[str],
        traj_uris: List[str],
        ctx,
        viking_fs,
    ) -> None:
        """Append traj_uris to the source_trajectories list of each experience file.

        This is the system-side management of source_trajectories — the LLM never
        outputs this field; the pipeline appends the batch after a write or edit.
        """
        from openviking.session.memory.utils.content import deserialize_full, serialize_with_metadata

        normalized_traj_uris = [uri for uri in traj_uris if uri]
        if not normalized_traj_uris:
            return

        for exp_uri in exp_uris:
            try:
                raw = await viking_fs.read_file(exp_uri, ctx=ctx) or ""
                file_content = deserialize_full(raw)
                plain_content = file_content.plain_content
                metadata = file_content.memory_fields or {}

                existing = metadata.get("source_trajectories", [])
                if isinstance(existing, list):
                    uris = list(existing)
                elif isinstance(existing, str) and existing.strip():
                    uris = [line.strip() for line in existing.splitlines() if line.strip()]
                else:
                    uris = []

                changed = False
                for traj_uri in normalized_traj_uris:
                    if traj_uri not in uris:
                        uris.append(traj_uri)
                        changed = True

                # Trim to the most recent N entries so the list doesn't grow unboundedly.
                if len(uris) > MAX_SOURCE_TRAJECTORIES:
                    uris = uris[-MAX_SOURCE_TRAJECTORIES:]
                    changed = True

                if changed:
                    metadata["source_trajectories"] = uris
                    metadata["content"] = plain_content
                    new_raw = serialize_with_metadata(metadata)
                    await viking_fs.write_file(exp_uri, new_raw, ctx=ctx)
                    tracer.info(
                        f"[source_traj] appended {len(normalized_traj_uris)} trajectories -> {exp_uri}"
                    )
                else:
                    tracer.info(f"[source_traj] already present, skip: {exp_uri}")
            except Exception as e:
                logger.warning(f"Failed to append source trajectories to {exp_uri}: {e}")

    async def _build_memory_diff(
        self,
        result: MemoryUpdateResult,
        operations: ResolvedOperations,
        viking_fs: VikingFS,
        ctx: RequestContext,
        archive_uri: str = "",
    ) -> Dict[str, Any]:
        """Build memory_diff.json structure from operations and result.

        Args:
            result: Memory update result containing written/edited/deleted URIs.
            operations: Resolved operations containing original content.
            viking_fs: VikingFS instance for reading file contents.
            ctx: Request context.
            archive_uri: The archive URI for this extraction.

        Returns:
            Dictionary containing memory_diff structure.
        """
        adds = []
        updates = []
        deletes = []

        # Build lookup maps for efficient access
        # Handle multi-URI operations correctly
        upsert_by_uri = {}
        for op in operations.upsert_operations:
            for uri in op.uris:
                upsert_by_uri[uri] = op
        delete_by_uri = {dc.uri: dc for dc in operations.delete_file_contents}

        # Process written_uris - distinguish between add and update
        # Use old_memory_file_content from the operation to determine if this is
        # an update (old content existed) or a new add.
        for uri in result.written_uris:
            op = upsert_by_uri.get(uri)
            memory_type = op.memory_type if op else self._get_memory_type_from_uri(uri)
            old_file = op.old_memory_file_content if op else None

            if old_file:
                # Old content existed, this is an update
                raw_before = old_file.plain_content
                parsed = parse_memory_file_with_fields(raw_before)
                updates.append(
                    {
                        "uri": uri,
                        "memory_type": memory_type,
                        "before": parsed.get("content", raw_before),
                        "after": "",  # Will be filled after
                    }
                )
            else:
                # No old content, this is a new add
                adds.append(
                    {
                        "uri": uri,
                        "memory_type": memory_type,
                        "after": "",  # Will be filled after
                    }
                )

        # Process edited_uris - these are updates
        for uri in result.edited_uris:
            op = upsert_by_uri.get(uri)
            memory_type = op.memory_type if op else self._get_memory_type_from_uri(uri)
            old_content = None
            if op and op.old_memory_file_content:
                old_content = op.old_memory_file_content.plain_content
            raw_before = old_content or ""
            parsed = parse_memory_file_with_fields(raw_before)
            updates.append(
                {
                    "uri": uri,
                    "memory_type": memory_type,
                    "before": parsed.get("content", raw_before) if raw_before else "",
                    "after": "",  # Will be filled after
                }
            )

        # Process deleted_uris - from delete_file_contents
        for uri in result.deleted_uris:
            deleted_content = None
            dc = delete_by_uri.get(uri)
            memory_type = dc.memory_fields.get("memory_type", "unknown") if dc else "unknown"
            if dc:
                deleted_content = dc.plain_content
            raw_deleted = deleted_content or ""
            parsed = parse_memory_file_with_fields(raw_deleted)
            deletes.append(
                {
                    "uri": uri,
                    "memory_type": memory_type,
                    "deleted_content": parsed.get("content", raw_deleted),
                }
            )

        # Read new content for adds and updates
        for item in adds + updates:
            try:
                content = await viking_fs.read_file(uri=item["uri"], ctx=ctx)
                # Strip MEMORY_FIELDS comment from content
                parsed = parse_memory_file_with_fields(content)
                item["after"] = parsed.get("content", content)
            except Exception:
                pass

        return {
            "archive_uri": archive_uri,
            "extracted_at": datetime.utcnow().isoformat() + "Z",
            "operations": {
                "adds": adds,
                "updates": updates,
                "deletes": deletes,
            },
            "summary": {
                "total_adds": len(adds),
                "total_updates": len(updates),
                "total_deletes": len(deletes),
            },
        }

    def _get_memory_type_from_uri(self, uri: str) -> str:
        """Extract memory type from URI.

        Examples:
            memory/user/xxx/identity.md -> identity
            memory/user/xxx/context/project.md -> context

        Args:
            uri: Memory file URI.

        Returns:
            Memory type (filename without extension) or 'unknown'.
        """
        parts = uri.split("/")
        for part in parts:
            if part.endswith(".md"):
                return part.replace(".md", "")
        return "unknown"

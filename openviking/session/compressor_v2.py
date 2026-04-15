# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
Session Compressor V2 for OpenViking.

Uses the new Memory Templating System with ReAct orchestrator.
Maintains the same interface as compressor.py for backward compatibility.
"""

import asyncio
from typing import List, Optional

from openviking.core.context import Context
from openviking.message import Message
from openviking.server.identity import RequestContext
from openviking.session.memory import ExtractLoop, MemoryUpdater
from openviking.session.memory.utils.json_parser import JsonUtils
from openviking.storage import VikingDBManager
from openviking.storage.viking_fs import get_viking_fs
from openviking.telemetry import get_current_telemetry
from openviking_cli.session.user_id import UserIdentifier
from openviking_cli.utils import get_logger
from openviking.telemetry import tracer
from openviking_cli.utils.config import get_openviking_config

logger = get_logger(__name__)


def _stamp_trajectory_names(operations) -> None:
    """Append a timestamp suffix to trajectory_name in all trajectory operations.

    This ensures every trajectory gets a unique filename and that the embedded
    MEMORY_FIELDS comment also records the full timestamped name.
    Only applies to add_only trajectory writes (no existing uri).
    """
    from datetime import datetime

    traj_field = getattr(operations, "trajectory", None)
    if traj_field is None:
        return
    items = traj_field if isinstance(traj_field, list) else [traj_field]
    now_suffix = datetime.now().strftime("%Y%m%d%H%M%S")
    for item in items:
        if isinstance(item, dict):
            name = item.get("trajectory_name")
            if name and not item.get("uri"):
                item["trajectory_name"] = f"{name}_{now_suffix}"
        else:
            name = getattr(item, "trajectory_name", None)
            uri = getattr(item, "uri", None)
            if name and not uri:
                item.trajectory_name = f"{name}_{now_suffix}"


class SessionCompressorV2:
    """Session memory extractor with v2 templating system."""

    def __init__(
        self,
        vikingdb: VikingDBManager,
    ):
        """Initialize session compressor."""
        self.vikingdb = vikingdb
        # registry 现在由 provider 负责加载，这里不再初始化
        # MemoryUpdater 会在 apply_operations 时从 provider 获取 registry
        pass

    def _get_or_create_react(
        self,
        ctx: Optional[RequestContext] = None,
        messages: Optional[List] = None,
        latest_archive_overview: str = "",
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
        )

        return ExtractLoop(
            vlm=vlm,
            viking_fs=viking_fs,
            ctx=ctx,
            context_provider=context_provider,
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
    ) -> List[Context]:
        """Extract long-term memories from messages using v2 templating system.

        Note: Returns empty List[Context] because v2 directly writes to storage.
        The list length is used for stats in session.py.
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
            # 获取所有记忆 schema 目录并加锁（仅在有锁管理器时）
            orchestrator = self._get_or_create_react(
                ctx=ctx,
                messages=messages,
                latest_archive_overview=latest_archive_overview,
            )
            if lock_manager:
                # 基于 provider 的 schemas 生成目录列表
                schemas = orchestrator.context_provider.get_memory_schemas(ctx)
                memory_schema_dirs = []
                for schema in schemas:
                    if not schema.directory:
                        continue
                    user_space = ctx.user.user_space_name() if ctx and ctx.user else "default"
                    agent_space = ctx.user.agent_space_name() if ctx and ctx.user else "default"
                    # 使用 Jinja2 渲染 directory
                    import jinja2

                    env = jinja2.Environment(autoescape=False)
                    template = env.from_string(schema.directory)
                    dir_path = template.render(user_space=user_space, agent_space=agent_space)
                    dir_path = viking_fs._uri_to_path(dir_path, ctx)
                    if dir_path not in memory_schema_dirs:
                        memory_schema_dirs.append(dir_path)
                logger.debug(f"Memory schema directories to lock: {memory_schema_dirs}")

                retry_interval = config.memory.v2_lock_retry_interval_seconds
                max_retries = config.memory.v2_lock_max_retries
                retry_count = 0

                # 循环重试获取锁（机制确保不会死锁）
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

            # Convert to legacy format for logging and apply_operations
            if hasattr(operations, "to_legacy_operations"):
                legacy = operations.to_legacy_operations()
                write_uris = legacy.get("write_uris", [])
                edit_uris = legacy.get("edit_uris", [])
            else:
                # Fallback for old format
                write_uris = operations.write_uris
                edit_uris = operations.edit_uris

            # 从 orchestrator 获取 registry（从 provider 获取）
            registry = orchestrator.context_provider._get_registry()
            updater = self._get_or_create_updater(registry, transaction_handle)

            tracer.info(
                f"Generated memory operations: write={len(write_uris)}, "
                f"edit={len(edit_uris)} "
                f"delete={len(operations.delete_uris)}"
            )

            # Create extract context from messages
            from openviking.session.memory.memory_updater import ExtractContext

            extract_context = ExtractContext(messages)

            # Apply operations
            result = await updater.apply_operations(
                operations, ctx, registry=registry, extract_context=extract_context
            )

            tracer.info(
                f"Applied memory operations: written={len(result.written_uris)}, "
                f"edited={len(result.edited_uris)}, deleted={len(result.deleted_uris)}, "
                f"errors={len(result.errors)}"
            )

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

    @tracer("agent.memory.extract", ignore_result=True)
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
            if exp_result is None:
                continue

            exp_written_uris, exp_edited_uris, exp_contexts, inherited_traj_uris = exp_result
            contexts.extend(exp_contexts)

            all_exp_uris = exp_written_uris + exp_edited_uris
            if all_exp_uris:
                # Include inherited source_trajectories from any deleted (superseded) experiences
                traj_uris_to_append = list(dict.fromkeys([traj_uri] + inherited_traj_uris))
                await self._append_trajectories_to_experiences(
                    all_exp_uris, traj_uris_to_append, ctx, viking_fs
                )

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

        Returns (written_uris: List[str], contexts: List[Context]) on success,
        or None on failure (unless strict_extract_errors is True, in which case
        the exception is re-raised).
        """
        from openviking.session.memory.memory_updater import ExtractContext
        from openviking.storage.transaction import get_lock_manager, init_lock_manager

        config = get_openviking_config()
        vlm = config.vlm.get_vlm_instance()
        viking_fs = get_viking_fs()

        orchestrator = ExtractLoop(
            vlm=vlm,
            viking_fs=viking_fs,
            ctx=ctx,
            context_provider=provider,
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
                    user_space = ctx.user.user_space_name() if ctx and ctx.user else "default"
                    agent_space = ctx.user.agent_space_name() if ctx and ctx.user else "default"
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

            orchestrator._transaction_handle = transaction_handle
            operations, _ = await orchestrator.run()

            if operations is None:
                tracer.info(f"[{phase_label}] No memory operations generated")
                return [], [], [], []

            # Log raw LLM operations before applying, to diagnose URI mismatches.
            _delete_uris_raw = getattr(operations, "delete_uris", []) or []
            _memory_type_fields = getattr(operations, "_memory_type_fields", []) or []
            _op_items = []
            for _ft in _memory_type_fields:
                _v = getattr(operations, _ft, None)
                if _v is None:
                    continue
                for _item in (_v if isinstance(_v, list) else [_v]):
                    _uri_field = _item.get("uri") if isinstance(_item, dict) else getattr(_item, "uri", None)
                    _op_items.append(f"{_ft}(uri={_uri_field!r})")
            tracer.info(
                f"[{phase_label}] LLM operations: ops={_op_items}, delete_uris={_delete_uris_raw}"
            )

            # Collect source_trajectories from files about to be deleted, before apply_operations
            # removes them. This is experience-specific: allows merge operations to inherit
            # historical source_trajectories into the replacement experience.
            inherited_traj_uris: List[str] = []
            delete_uris = getattr(operations, "delete_uris", []) or []
            if delete_uris:
                from openviking.session.memory.utils.content import deserialize_full as _deser_full
                for del_uri in delete_uris:
                    try:
                        raw = await viking_fs.read_file(del_uri, ctx=ctx) or ""
                        _, meta = _deser_full(raw)
                        existing = (meta or {}).get("source_trajectories", [])
                        if isinstance(existing, list):
                            inherited_traj_uris.extend(existing)
                        elif isinstance(existing, str) and existing.strip():
                            inherited_traj_uris.extend(
                                line.strip() for line in existing.splitlines() if line.strip()
                            )
                    except Exception:
                        pass

            # Append timestamp suffix to trajectory_name so each trajectory gets a
            # unique filename and MEMORY_FIELDS also records the timestamped name.
            _stamp_trajectory_names(operations)

            registry = provider._get_registry()
            updater = self._get_or_create_updater(registry, transaction_handle)
            extract_context = ExtractContext(messages)
            result = await updater.apply_operations(
                operations, ctx, registry=registry, extract_context=extract_context
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

            return list(result.written_uris), list(result.edited_uris), contexts, inherited_traj_uris
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
                plain_content, metadata = deserialize_full(raw)
                metadata = metadata or {}

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

                if changed:
                    metadata["source_trajectories"] = uris
                    metadata["content"] = plain_content
                    new_raw = serialize_with_metadata(metadata)
                    try:
                        await viking_fs.rm(exp_uri, ctx=ctx)
                    except Exception:
                        pass
                    await viking_fs.write_file(exp_uri, new_raw, ctx=ctx)
                    tracer.info(
                        f"[source_traj] appended {len(normalized_traj_uris)} trajectories -> {exp_uri}"
                    )
                else:
                    tracer.info(f"[source_traj] already present, skip: {exp_uri}")
            except Exception as e:
                logger.warning(f"Failed to append source trajectories to {exp_uri}: {e}")

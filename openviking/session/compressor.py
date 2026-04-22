# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
Session Compressor for OpenViking.

Handles extraction of long-term memories from session conversations.
Uses MemoryExtractor for 8-category extraction and MemoryDeduplicator for LLM-based dedup.
"""

from dataclasses import dataclass
from typing import Dict, List, Optional

from openviking.core.context import Context, Vectorize
from openviking.message import Message
from openviking.models.embedder.base import embed_compat
from openviking.server.identity import RequestContext
from openviking.storage import VikingDBManager
from openviking.storage.viking_fs import get_viking_fs
from openviking.telemetry import get_current_telemetry
from openviking.telemetry.request_wait_tracker import get_request_wait_tracker
from openviking_cli.session.user_id import UserIdentifier
from openviking_cli.utils import get_logger

from .memory_deduplicator import DedupDecision, MemoryActionDecision, MemoryDeduplicator
from .memory_extractor import (
    CandidateMemory,
    MemoryCategory,
    MemoryExtractor,
    ToolSkillCandidateMemory,
)

logger = get_logger(__name__)

# Categories that always merge (skip dedup)
ALWAYS_MERGE_CATEGORIES = {MemoryCategory.PROFILE}

# Categories that support MERGE decision
MERGE_SUPPORTED_CATEGORIES = {
    MemoryCategory.PREFERENCES,
    MemoryCategory.ENTITIES,
    MemoryCategory.PATTERNS,
}

# Tool/Skill Memory categories
TOOL_SKILL_CATEGORIES = {
    MemoryCategory.TOOLS,
    MemoryCategory.SKILLS,
}


@dataclass
class ExtractionStats:
    """Statistics for memory extraction."""

    created: int = 0
    merged: int = 0
    deleted: int = 0
    skipped: int = 0


class SessionCompressor:
    """Session memory extractor with 8-category memory extraction."""

    def __init__(
        self,
        vikingdb: VikingDBManager,
    ):
        """Initialize session compressor."""
        self.vikingdb = vikingdb
        self.extractor = MemoryExtractor()
        self.deduplicator = MemoryDeduplicator(vikingdb=vikingdb)
        self._pending_semantic_changes: Dict[str, Dict[str, set]] = {}

    def _record_semantic_change(
        self, file_uri: str, change_type: str, parent_uri: Optional[str] = None
    ) -> None:
        """Record a file change for batch semantic processing.

        Args:
            file_uri: The URI of the file that changed
            change_type: One of "added", "modified", "deleted"
            parent_uri: Optional parent directory URI. If not provided, will be derived from file_uri
        """
        if change_type not in ("added", "modified", "deleted"):
            logger.warning(f"Invalid change_type: {change_type}, skipping")
            return

        if not parent_uri:
            parent_uri = "/".join(file_uri.rsplit("/", 1)[:-1])

        if not parent_uri:
            logger.warning(f"Could not determine parent URI for {file_uri}, skipping")
            return

        if parent_uri not in self._pending_semantic_changes:
            self._pending_semantic_changes[parent_uri] = {
                "added": set(),
                "modified": set(),
                "deleted": set(),
            }

        self._pending_semantic_changes[parent_uri][change_type].add(file_uri)
        logger.debug(f"Recorded semantic change: {change_type} {file_uri} in {parent_uri}")

    async def _flush_semantic_operations(self, ctx: RequestContext) -> None:
        """Flush all pending semantic operations.

        This method should be called after all memory changes are complete.
        It will deduplicate parent URIs and enqueue semantic operations with change info.
        """
        if not self._pending_semantic_changes:
            return

        try:
            from openviking.storage.queuefs import get_queue_manager
            from openviking.storage.queuefs.semantic_msg import SemanticMsg

            queue_manager = get_queue_manager()
            semantic_queue = queue_manager.get_queue(queue_manager.SEMANTIC, allow_create=True)
            telemetry = get_current_telemetry()
            request_wait_tracker = get_request_wait_tracker()

            for parent_uri, changes in self._pending_semantic_changes.items():
                changes_dict = {
                    "added": list(changes["added"]),
                    "modified": list(changes["modified"]),
                    "deleted": list(changes["deleted"]),
                }

                msg = SemanticMsg(
                    uri=parent_uri,
                    context_type="memory",
                    account_id=ctx.account_id,
                    user_id=ctx.user.user_id,
                    agent_id=ctx.user.agent_id,
                    role=ctx.role.value,
                    changes=changes_dict,
                    telemetry_id=telemetry.telemetry_id,
                )
                await semantic_queue.enqueue(msg)
                if msg.telemetry_id:
                    request_wait_tracker.register_semantic_root(msg.telemetry_id, msg.id)
                logger.info(
                    f"Enqueued semantic generation for {parent_uri} with changes: "
                    f"added={len(changes['added'])}, modified={len(changes['modified'])}, "
                    f"deleted={len(changes['deleted'])}"
                )

        except Exception as e:
            logger.error(f"Failed to flush semantic operations: {e}", exc_info=True)
        finally:
            self._pending_semantic_changes.clear()

    async def _index_memory(
        self, memory: Context, ctx: RequestContext, change_type: str = "added"
    ) -> bool:
        """Add memory to vectorization queue and record semantic change.

        For long memories, splits content into chunks and enqueues each chunk
        as a separate vector record for better retrieval precision.

        Args:
            memory: The memory context to index
            ctx: Request context
            change_type: One of "added" or "modified"
        """
        from openviking.storage.queuefs.embedding_msg_converter import EmbeddingMsgConverter
        from openviking_cli.utils.config import get_openviking_config

        semantic = get_openviking_config().semantic
        request_wait_tracker = get_request_wait_tracker()
        vectorize_text = memory.get_vectorization_text()

        if vectorize_text and len(vectorize_text) > semantic.memory_chunk_chars:
            # Chunk long memory into multiple vector records
            chunks = self._chunk_text(
                vectorize_text,
                semantic.memory_chunk_chars,
                semantic.memory_chunk_overlap,
            )
            logger.info(
                f"Chunking memory {memory.uri} into {len(chunks)} chunks "
                f"({len(vectorize_text)} chars)"
            )
            import copy

            for i, chunk in enumerate(chunks):
                chunk_memory = copy.deepcopy(memory)
                chunk_memory.uri = f"{memory.uri}#chunk_{i:04d}"
                chunk_memory.parent_uri = memory.uri
                chunk_memory.set_vectorize(Vectorize(text=chunk))
                chunk_msg = EmbeddingMsgConverter.from_context(chunk_memory)
                if chunk_msg:
                    enqueued = await self.vikingdb.enqueue_embedding_msg(chunk_msg)
                    if enqueued and chunk_msg.telemetry_id:
                        request_wait_tracker.register_embedding_root(
                            chunk_msg.telemetry_id, chunk_msg.id
                        )

        # Always enqueue the base record (uses abstract as vector text)
        embedding_msg = EmbeddingMsgConverter.from_context(memory)
        enqueued = await self.vikingdb.enqueue_embedding_msg(embedding_msg)
        if enqueued and embedding_msg.telemetry_id:
            request_wait_tracker.register_embedding_root(
                embedding_msg.telemetry_id, embedding_msg.id
            )
        logger.info(f"Enqueued memory for vectorization: {memory.uri}")

        self._record_semantic_change(memory.uri, change_type, parent_uri=memory.parent_uri)
        return True

    @staticmethod
    def _chunk_text(text: str, chunk_size: int, overlap: int) -> list:
        """Split text into overlapping chunks, preferring paragraph boundaries."""
        if len(text) <= chunk_size:
            return [text]

        chunks = []
        start = 0
        while start < len(text):
            end = start + chunk_size

            # Try to break at paragraph boundary
            if end < len(text):
                boundary = text.rfind("\n\n", start, end)
                if boundary > start + chunk_size // 2:
                    end = boundary + 2  # Include the double newline

            chunks.append(text[start:end].strip())
            start = end - overlap
            if start >= len(text):
                break

        return [c for c in chunks if c]

    async def _merge_into_existing(
        self,
        candidate: CandidateMemory,
        target_memory: Context,
        viking_fs,
        ctx: RequestContext,
    ) -> bool:
        """Merge candidate content into an existing memory file."""
        try:
            existing_content = await viking_fs.read_file(target_memory.uri, ctx=ctx)
            payload = await self.extractor._merge_memory_bundle(
                existing_abstract=target_memory.abstract,
                existing_overview=(target_memory.meta or {}).get("overview") or "",
                existing_content=existing_content,
                new_abstract=candidate.abstract,
                new_overview=candidate.overview,
                new_content=candidate.content,
                category=candidate.category.value,
                output_language=candidate.language,
            )
            if not payload:
                return False

            await viking_fs.write_file(target_memory.uri, payload.content, ctx=ctx)
            target_memory.abstract = payload.abstract
            target_memory.meta = {**(target_memory.meta or {}), "overview": payload.overview}
            logger.info(
                "Merged memory %s with abstract %s", target_memory.uri, target_memory.abstract
            )
            target_memory.set_vectorize(Vectorize(text=payload.content))
            await self._index_memory(target_memory, ctx, change_type="modified")
            return True
        except FileNotFoundError:
            logger.warning(
                "Target memory %s no longer exists — removing orphaned reference", target_memory.uri
            )
            # Clean up vector record for the missing file so it's not retried
            try:
                await self.vikingdb.delete_uris(ctx, [target_memory.uri])
            except Exception:
                pass
            return False
        except Exception as e:
            logger.error(f"Failed to merge memory {target_memory.uri}: {e}")
            return False

    async def _delete_existing_memory(
        self, memory: Context, viking_fs, ctx: RequestContext
    ) -> bool:
        """Hard delete an existing memory file and clean up its vector record."""
        try:
            await viking_fs.rm(memory.uri, recursive=False, ctx=ctx)
        except Exception as e:
            logger.error(f"Failed to delete memory file {memory.uri}: {e}")
            return False

        try:
            # rm() already syncs vector deletion in most cases; keep this as a safe fallback.
            await self.vikingdb.delete_uris(ctx, [memory.uri])
        except Exception as e:
            logger.warning(f"Failed to remove vector record for {memory.uri}: {e}")

        self._record_semantic_change(memory.uri, "deleted", parent_uri=memory.parent_uri)
        return True

    async def extract_long_term_memories(
        self,
        messages: List[Message],
        user: Optional["UserIdentifier"] = None,
        session_id: Optional[str] = None,
        ctx: Optional[RequestContext] = None,
        strict_extract_errors: bool = False,
        latest_archive_overview: str = "",
    ) -> List[Context]:
        """Extract long-term memories from messages."""
        if not messages:
            return []

        context = {
            "messages": messages,
            "summary": latest_archive_overview or "",
        }
        if not ctx:
            return []

        self._pending_semantic_changes.clear()
        telemetry = get_current_telemetry()
        telemetry.set("memory.extract.candidates.total", 0)
        telemetry.set("memory.extract.candidates.standard", 0)
        telemetry.set("memory.extract.candidates.tool_skill", 0)
        telemetry.set("memory.extract.created", 0)
        telemetry.set("memory.extract.merged", 0)
        telemetry.set("memory.extract.deleted", 0)
        telemetry.set("memory.extract.skipped", 0)

        with telemetry.measure("memory.extract.total"):
            try:
                if strict_extract_errors:
                    # Intentionally let extraction errors bubble up so caller (task tracker)
                    # can mark background commit tasks as failed with an explicit error.
                    candidates = await self.extractor.extract_strict(context, user, session_id)
                else:
                    candidates = await self.extractor.extract(context, user, session_id)

                if not candidates:
                    return []

                tool_skill_count = sum(
                    1 for candidate in candidates if candidate.category in TOOL_SKILL_CATEGORIES
                )
                telemetry.set("memory.extract.candidates.total", len(candidates))
                telemetry.set("memory.extract.candidates.tool_skill", tool_skill_count)
                telemetry.set(
                    "memory.extract.candidates.standard",
                    len(candidates) - tool_skill_count,
                )

                memories: List[Context] = []
                stats = ExtractionStats()
                # Track created memories' embeddings for batch-internal dedup (#687)
                batch_memories: list[tuple[list[float], Context]] = []
                viking_fs = get_viking_fs()

                tool_parts = self._extract_tool_parts(messages)
                from .tool_skill_utils import collect_skill_stats, collect_tool_stats

                tool_stats_map = collect_tool_stats(tool_parts)
                skill_stats_map = collect_skill_stats(tool_parts)

                for candidate in candidates:
                    # Profile: skip dedup, always merge
                    if candidate.category in ALWAYS_MERGE_CATEGORIES:
                        with telemetry.measure("memory.extract.stage.profile_create"):
                            memory = await self.extractor.create_memory(
                                candidate, user, session_id, ctx=ctx
                            )
                        if memory:
                            memories.append(memory)
                            stats.created += 1
                            await self._index_memory(memory, ctx)
                        else:
                            stats.skipped += 1
                        continue

                    # Tool/Skill Memory: 特殊合并逻辑
                    if candidate.category in TOOL_SKILL_CATEGORIES:
                        if isinstance(candidate, ToolSkillCandidateMemory):
                            tool_name, skill_name, tool_status = self._get_tool_skill_info(
                                candidate, tool_parts
                            )
                            candidate.tool_status = tool_status
                            if tool_name:
                                candidate.tool_name = tool_name
                            if skill_name:
                                candidate.skill_name = skill_name

                            if tool_name and candidate.call_time == 0:
                                tool_stats = tool_stats_map.get(tool_name, {})
                                candidate.call_time = tool_stats.get(
                                    "call_count", candidate.call_time
                                )
                                candidate.success_time = tool_stats.get(
                                    "success_time", candidate.success_time
                                )
                                candidate.duration_ms = tool_stats.get(
                                    "duration_ms", candidate.duration_ms
                                )
                                candidate.prompt_tokens = tool_stats.get(
                                    "prompt_tokens", candidate.prompt_tokens
                                )
                                candidate.completion_tokens = tool_stats.get(
                                    "completion_tokens", candidate.completion_tokens
                                )

                            if skill_name and candidate.call_time == 0:
                                skill_stats = skill_stats_map.get(skill_name, {})
                                candidate.call_time = skill_stats.get(
                                    "call_count", candidate.call_time
                                )
                                candidate.success_time = skill_stats.get(
                                    "success_time", candidate.success_time
                                )
                            with telemetry.measure("memory.extract.stage.tool_skill_merge"):
                                if skill_name:
                                    memory = await self.extractor._merge_skill_memory(
                                        skill_name, candidate, ctx=ctx
                                    )
                                elif tool_name:
                                    memory = await self.extractor._merge_tool_memory(
                                        tool_name, candidate, ctx=ctx
                                    )
                                else:
                                    memory = None
                            if not tool_name and not skill_name:
                                logger.warning("No tool_name or skill_name found, skipping")
                                stats.skipped += 1
                                continue
                            if memory:
                                memories.append(memory)
                                stats.merged += 1
                                await self._index_memory(memory, ctx, change_type="modified")
                        continue

                    # Dedup check for other categories
                    with telemetry.measure("memory.extract.stage.dedup"):
                        result = await self.deduplicator.deduplicate(
                            candidate, ctx, batch_memories=batch_memories
                        )
                    actions = result.actions or []
                    decision = result.decision

                    # Safety net: create+merge should be treated as none.
                    if decision == DedupDecision.CREATE and any(
                        a.decision == MemoryActionDecision.MERGE for a in actions
                    ):
                        logger.warning(
                            f"Dedup returned create with merge action, normalizing to none: "
                            f"{candidate.abstract}"
                        )
                        decision = DedupDecision.NONE

                    if decision == DedupDecision.SKIP:
                        stats.skipped += 1
                        continue

                    if decision == DedupDecision.NONE:
                        if not actions:
                            stats.skipped += 1
                            continue

                        for action in actions:
                            if action.decision == MemoryActionDecision.DELETE:
                                with telemetry.measure("memory.extract.stage.delete_existing"):
                                    deleted = viking_fs and await self._delete_existing_memory(
                                        action.memory, viking_fs, ctx=ctx
                                    )
                                if deleted:
                                    stats.deleted += 1
                                    # Remove deleted memory from batch tracking (#687)
                                    batch_memories = [
                                        (v, m)
                                        for v, m in batch_memories
                                        if m.uri != action.memory.uri
                                    ]
                                else:
                                    stats.skipped += 1
                            elif action.decision == MemoryActionDecision.MERGE:
                                if candidate.category in MERGE_SUPPORTED_CATEGORIES and viking_fs:
                                    with telemetry.measure("memory.extract.stage.merge_existing"):
                                        merged = await self._merge_into_existing(
                                            candidate, action.memory, viking_fs, ctx=ctx
                                        )
                                    if merged:
                                        stats.merged += 1
                                        # Remove stale batch entry and re-add with updated
                                        # embedding so 3rd+ candidates can still find it (#687).
                                        batch_memories = [
                                            (v, m)
                                            for v, m in batch_memories
                                            if m.uri != action.memory.uri
                                        ]
                                        if self.deduplicator.embedder:
                                            merged_text = (
                                                f"{action.memory.abstract} {candidate.content}"
                                            )
                                            merged_embed = await embed_compat(
                                                self.deduplicator.embedder,
                                                merged_text,
                                            )
                                            batch_memories.append(
                                                (merged_embed.dense_vector, action.memory)
                                            )
                                    else:
                                        stats.skipped += 1
                                else:
                                    # events/cases don't support MERGE, treat as SKIP
                                    stats.skipped += 1
                        continue

                    if decision == DedupDecision.CREATE:
                        # create can optionally include delete actions (delete first, then create)
                        for action in actions:
                            if action.decision == MemoryActionDecision.DELETE:
                                with telemetry.measure("memory.extract.stage.delete_existing"):
                                    deleted = viking_fs and await self._delete_existing_memory(
                                        action.memory, viking_fs, ctx=ctx
                                    )
                                if deleted:
                                    stats.deleted += 1
                                    # Remove deleted memory from batch tracking (#687)
                                    batch_memories = [
                                        (v, m)
                                        for v, m in batch_memories
                                        if m.uri != action.memory.uri
                                    ]
                                else:
                                    stats.skipped += 1

                        with telemetry.measure("memory.extract.stage.create_memory"):
                            memory = await self.extractor.create_memory(
                                candidate, user, session_id, ctx=ctx
                            )
                        if memory:
                            memories.append(memory)
                            stats.created += 1
                            await self._index_memory(memory, ctx)
                            # Store embedding for batch-internal dedup of subsequent candidates (#687)
                            if result.query_vector:
                                batch_memories.append((result.query_vector, memory))
                        else:
                            stats.skipped += 1

                # Extract URIs used in messages, create relations
                used_uris = self._extract_used_uris(messages)
                if used_uris and memories:
                    with telemetry.measure("memory.extract.stage.create_relations"):
                        await self._create_relations(memories, used_uris, ctx=ctx)

                with telemetry.measure("memory.extract.stage.flush_semantic"):
                    await self._flush_semantic_operations(ctx)

                telemetry.set("memory.extract.created", stats.created)
                telemetry.set("memory.extract.merged", stats.merged)
                telemetry.set("memory.extract.deleted", stats.deleted)
                telemetry.set("memory.extract.skipped", stats.skipped)

                logger.info(
                    f"Memory extraction: created={stats.created}, "
                    f"merged={stats.merged}, deleted={stats.deleted}, skipped={stats.skipped}"
                )
                return memories

            except Exception:
                self._pending_semantic_changes.clear()
                raise

    def _extract_tool_parts(self, messages: List[Message]) -> List:
        """Extract all ToolPart from messages."""
        from openviking.message.part import ToolPart

        tool_parts = []
        for msg in messages:
            for part in getattr(msg, "parts", []):
                if isinstance(part, ToolPart):
                    tool_parts.append(part)
        return tool_parts

    def _get_tool_skill_info(
        self, candidate: "ToolSkillCandidateMemory", tool_parts: List
    ) -> tuple:
        """Get tool_name, skill_name and tool_status with calibration from ToolPart.

        LLM candidate provides initial guess, ToolPart provides ground truth for calibration.
        For tools: ToolPart.tool_name is authoritative
        For skills: Use similarity matching between candidate.skill_name and ToolPart info

        Returns:
            (tool_name, skill_name, tool_status) tuple
        """
        from .tool_skill_utils import calibrate_skill_name, calibrate_tool_name

        if candidate.category == MemoryCategory.TOOLS:
            candidate_tool = (candidate.tool_name or "").strip()
            if not candidate_tool:
                return ("", "", "completed")
            calibrated_name, status = calibrate_tool_name(candidate_tool, tool_parts)
            return (calibrated_name, "", status)

        if candidate.category == MemoryCategory.SKILLS:
            candidate_skill = (candidate.skill_name or "").strip()
            if not candidate_skill:
                return ("", "", "completed")
            calibrated_name, status = calibrate_skill_name(candidate_skill, tool_parts)
            return ("", calibrated_name, status)

        return ("", "", "completed")

    def _is_similar_name(self, name1: str, name2: str) -> bool:
        """Check if two names are similar enough to be considered the same.

        Uses simple string similarity for now. Can be extended with LLM-based matching.
        """
        if not name1 or not name2:
            return False

        n1 = name1.lower().strip().replace("_", "").replace("-", "")
        n2 = name2.lower().strip().replace("_", "").replace("-", "")

        if n1 == n2:
            return True

        if n1 in n2 or n2 in n1:
            return True

        from difflib import SequenceMatcher

        ratio = SequenceMatcher(None, n1, n2).ratio()
        return ratio >= 0.7

    def _extract_used_uris(self, messages: List[Message]) -> Dict[str, List[str]]:
        """Extract URIs used in messages."""
        uris = {"memories": set(), "resources": set(), "skills": set()}

        for msg in messages:
            for part in msg.parts:
                if part.type == "context":
                    if part.uri and part.context_type in uris:
                        uris[part.context_type].add(part.uri)
                elif part.type == "tool":
                    if part.skill_uri:
                        uris["skills"].add(part.skill_uri)

        return {k: list(v) for k, v in uris.items() if v}

    async def _create_relations(
        self,
        memories: List[Context],
        used_uris: Dict[str, List[str]],
        ctx: RequestContext,
    ) -> None:
        """Create bidirectional relations between memories and resources/skills."""
        viking_fs = get_viking_fs()
        if not viking_fs:
            return

        try:
            memory_uris = [m.uri for m in memories]
            resource_uris = used_uris.get("resources", [])
            skill_uris = used_uris.get("skills", [])

            valid_resource_uris = []
            for uri in resource_uris:
                if await self._uri_exists(uri, viking_fs, ctx):
                    valid_resource_uris.append(uri)

            valid_skill_uris = []
            for uri in skill_uris:
                if await self._uri_exists(uri, viking_fs, ctx):
                    valid_skill_uris.append(uri)

            for memory_uri in memory_uris:
                if valid_resource_uris:
                    await viking_fs.link(
                        memory_uri,
                        valid_resource_uris,
                        reason="Memory extracted from session using these resources",
                        ctx=ctx,
                    )
                if valid_skill_uris:
                    await viking_fs.link(
                        memory_uri,
                        valid_skill_uris,
                        reason="Memory extracted from session calling these skills",
                        ctx=ctx,
                    )

            for resource_uri in valid_resource_uris:
                await viking_fs.link(
                    resource_uri, memory_uris, reason="Referenced by these memories", ctx=ctx
                )
            for skill_uri in valid_skill_uris:
                await viking_fs.link(
                    skill_uri, memory_uris, reason="Called by these memories", ctx=ctx
                )

            logger.info(f"Created bidirectional relations for {len(memories)} memories")
        except Exception as e:
            logger.error(f"Error creating memory relations: {e}")

    async def _uri_exists(self, uri: str, viking_fs, ctx: RequestContext) -> bool:
        """Check if a URI exists."""
        try:
            await viking_fs.read_file(uri, ctx=ctx)
            return True
        except Exception:
            return False

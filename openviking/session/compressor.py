# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""
Session Compressor for OpenViking.

Handles extraction of long-term memories from session conversations.
Uses MemoryExtractor for 6-category extraction and MemoryDeduplicator for LLM-based dedup.
"""

from dataclasses import dataclass
from typing import Dict, List, Optional

from openviking.core.context import Context, Vectorize
from openviking.message import Message
from openviking.storage import VikingDBManager
from openviking.storage.viking_fs import get_viking_fs
from openviking_cli.session.user_id import UserIdentifier
from openviking_cli.utils import get_logger

from .memory_deduplicator import DedupDecision, MemoryActionDecision, MemoryDeduplicator
from .memory_extractor import CandidateMemory, MemoryCategory, MemoryExtractor

logger = get_logger(__name__)

# Categories that always merge (skip dedup)
ALWAYS_MERGE_CATEGORIES = {MemoryCategory.PROFILE}

# Categories that support MERGE decision
MERGE_SUPPORTED_CATEGORIES = {
    MemoryCategory.PREFERENCES,
    MemoryCategory.ENTITIES,
    MemoryCategory.PATTERNS,
}


@dataclass
class ExtractionStats:
    """Statistics for memory extraction."""

    created: int = 0
    merged: int = 0
    deleted: int = 0
    skipped: int = 0


class SessionCompressor:
    """Session memory extractor with 6-category memory extraction."""

    def __init__(
        self,
        vikingdb: VikingDBManager,
    ):
        """Initialize session compressor."""
        self.vikingdb = vikingdb
        self.extractor = MemoryExtractor()
        self.deduplicator = MemoryDeduplicator(vikingdb=vikingdb)

    async def _index_memory(self, memory: Context) -> bool:
        """Add memory to vectorization queue."""
        from openviking.storage.queuefs.embedding_msg_converter import EmbeddingMsgConverter

        embedding_msg = EmbeddingMsgConverter.from_context(memory)
        await self.vikingdb.enqueue_embedding_msg(embedding_msg)
        logger.info(f"Enqueued memory for vectorization: {memory.uri}")
        return True

    async def _merge_into_existing(
        self,
        candidate: CandidateMemory,
        target_memory: Context,
        viking_fs,
    ) -> bool:
        """Merge candidate content into an existing memory file."""
        try:
            existing_content = await viking_fs.read_file(target_memory.uri)
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

            await viking_fs.write_file(target_memory.uri, payload.content)
            target_memory.abstract = payload.abstract
            target_memory.meta = {**(target_memory.meta or {}), "overview": payload.overview}
            logger.info(
                "Merged memory %s with abstract %s", target_memory.uri, target_memory.abstract
            )
            target_memory.set_vectorize(Vectorize(text=payload.content))
            await self._index_memory(target_memory)
            return True
        except Exception as e:
            logger.error(f"Failed to merge memory {target_memory.uri}: {e}")
            return False

    async def _delete_existing_memory(self, memory: Context, viking_fs) -> bool:
        """Hard delete an existing memory file and clean up its vector record."""
        try:
            await viking_fs.rm(memory.uri, recursive=False)
        except Exception as e:
            logger.error(f"Failed to delete memory file {memory.uri}: {e}")
            return False

        try:
            # rm() already syncs vector deletion in most cases; keep this as a safe fallback.
            await self.vikingdb.remove_by_uri("context", memory.uri)
        except Exception as e:
            logger.warning(f"Failed to remove vector record for {memory.uri}: {e}")
        return True

    async def extract_long_term_memories(
        self,
        messages: List[Message],
        user: Optional["UserIdentifier"] = None,
        session_id: Optional[str] = None,
    ) -> List[Context]:
        """Extract long-term memories from messages."""
        if not messages:
            return []

        context = {"messages": messages}
        candidates = await self.extractor.extract(context, user, session_id)

        if not candidates:
            return []

        memories: List[Context] = []
        stats = ExtractionStats()
        viking_fs = get_viking_fs()

        for candidate in candidates:
            # Profile: skip dedup, always merge
            if candidate.category in ALWAYS_MERGE_CATEGORIES:
                memory = await self.extractor.create_memory(candidate, user, session_id)
                if memory:
                    memories.append(memory)
                    stats.created += 1
                    await self._index_memory(memory)
                else:
                    stats.skipped += 1
                continue

            # Dedup check for other categories
            result = await self.deduplicator.deduplicate(candidate)
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
                        if viking_fs and await self._delete_existing_memory(
                            action.memory, viking_fs
                        ):
                            stats.deleted += 1
                        else:
                            stats.skipped += 1
                    elif action.decision == MemoryActionDecision.MERGE:
                        if candidate.category in MERGE_SUPPORTED_CATEGORIES and viking_fs:
                            if await self._merge_into_existing(candidate, action.memory, viking_fs):
                                stats.merged += 1
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
                        if viking_fs and await self._delete_existing_memory(
                            action.memory, viking_fs
                        ):
                            stats.deleted += 1
                        else:
                            stats.skipped += 1

                memory = await self.extractor.create_memory(candidate, user, session_id)
                if memory:
                    memories.append(memory)
                    stats.created += 1
                    await self._index_memory(memory)
                else:
                    stats.skipped += 1

        # Extract URIs used in messages, create relations
        used_uris = self._extract_used_uris(messages)
        if used_uris and memories:
            await self._create_relations(memories, used_uris)

        logger.info(
            f"Memory extraction: created={stats.created}, "
            f"merged={stats.merged}, deleted={stats.deleted}, skipped={stats.skipped}"
        )
        return memories

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
    ) -> None:
        """Create bidirectional relations between memories and resources/skills."""
        viking_fs = get_viking_fs()
        if not viking_fs:
            return

        try:
            memory_uris = [m.uri for m in memories]
            resource_uris = used_uris.get("resources", [])
            skill_uris = used_uris.get("skills", [])

            # Memory -> resources/skills
            for memory_uri in memory_uris:
                if resource_uris:
                    await viking_fs.link(
                        memory_uri,
                        resource_uris,
                        reason="Memory extracted from session using these resources",
                    )
                if skill_uris:
                    await viking_fs.link(
                        memory_uri,
                        skill_uris,
                        reason="Memory extracted from session calling these skills",
                    )

            # Resources/skills -> memories (reverse)
            for resource_uri in resource_uris:
                await viking_fs.link(
                    resource_uri, memory_uris, reason="Referenced by these memories"
                )
            for skill_uri in skill_uris:
                await viking_fs.link(skill_uri, memory_uris, reason="Called by these memories")

            logger.info(f"Created bidirectional relations for {len(memories)} memories")
        except Exception as e:
            logger.error(f"Error creating memory relations: {e}")

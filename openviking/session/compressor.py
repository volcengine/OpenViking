# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""
Session Compressor for OpenViking.

Handles extraction of long-term memories from session conversations.
Uses MemoryExtractor for 6-category extraction and MemoryDeduplicator for LLM-based dedup.
"""

from dataclasses import dataclass
from typing import Dict, List

from openviking.core.context import Context
from openviking.message import Message
from openviking.storage import VikingDBManager
from openviking.storage.viking_fs import get_viking_fs
from openviking.utils import get_logger

from .memory_deduplicator import DedupDecision, MemoryDeduplicator
from .memory_extractor import MemoryExtractor

logger = get_logger(__name__)


@dataclass
class ExtractionStats:
    """Statistics for memory extraction."""

    created: int = 0
    updated: int = 0
    merged: int = 0
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

    async def extract_long_term_memories(
        self,
        messages: List[Message],
        user: str,
        session_id: str,
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

        for candidate in candidates:
            result = await self.deduplicator.deduplicate(candidate)

            if result.decision == DedupDecision.SKIP:
                stats.skipped += 1
                continue

            if result.decision == DedupDecision.CREATE:
                memory = await self.extractor.create_memory(candidate, user, session_id)
                if memory:
                    memories.append(memory)
                    stats.created += 1
                    await self._index_memory(memory)

            elif result.decision == DedupDecision.UPDATE:
                if result.similar_memories and result.merged_content:
                    candidate.content = result.merged_content
                    memory = await self.extractor.create_memory(candidate, user, session_id)
                    if memory:
                        memories.append(memory)
                        stats.updated += 1
                        await self._index_memory(memory)

            elif result.decision == DedupDecision.MERGE:
                if result.merged_content:
                    candidate.content = result.merged_content
                    memory = await self.extractor.create_memory(candidate, user, session_id)
                    if memory:
                        memories.append(memory)
                        stats.merged += 1
                        await self._index_memory(memory)

        # Extract URIs used in messages, create relations
        used_uris = self._extract_used_uris(messages)
        if used_uris and memories:
            await self._create_relations(memories, used_uris)

        logger.info(
            f"Memory extraction: created={stats.created}, updated={stats.updated}, "
            f"merged={stats.merged}, skipped={stats.skipped}"
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
